// The session's single audio output: plays the audible player's ring, resampled to the device.
#pragma once

#include <SDL.h>

#include <atomic>
#include <string>
#include <vector>

#include "config.hpp"
#include "shm.hpp"

namespace mt::session {

class AudioOut {
 public:
  ~AudioOut() { close(); }

  // Opens the configured driver and device, falling back to SDL's defaults. Returns a warning
  // when it had to fall back (empty if all went as configured); `ok()` says whether any
  // device opened.
  std::string open(const AudioCfg &cfg);
  void close();
  bool ok() const { return dev_ != 0; }

  // The ring to play (nullptr = silence). Queued audio in it is skipped first. The ring must
  // stay mapped until another source is set or close() returns.
  void set_source(shm::Audio *ring) { src_.store(ring, std::memory_order_release); }

  const std::string &driver() const { return driver_; }
  int device_rate() const { return have_.freq; }

 private:
  static void callback(void *self, Uint8 *out, int len);
  void fill(int16_t *out, int frames);

  SDL_AudioDeviceID dev_ = 0;
  SDL_AudioSpec have_{};
  std::string driver_;
  int volume_ = 100;
  std::atomic<shm::Audio *> src_{nullptr};
  // Audio thread only.
  shm::Audio *cur_ = nullptr;
  double cur_rate_ = 0;
  SDL_AudioStream *stream_ = nullptr;
  std::vector<int16_t> buf_;
};

}  // namespace mt::session
