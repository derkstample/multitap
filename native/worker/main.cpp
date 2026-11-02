// multitap-worker: runs one player's libretro core (port of multitap/worker/main.py).
//
//   multitap-worker --control-fd N --core CORE --rom ROM --system-dir DIR --save-dir DIR
//                   [--slot N] [--shm-prefix NAME] [--username NAME] [--option KEY=VALUE]... [--audible]
//   multitap-worker --probe info  CORE
//   multitap-worker --probe trial CORE ROM SYSTEM_DIR
//
// Probe modes print one JSON object on the last stdout line (cores may print before it).
//
// Control: line-delimited JSON objects with a "type" field over --control-fd (a socketpair).
//   session -> worker: start{t0_ns}  pause  resume{t0_ns}  reset  quit  audible{audible: bool}
//   worker -> session: loaded{name, fps, aspect, sample_rate, geometry, shm}
//                      error{message}  log{message}  stats{frame_ms, fps}  exited
// t0_ns is CLOCK_MONOTONIC; it defaults to "now". After `loaded` the worker waits for `start`,
// then runs frame n at t0 + n / fps. Shared memory (see common/shm.hpp) is created before
// `loaded` and unlinked on exit; the prefix defaults to multitap-<parent pid>-<slot>. The worker never opens an audio device: the audible worker
// fills its audio ring and nudges its pace by up to 0.5% to keep the ring near its target.
#include <signal.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <map>
#include <optional>

#include "content.hpp"
#include "control.hpp"
#include "core.hpp"
#include "shm.hpp"

namespace fs = std::filesystem;
using nlohmann::json;

namespace {

constexpr int kTrialFrames = 10;
constexpr double kSramIntervalS = 30.0;
constexpr double kAudioTargetMs = 60.0;
constexpr double kAudioAdjust = 0.005;  // max pacing correction while audible (0.5%)
constexpr int64_t kResyncNs = 100'000'000;  // fell this far behind (suspend?): restart the clock

volatile sig_atomic_t g_quit = 0;

int64_t now_ns() {
  timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return int64_t(ts.tv_sec) * 1'000'000'000 + ts.tv_nsec;
}

void sleep_until(int64_t t_ns) {
  timespec ts{time_t(t_ns / 1'000'000'000), long(t_ns % 1'000'000'000)};
  while (!g_quit && clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, nullptr) == EINTR) {
  }
}

// A private temp dir for extracted content, removed on destruction.
struct TempDir {
  std::string path;
  TempDir() {
    const char *base = getenv("TMPDIR");
    std::string tmpl = std::string(base && *base ? base : "/tmp") + "/multitap-XXXXXX";
    if (!mkdtemp(tmpl.data())) throw std::runtime_error("mkdtemp: " + std::string(strerror(errno)));
    path = tmpl;
  }
  ~TempDir() {
    std::error_code ec;
    fs::remove_all(path, ec);
  }
};

json system_info_json(const mt::SystemInfo &i) {
  return {{"library_name", i.library_name},
          {"library_version", i.library_version},
          {"valid_extensions", i.valid_extensions},
          {"need_fullpath", i.need_fullpath},
          {"block_extract", i.block_extract}};
}

void emit(const json &obj) {
  std::fflush(stdout);
  std::cout << "\n" << obj.dump(-1, ' ', false, json::error_handler_t::replace) << std::endl;
}

// ---- probes ----------------------------------------------------------------------------

json probe_info(const std::string &core_path) {
  try {
    return system_info_json(mt::read_system_info(core_path));
  } catch (const mt::CoreError &e) {
    return {{"error", e.what()}};
  }
}

json probe_trial(const std::string &core_path, const std::string &rom,
                 const std::string &system_dir) {
  try {
    TempDir tmp;
    fs::create_directories(system_dir);
    int frames = 0;
    mt::Core::Hooks hooks;
    hooks.video = [&](const void *data, unsigned, unsigned, size_t) { frames += data != nullptr; };
    mt::Core core(core_path, system_dir, tmp.path, "", {}, hooks);
    std::string content = mt::prepare_content(rom, core.system_info().valid_extensions, tmp.path,
                                              core.system_info().block_extract);
    core.load_game(content);
    for (int i = 0; i < kTrialFrames; i++) core.run();
    json result = {{"ok", true}, {"name", core.system_info().library_name},
                   {"fps", core.fps()}, {"aspect", core.aspect()}, {"frames", frames}};
    if (!frames) result["warning"] = "The core produced no video in its first frames";
    return result;
  } catch (const std::exception &e) {
    return {{"ok", false}, {"error", e.what()}};
  }
}

