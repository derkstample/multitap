#include "osd.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <filesystem>

#include "imgui.h"
#include "imgui_impl_opengl3.h"
#include "imgui_impl_sdl2.h"
#include "players.hpp"
#include "renderer.hpp"

namespace mt::session {

namespace {

const char *kSansFonts[] = {
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/google-noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
};
const char *kMonoFonts[] = {
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansMono-Bold.ttf",
};

std::string find_font(const char *const *paths, size_t n, const char *pattern) {
  for (size_t i = 0; i < n; i++)
    if (std::filesystem::is_regular_file(paths[i])) return paths[i];
  std::string cmd = std::string("fc-match -f '%{file}' '") + pattern + "' 2>/dev/null";
  std::string out;
  if (FILE *f = popen(cmd.c_str(), "r")) {
    char buf[512];
    while (fgets(buf, sizeof buf, f)) out += buf;
    pclose(f);
  }
  return std::filesystem::is_regular_file(out) ? out : "";
}

// Latin-1, general punctuation (… · — ‘’ “”), arrows, multiplication sign.
const ImWchar kRanges[] = {0x0020, 0x00FF, 0x2010, 0x2027, 0x2190, 0x2193, 0x00D7, 0x00D7, 0};

ImU32 rgb(uint32_t c, int a = 255) { return IM_COL32((c >> 16) & 255, (c >> 8) & 255, c & 255, a); }

ImVec2 text_size(ImFont *f, float size, const std::string &s) {
  return f->CalcTextSizeA(size, FLT_MAX, 0, s.c_str());
}

// Greedy word wrap of each line of `text` to `max_w`.
std::vector<std::string> wrap(ImFont *f, float size, const std::string &text, float max_w) {
  std::vector<std::string> out;
  size_t start = 0;
  while (start <= text.size()) {
    size_t nl = text.find('\n', start);
    std::string line = text.substr(start, nl == std::string::npos ? std::string::npos : nl - start);
    std::string cur;
    size_t i = 0;
    while (i <= line.size()) {
      size_t sp = line.find(' ', i);
      std::string word = line.substr(i, sp == std::string::npos ? std::string::npos : sp - i);
      std::string next = cur.empty() ? word : cur + " " + word;
      if (!cur.empty() && text_size(f, size, next).x > max_w) {
        out.push_back(cur);
        cur = word;
      } else {
        cur = next;
      }
      if (sp == std::string::npos) break;
      i = sp + 1;
    }
    out.push_back(cur);
    if (nl == std::string::npos) break;
    start = nl + 1;
  }
  return out;
}

std::string elide(ImFont *f, float size, const std::string &s, float max_w) {
  if (text_size(f, size, s).x <= max_w) return s;
  std::string out = s;
  while (!out.empty() && text_size(f, size, out + "…").x > max_w) {
    out.pop_back();
    while (!out.empty() && (static_cast<unsigned char>(out.back()) & 0xC0) == 0x80) out.pop_back();
    if (!out.empty() && (static_cast<unsigned char>(out.back()) & 0x80)) out.pop_back();
  }
  return out + "…";
}

void speaker(ImDrawList *d, ImVec2 a, ImVec2 b) {
  float w = b.x - a.x, h = b.y - a.y;
  ImVec2 body[] = {{a.x, a.y + h * 0.35f},           {a.x + w * 0.22f, a.y + h * 0.35f},
                   {a.x + w * 0.5f, a.y + h * 0.1f}, {a.x + w * 0.5f, a.y + h * 0.9f},
                   {a.x + w * 0.22f, a.y + h * 0.65f}, {a.x, a.y + h * 0.65f}};
  d->AddConvexPolyFilled(body, 6, IM_COL32_WHITE);
  ImVec2 c{a.x + w * 0.5f, a.y + h * 0.5f};
  float thick = std::max(1.5f, w * 0.07f);
  for (float r : {w * 0.2f, w * 0.38f}) {
    d->PathArcTo(c, r, float(-M_PI / 4), float(M_PI / 4), 12);
    d->PathStroke(IM_COL32_WHITE, 0, thick);
  }
}

}  // namespace

Overlay::~Overlay() {
  if (ready_) {
    ImGui_ImplOpenGL3_Shutdown();
    ImGui_ImplSDL2_Shutdown();
    ImGui::DestroyContext();
  }
}

bool Overlay::init(SDL_Window *window, SDL_GLContext ctx, int height) {
  IMGUI_CHECKVERSION();
  ImGui::CreateContext();
  ImGuiIO &io = ImGui::GetIO();
  io.IniFilename = nullptr;
  io.LogFilename = nullptr;
  io.ConfigFlags |= ImGuiConfigFlags_NoMouse;
  ImGui_ImplSDL2_InitForOpenGL(window, ctx);
  ImGui_ImplOpenGL3_Init("#version 330 core");
  ready_ = true;

  std::string sans = find_font(kSansFonts, std::size(kSansFonts), "sans:bold");
  std::string mono = find_font(kMonoFonts, std::size(kMonoFonts), "monospace:bold");
  auto load = [&](const std::string &path, float px) -> ImFont * {
    ImFontConfig cfg;
    cfg.OversampleH = 2;
    cfg.OversampleV = 1;
    cfg.SizePixels = px;
    if (!path.empty())
      if (ImFont *f = io.Fonts->AddFontFromFileTTF(path.c_str(), px, &cfg, kRanges)) return f;
    return io.Fonts->AddFontDefault(&cfg);
  };
  label_ = load(sans, 28);
  mono_ = load(mono.empty() ? sans : mono, 22);
  banner_ = load(sans, std::max(24.0f, height / 12.0f));
  sub_ = load(sans, std::max(14.0f, height / 45.0f));
  return true;
}

void Overlay::draw(const OsdView &v) {
  if (!ready_) return;
  ImGui_ImplOpenGL3_NewFrame();
  ImGui_ImplSDL2_NewFrame();
  ImGui::GetIO().DisplaySize = ImVec2(float(v.width), float(v.height));
  ImGui::NewFrame();
  for (const auto &t : v.tiles) tile(v, t);
  ImDrawList *d = ImGui::GetForegroundDrawList();
  if (!v.warning.empty()) {
    float size = std::max(14.0f, v.height / 50.0f);
    ImVec2 ts = text_size(sub_, size, v.warning);
    float pad = size * 0.5f, x = (v.width - ts.x) / 2, y = pad * 2;
    d->AddRectFilled({x - pad, y - pad / 2}, {x + ts.x + pad, y + ts.y + pad / 2},
                     IM_COL32(0, 0, 0, 200), pad);
    d->AddText(sub_, size, {x, y}, IM_COL32(245, 165, 36, 255), v.warning.c_str());
  }
  if (v.paused) banner(v, "PAUSED", v.banner_sub, false);
  if (!v.toast.empty()) banner(v, v.toast, "", true);
  ImGui::Render();
  ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());
}

