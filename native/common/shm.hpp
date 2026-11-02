// Shared memory v2 between multitap-worker (one per player) and the session that shows it.
//
// The worker creates three blocks after the core has loaded, named
//   /dev/shm/multitap-<session pid>-<slot>-video
//   /dev/shm/multitap-<session pid>-<slot>-input
//   /dev/shm/multitap-<session pid>-<slot>-audio
// (the "multitap-<pid>-<slot>" prefix is the worker's --shm-prefix). All integers are
// little-endian; fields marked "atomic" are only touched with atomic loads/stores.
// Every block starts with a u32 magic and a u32 version (2).
//
// VIDEO: a 256-byte header, then 3 slots of `slot_stride` bytes each.
//   off  type  field
//     0  u32   magic         0x3256544D ("MTV2")
//     4  u32   version       2
//     8  u32   header_size   256 (offset of slot 0)
//    12  u32   slot_count    3
//    16  u64   slot_stride   64-byte slot header + slot_capacity, rounded up to 64
//    24  u32   slot_capacity pixel bytes per slot: max_width * max_height * 4 from av_info
//    28  u32   state         atomic; 0 loading, 1 running, 2 paused, 3 error, 4 exited
//    32  f32   aspect        display aspect ratio of the core
//    36  f32   measured_fps  frames run per second, updated about once a second
//    40  f64   core_fps      av_info.timing.fps
//    48  f64   sample_rate   av_info.timing.sample_rate
//    64  u32   latest        atomic; index of the newest complete slot, 0xFFFFFFFF = none yet
//   128  u32   reading       atomic; slot the reader holds, 0xFFFFFFFF = none
//   192  u64   frame_no      atomic; retro_run calls since load (dupes and paused time excluded)
//  Slot header (64 bytes at the start of each slot), then pixels at +64:
//     0  u32   seq           atomic; odd while the worker writes the slot, even when complete
//     4  u32   width
//     8  u32   height
//    12  u32   pitch         bytes per row; rows are packed, so pitch = width * bytes per pixel
//    16  u32   fmt           RETRO_PIXEL_FORMAT_* (0 0RGB1555, 1 XRGB8888, 2 RGB565)
//    20  f32   aspect        aspect at the time of this frame
//    24  u64   frame_no      header frame_no when this frame was produced
//  Writer: fill a slot that is neither `latest` nor `reading`, then store `latest` (release).
//  Reader: r = latest; loop { reading = r; if (latest == r) break; r = latest; } then copy
//  slot r; both sides use seq_cst for the reading/latest pair. Once confirmed, the writer
//  never touches slot r until `reading` changes. Readers without atomics (the Python test
//  harness) may instead copy `latest` and check that slot `seq` is even and unchanged.
//
// INPUT: 64 bytes, written by the session, read by the worker once per frame.
//     0  u32   magic         0x3249544D ("MTI2")
//     4  u32   version       2
//     8  u32   held          atomic; RetroPad mask (bit = RETRO_DEVICE_ID_JOYPAD_*) held now
//    12  u32   pressed       atomic; latched: the session ORs in bits on press, the worker
//                            swaps it to 0 each frame, so taps shorter than a frame still count
//    16  i32×4 analog        atomic; left x, left y, right x, right y (int16 range)
//    32  u32   seq           atomic; bumped by the session on every update
//  The core sees held | pressed for a frame.
//
// AUDIO: a 256-byte header, then `capacity` frames of interleaved stereo int16.
//     0  u32   magic         0x3241544D ("MTA2")
//     4  u32   version       2
//     8  u32   channels      2
//    12  u32   capacity      frames in the ring, a power of two
//    16  f64   sample_rate   atomic; the core's rate (no resampling happens in the worker)
//    64  u64   write_pos     atomic; frames ever written (producer: the worker)
//   128  u64   read_pos      atomic; frames ever consumed (consumer: the session)
//  Frame i lives at data[(i % capacity) * 2]. fill = write_pos - read_pos. The worker only
//  writes while audible and drops what doesn't fit; the session skips stale audio by setting
//  read_pos = write_pos when it makes a player audible.
#pragma once

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>
#include <utility>

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

