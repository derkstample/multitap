// A libretro core wrapper (one core per process), ported from multitap/libretro/core.py.
// OpenGL cores render into an offscreen EGL context (hwrender.hpp); their frames reach the video
// hook as XRGB8888 like any software frame.
#pragma once

#include <libretro.h>

#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <span>
#include <stdexcept>
#include <string>
#include <vector>

#include "hwrender.hpp"

namespace mt {

struct CoreError : std::runtime_error {
  using std::runtime_error::runtime_error;
};

struct SystemInfo {
  std::string library_name, library_version;
  std::vector<std::string> valid_extensions;  // lower case, without dots
  bool need_fullpath = false, block_extract = false;
};

// Query name/version/extensions without initialising the core.
SystemInfo read_system_info(const std::string &core_path);

class Core {
 public:
  struct Hooks {
    // data is null for a duplicate frame.
    std::function<void(const void *data, unsigned w, unsigned h, size_t pitch)> video;
    std::function<void()> av_changed;
    std::function<void(const std::string &)> log;  // warnings and errors from the core
  };

  // Only one Core may exist per process: libretro callbacks carry no user pointer.
  Core(const std::string &core_path, const std::string &system_dir, const std::string &save_dir,
       const std::string &username, std::map<std::string, std::string> option_overrides,
       Hooks hooks);
  ~Core();
  Core(const Core &) = delete;
  Core &operator=(const Core &) = delete;

  void load_game(const std::string &content_path);
  void run();
  void reset();
  void close();

  const SystemInfo &system_info() const { return info_; }
  const retro_system_av_info &av_info() const { return av_; }
  double fps() const;
  double sample_rate() const { return av_.timing.sample_rate; }
  double aspect() const;
  unsigned pixel_format() const {
    return hw_->active() ? unsigned(RETRO_PIXEL_FORMAT_XRGB8888) : pixel_format_;
  }
  bool hw_render_requested() const { return hw_render_requested_; }
  // "OpenGL 4.6 (renderer)" for hardware-rendered cores, else empty.
  std::string hw_description() const { return hw_->active() ? hw_->description() : ""; }
  bool shutdown_requested() const { return shutdown_requested_; }
  const std::map<std::string, std::string> &options() const { return option_values_; }

  // Save RAM straight from the core's memory (empty when it has none).
  std::span<uint8_t> sram();

  // Set by the owner before each run(); read from the input_state callback.
  uint32_t input_bits = 0;
  int32_t analog[4] = {0, 0, 0, 0};  // left x, left y, right x, right y
  // Interleaved int16 stereo produced during the last run(); the owner drains it.
  std::vector<int16_t> audio;

  // Frame time callback (RETRO_ENVIRONMENT_SET_FRAME_TIME_CALLBACK), if the core set one.
  bool has_frame_time_cb() const { return frame_time_.callback != nullptr; }
  int64_t frame_time_reference() const { return frame_time_.reference; }
  void call_frame_time(int64_t usec) {
    if (frame_time_.callback) frame_time_.callback(usec);
  }

 private:
  friend SystemInfo read_system_info(const std::string &core_path);
  struct Api;
  static bool env_cb(unsigned cmd, void *data);
  static void video_cb(const void *data, unsigned w, unsigned h, size_t pitch);
  static void audio_sample_cb(int16_t l, int16_t r);
  static size_t audio_batch_cb(const int16_t *data, size_t frames);
  static void input_poll_cb();
  static int16_t input_state_cb(unsigned port, unsigned device, unsigned index, unsigned id);
  static void log_cb(enum retro_log_level level, const char *fmt, ...);

  bool handle_env(unsigned cmd, void *data);
  void define_option(const std::string &key, const std::vector<std::string> &values,
                     const char *default_value);
  void set_option(const std::string &key, const std::string &value);
  template <class Def>
  void define_options(const Def *defs);

  Api *api_;
  std::unique_ptr<HwRender> hw_;
  SystemInfo info_;
  Hooks hooks_;
  retro_system_av_info av_{};
  unsigned pixel_format_ = RETRO_PIXEL_FORMAT_0RGB1555;
  bool hw_render_requested_ = false, shutdown_requested_ = false;
  bool loaded_ = false, initialised_ = false;
  retro_frame_time_callback frame_time_{};
  std::string path_, system_dir_, save_dir_, username_;
  std::map<std::string, std::string> overrides_, option_values_;
  bool options_dirty_ = false;
  std::vector<uint8_t> game_data_;
  std::string game_path_;
};

}  // namespace mt