void Overlay::tile(const OsdView &v, const TileView &t) {
  ImDrawList *d = ImGui::GetForegroundDrawList();
  const Player &p = *t.player;
  const IRect &r = t.rect;
  float size = std::clamp(r.h / 20.0f, 12.0f, 28.0f), pad = size * 0.4f;
  d->PushClipRect({float(r.x), float(r.y)}, {float(r.x + r.w), float(r.y + r.h)}, true);

  std::string num = "Player " + std::to_string(p.cfg.index + 1);
  std::string msg;
  if (!p.error.empty())
    msg = num + (p.loaded ? ": emulator stopped\n" : ": failed to start\n") + p.error;
  else if (p.exited && !v.quitting)
    msg = num + ": emulator stopped";
  else if (!t.frame->has_frame)
    msg = "Loading…";
  if (t.disconnected) msg = num + ": controller disconnected\nReconnect, then press P";
  if (!msg.empty()) {
    auto lines = wrap(label_, size, msg, r.w - 40.0f);
    float lh = size * 1.25f;
    float y = r.y + (r.h - lh * lines.size()) / 2;
    for (const auto &line : lines) {
      float w = text_size(label_, size, line).x;
      d->AddText(label_, size, {r.x + (r.w - w) / 2, y}, IM_COL32(230, 230, 230, 255), line.c_str());
      y += lh;
    }
  }

  if (!p.cfg.name.empty()) {
    std::string text = elide(label_, size, p.cfg.name, r.w * 0.6f);
    ImVec2 ts = text_size(label_, size, text);
    ImVec2 a{r.x + pad, r.y + r.h - pad - ts.y - pad};
    ImVec2 b{a.x + ts.x + 2 * pad, a.y + ts.y + pad};
    d->AddRectFilled(a, b, IM_COL32(0, 0, 0, 170), pad);
    d->AddText(label_, size, {a.x + pad, a.y + pad / 2}, rgb(p.cfg.color), text.c_str());
  }

  if (t.audible && v.show_speaker) {
    float s = size * 1.5f;
    ImVec2 a{r.x + r.w - pad - s - pad, r.y + r.h - pad - s - pad / 2};
    ImVec2 b{a.x + s + pad, a.y + s + pad / 2};
    d->AddRectFilled(a, b, IM_COL32(0, 0, 0, 170), pad);
    speaker(d, {a.x + pad / 2, a.y + pad / 4}, {b.x - pad / 2, b.y - pad / 4});
  }

  if (v.cfg.enabled && p.loaded) diagnostics(v, t, size);
  d->PopClipRect();
}

