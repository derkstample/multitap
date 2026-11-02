// multitap-session: the in-game window. Spawns one multitap-worker per player, starts them
// together, and composites their frames into a split-screen layout with an OSD.
//
//   multitap-session CONFIG.json [--hidden] [--size WxH] [--frames N --screenshot OUT.bmp]
//
// CONFIG.json is written by the launcher (see config.hpp). --frames/--screenshot wait until
// every player has run N frames, save the window as a BMP and exit (for tests).
//
// Hotkeys: Esc back to the launcher (twice quickly: quit MultiTap), P pause, R reset all,
// F1 toggle the OSD.
//
// On exit the last stdout line is a JSON object:
//   {"type": "exit", "esc_ns": <CLOCK_MONOTONIC of the Esc press, or 0>, "osd_enabled": bool}
// so the launcher can keep the OSD toggle and treat an Esc right after as the second press.
// Exit status: 0 back to the launcher, 3 quit MultiTap, 1 could not start, 2 bad arguments.
#include <SDL.h>
#include <signal.h>
#include <unistd.h>

#include <cmath>
#include <csignal>
#include <filesystem>
#include <iostream>
#include <memory>
#include <optional>
#include <set>
#include <string>
#include <vector>

#include "audio.hpp"
#include "config.hpp"
#include "input.hpp"
#include "osd.hpp"
#include "players.hpp"
#include "renderer.hpp"

namespace fs = std::filesystem;
using nlohmann::json;
using namespace mt::session;

namespace {

constexpr int64_t kSecond = 1'000'000'000;
constexpr int64_t kLoadTimeoutNs = 20 * kSecond;  // start whoever is ready after this long
constexpr int64_t kStartLeadNs = 50'000'000;       // every worker gets t0 this far ahead
constexpr int64_t kDoubleEscNs = 500'000'000;
constexpr int64_t kQuitGraceNs = 3 * kSecond;      // then SIGKILL (SRAM may be lost)
constexpr int64_t kDeviceGraceNs = kSecond;        // controllers still being enumerated
constexpr int64_t kWarningNs = 8 * kSecond;
constexpr int kExitQuitApp = 3;
const char *kPausedHelp = "P resume  ·  R reset  ·  F1 OSD  ·  Esc menu (twice to quit)";

volatile sig_atomic_t g_term = 0;

void log(const std::string &msg) { std::cerr << "multitap-session: " << msg << std::endl; }

struct Options {
  std::string config, screenshot;
  bool hidden = false;
  int frames = 0, width = 0, height = 0;
};

std::string self_dir() {
  std::error_code ec;
  fs::path p = fs::read_symlink("/proc/self/exe", ec);
  return ec ? "." : p.parent_path().string();
}

class Session {
 public:
  Session(Config cfg, Options opt)
      : cfg_(std::move(cfg)), opt_(std::move(opt)) {
    for (size_t i = 0; i < cfg_.players.size(); i++)
      players_.push_back(std::make_unique<Player>(int(i), cfg_.players[i]));
  }

  ~Session() {
    audio_.close();  // before the rings it reads are unmapped
    input_.reset();
    overlay_.reset();  // GL objects go before the context
    renderer_.reset();
    if (gl_) SDL_GL_DeleteContext(gl_);
    if (window_) SDL_DestroyWindow(window_);
    SDL_Quit();
  }

  int run() {
    // Workers first: they load while the window and GL come up.
    std::string worker = cfg_.worker.empty() ? self_dir() + "/multitap-worker" : cfg_.worker;
    for (auto &p : players_) p->spawn(worker, cfg_, p->slot == audible_);
    started_ns_ = mt::session::now_ns();
    if (std::string err = init_window(); !err.empty()) {
      log(err);
      for (auto &p : players_) p->kill();
      emit_exit();
      return 1;
    }
    input_ = std::make_unique<Input>(players_);
    if (std::string warn = audio_.open(cfg_.audio); !warn.empty()) warn_(warn);

    while (!done()) {
      pump();
      if (g_term) begin_quit();
      poll_players();
      if (!released_ && !quitting_) maybe_release();
      check_devices();
      rotate_if_due();
      render();
    }
    emit_exit();
    return quit_app_ ? kExitQuitApp : 0;
  }