// ---- worker ----------------------------------------------------------------------------

struct Args {
  int control_fd = -1, slot = 0;
  std::string core, rom, system_dir, save_dir, shm_prefix, username;
  std::map<std::string, std::string> options;
  bool audible = false;
};

class Worker {
 public:
  Worker(Args args) : a_(std::move(args)), ctl_(a_.control_fd), audible_(a_.audible) {}

  int run() {
    try {
      load();
    } catch (const std::exception &e) {
      ctl_.send({{"type", "error"}, {"message", e.what()}});
      for (auto *m : {&video_map_, &input_map_, &audio_map_}) m->unlink();
      return 1;
    }
    int code = 0;
    try {
      loop();
    } catch (const std::exception &e) {
      ctl_.send({{"type", "error"}, {"message", e.what()}});
      code = 1;
    }
    shutdown();
    return code;
  }

 private:
  void log(const std::string &msg) { ctl_.send({{"type", "log"}, {"message", msg}}); }

  void load() {
    fs::create_directories(a_.save_dir);
    fs::create_directories(a_.system_dir);
    mt::Core::Hooks hooks;
    hooks.video = [this](const void *d, unsigned w, unsigned h, size_t p) { on_video(d, w, h, p); };
    hooks.av_changed = [this] { on_av_change(); };
    hooks.log = [this](const std::string &m) { log(m); };
    core_.emplace(a_.core, a_.system_dir, a_.save_dir, a_.username, a_.options, hooks);
    const auto &info = core_->system_info();
    std::string content =
        mt::prepare_content(a_.rom, info.valid_extensions, tmp_.path, info.block_extract);
    core_->load_game(content);

    sram_path_ = (fs::path(a_.save_dir) / (mt::stem_of(a_.rom) + ".srm")).string();
    if (auto sram = core_->sram(); !sram.empty() && fs::is_regular_file(sram_path_)) {
      std::ifstream f(sram_path_, std::ios::binary);
      std::vector<char> data{std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>()};
      std::memcpy(sram.data(), data.data(), std::min(sram.size(), data.size()));
    }
    auto sram = core_->sram();
    last_sram_.assign(sram.begin(), sram.end());

    create_shm();
    const auto &g = core_->av_info().geometry;
    ctl_.send({{"type", "loaded"},
               {"name", info.library_name},
               {"fps", core_->fps()},
               {"aspect", core_->aspect()},
               {"sample_rate", core_->sample_rate()},
               {"geometry",
                {{"base_width", g.base_width}, {"base_height", g.base_height},
                 {"max_width", g.max_width}, {"max_height", g.max_height}}},
               {"shm", a_.shm_prefix}});
  }

  void create_shm() {
    const auto &g = core_->av_info().geometry;
    uint64_t w = std::max(g.max_width, g.base_width), h = std::max(g.max_height, g.base_height);
    uint64_t cap = w * h * 4;
    if (!cap || cap > (256u << 20)) cap = 4u << 20;  // a core that reports nothing useful
    video_map_ = mt::shm::Mapping::create(a_.shm_prefix + "-video",
                                          mt::shm::video_size(uint32_t(cap)));
    input_map_ = mt::shm::Mapping::create(a_.shm_prefix + "-input", sizeof(mt::shm::InputBlock));
    uint32_t acap = mt::shm::audio_capacity_for(core_->sample_rate());
    audio_map_ = mt::shm::Mapping::create(a_.shm_prefix + "-audio", mt::shm::audio_size(acap));
    video_.emplace(video_map_.data());
    video_->init(uint32_t(cap));
    input_.emplace(input_map_.data());
    input_->init();
    audio_.emplace(audio_map_.data());
    audio_->init(acap, core_->sample_rate());
    auto &hdr = video_->header();
    hdr.aspect = float(core_->aspect());
    hdr.core_fps = core_->fps();
    hdr.sample_rate = core_->sample_rate();
    video_->set_state(mt::shm::kPaused);  // until `start`
  }

  void on_video(const void *data, unsigned w, unsigned h, size_t pitch) {
    if (!data || !video_ || !w || !h) return;  // a dupe: keep showing the previous frame
    unsigned bpp = core_->pixel_format() == RETRO_PIXEL_FORMAT_XRGB8888 ? 4 : 2;
    uint32_t row = w * bpp;
    if (uint64_t(row) * h > video_->header().slot_capacity && !warned_clip_) {
      warned_clip_ = true;
      log("frame " + std::to_string(w) + "x" + std::to_string(h) +
          " exceeds the core's max geometry; clipping");
    }
    uint32_t slot = video_->begin_write();
    video_->write_rows(slot, data, row, h, pitch);
    video_->publish(slot, w, core_->pixel_format(), float(core_->aspect()));
  }