void Overlay::diagnostics(const OsdView &v, const TileView &t, float label_size) {
  ImDrawList *d = ImGui::GetForegroundDrawList();
  const Player &p = *t.player;
  const TileFrame &f = *t.frame;
  const IRect &r = t.rect;
  float size = std::clamp(r.h / 30.0f, 11.0f, 20.0f), pad = size * 0.5f, lh = size * 1.2f;
  char buf[160];

  std::vector<std::pair<std::string, ImU32>> lines;
  const ImU32 text = IM_COL32(232, 234, 240, 255), muted = IM_COL32(150, 156, 170, 255);
  if (v.cfg.fps) {
    std::snprintf(buf, sizeof buf, "FPS %5.1f / %.2f", p.measured_fps, p.fps);
    lines.emplace_back(buf, text);
    std::snprintf(buf, sizeof buf, "Display %.0f Hz", v.display_fps);
    lines.emplace_back(buf, muted);
  }
  bool graph = v.cfg.frame_time && !f.intervals_ms.empty();
  if (v.cfg.frame_time) {
    std::snprintf(buf, sizeof buf, "Frame %.2f ms", p.frame_ms);
    lines.emplace_back(buf, text);
  }
  if (v.cfg.audio) {
    if (!t.audible) std::snprintf(buf, sizeof buf, "Audio muted");
    else if (v.audio_switch_s >= 0) std::snprintf(buf, sizeof buf, "Audio on · next in %d s", v.audio_switch_s);
    else std::snprintf(buf, sizeof buf, "Audio on");
    lines.emplace_back(buf, t.audible ? text : muted);
  }
  if (v.cfg.core_info) {
    lines.emplace_back(p.core_name.empty() ? "?" : p.core_name, text);
    if (!v.game.empty()) lines.emplace_back(v.game, muted);
    static const char *fmts[] = {"0RGB1555", "XRGB8888", "RGB565"};
    std::snprintf(buf, sizeof buf, "%d×%d %s", f.width, f.height,
                  f.fmt >= 0 && f.fmt < 3 ? fmts[f.fmt] : "");
    lines.emplace_back(buf, muted);
  }
  bool pad_glyph = v.cfg.inputs;
  if (lines.empty() && !graph && !pad_glyph) return;

  float graph_w = size * 10, graph_h = size * 2.5f, glyph_h = size * 2.4f, glyph_w = size * 6.4f;
  float max_w = 0;
  for (auto &[s, _] : lines) {
    s = elide(mono_, size, s, r.w * 0.8f);
    max_w = std::max(max_w, text_size(mono_, size, s).x);
  }
  if (graph) max_w = std::max(max_w, graph_w);
  if (pad_glyph) max_w = std::max(max_w, glyph_w);
  float h = lines.size() * lh + (graph ? graph_h + pad : 0) + (pad_glyph ? glyph_h + pad : 0);
  float w = max_w + 2 * pad, box_h = h + pad;

  // Keep clear of the name tag and speaker along the bottom edge.
  float margin = label_size * 0.4f, bottom_reserve = label_size * 2.2f;
  bool right = v.cfg.position.find("right") != std::string::npos;
  bool bottom = v.cfg.position.find("bottom") != std::string::npos;
  float x = right ? r.x + r.w - margin - w : r.x + margin;
  float y = bottom ? r.y + r.h - bottom_reserve - box_h : r.y + margin;
  d->AddRectFilled({x, y}, {x + w, y + box_h}, IM_COL32(0, 0, 0, 160), pad);
  float cy = y + pad / 2;
  for (const auto &[s, col] : lines) {
    d->AddText(mono_, size, {x + pad, cy}, col, s.c_str());
    cy += lh;
  }
  if (graph) {
    // Time between new frames; the line marks the core's frame period.
    float target = float(1000.0 / std::max(1.0, p.fps)), top = target * 2.5f;
    ImVec2 a{x + pad, cy + pad / 2}, b{a.x + graph_w, a.y + graph_h};
    d->AddRectFilled(a, b, IM_COL32(255, 255, 255, 20));
    float ty = b.y - graph_h * target / top;
    d->AddLine({a.x, ty}, {b.x, ty}, IM_COL32(62, 207, 110, 160));
    std::vector<ImVec2> pts;
    size_t n = f.intervals_ms.size();
    for (size_t i = 0; i < n; i++) {
      float val = std::min(f.intervals_ms[i], top);
      pts.push_back({a.x + graph_w * (i + 0.5f) / 120.0f + graph_w * (120.0f - n) / 120.0f,
                     b.y - graph_h * val / top});
    }
    d->AddPolyline(pts.data(), int(pts.size()), IM_COL32(245, 165, 36, 255), 0, 1.5f);
    cy += graph_h + pad;
  }
  if (pad_glyph) pad_glyphs(x + pad, cy + pad / 2, size, t.held, p.cfg.color);
}

