#include "audio.hpp"

#include <algorithm>
#include <cstdlib>
#include <cstring>

namespace mt::session {

constexpr int kDeviceRate = 48000;
constexpr int kDeviceSamples = 512;  // ~11 ms per callback

std::string AudioOut::open(const AudioCfg &cfg) {
  close();
  volume_ = cfg.volume;
  std::string warning;
  bool inited = false;
  if (cfg.driver != "auto") {
    inited = SDL_AudioInit(cfg.driver.c_str()) == 0;
    if (!inited) warning = "Audio backend " + cfg.driver + " is unavailable; using the default";
  }
  if (!inited && SDL_AudioInit(nullptr) != 0) return std::string("No audio: ") + SDL_GetError();
  const char *drv = SDL_GetCurrentAudioDriver();
  driver_ = drv ? drv : "";

  SDL_AudioSpec want{};
  want.freq = kDeviceRate;
  want.format = AUDIO_S16SYS;
  want.channels = 2;
  want.samples = kDeviceSamples;
  want.callback = &AudioOut::callback;
  want.userdata = this;
  const char *name = cfg.device.empty() ? nullptr : cfg.device.c_str();
  dev_ = SDL_OpenAudioDevice(name, 0, &want, &have_, SDL_AUDIO_ALLOW_FREQUENCY_CHANGE);
  if (!dev_ && name) {
    warning = "Audio device \"" + cfg.device + "\" not found; using the default";
    dev_ = SDL_OpenAudioDevice(nullptr, 0, &want, &have_, SDL_AUDIO_ALLOW_FREQUENCY_CHANGE);
  }
  if (!dev_) return std::string("No audio: ") + SDL_GetError();
  SDL_PauseAudioDevice(dev_, 0);
  return warning;
}

void AudioOut::close() {
  if (dev_) {
    SDL_CloseAudioDevice(dev_);
    dev_ = 0;
  }
  if (stream_) {
    SDL_FreeAudioStream(stream_);
    stream_ = nullptr;
  }
  cur_ = nullptr;
  cur_rate_ = 0;
  if (!driver_.empty()) {
    SDL_AudioQuit();
    driver_.clear();
  }
}

void AudioOut::callback(void *self, Uint8 *out, int len) {
  static_cast<AudioOut *>(self)->fill(reinterpret_cast<int16_t *>(out), len / 4);
}

void AudioOut::fill(int16_t *out, int frames) {
  int bytes = frames * 4;
  shm::Audio *want = src_.load(std::memory_order_acquire);
  if (want != cur_) {
    cur_ = want;
    if (cur_) cur_->skip_all();  // whatever it queued while muted is stale
    if (stream_) SDL_AudioStreamClear(stream_);
  }
  double rate = cur_ ? cur_->sample_rate() : 0;
  if (!cur_ || rate <= 0) {
    std::memset(out, 0, size_t(bytes));
    return;
  }
  if (!stream_ || rate != cur_rate_) {
    if (stream_) SDL_FreeAudioStream(stream_);
    stream_ = SDL_NewAudioStream(AUDIO_S16SYS, 2, int(rate + 0.5), AUDIO_S16SYS, 2, have_.freq);
    cur_rate_ = rate;
    if (!stream_) {
      std::memset(out, 0, size_t(bytes));
      return;
    }
  }
  // Take only what this callback needs, so the worker's pacing keeps the ring near its target.
  while (SDL_AudioStreamAvailable(stream_) < bytes) {
    int missing = bytes - SDL_AudioStreamAvailable(stream_);
    size_t want_frames = size_t(missing / 4 * rate / have_.freq) + 8;
    buf_.resize(want_frames * 2);
    size_t got = cur_->read(buf_.data(), want_frames);
    if (!got) break;  // underrun: the rest is silence
    SDL_AudioStreamPut(stream_, buf_.data(), int(got * 4));
  }
  int got = std::max(0, SDL_AudioStreamGet(stream_, out, bytes));
  std::memset(reinterpret_cast<uint8_t *>(out) + got, 0, size_t(bytes - got));
  if (volume_ < 100) {
    for (int i = 0; i < frames * 2; i++) out[i] = int16_t(out[i] * volume_ / 100);
  }
}

}  // namespace mt::session