  void on_av_change() {
    if (!video_) return;
    auto &hdr = video_->header();
    hdr.aspect = float(core_->aspect());
    hdr.core_fps = core_->fps();
    hdr.sample_rate = core_->sample_rate();
    audio_->set_sample_rate(core_->sample_rate());
  }

  // (Re)start the frame clock at t0 (now when absent).
  void restart_clock(const json &msg) {
    t0_ = msg.contains("t0_ns") ? msg["t0_ns"].get<int64_t>() : now_ns();
    n_ = 0;
    period_ns_ = 1e9 / core_->fps();
    last_frame_ns_ = 0;
    stats_t0_ = std::max(t0_, now_ns());  // paused or waiting time isn't frame time
    stats_frames_ = 0;
    run_ns_ = 0;
  }

  void handle(const json &msg) {
    std::string type = msg.value("type", "");
    if (type == "start" || type == "resume") {
      restart_clock(msg);
      started_ = true;
      paused_ = false;
      video_->set_state(mt::shm::kRunning);
    } else if (type == "pause") {
      paused_ = true;
      video_->set_state(mt::shm::kPaused);
      save_sram();
    } else if (type == "reset") {
      core_->reset();
    } else if (type == "audible") {
      audible_ = msg.value("audible", false);
    } else if (type == "quit") {
      running_ = false;
    }
  }

  void poll_commands(int timeout_ms) {
    for (const auto &msg : ctl_.poll(timeout_ms)) handle(msg);
    if (ctl_.closed() || g_quit) running_ = false;  // the session went away
  }

  void loop() {
    int64_t sram_t0 = now_ns();
    while (running_) {
      if (!started_ || paused_) {
        poll_commands(1000);
        continue;
      }
      poll_commands(0);
      if (!running_ || paused_) continue;

      // Wait for frame n's slot, staying responsive to commands while it is far away.
      int64_t deadline = t0_ + int64_t(std::llround(n_ * period_ns_));
      int64_t wait = deadline - now_ns();
      if (wait > 5'000'000) {
        poll_commands(int((wait - 2'000'000) / 1'000'000));
        continue;  // re-evaluate: a command may have moved t0
      }
      sleep_until(deadline);
      int64_t start = now_ns();
      if (start - deadline > kResyncNs) {
        t0_ = start - int64_t(std::llround(n_ * period_ns_));
      }

      mt::shm::InputState in = input_->take();
      core_->input_bits = in.buttons;
      std::copy(std::begin(in.analog), std::end(in.analog), core_->analog);
      if (core_->has_frame_time_cb()) {
        int64_t usec = last_frame_ns_ ? (start - last_frame_ns_) / 1000 : 0;
        core_->call_frame_time(usec ? usec : core_->frame_time_reference());
      }
      last_frame_ns_ = start;
      video_->bump_frame_no();
      core_->run();
      int64_t end = now_ns();
      run_ns_ += end - start;
      n_++;
      if (core_->shutdown_requested()) break;

      double fps = core_->fps();
      if (double p = 1e9 / fps; std::abs(p - period_ns_) > 1) {  // the core changed its rate
        t0_ += int64_t(std::llround(n_ * period_ns_)) - int64_t(std::llround(n_ * p));
        period_ns_ = p;
      }
      auto &samples = core_->audio;
      if (audible_ && !samples.empty()) {
        audio_->write(samples.data(), samples.size() / 2);
        double queued_ms = audio_->fill() * 1000.0 / core_->sample_rate();
        double err = (queued_ms - kAudioTargetMs) / kAudioTargetMs;
        double adj = std::clamp(err * kAudioAdjust, -kAudioAdjust, kAudioAdjust);
        t0_ += int64_t(period_ns_ * adj);  // later deadlines = slower while the ring is full
      }
      samples.clear();

      stats_frames_++;
      if (end - stats_t0_ >= 1'000'000'000) {
        double secs = (end - stats_t0_) / 1e9;
        video_->header().measured_fps = float(stats_frames_ / secs);
        ctl_.send({{"type", "stats"},
                   {"frame_ms", run_ns_ / 1e6 / stats_frames_},
                   {"fps", stats_frames_ / secs}});
        stats_t0_ = end;
        stats_frames_ = 0;
        run_ns_ = 0;
      }
      if (end - sram_t0 >= int64_t(kSramIntervalS * 1e9)) {
        save_sram();
        sram_t0 = end;
      }
    }
  }

