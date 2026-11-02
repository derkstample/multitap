#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#include <doctest/doctest.h>

#include <atomic>
#include <cstdlib>
#include <memory>
#include <thread>
#include <vector>

#include "shm.hpp"

using namespace mt::shm;

namespace {

struct Block {
  explicit Block(size_t n) : p(static_cast<uint8_t *>(std::aligned_alloc(64, round_up(n, 64)))) {
    std::memset(p.get(), 0, n);
  }
  struct Free {
    void operator()(uint8_t *q) const { std::free(q); }
  };
  std::unique_ptr<uint8_t, Free> p;
  void *get() const { return p.get(); }
};

void write_frame(Video &v, uint32_t value, uint32_t w = 4, uint32_t h = 4) {
  std::vector<uint32_t> px(w * h, value);
  uint32_t i = v.begin_write();
  v.write_rows(i, px.data(), w * 4, h, w * 4);
  v.bump_frame_no();
  v.publish(i, w, 1, 1.0f);
}

}  // namespace

TEST_CASE("video: empty until the first publish, then the newest frame wins") {
  Block mem(video_size(64));
  Video v(mem.get());
  v.init(64);
  CHECK(v.valid());
  CHECK_FALSE(v.acquire());

  write_frame(v, 7);
  write_frame(v, 9);
  FrameView f = v.acquire();
  REQUIRE(f);
  CHECK(f.slot->width == 4);
  CHECK(f.slot->pitch == 16);
  CHECK(f.slot->frame_no == 2);
  CHECK(f.slot->seq % 2 == 0);
  CHECK(reinterpret_cast<const uint32_t *>(f.pixels)[0] == 9);
}

TEST_CASE("video: the writer never touches the slot being read") {
  Block mem(video_size(64));
  Video v(mem.get());
  v.init(64);
  write_frame(v, 1);
  FrameView held = v.acquire();
  REQUIRE(held);
  for (uint32_t n = 2; n < 50; n++) write_frame(v, n);
  CHECK(reinterpret_cast<const uint32_t *>(held.pixels)[15] == 1);
  v.release();
  CHECK(reinterpret_cast<const uint32_t *>(v.acquire().pixels)[0] == 49);
}

TEST_CASE("video: rows are packed and clipped to the slot capacity") {
  Block mem(video_size(2 * 2 * 4));
  Video v(mem.get());
  v.init(2 * 2 * 4);
  std::vector<uint32_t> src(4 * 3, 5);  // 3 rows of 2 px with a 4 px pitch
  uint32_t i = v.begin_write();
  v.write_rows(i, src.data(), 8, 3, 16);
  v.publish(i, 2, 1, 1.0f);
  FrameView f = v.acquire();
  CHECK(f.slot->pitch == 8);
  CHECK(f.slot->height == 2);
}

TEST_CASE("video: concurrent reader never sees a torn frame") {
  constexpr uint32_t W = 64, H = 64;
  Block mem(video_size(W * H * 4));
  Video v(mem.get());
  v.init(W * H * 4);
  std::atomic<bool> done{false};
  std::thread writer([&] {
    for (uint32_t n = 1; n <= 20000; n++) write_frame(v, n, W, H);
    done = true;
  });
  uint64_t frames = 0, last = 0;
  bool torn = false, backwards = false;
  while (!done) {
    FrameView f = v.acquire();
    if (!f) continue;
    auto *px = reinterpret_cast<const uint32_t *>(f.pixels);
    uint32_t first = px[0];
    for (uint32_t k = 0; k < W * H; k++) torn |= px[k] != first;
    backwards |= first < last;
    last = first;
    v.release();
    frames++;
  }
  writer.join();
  CHECK_FALSE(torn);
  CHECK_FALSE(backwards);
  CHECK(frames > 0);
}

TEST_CASE("input: held plus latched presses, consumed once per frame") {
  Block mem(sizeof(InputBlock));
  Input in(mem.get());
  in.init();
  CHECK(in.valid());

  in.set_held(0b1001);
  CHECK(in.take().buttons == 0b1001);
  CHECK(in.take().buttons == 0b1001);  // still held

  in.set_held(0b0001);  // release Start
  in.set_held(0b0011);  // tap A ...
  in.set_held(0b0001);  // ... and let go before the worker polls
  CHECK(in.take().buttons == 0b0011);
  CHECK(in.take().buttons == 0b0001);

  in.set_analog(2, -12345);
  CHECK(in.take().analog[2] == -12345);
}

TEST_CASE("audio: ring wraps, drops on overflow and reports fill") {
  Block mem(audio_size(1024));
  Audio a(mem.get());
  a.init(1024, 44100.0);
  CHECK(a.valid());
  CHECK(audio_capacity_for(44100.0) == 32768);

  std::vector<int16_t> buf(2 * 1000);
  for (size_t i = 0; i < buf.size(); i++) buf[i] = int16_t(i);
  CHECK(a.write(buf.data(), 1000) == 1000);
  CHECK(a.write(buf.data(), 1000) == 24);  // full: the rest is dropped
  CHECK(a.fill() == 1024);

  std::vector<int16_t> out(2 * 1000);
  CHECK(a.read(out.data(), 1000) == 1000);
  CHECK(out == buf);
  CHECK(a.write(buf.data(), 1000) == 1000);  // wraps around the end
  CHECK(a.read(out.data(), 24) == 24);
  CHECK(a.read(out.data(), 1000) == 1000);
  CHECK(out == buf);
  CHECK(a.fill() == 0);

  a.write(buf.data(), 10);
  a.skip_all();
  CHECK(a.fill() == 0);
}

TEST_CASE("audio: concurrent producer and consumer keep sample order") {
  Block mem(audio_size(256));
  Audio a(mem.get());
  a.init(256, 48000.0);
  constexpr int16_t kTotal = 30000;
  std::thread producer([&] {
    int16_t next = 0;
    while (next < kTotal) {
      int16_t frame[2] = {next, int16_t(-next)};
      if (a.write(frame, 1)) next++;
    }
  });
  int16_t expect = 0;
  bool ok = true;
  while (expect < kTotal) {
    int16_t frame[2];
    if (!a.read(frame, 1)) continue;
    ok &= frame[0] == expect && frame[1] == -expect;
    expect++;
  }
  producer.join();
  CHECK(ok);
}
