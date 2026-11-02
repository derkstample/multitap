#include "config.hpp"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <stdexcept>

using nlohmann::json;

namespace mt::session {

int retropad_id(const std::string &name) {
  static const std::map<std::string, int> ids = {
      {"B", 0},  {"Y", 1},  {"Select", 2}, {"Start", 3}, {"Up", 4},  {"Down", 5},
      {"Left", 6}, {"Right", 7}, {"A", 8},  {"X", 9},     {"L", 10}, {"R", 11},
      {"L2", 12}, {"R2", 13}, {"L3", 14}, {"R3", 15}};
  auto it = ids.find(name);
  return it == ids.end() ? -1 : it->second;
}

std::vector<IRect> Layout::pixel_rects(int width, int height, int gap) const {
  auto edge = [](int i, int total, int n) { return int(std::lround(double(i) * total / n)); };
  int half = gap / 2;
  std::vector<IRect> out;
  for (const auto &r : rects) {
    int x0 = edge(r.col, width, cols), x1 = edge(r.col + r.colspan, width, cols);
    int y0 = edge(r.row, height, rows), y1 = edge(r.row + r.rowspan, height, rows);
    int l = r.col > 0 ? half : 0, t = r.row > 0 ? half : 0;
    int rr = r.col + r.colspan < cols ? half : 0, b = r.row + r.rowspan < rows ? half : 0;
    out.push_back({x0 + l, y0 + t, x1 - x0 - l - rr, y1 - y0 - t - b});
  }
  return out;
}

std::string Device::key() const {
  if (kind == "keyboard") return "kbd:" + set;
  return "pad:" + guid + ":" + std::to_string(ordinal);
}

namespace {

uint32_t parse_color(const std::string &s) {
  if (s.size() == 7 && s[0] == '#') return uint32_t(std::stoul(s.substr(1), nullptr, 16));
  return 0xFFFFFF;
}

template <class T>
void get(const json &j, const char *key, T &out) {
  if (j.is_object() && j.contains(key) && !j[key].is_null()) out = j[key].get<T>();
}

}  // namespace

Config parse_config(const json &j) {
  Config c;
  try {
    c.core_path = j.at("core_path").get<std::string>();
    c.rom_path = j.at("rom_path").get<std::string>();
    c.system_dir = j.at("system_dir").get<std::string>();
    get(j, "worker", c.worker);
    if (j.contains("screen") && j["screen"].is_array() && j["screen"].size() == 4)
      for (int i = 0; i < 4; i++) c.screen[i] = j["screen"][i].get<int>();

    const json &lay = j.at("layout");
    c.layout.cols = std::max(1, lay.at("cols").get<int>());
    c.layout.rows = std::max(1, lay.at("rows").get<int>());
    for (const auto &r : lay.at("rects")) {
      GridRect g{r.at(0).get<int>(), r.at(1).get<int>(), r.at(2).get<int>(), r.at(3).get<int>()};
      c.layout.rects.push_back(g);
    }

    for (const auto &p : j.at("players")) {
      PlayerCfg pc;
      get(p, "index", pc.index);
      get(p, "name", pc.name);
      get(p, "save_dir", pc.save_dir);
      std::string color;
      get(p, "color", color);
      pc.color = parse_color(color);
      const json &d = p.contains("device") ? p["device"] : json::object();
      get(d, "kind", pc.device.kind);
      get(d, "set", pc.device.set);
      get(d, "guid", pc.device.guid);
      get(d, "name", pc.device.name);
      get(d, "ordinal", pc.device.ordinal);
      if (p.contains("bindings") && p["bindings"].is_object()) {
        for (const auto &[btn, v] : p["bindings"].items()) {
          int id = retropad_id(btn);
          if (id < 0) continue;
          if (v.is_number_integer()) pc.keys[v.get<int>()] = id;
          else if (v.is_string()) pc.pad[v.get<std::string>()] = id;
        }
      }
      c.players.push_back(std::move(pc));
    }
    if (c.layout.rects.size() < c.players.size())
      throw std::runtime_error("layout has fewer tiles than players");

    if (j.contains("graphics")) {
      const json &g = j["graphics"];
      get(g, "filter", c.graphics.filter);
      get(g, "integer_scale", c.graphics.integer_scale);
      get(g, "aspect", c.graphics.aspect);
      get(g, "custom_aspect", c.graphics.custom_aspect);
      get(g, "crop_overscan", c.graphics.crop_overscan);
      get(g, "shader_preset", c.graphics.shader_preset);
      get(g, "vsync", c.graphics.vsync);
      if (!(c.graphics.custom_aspect > 0.05)) c.graphics.custom_aspect = 4.0 / 3.0;
    }
    if (j.contains("osd")) {
      const json &o = j["osd"];
      get(o, "enabled", c.osd.enabled);
      get(o, "fps", c.osd.fps);
      get(o, "frame_time", c.osd.frame_time);
      get(o, "inputs", c.osd.inputs);
      get(o, "audio", c.osd.audio);
      get(o, "core_info", c.osd.core_info);
      get(o, "position", c.osd.position);
    }
    if (j.contains("audio")) {
      const json &a = j["audio"];
      get(a, "driver", c.audio.driver);
      get(a, "device", c.audio.device);
      get(a, "volume", c.audio.volume);
      get(a, "rotation_s", c.audio.rotation_s);
      c.audio.volume = std::clamp(c.audio.volume, 0, 100);
      c.audio.rotation_s = std::max(5, c.audio.rotation_s);
    }
  } catch (const json::exception &e) {
    throw std::runtime_error(std::string("bad session config: ") + e.what());
  }
  if (c.players.empty()) throw std::runtime_error("bad session config: no players");
  return c;
}

Config load_config(const std::string &path) {
  std::ifstream f(path);
  if (!f) throw std::runtime_error("cannot read " + path);
  json j = json::parse(f, nullptr, false);
  if (j.is_discarded()) throw std::runtime_error(path + " is not valid JSON");
  return parse_config(j);
}

Placement frame_geometry(double tile_w, double tile_h, int frame_w, int frame_h,
                         double core_aspect, const Graphics &g) {
  int sy = 0, sh = frame_h;
  if (g.crop_overscan && frame_h > 4 * kOverscanRows) {
    sy = kOverscanRows;
    sh = frame_h - 2 * kOverscanRows;
  }
  double w, h;
  if (g.aspect == "stretch") {
    w = tile_w;
    h = tile_h;
    if (g.integer_scale && frame_w <= tile_w && sh <= tile_h) {
      w = frame_w * std::floor(tile_w / frame_w);
      h = sh * std::floor(tile_h / sh);
    }
  } else {
    double aspect;
    if (g.aspect == "square") aspect = double(frame_w) / sh;
    else if (g.aspect == "custom") aspect = g.custom_aspect;
    else if (g.aspect == "4:3") aspect = 4.0 / 3.0;
    else if (g.aspect == "16:9") aspect = 16.0 / 9.0;
    else  // the core's display aspect describes the whole frame; keep it when cropping
      aspect = (core_aspect > 0 ? core_aspect : double(frame_w) / frame_h) * frame_h / sh;
    int k = g.integer_scale ? int(std::min(tile_h / sh, tile_w / (sh * aspect))) : 0;
    if (k >= 1) {
      w = sh * k * aspect;
      h = double(sh) * k;
    } else if (tile_w / tile_h < aspect) {
      w = tile_w;
      h = tile_w / aspect;
    } else {
      w = tile_h * aspect;
      h = tile_h;
    }
  }
  return {{0, sy, frame_w, sh}, {(tile_w - w) / 2, (tile_h - h) / 2, w, h}};
}

}  // namespace mt::session