namespace mt::shm {

constexpr uint32_t kVersion = 2;
constexpr uint32_t kVideoMagic = 0x3256544D;
constexpr uint32_t kInputMagic = 0x3249544D;
constexpr uint32_t kAudioMagic = 0x3241544D;
constexpr uint32_t kNone = 0xFFFFFFFFu;
constexpr uint32_t kSlots = 3;

enum State : uint32_t { kLoading = 0, kRunning = 1, kPaused = 2, kError = 3, kExited = 4 };

struct VideoHeader {
  uint32_t magic, version, header_size, slot_count;
  uint64_t slot_stride;
  uint32_t slot_capacity, state;
  float aspect, measured_fps;
  double core_fps, sample_rate;
  uint8_t pad0[8];
  alignas(64) uint32_t latest;
  alignas(64) uint32_t reading;
  alignas(64) uint64_t frame_no;
  uint8_t pad1[56];
};
static_assert(sizeof(VideoHeader) == 256);
static_assert(offsetof(VideoHeader, state) == 28 && offsetof(VideoHeader, core_fps) == 40);
static_assert(offsetof(VideoHeader, latest) == 64 && offsetof(VideoHeader, reading) == 128);
static_assert(offsetof(VideoHeader, frame_no) == 192);

struct SlotHeader {
  uint32_t seq, width, height, pitch, fmt;
  float aspect;
  uint64_t frame_no;
  uint8_t pad[32];
};
static_assert(sizeof(SlotHeader) == 64 && offsetof(SlotHeader, frame_no) == 24);

struct InputBlock {
  uint32_t magic, version, held, pressed;
  int32_t analog[4];
  uint32_t seq;
  uint8_t pad[28];
};
static_assert(sizeof(InputBlock) == 64 && offsetof(InputBlock, seq) == 32);

struct AudioHeader {
  uint32_t magic, version, channels, capacity;
  double sample_rate;
  uint8_t pad0[40];
  alignas(64) uint64_t write_pos;
  alignas(64) uint64_t read_pos;
  uint8_t pad1[120];
};
static_assert(sizeof(AudioHeader) == 256 && offsetof(AudioHeader, write_pos) == 64);
static_assert(offsetof(AudioHeader, read_pos) == 128);

template <class T>
inline std::atomic_ref<T> at(T &v) { return std::atomic_ref<T>(v); }

inline size_t round_up(size_t n, size_t to) { return (n + to - 1) / to * to; }

inline size_t video_size(uint32_t slot_capacity) {
  return sizeof(VideoHeader) + kSlots * round_up(sizeof(SlotHeader) + slot_capacity, 64);
}

inline size_t audio_size(uint32_t capacity) {
  return sizeof(AudioHeader) + size_t(capacity) * 2 * sizeof(int16_t);
}

inline uint32_t audio_capacity_for(double rate, double seconds = 0.5) {
  uint32_t cap = 1024;
  while (cap < rate * seconds && cap < (1u << 24)) cap <<= 1;
  return cap;
}

// ---- mapping ---------------------------------------------------------------------------

// A POSIX shm object mapped read/write. `name` has no leading slash ("multitap-1-0-video").
class Mapping {
 public:
  Mapping() = default;
  Mapping(const Mapping &) = delete;
  Mapping &operator=(const Mapping &) = delete;
  Mapping(Mapping &&o) noexcept { *this = std::move(o); }
  Mapping &operator=(Mapping &&o) noexcept {
    std::swap(base_, o.base_);
    std::swap(size_, o.size_);
    std::swap(name_, o.name_);
    return *this;
  }
  ~Mapping() {
    if (base_) munmap(base_, size_);
  }

  static Mapping create(const std::string &name, size_t size) {
    return map(name, size, O_RDWR | O_CREAT | O_TRUNC);
  }
  static Mapping open(const std::string &name) { return map(name, 0, O_RDWR); }

  void unlink() const {
    if (!name_.empty()) shm_unlink(("/" + name_).c_str());
  }
  void *data() const { return base_; }
  size_t size() const { return size_; }
  const std::string &name() const { return name_; }

 private:
  static Mapping map(const std::string &name, size_t size, int flags) {
    int fd = shm_open(("/" + name).c_str(), flags, 0600);
    if (fd < 0) throw std::runtime_error("shm_open " + name + ": " + std::strerror(errno));
    struct stat st {};
    // ftruncate on tmpfs is sparse: pages appear only when first written.
    if ((flags & O_CREAT) ? ftruncate(fd, off_t(size)) != 0 : fstat(fd, &st) != 0) {
      int e = errno;
      close(fd);
      throw std::runtime_error("shm size " + name + ": " + std::strerror(e));
    }
    if (!(flags & O_CREAT)) size = size_t(st.st_size);
    void *p = mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (p == MAP_FAILED) throw std::runtime_error("mmap " + name + ": " + std::strerror(errno));
    Mapping m;
    m.base_ = p;
    m.size_ = size;
    m.name_ = name;
    return m;
  }