 private:
  // ---- setup -----------------------------------------------------------------------------
  std::string init_window() {
    SDL_SetHint(SDL_HINT_NO_SIGNAL_HANDLERS, "1");
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
    SDL_SetHint(SDL_HINT_VIDEO_MINIMIZE_ON_FOCUS_LOSS, "0");
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMECONTROLLER | SDL_INIT_EVENTS) != 0)
      return std::string("SDL_Init: ") + SDL_GetError();
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION, 3);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, 3);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, SDL_GL_CONTEXT_PROFILE_CORE);
    SDL_GL_SetAttribute(SDL_GL_DOUBLEBUFFER, 1);

    // The display the launcher was on.
    int display = 0;
    if (cfg_.screen[2] > 0) {
      SDL_Point c{cfg_.screen[0] + cfg_.screen[2] / 2, cfg_.screen[1] + cfg_.screen[3] / 2};
      for (int i = 0; i < SDL_GetNumVideoDisplays(); i++) {
        SDL_Rect b;
        if (SDL_GetDisplayBounds(i, &b) == 0 && SDL_PointInRect(&c, &b)) display = i;
      }
    }
    Uint32 flags = SDL_WINDOW_OPENGL | SDL_WINDOW_ALLOW_HIGHDPI;
    if (opt_.hidden) flags |= SDL_WINDOW_HIDDEN;
    int w = opt_.width, h = opt_.height;
    if (!w || !h) {
      SDL_Rect b{0, 0, 1280, 720};
      SDL_GetDisplayBounds(display, &b);
      w = b.w;
      h = b.h;
      flags |= SDL_WINDOW_FULLSCREEN_DESKTOP;
    }
    window_ = SDL_CreateWindow("MultiTap", SDL_WINDOWPOS_CENTERED_DISPLAY(display),
                               SDL_WINDOWPOS_CENTERED_DISPLAY(display), w, h, flags);
    if (!window_) return std::string("SDL_CreateWindow: ") + SDL_GetError();
    gl_ = SDL_GL_CreateContext(window_);
    if (!gl_) return std::string("OpenGL 3.3: ") + SDL_GetError();
    SDL_GL_MakeCurrent(window_, gl_);
    // Prefer adaptive vsync (late frames tear instead of waiting a whole refresh).
    if (cfg_.graphics.vsync) {
      if (SDL_GL_SetSwapInterval(-1) != 0) SDL_GL_SetSwapInterval(1);
    } else {
      SDL_GL_SetSwapInterval(0);
    }
    SDL_ShowCursor(SDL_DISABLE);
    SDL_GL_GetDrawableSize(window_, &width_, &height_);
    renderer_ = std::make_unique<Renderer>(cfg_.graphics, players_.size());
    if (std::string err = renderer_->init(); !err.empty()) return err;
    overlay_ = std::make_unique<Overlay>();
    overlay_->init(window_, gl_, height_);
    log(std::string("OpenGL ") + reinterpret_cast<const char *>(glGetString(GL_VERSION)) + ", " +
        std::to_string(width_) + "x" + std::to_string(height_));
    return "";
  }

  // ---- events ----------------------------------------------------------------------------
  void pump() {
    SDL_Event e;
    while (SDL_PollEvent(&e)) {
      if (e.type == SDL_QUIT) {
        begin_quit();
        continue;
      }
      if (e.type == SDL_WINDOWEVENT) {
        if (e.window.event == SDL_WINDOWEVENT_FOCUS_LOST) input_->clear_keyboard();
        if (e.window.event == SDL_WINDOWEVENT_SIZE_CHANGED)
          SDL_GL_GetDrawableSize(window_, &width_, &height_);
        continue;
      }
      switch (input_->handle(e)) {
        case Hotkey::Escape:
          if (quitting_ && mt::session::now_ns() - esc_ns_ <= kDoubleEscNs) quit_app_ = true;
          else {
            esc_ns_ = mt::session::now_ns();
            begin_quit();
          }
          break;
        case Hotkey::Pause:
          if (!quitting_) set_paused(!paused_);
          break;
        case Hotkey::Reset:
          if (quitting_) break;
          for (auto &p : players_) p->send({{"type", "reset"}});
          toast("Reset", 1.2);
          break;
        case Hotkey::ToggleOsd:
          cfg_.osd.enabled = !cfg_.osd.enabled;
          toast(cfg_.osd.enabled ? "OSD on" : "OSD off", 0.8);
          break;
        case Hotkey::None:
          break;
      }
    }
  }

  // ---- workers ---------------------------------------------------------------------------
  void poll_players() {
    for (auto &p : players_) {
      bool was_loaded = p->loaded;
      p->poll();
      if (!was_loaded && p->loaded) on_loaded(*p);
    }
    // The audible player failed or stopped: hand audio to the first healthy one.
    Player &cur = *players_[audible_];
    if (!quitting_ && (cur.exited || !cur.error.empty()) && cur.settled()) {
      for (auto &p : players_) {
        if (p->ready()) {
          set_audible(p->slot);
          break;
        }
      }
    }
  }

  void on_loaded(Player &p) {
    input_->refresh(p.slot);
    if (p.slot == audible_) audio_.set_source(&*p.audio);
    if (released_ && !paused_ && !quitting_)  // loaded after the barrier gave up waiting
      p.send({{"type", "start"}, {"t0_ns", mt::session::now_ns()}});
  }

  // Start everyone on the same frame once all players have loaded (or failed).
  void maybe_release() {
    int64_t now = mt::session::now_ns();
    bool all = true;
    for (auto &p : players_) all = all && p->settled();
    if (!all && now - started_ns_ < kLoadTimeoutNs) return;
    released_ = true;
    std::string loads;
    for (auto &p : players_) {
      char buf[64];
      if (p->loaded) std::snprintf(buf, sizeof buf, "P%d %.2fs", p->cfg.index + 1,
                                   (p->loaded_ns - p->spawned_ns) / 1e9);
      else std::snprintf(buf, sizeof buf, "P%d failed", p->cfg.index + 1);
      loads += (loads.empty() ? "" : ", ") + std::string(buf);
    }
    log("players loaded: " + loads + "; released together");
    if (!paused_) {
      int64_t t0 = now + kStartLeadNs;
      for (auto &p : players_) p->send({{"type", "start"}, {"t0_ns", t0}});
      next_rotate_ns_ = now + int64_t(cfg_.audio.rotation_s) * kSecond;
    } else {
      rotate_left_ns_ = int64_t(cfg_.audio.rotation_s) * kSecond;
    }
  }

  void set_paused(bool paused) {
    if (paused == paused_) return;
    paused_ = paused;
    if (!released_) return;  // workers are still waiting for start; the barrier honours this
    int64_t now = mt::session::now_ns();
    if (paused) {
      for (auto &p : players_) p->send({{"type", "pause"}});
      rotate_left_ns_ = std::max<int64_t>(0, next_rotate_ns_ - now);
    } else {
      // One shared t0 keeps everyone in step after a pause.
      int64_t t0 = now + kStartLeadNs / 2;
      for (auto &p : players_) p->send({{"type", "resume"}, {"t0_ns", t0}});
      next_rotate_ns_ = now + rotate_left_ns_;
    }
  }

  int ready_count() const {
    int n = 0;
    for (auto &p : players_) n += p->ready();
    return n;
  }

  void set_audible(int slot) {
    if (slot != audible_) players_[audible_]->send({{"type", "audible"}, {"audible", false}});
    audible_ = slot;
    Player &p = *players_[slot];
    p.send({{"type", "audible"}, {"audible", true}});
    audio_.set_source(p.audio ? &*p.audio : nullptr);
  }

  void rotate_if_due() {
    if (!released_ || paused_ || quitting_ || ready_count() < 2) return;
    int64_t now = mt::session::now_ns();
    if (now < next_rotate_ns_) return;
    next_rotate_ns_ = now + int64_t(cfg_.audio.rotation_s) * kSecond;
    int next = -1;
    for (auto &p : players_)
      if (p->ready() && p->slot > audible_) {
        next = p->slot;
        break;
      }
    if (next < 0)
      for (auto &p : players_)
        if (p->ready()) {
          next = p->slot;
          break;
        }
    if (next >= 0 && next != audible_) set_audible(next);
  }

  void check_devices() {
    bool changed = input_->take_devices_changed();
    bool armed = released_ || mt::session::now_ns() - started_ns_ > kDeviceGraceNs;
    if (!changed && (devices_armed_ || !armed)) return;
    if (!armed) return;
    devices_armed_ = true;
    std::set<int> missing = input_->missing_slots();
    bool newly = false;
    for (int s : missing) newly = newly || !disconnected_.count(s);
    disconnected_ = missing;
    if (newly && !paused_ && !quitting_) {
      set_paused(true);
    }
  }

  // ---- quitting --------------------------------------------------------------------------
  void begin_quit() {
    if (quitting_) return;
    quitting_ = true;
    quit_deadline_ns_ = mt::session::now_ns() + kQuitGraceNs;
    audio_.set_source(nullptr);
    for (auto &p : players_) p->quit();
  }

  bool done() {
    if (!quitting_) return false;
    bool alive = false;
    for (auto &p : players_) alive = alive || p->alive();
    if (!alive) return true;
    if (mt::session::now_ns() > quit_deadline_ns_) {
      for (auto &p : players_)
        if (p->alive()) {
          log("P" + std::to_string(p->cfg.index + 1) + " did not quit in time; killed");
          p->kill();
        }
      return true;
    }
    return false;
  }

  void emit_exit() {
    std::cout << "\n"
              << json{{"type", "exit"}, {"esc_ns", esc_ns_}, {"osd_enabled", cfg_.osd.enabled},
                      {"quit", quit_app_}}.dump()
              << std::endl;
  }

  // ---- drawing ---------------------------------------------------------------------------
  void toast(const std::string &text, double seconds) {
    toast_ = text;
    toast_until_ns_ = mt::session::now_ns() + int64_t(seconds * kSecond);
  }

  void warn_(const std::string &text) {
    log(text);
    warning_ = text;
    warning_until_ns_ = mt::session::now_ns() + kWarningNs;
  }

  void render() {
    int64_t now = mt::session::now_ns();
    bool shot = !opt_.screenshot.empty();
    renderer_->begin(width_, height_, shot);
    auto rects = cfg_.layout.pixel_rects(width_, height_, kTileGap);
    bool fresh = false;
    OsdView v;
    v.width = width_;
    v.height = height_;
    v.cfg = cfg_.osd;
    v.game = fs::path(cfg_.rom_path).stem().string();
    v.paused = paused_ && !quitting_;
    v.quitting = quitting_;
    v.banner_sub = kPausedHelp;
    v.show_speaker = players_.size() > 1;
    v.display_fps = display_fps_;
    if (released_ && ready_count() > 1) {
      int64_t left = paused_ ? rotate_left_ns_ : next_rotate_ns_ - now;
      v.audio_switch_s = int(std::max<int64_t>(0, (left + kSecond - 1) / kSecond));
    }
    bool all_shown = true;
    for (size_t i = 0; i < players_.size(); i++) {
      Player &p = *players_[i];
      fresh |= renderer_->update(i, p);
      double aspect = p.video ? p.video->header().aspect : p.aspect;
      renderer_->draw(i, rects[i], aspect, p.fps);
      TileView t;
      t.player = &p;
      t.frame = &renderer_->frame(i);
      t.rect = rects[i];
      t.audible = int(i) == audible_ && p.ready();
      t.disconnected = disconnected_.count(int(i)) > 0;
      t.held = p.input ? p.input->held() : 0;
      v.tiles.push_back(t);
      if (p.ready() && t.frame->frame_no < uint64_t(opt_.frames)) all_shown = false;
    }
    if (!renderer_->shader_warning().empty() && warned_shader_ != renderer_->shader_warning()) {
      warned_shader_ = renderer_->shader_warning();
      warn_(warned_shader_);
    }
    if (now < toast_until_ns_) v.toast = toast_;
    if (now < warning_until_ns_) v.warning = warning_;
    overlay_->draw(v);

    if (shot) {
      std::vector<uint8_t> rgb = renderer_->finish_offscreen();
      if (released_ && all_shown && !quitting_) {  // (nobody ready: a picture of the errors)
        std::string frames;
        for (size_t i = 0; i < players_.size(); i++)
          frames += (i ? ", P" : "P") + std::to_string(players_[i]->cfg.index + 1) + " " +
                    std::to_string(renderer_->frame(i).frame_no);
        log("screenshot at frames: " + frames);
        save_bmp(rgb);
        begin_quit();
      }
    }
    SDL_GL_SwapWindow(window_);

    swaps_++;
    if (now - fps_t0_ >= kSecond) {
      display_fps_ = swaps_ * double(kSecond) / double(now - fps_t0_);
      swaps_ = 0;
      fps_t0_ = now;
    }
    // Without vsync (or with a hidden window) swapping doesn't wait: don't spin.
    if (!fresh && mt::session::now_ns() - now < 2'000'000) SDL_Delay(1);
  }

  void save_bmp(std::vector<uint8_t> &rgb) {
    SDL_Surface *s = SDL_CreateRGBSurfaceWithFormatFrom(rgb.data(), width_, height_, 24,
                                                        width_ * 3, SDL_PIXELFORMAT_RGB24);
    if (!s || SDL_SaveBMP(s, opt_.screenshot.c_str()) != 0)
      log(std::string("screenshot failed: ") + SDL_GetError());
    if (s) SDL_FreeSurface(s);
  }

  Config cfg_;
  Options opt_;
  std::vector<std::unique_ptr<Player>> players_;
  SDL_Window *window_ = nullptr;
  SDL_GLContext gl_ = nullptr;
  int width_ = 0, height_ = 0;
  std::unique_ptr<Renderer> renderer_;
  std::unique_ptr<Overlay> overlay_;
  std::unique_ptr<Input> input_;
  AudioOut audio_;

  int audible_ = 0;
  bool released_ = false, paused_ = false;
  bool quitting_ = false, quit_app_ = false, devices_armed_ = false;
  std::set<int> disconnected_;
  int64_t started_ns_ = 0, esc_ns_ = 0, quit_deadline_ns_ = 0;
  int64_t next_rotate_ns_ = 0, rotate_left_ns_ = 0;
  std::string toast_, warning_, warned_shader_;
  int64_t toast_until_ns_ = 0, warning_until_ns_ = 0;
  int swaps_ = 0;
  int64_t fps_t0_ = 0;
  double display_fps_ = 0;
};