  void save_sram() {
    if (!core_ || sram_path_.empty()) return;
    auto data = core_->sram();
    if (data.empty() || std::equal(data.begin(), data.end(), last_sram_.begin(), last_sram_.end()))
      return;
    std::string tmp = sram_path_ + ".tmp";
    {
      std::ofstream f(tmp, std::ios::binary | std::ios::trunc);
      f.write(reinterpret_cast<const char *>(data.data()), std::streamsize(data.size()));
      if (!f.flush()) throw std::runtime_error("cannot write " + tmp);
    }
    fs::rename(tmp, sram_path_);
    last_sram_.assign(data.begin(), data.end());
  }

  void shutdown() {
    try {
      save_sram();
    } catch (const std::exception &e) {
      log(std::string("SRAM save failed: ") + e.what());
    }
    if (core_) core_->close();
    if (video_) video_->set_state(mt::shm::kExited);
    ctl_.send({{"type", "exited"}});
    for (auto *m : {&video_map_, &input_map_, &audio_map_}) m->unlink();
  }

  Args a_;
  mt::Control ctl_;
  TempDir tmp_;
  std::optional<mt::Core> core_;
  mt::shm::Mapping video_map_, input_map_, audio_map_;
  std::optional<mt::shm::Video> video_;
  std::optional<mt::shm::Input> input_;
  std::optional<mt::shm::Audio> audio_;
  std::string sram_path_;
  std::vector<uint8_t> last_sram_;
  bool running_ = true, started_ = false, paused_ = false, audible_ = false;
  bool warned_clip_ = false;
  int64_t t0_ = 0, n_ = 0, last_frame_ns_ = 0;
  int64_t stats_t0_ = 0, run_ns_ = 0;
  int stats_frames_ = 0;
  double period_ns_ = 1e9 / 60;
};

int usage() {
  std::cerr << "usage: multitap-worker --control-fd N --core CORE --rom ROM --system-dir DIR\n"
               "                       --save-dir DIR [--slot N] [--shm-prefix NAME] [--username NAME]\n"
               "                       [--option KEY=VALUE]... [--audible]\n"
               "       multitap-worker --probe info CORE\n"
               "       multitap-worker --probe trial CORE ROM SYSTEM_DIR\n";
  return 2;
}

}  // namespace

int main(int argc, char **argv) {
  std::vector<std::string> args(argv + 1, argv + argc);
  if (!args.empty() && args[0] == "--probe") {
    if (args.size() >= 3 && args[1] == "info") {
      emit(probe_info(args[2]));
      return 0;
    }
    if (args.size() >= 5 && args[1] == "trial") {
      emit(probe_trial(args[2], args[3], args[4]));
      return 0;
    }
    return usage();
  }

  Args a;
  for (size_t i = 0; i < args.size(); i++) {
    const std::string &k = args[i];
    auto next = [&]() -> std::optional<std::string> {
      if (i + 1 >= args.size()) return std::nullopt;
      return args[++i];
    };
    std::optional<std::string> v;
    if (k == "--audible") {
      a.audible = true;
      continue;
    }
    if (!(v = next())) return usage();
    if (k == "--control-fd") a.control_fd = std::atoi(v->c_str());
    else if (k == "--core") a.core = *v;
    else if (k == "--rom") a.rom = *v;
    else if (k == "--system-dir") a.system_dir = *v;
    else if (k == "--save-dir") a.save_dir = *v;
    else if (k == "--slot") a.slot = std::atoi(v->c_str());
    else if (k == "--shm-prefix") a.shm_prefix = *v;
    else if (k == "--username") a.username = *v;
    else if (k == "--option" && v->find('=') != std::string::npos)
      a.options[v->substr(0, v->find('='))] = v->substr(v->find('=') + 1);
    else return usage();
  }
  if (a.control_fd < 0 || a.core.empty() || a.rom.empty() || a.system_dir.empty() ||
      a.save_dir.empty())
    return usage();
  if (a.shm_prefix.empty())
    a.shm_prefix = "multitap-" + std::to_string(getppid()) + "-" + std::to_string(a.slot);

  signal(SIGINT, SIG_IGN);  // Ctrl+C in the terminal is the session's to handle
  signal(SIGPIPE, SIG_IGN);
  struct sigaction sa {};
  sa.sa_handler = [](int) { g_quit = 1; };
  sigaction(SIGTERM, &sa, nullptr);  // no SA_RESTART: interrupt poll/sleep, then save and exit

  int fd = a.control_fd;
  try {
    return Worker(std::move(a)).run();
  } catch (const std::exception &e) {  // e.g. mkdtemp failed before the control loop
    mt::Control(fd).send({{"type", "error"}, {"message", e.what()}});
    return 1;
  }
}