  void *base_ = nullptr;
  size_t size_ = 0;
  std::string name_;
};

// ---- video -----------------------------------------------------------------------------

struct FrameView {
  const SlotHeader *slot = nullptr;
  const uint8_t *pixels = nullptr;
  explicit operator bool() const { return slot != nullptr; }
};

class Video {
 public:
  explicit Video(void *base) : h_(static_cast<VideoHeader *>(base)) {}

  // Worker side: format a fresh block of video_size(slot_capacity) bytes.
  void init(uint32_t slot_capacity) {
    std::memset(h_, 0, sizeof(VideoHeader));
    h_->magic = kVideoMagic;
    h_->version = kVersion;
    h_->header_size = sizeof(VideoHeader);
    h_->slot_count = kSlots;
    h_->slot_capacity = slot_capacity;
    h_->slot_stride = round_up(sizeof(SlotHeader) + slot_capacity, 64);
    h_->latest = kNone;
    h_->reading = kNone;
  }
  bool valid() const { return h_->magic == kVideoMagic && h_->version == kVersion; }
  VideoHeader &header() const { return *h_; }

  SlotHeader &slot(uint32_t i) const {
    return *reinterpret_cast<SlotHeader *>(reinterpret_cast<uint8_t *>(h_) + h_->header_size +
                                           i * h_->slot_stride);
  }
  uint8_t *pixels(uint32_t i) const {
    return reinterpret_cast<uint8_t *>(&slot(i)) + sizeof(SlotHeader);
  }

  void set_state(State s) { at(h_->state).store(s, std::memory_order_release); }
  State state() const { return State(at(h_->state).load(std::memory_order_acquire)); }
  uint64_t frame_no() const { return at(h_->frame_no).load(std::memory_order_acquire); }
  void bump_frame_no() { at(h_->frame_no).fetch_add(1, std::memory_order_release); }

  // Writer: the slot to fill next (neither the newest frame nor the one being read).
  uint32_t begin_write() {
    uint32_t latest = at(h_->latest).load(std::memory_order_relaxed);  // only we store it
    uint32_t reading = at(h_->reading).load(std::memory_order_seq_cst);
    uint32_t i = 0;
    while (i == latest || i == reading) i++;
    at(slot(i).seq).fetch_add(1, std::memory_order_relaxed);  // odd: in progress
    std::atomic_thread_fence(std::memory_order_release);
    return i;
  }

  // Writer: copy `height` rows of `row_bytes` from `src` (stride `src_pitch`) into slot i.
  // Rows that don't fit the slot capacity are dropped.
  void write_rows(uint32_t i, const void *src, uint32_t row_bytes, uint32_t height,
                  size_t src_pitch) {
    uint32_t cap = h_->slot_capacity;
    if (row_bytes > cap) row_bytes = cap;
    if (row_bytes && uint64_t(row_bytes) * height > cap) height = cap / row_bytes;
    uint8_t *dst = pixels(i);
    auto *s = static_cast<const uint8_t *>(src);
    if (src_pitch == row_bytes) {
      std::memcpy(dst, s, size_t(row_bytes) * height);
    } else {
      for (uint32_t y = 0; y < height; y++)
        std::memcpy(dst + size_t(y) * row_bytes, s + y * src_pitch, row_bytes);
    }
    SlotHeader &sl = slot(i);
    sl.height = height;
    sl.pitch = row_bytes;
  }

  // Writer: finish slot i and make it the newest frame.
  void publish(uint32_t i, uint32_t width, uint32_t fmt, float aspect) {
    SlotHeader &sl = slot(i);
    sl.width = width;
    sl.fmt = fmt;
    sl.aspect = aspect;
    sl.frame_no = at(h_->frame_no).load(std::memory_order_relaxed);
    at(sl.seq).fetch_add(1, std::memory_order_release);  // even: complete
    at(h_->latest).store(i, std::memory_order_seq_cst);
  }

  // Reader: claim the newest frame. The view stays valid until release() or the next acquire().
  FrameView acquire() {
    uint32_t r = at(h_->latest).load(std::memory_order_seq_cst);
    for (;;) {
      if (r == kNone) {
        at(h_->reading).store(kNone, std::memory_order_seq_cst);
        return {};
      }
      at(h_->reading).store(r, std::memory_order_seq_cst);
      uint32_t again = at(h_->latest).load(std::memory_order_seq_cst);
      if (again == r) break;
      r = again;
    }
    return {&slot(r), pixels(r)};
  }
  void release() { at(h_->reading).store(kNone, std::memory_order_seq_cst); }

 private:
  VideoHeader *h_;
};

// ---- input -----------------------------------------------------------------------------

struct InputState {
  uint32_t buttons = 0;
  int32_t analog[4] = {0, 0, 0, 0};
};

class Input {
 public:
  explicit Input(void *base) : b_(static_cast<InputBlock *>(base)) {}

