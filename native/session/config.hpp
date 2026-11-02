// The session config the launcher writes (multitap/session_config.py), plus the pure geometry
// helpers ported from the Python session: tile rects and where a frame goes inside its tile.
#pragma once

#include <nlohmann/json.hpp>

#include <array>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace mt::session {

constexpr int kOverscanRows = 8;
constexpr int kTileGap = 4;

// RetroPad button name ("B", "Up", ...) -> RETRO_DEVICE_ID_JOYPAD_*; -1 if unknown.
int retropad_id(const std::string &name);

struct Graphics {
  std::string filter = "nearest";  // nearest | bilinear
  bool integer_scale = false;
  std::string aspect = "core";  // core | 4:3 | 16:9 | square | stretch | custom
  double custom_aspect = 4.0 / 3.0;
  bool crop_overscan = false;
  std::string shader_preset;  // a .slangp, or empty
  bool vsync = true;
};

struct Osd {
  bool enabled = false;
  bool fps = true, frame_time = false, inputs = false, audio = true, core_info = false;
  std::string position = "top-left";  // top-left | top-right | bottom-left | bottom-right
};

struct AudioCfg {
  std::string driver = "auto";  // an SDL audio driver name, or auto
  std::string device;           // an output device name, or empty for the default
  int volume = 100;             // 0..100
  int rotation_s = 120;         // seconds each player stays audible
};

struct GridRect {
  int row = 0, col = 0, rowspan = 1, colspan = 1;
};

struct IRect {
  int x = 0, y = 0, w = 0, h = 0;
  bool operator==(const IRect &) const = default;
};

struct Layout {
  int cols = 1, rows = 1;
  std::vector<GridRect> rects;
  // (x, y, w, h) for each tile inside a width x height area; `gap` pixels between tiles
  // (port of Layout.pixel_rects).
  std::vector<IRect> pixel_rects(int width, int height, int gap = 0) const;
};

struct Device {
  std::string kind;  // keyboard | pad
  std::string set = "A";
  std::string guid, name;
  int ordinal = 0;
  // Same as input.mapping.device_key: "kbd:A" or "pad:<guid>:<ordinal>".
  std::string key() const;
};

struct PlayerCfg {
  int index = 0;  // the player number - 1 (not always the tile index)
  std::string name;
  uint32_t color = 0xFFFFFF;  // 0xRRGGBB
  std::string save_dir;
  Device device;
  std::map<int, int> keys;          // keyboard: SDL keycode -> RetroPad id
  std::map<std::string, int> pad;   // pad: element ("button:a", "axis:lefttrigger+") -> id
};

struct Config {
  std::string core_path, rom_path, system_dir;
  std::string worker;  // multitap-worker path; empty = next to multitap-session
  Layout layout;
  std::vector<PlayerCfg> players;
  Graphics graphics;
  Osd osd;
  AudioCfg audio;
  std::array<int, 4> screen{0, 0, 0, 0};  // launcher's screen (x, y, w, h); w = 0: unknown
};

// Throws std::runtime_error with a readable message on bad input. Missing optional sections
// keep their defaults.
Config parse_config(const nlohmann::json &j);
Config load_config(const std::string &path);

struct FRect {
  double x = 0, y = 0, w = 0, h = 0;
};

// Where a frame goes in its tile: the source rows to show (x, y, w, h in frame pixels) and the
// target rect relative to the tile (port of ui.session.frame_geometry).
struct Placement {
  IRect src;
  FRect dst;
};
Placement frame_geometry(double tile_w, double tile_h, int frame_w, int frame_h,
                         double core_aspect, const Graphics &g);

}  // namespace mt::session
