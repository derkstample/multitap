#include <doctest/doctest.h>

#include <cmath>

#include "config.hpp"
#include "input.hpp"

using namespace mt::session;
using nlohmann::json;

TEST_CASE("frame_geometry matches the Python session") {
  Graphics g;
  // NES 256x240 at 4:3 in a 1000x600 tile: height-limited, centred.
  Placement p = frame_geometry(1000, 600, 256, 240, 4.0 / 3, g);
  CHECK(p.src == IRect{0, 0, 256, 240});
  CHECK(p.dst.w == doctest::Approx(800));
  CHECK(p.dst.h == doctest::Approx(600));
  CHECK(p.dst.x == doctest::Approx(100));

  g.integer_scale = true;  // 600 // 240 = 2x
  p = frame_geometry(1000, 600, 256, 240, 4.0 / 3, g);
  CHECK(p.dst.h == doctest::Approx(480));
  CHECK(std::lround(p.dst.w) == 640);
  CHECK(p.dst.y == doctest::Approx(60));

  g = Graphics{};
  g.aspect = "square";
  g.crop_overscan = true;
  p = frame_geometry(1000, 600, 256, 240, 4.0 / 3, g);
  CHECK(p.src == IRect{0, 8, 256, 224});
  CHECK(p.dst.h == doctest::Approx(600));
  CHECK(std::lround(p.dst.w) == std::lround(600.0 * 256 / 224));

  g = Graphics{};
  g.aspect = "stretch";
  p = frame_geometry(1000, 600, 256, 240, 4.0 / 3, g);
  CHECK(p.dst.x == 0);
  CHECK(p.dst.y == 0);
  CHECK(p.dst.w == 1000);
  CHECK(p.dst.h == 600);
}

TEST_CASE("pixel_rects puts half the gap on interior edges") {
  Layout l;
  l.cols = 2;
  l.rows = 1;
  l.rects = {{0, 0, 1, 1}, {0, 1, 1, 1}};
  auto r = l.pixel_rects(1001, 600, 4);
  CHECK(r[0] == IRect{0, 0, 499, 600});
  CHECK(r[1] == IRect{503, 0, 498, 600});
}

TEST_CASE("parse_config reads players, bindings and settings") {
  json j = {
      {"core_path", "/c.so"},
      {"rom_path", "/r.nes"},
      {"system_dir", "/sys"},
      {"layout", {{"cols", 2}, {"rows", 1}, {"rects", {{0, 0, 1, 1}, {0, 1, 1, 1}}}}},
      {"players",
       {{{"index", 0}, {"name", "Ann"}, {"color", "#E53935"}, {"save_dir", "/s0"},
         {"device", {{"kind", "keyboard"}, {"set", "A"}}},
         {"bindings", {{"Up", 119}, {"B", 106}, {"Bogus", 1}}}},
        {{"index", 3}, {"device", {{"kind", "pad"}, {"guid", "abc"}, {"ordinal", 1}}},
         {"bindings", {{"A", "button:b"}, {"L2", "axis:lefttrigger+"}}}}}},
      {"graphics", {{"filter", "bilinear"}, {"integer_scale", true}}},
      {"osd", {{"enabled", true}, {"position", "bottom-right"}}},
      {"audio", {{"volume", 250}, {"rotation_s", 1}}},
  };
  Config c = parse_config(j);
  REQUIRE(c.players.size() == 2);
  CHECK(c.players[0].color == 0xE53935);
  CHECK(c.players[0].keys.at(119) == 4);
  CHECK(c.players[0].keys.at(106) == 0);
  CHECK(c.players[0].keys.size() == 2);
  CHECK(c.players[1].device.key() == "pad:abc:1");
  CHECK(c.players[1].pad.at("axis:lefttrigger+") == 12);
  CHECK(c.players[1].name.empty());
  CHECK(c.graphics.filter == "bilinear");
  CHECK(c.graphics.integer_scale);
  CHECK(c.graphics.aspect == "core");
  CHECK(c.osd.enabled);
  CHECK(c.osd.position == "bottom-right");
  CHECK(c.audio.volume == 100);  // clamped
  CHECK(c.audio.rotation_s == 5);
}

TEST_CASE("parse_config rejects configs it can't run") {
  CHECK_THROWS(parse_config(json::object()));
  json j = {{"core_path", "/c.so"}, {"rom_path", "/r"}, {"system_dir", "/s"},
            {"layout", {{"cols", 1}, {"rows", 1}, {"rects", json::array()}}},
            {"players", {{{"index", 0}}}}};
  CHECK_THROWS_WITH(parse_config(j), doctest::Contains("fewer tiles"));
}

TEST_CASE("pad_state_bits applies bindings and the left-stick d-pad") {
  std::map<std::string, int> binds = {{"button:a", 0}, {"axis:lefttrigger+", 12}, {"button:dpup", 4}};
  PadBits b = pad_state_bits({"a"}, {{"lefttrigger", 20000}, {"leftx", -30000}, {"righty", 5}}, binds);
  CHECK(b.bits == ((1u << 0) | (1u << 12) | (1u << 6)));  // B, L2, stick left
  CHECK(b.analog[0] == -30000);
  CHECK(b.analog[3] == 5);
  // Under the threshold: nothing.
  b = pad_state_bits({}, {{"lefttrigger", 16000}, {"lefty", 15000}}, binds);
  CHECK(b.bits == 0);
  CHECK(active_elements({"start"}, {{"rightx", -20000}}) ==
        std::set<std::string>{"button:start", "axis:rightx-"});
}