int usage() {
  std::cerr << "usage: multitap-session CONFIG.json [--hidden] [--size WxH]\n"
               "                        [--frames N --screenshot OUT.bmp]\n";
  return 2;
}

}  // namespace

int main(int argc, char **argv) {
  Options opt;
  std::vector<std::string> args(argv + 1, argv + argc);
  for (size_t i = 0; i < args.size(); i++) {
    const std::string &a = args[i];
    bool has_value = i + 1 < args.size();
    if (a == "--hidden") opt.hidden = true;
    else if (a == "--size" && has_value) {
      if (std::sscanf(args[++i].c_str(), "%dx%d", &opt.width, &opt.height) != 2) return usage();
    } else if (a == "--frames" && has_value) opt.frames = std::atoi(args[++i].c_str());
    else if (a == "--screenshot" && has_value) opt.screenshot = args[++i];
    else if (a.rfind("--", 0) == 0 || !opt.config.empty()) return usage();
    else opt.config = a;
  }
  if (opt.config.empty()) return usage();
  if (!opt.screenshot.empty() && opt.frames < 1) opt.frames = 1;

  Config cfg;
  try {
    cfg = load_config(opt.config);
  } catch (const std::exception &e) {
    log(e.what());
    return 1;
  }
  signal(SIGPIPE, SIG_IGN);
  struct sigaction sa {};
  sa.sa_handler = [](int) { g_term = 1; };
  sigaction(SIGTERM, &sa, nullptr);
  sigaction(SIGINT, &sa, nullptr);
  return Session(std::move(cfg), std::move(opt)).run();
}
