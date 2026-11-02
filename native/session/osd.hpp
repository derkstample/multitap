// Overlays drawn with Dear ImGui's draw lists: per-tile name tags, the audible speaker,
// loading/error messages, banners, and (when enabled) the diagnostics OSD.
#pragma once

#include <SDL.h>

#include <deque>
#include <string>
#include <vector>

#include "config.hpp"

struct ImFont;

namespace mt::session {

class Player;
struct TileFrame;

struct TileView {
  const Player *player = nullptr;
  const TileFrame *frame = nullptr;
  IRect rect;
  bool audible = false, disconnected = false;
  uint32_t held = 0;  // RetroPad mask the worker sees
};

struct OsdView {
  int width = 0, height = 0;
  std::vector<TileView> tiles;
  Osd cfg;
  std::string game;
  bool paused = false, quitting = false, show_speaker = false;
  std::string banner_sub;
  std::string toast;
  std::string warning;  // e.g. shader preset unavailable
  double display_fps = 0;
  int audio_switch_s = -1;  // seconds until the next audible player, -1 = no rotation
};

class Overlay {
 public:
  ~Overlay();
  // Needs the window's GL context current.
  bool init(SDL_Window *window, SDL_GLContext ctx, int height);
  void draw(const OsdView &v);

 private:
  void tile(const OsdView &v, const TileView &t);
  void diagnostics(const OsdView &v, const TileView &t, float size);
  void pad_glyphs(float x, float y, float s, uint32_t held, uint32_t color);
  void banner(const OsdView &v, const std::string &title, const std::string &sub, bool small);

  bool ready_ = false;
  ImFont *label_ = nullptr, *mono_ = nullptr, *banner_ = nullptr, *sub_ = nullptr;
};

}  // namespace mt::session