// A compact RetroPad: shoulders, d-pad, Select/Start, and the SNES-style face diamond.
void Overlay::pad_glyphs(float x, float y, float s, uint32_t held, uint32_t color) {
  ImDrawList *d = ImGui::GetForegroundDrawList();
  auto col = [&](int id) { return (held >> id) & 1 ? rgb(color) : IM_COL32(80, 84, 96, 255); };
  float u = s * 0.4f;  // grid unit
  // L / R / L2 / R2 along the top
  d->AddRectFilled({x, y}, {x + u * 3, y + u * 0.7f}, col(10), u * 0.3f);
  d->AddRectFilled({x + u * 13, y}, {x + u * 16, y + u * 0.7f}, col(11), u * 0.3f);
  d->AddRectFilled({x + u * 3.4f, y}, {x + u * 4.6f, y + u * 0.7f}, col(12), u * 0.3f);
  d->AddRectFilled({x + u * 11.4f, y}, {x + u * 12.6f, y + u * 0.7f}, col(13), u * 0.3f);
  float cy = y + u * 3.4f, dx = x + u * 2.5f;
  // D-pad: Up 4, Down 5, Left 6, Right 7
  d->AddRectFilled({dx - u / 2, cy - u * 1.6f}, {dx + u / 2, cy - u / 2}, col(4));
  d->AddRectFilled({dx - u / 2, cy + u / 2}, {dx + u / 2, cy + u * 1.6f}, col(5));
  d->AddRectFilled({dx - u * 1.6f, cy - u / 2}, {dx - u / 2, cy + u / 2}, col(6));
  d->AddRectFilled({dx + u / 2, cy - u / 2}, {dx + u * 1.6f, cy + u / 2}, col(7));
  // Select 2, Start 3
  d->AddRectFilled({x + u * 6, cy - u * 0.3f}, {x + u * 7.6f, cy + u * 0.3f}, col(2), u * 0.3f);
  d->AddRectFilled({x + u * 8.4f, cy - u * 0.3f}, {x + u * 10, cy + u * 0.3f}, col(3), u * 0.3f);
  // Face: X 9 top, B 0 bottom, Y 1 left, A 8 right
  float fx = x + u * 13.5f, r = u * 0.55f;
  d->AddCircleFilled({fx, cy - u * 1.1f}, r, col(9));
  d->AddCircleFilled({fx, cy + u * 1.1f}, r, col(0));
  d->AddCircleFilled({fx - u * 1.1f, cy}, r, col(1));
  d->AddCircleFilled({fx + u * 1.1f, cy}, r, col(8));
}

void Overlay::banner(const OsdView &v, const std::string &title, const std::string &sub, bool small) {
  ImDrawList *d = ImGui::GetForegroundDrawList();
  float tsize = v.height / (small ? 22.0f : 12.0f), ssize = std::max(14.0f, v.height / 45.0f);
  ImVec2 tt = text_size(banner_, tsize, title);
  ImVec2 st = sub.empty() ? ImVec2(0, 0) : text_size(sub_, ssize, sub);
  float w = std::max(tt.x, st.x) + 80, gap = sub.empty() ? 0 : tt.y * 0.15f;
  float h = tt.y + gap + st.y + 30;
  ImVec2 a{(v.width - w) / 2, (v.height - h) / 2};
  d->AddRectFilled(a, {a.x + w, a.y + h}, IM_COL32(0, 0, 0, 200), 14);
  d->AddText(banner_, tsize, {(v.width - tt.x) / 2, a.y + 15}, IM_COL32_WHITE, title.c_str());
  if (!sub.empty())
    d->AddText(sub_, ssize, {(v.width - st.x) / 2, a.y + 15 + tt.y + gap},
               IM_COL32(200, 204, 214, 255), sub.c_str());
}

}  // namespace mt::session