  void init() {
    std::memset(b_, 0, sizeof(InputBlock));
    b_->magic = kInputMagic;
    b_->version = kVersion;
  }
  bool valid() const { return b_->magic == kInputMagic && b_->version == kVersion; }

  // Session side.
  void set_held(uint32_t mask) {
    uint32_t prev = at(b_->held).exchange(mask, std::memory_order_acq_rel);
    if (uint32_t down = mask & ~prev) at(b_->pressed).fetch_or(down, std::memory_order_acq_rel);
    at(b_->seq).fetch_add(1, std::memory_order_release);
  }
  void set_analog(int index, int32_t value) {
    at(b_->analog[index]).store(value, std::memory_order_release);
    at(b_->seq).fetch_add(1, std::memory_order_release);
  }

  // Worker side, once per frame: what the core sees this frame.
  InputState take() {
    InputState s;
    uint32_t pressed = at(b_->pressed).exchange(0, std::memory_order_acq_rel);
    s.buttons = (at(b_->held).load(std::memory_order_acquire) | pressed) & 0xFFFF;
    for (int i = 0; i < 4; i++) s.analog[i] = at(b_->analog[i]).load(std::memory_order_acquire);
    return s;
  }
  // Non-consuming view (for an OSD).
  uint32_t held() const { return at(b_->held).load(std::memory_order_acquire); }

 private:
  InputBlock *b_;
};

// ---- audio -----------------------------------------------------------------------------

class Audio {
 public:
  explicit Audio(void *base) : h_(static_cast<AudioHeader *>(base)) {}

  void init(uint32_t capacity, double sample_rate) {
    std::memset(h_, 0, sizeof(AudioHeader));
    h_->magic = kAudioMagic;
    h_->version = kVersion;
    h_->channels = 2;
    h_->capacity = capacity;
    h_->sample_rate = sample_rate;
  }
  bool valid() const { return h_->magic == kAudioMagic && h_->version == kVersion; }
  uint32_t capacity() const { return h_->capacity; }
  double sample_rate() const { return at(h_->sample_rate).load(std::memory_order_acquire); }
  void set_sample_rate(double r) { at(h_->sample_rate).store(r, std::memory_order_release); }

  // Frames queued and not yet consumed (either side may call).
  uint64_t fill() const {
    uint64_t w = at(h_->write_pos).load(std::memory_order_acquire);
    uint64_t r = at(h_->read_pos).load(std::memory_order_acquire);
    return w - r;
  }

  // Producer: append up to `frames` stereo frames; returns how many fit.
  size_t write(const int16_t *src, size_t frames) {
    uint64_t w = at(h_->write_pos).load(std::memory_order_relaxed);
    uint64_t r = at(h_->read_pos).load(std::memory_order_acquire);
    size_t room = h_->capacity - size_t(w - r);
    if (frames > room) frames = room;
    copy(w, const_cast<int16_t *>(src), frames, true);
    at(h_->write_pos).store(w + frames, std::memory_order_release);
    return frames;
  }

  // Consumer: take up to `frames` stereo frames; returns how many were read.
  size_t read(int16_t *dst, size_t frames) {
    uint64_t r = at(h_->read_pos).load(std::memory_order_relaxed);
    uint64_t w = at(h_->write_pos).load(std::memory_order_acquire);
    if (frames > w - r) frames = size_t(w - r);
    copy(r, dst, frames, false);
    at(h_->read_pos).store(r + frames, std::memory_order_release);
    return frames;
  }

  // Consumer: drop everything queued (used when a player becomes audible).
  void skip_all() {
    at(h_->read_pos).store(at(h_->write_pos).load(std::memory_order_acquire),
                           std::memory_order_release);
  }

 private:
  int16_t *data() const {
    return reinterpret_cast<int16_t *>(reinterpret_cast<uint8_t *>(h_) + sizeof(AudioHeader));
  }
  void copy(uint64_t pos, int16_t *buf, size_t frames, bool to_ring) const {
    size_t cap = h_->capacity, start = size_t(pos & (cap - 1));
    size_t first = std::min(frames, cap - start);
    int16_t *ring = data();
    if (to_ring) {
      std::memcpy(ring + start * 2, buf, first * 4);
      std::memcpy(ring, buf + first * 2, (frames - first) * 4);
    } else {
      std::memcpy(buf, ring + start * 2, first * 4);
      std::memcpy(buf + first * 2, ring, (frames - first) * 4);
    }
  }

  AudioHeader *h_;
};

}  // namespace mt::shm
