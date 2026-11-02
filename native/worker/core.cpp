#include "core.hpp"

#include <dlfcn.h>

#include <algorithm>
#include <cctype>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iterator>

namespace mt {

namespace {

Core *g_core = nullptr;

constexpr unsigned kEnvCmdMask = 0xFFFF;  // drop the EXPERIMENTAL/PRIVATE flags

std::string dl_error() {
  const char *e = dlerror();
  return e ? e : "unknown error";
}

std::vector<std::string> split_extensions(const char *exts) {
  std::vector<std::string> out;
  std::string cur;
  auto flush = [&] {
    auto b = cur.find_first_not_of(" \t"), e = cur.find_last_not_of(" \t");
    if (b != std::string::npos) {
      std::string ext = cur.substr(b, e - b + 1);
      std::transform(ext.begin(), ext.end(), ext.begin(), ::tolower);
      out.push_back(ext);
    }
    cur.clear();
  };
  for (const char *p = exts ? exts : ""; *p; p++) {
    if (*p == '|') flush();
    else cur += *p;
  }
  flush();
  return out;
}

SystemInfo to_system_info(const retro_system_info &si) {
  SystemInfo info;
  info.library_name = si.library_name ? si.library_name : "";
  info.library_version = si.library_version ? si.library_version : "";
  info.valid_extensions = split_extensions(si.valid_extensions);
  info.need_fullpath = si.need_fullpath;
  info.block_extract = si.block_extract;
  return info;
}

}  // namespace

// Entry points resolved from the core library.
struct Core::Api {
  void *handle = nullptr;
#define MT_RETRO_FNS(X)                                \
  X(void, retro_set_environment, retro_environment_t)  \
  X(void, retro_set_video_refresh, retro_video_refresh_t) \
  X(void, retro_set_audio_sample, retro_audio_sample_t) \
  X(void, retro_set_audio_sample_batch, retro_audio_sample_batch_t) \
  X(void, retro_set_input_poll, retro_input_poll_t)    \
  X(void, retro_set_input_state, retro_input_state_t)  \
  X(void, retro_init)                                  \
  X(void, retro_deinit)                                \
  X(unsigned, retro_api_version)                       \
  X(void, retro_get_system_info, retro_system_info *)  \
  X(void, retro_get_system_av_info, retro_system_av_info *) \
  X(void, retro_set_controller_port_device, unsigned, unsigned) \
  X(void, retro_reset)                                 \
  X(void, retro_run)                                   \
  X(bool, retro_load_game, const retro_game_info *)    \
  X(void, retro_unload_game)                           \
  X(void *, retro_get_memory_data, unsigned)           \
  X(size_t, retro_get_memory_size, unsigned)
#define MT_DECLARE(ret, name, ...) ret (*name)(__VA_ARGS__) = nullptr;
  MT_RETRO_FNS(MT_DECLARE)
#undef MT_DECLARE

  // dlopen with RTLD_LOCAL, resolve every symbol and check the API version.
  // The library is never dlclosed: some cores crash on unload, and the process exits anyway.
  explicit Api(const std::string &path) {
    handle = dlopen(path.c_str(), RTLD_NOW | RTLD_LOCAL);
    if (!handle) throw CoreError("Cannot load core: " + dl_error());
#define MT_RESOLVE(ret, name, ...)                                                   \
  dlerror();                                                                         \
  name = reinterpret_cast<decltype(name)>(dlsym(handle, #name));                     \
  if (!name) throw CoreError("Not a libretro core (missing symbol): " + dl_error());
    MT_RETRO_FNS(MT_RESOLVE)
#undef MT_RESOLVE
    if (retro_api_version() != RETRO_API_VERSION)
      throw CoreError("Unsupported libretro API version");
  }
#undef MT_RETRO_FNS
};

SystemInfo read_system_info(const std::string &core_path) {
  Core::Api api(core_path);
  retro_system_info si{};
  api.retro_get_system_info(&si);
  return to_system_info(si);
}

Core::Core(const std::string &core_path, const std::string &system_dir,
           const std::string &save_dir, const std::string &username,
           std::map<std::string, std::string> option_overrides, Hooks hooks)
    : hooks_(std::move(hooks)),
      path_(core_path),
      system_dir_(system_dir),
      save_dir_(save_dir),
      username_(username.empty() ? "Player" : username),
      overrides_(std::move(option_overrides)) {
  if (g_core) throw CoreError("Only one core per process");
  api_ = new Api(core_path);
  g_core = this;
  api_->retro_set_environment(env_cb);
  api_->retro_set_video_refresh(video_cb);
  api_->retro_set_audio_sample(audio_sample_cb);
  api_->retro_set_audio_sample_batch(audio_batch_cb);
  api_->retro_set_input_poll(input_poll_cb);
  api_->retro_set_input_state(input_state_cb);
  api_->retro_init();
  initialised_ = true;
  retro_system_info si{};
  api_->retro_get_system_info(&si);
  info_ = to_system_info(si);
}

Core::~Core() {
  close();
  g_core = nullptr;
  delete api_;
}

// ---- lifecycle -------------------------------------------------------------------------

void Core::load_game(const std::string &content_path) {
  game_path_ = content_path;
  retro_game_info game{};
  game.path = game_path_.c_str();
  if (!info_.need_fullpath) {
    std::ifstream f(content_path, std::ios::binary);
    if (!f) throw std::runtime_error("Cannot read " + content_path + ": " + std::strerror(errno));
    game_data_.assign(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
    game.data = game_data_.data();
    game.size = game_data_.size();
  }
  if (!api_->retro_load_game(&game)) {
    if (hw_render_requested_)
      throw CoreError(
          "This core needs hardware (OpenGL/Vulkan) rendering, which MultiTap v1 does not "
          "support");
    throw CoreError("The core rejected this file");
  }
  loaded_ = true;
  api_->retro_get_system_av_info(&av_);
  api_->retro_set_controller_port_device(0, RETRO_DEVICE_JOYPAD);
}

void Core::run() { api_->retro_run(); }
void Core::reset() { api_->retro_reset(); }

void Core::close() {
  if (loaded_) {
    api_->retro_unload_game();
    loaded_ = false;
  }
  if (initialised_) {
    api_->retro_deinit();
    initialised_ = false;
  }
}

double Core::fps() const { return av_.timing.fps > 1 ? av_.timing.fps : 60.0; }

double Core::aspect() const {
  const auto &g = av_.geometry;
  if (g.aspect_ratio > 0) return g.aspect_ratio;
  return g.base_height ? double(g.base_width) / g.base_height : 4.0 / 3.0;
}

std::span<uint8_t> Core::sram() {
  size_t size = api_->retro_get_memory_size(RETRO_MEMORY_SAVE_RAM);
  void *ptr = api_->retro_get_memory_data(RETRO_MEMORY_SAVE_RAM);
  if (!size || !ptr) return {};
  return {static_cast<uint8_t *>(ptr), size};
}

// ---- callbacks -------------------------------------------------------------------------

void Core::video_cb(const void *data, unsigned w, unsigned h, size_t pitch) {
  if (data == RETRO_HW_FRAME_BUFFER_VALID) data = nullptr;
  if (g_core->hooks_.video) g_core->hooks_.video(data, w, h, pitch);
}

void Core::audio_sample_cb(int16_t l, int16_t r) {
  g_core->audio.push_back(l);
  g_core->audio.push_back(r);
}

size_t Core::audio_batch_cb(const int16_t *data, size_t frames) {
  if (frames) g_core->audio.insert(g_core->audio.end(), data, data + frames * 2);
  return frames;
}

void Core::input_poll_cb() {}

int16_t Core::input_state_cb(unsigned port, unsigned device, unsigned index, unsigned id) {
  if (port != 0) return 0;
  device &= RETRO_DEVICE_MASK;
  if (device == RETRO_DEVICE_JOYPAD) {
    if (id == RETRO_DEVICE_ID_JOYPAD_MASK) return int16_t(g_core->input_bits & 0xFFFF);
    return id < 16 ? (g_core->input_bits >> id) & 1 : 0;
  }
  if (device == RETRO_DEVICE_ANALOG && index < 2 && id < 2)
    return int16_t(g_core->analog[index * 2 + id]);
  return 0;
}

void Core::log_cb(enum retro_log_level level, const char *fmt, ...) {
  if (!fmt || level < RETRO_LOG_WARN || !g_core || !g_core->hooks_.log) return;
  char small[512];
  va_list ap;
  va_start(ap, fmt);
  int n = std::vsnprintf(small, sizeof small, fmt, ap);
  va_end(ap);
  if (n < 0) return;
  std::string msg;
  if (size_t(n) < sizeof small) {
    msg.assign(small, size_t(n));
  } else {
    msg.resize(size_t(n) + 1);
    va_start(ap, fmt);
    std::vsnprintf(msg.data(), msg.size(), fmt, ap);
    va_end(ap);
    msg.resize(size_t(n));
  }
  while (!msg.empty() && std::isspace(static_cast<unsigned char>(msg.back()))) msg.pop_back();
  g_core->hooks_.log(msg);
}

bool Core::env_cb(unsigned cmd, void *data) {
  try {
    return g_core->handle_env(cmd & kEnvCmdMask, data);
  } catch (const std::exception &e) {  // never unwind into C
    if (g_core->hooks_.log) g_core->hooks_.log("env " + std::to_string(cmd) + ": " + e.what());
    return false;
  }
}

bool Core::handle_env(unsigned cmd, void *data) {
  switch (cmd) {
    case RETRO_ENVIRONMENT_GET_CAN_DUPE:
      *static_cast<bool *>(data) = true;
      return true;
    case RETRO_ENVIRONMENT_GET_OVERSCAN:
      *static_cast<bool *>(data) = false;
      return true;
    case RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY:
    case RETRO_ENVIRONMENT_GET_CORE_ASSETS_DIRECTORY:
      *static_cast<const char **>(data) = system_dir_.c_str();
      return true;
    case RETRO_ENVIRONMENT_GET_SAVE_DIRECTORY:
      *static_cast<const char **>(data) = save_dir_.c_str();
      return true;
    case RETRO_ENVIRONMENT_GET_LIBRETRO_PATH:
      *static_cast<const char **>(data) = path_.c_str();
      return true;
    case RETRO_ENVIRONMENT_GET_USERNAME:
      *static_cast<const char **>(data) = username_.c_str();
      return true;
    case RETRO_ENVIRONMENT_SET_PIXEL_FORMAT: {
      unsigned fmt = *static_cast<const unsigned *>(data);
      if (fmt > RETRO_PIXEL_FORMAT_RGB565) return false;
      pixel_format_ = fmt;
      return true;
    }
    case RETRO_ENVIRONMENT_GET_LOG_INTERFACE:
      static_cast<retro_log_callback *>(data)->log = log_cb;
      return true;

    // ---- core options: legacy variables, v1 and v2 ----
    case RETRO_ENVIRONMENT_GET_CORE_OPTIONS_VERSION:
      *static_cast<unsigned *>(data) = 2;
      return true;
    case RETRO_ENVIRONMENT_SET_VARIABLES:
      for (auto *v = static_cast<const retro_variable *>(data); v && v->key; v++) {
        // "Description; first|second|third" - the first value is the default.
        std::string desc = v->value ? v->value : "";
        std::vector<std::string> values;
        if (auto semi = desc.find(';'); semi != std::string::npos) {
          std::string list = desc.substr(semi + 1);
          auto b = list.find_first_not_of(" \t\r\n");
          auto e = list.find_last_not_of(" \t\r\n");
          list = b == std::string::npos ? "" : list.substr(b, e - b + 1);
          size_t start = 0;
          for (size_t bar; (bar = list.find('|', start)) != std::string::npos; start = bar + 1)
            values.push_back(list.substr(start, bar - start));
          values.push_back(list.substr(start));
        }
        define_option(v->key, values, nullptr);
      }
      return true;
    case RETRO_ENVIRONMENT_SET_CORE_OPTIONS:
      define_options(static_cast<const retro_core_option_definition *>(data));
      return true;
    case RETRO_ENVIRONMENT_SET_CORE_OPTIONS_INTL:
      define_options(static_cast<const retro_core_options_intl *>(data)->us);
      return true;
    case RETRO_ENVIRONMENT_SET_CORE_OPTIONS_V2:
      if (data) define_options(static_cast<const retro_core_options_v2 *>(data)->definitions);
      return true;
    case RETRO_ENVIRONMENT_SET_CORE_OPTIONS_V2_INTL: {
      auto *intl = static_cast<const retro_core_options_v2_intl *>(data);
      if (intl && intl->us) define_options(intl->us->definitions);
      return true;
    }
    case RETRO_ENVIRONMENT_GET_VARIABLE: {
      auto *var = static_cast<retro_variable *>(data);
      auto it = var->key ? option_values_.find(var->key) : option_values_.end();
      if (it == option_values_.end()) {
        var->value = nullptr;
        return false;
      }
      var->value = it->second.c_str();
      return true;
    }
    case RETRO_ENVIRONMENT_SET_VARIABLE: {
      auto *var = static_cast<const retro_variable *>(data);
      if (var && var->key && var->value) set_option(var->key, var->value);
      return true;  // a null pointer only queries support
    }
    case RETRO_ENVIRONMENT_GET_VARIABLE_UPDATE:
      *static_cast<bool *>(data) = options_dirty_;
      options_dirty_ = false;
      return true;
    case RETRO_ENVIRONMENT_SET_CORE_OPTIONS_DISPLAY:
    case RETRO_ENVIRONMENT_SET_CORE_OPTIONS_UPDATE_DISPLAY_CALLBACK:
      return true;  // nothing shows core options yet

    case RETRO_ENVIRONMENT_SET_HW_RENDER:
      hw_render_requested_ = true;  // accepted in phase 5
      return false;
    case RETRO_ENVIRONMENT_SET_SYSTEM_AV_INFO:
      av_ = *static_cast<const retro_system_av_info *>(data);
      if (hooks_.av_changed) hooks_.av_changed();
      return true;
    case RETRO_ENVIRONMENT_SET_GEOMETRY:
      av_.geometry = *static_cast<const retro_game_geometry *>(data);
      if (hooks_.av_changed) hooks_.av_changed();
      return true;
    case RETRO_ENVIRONMENT_GET_LANGUAGE:
      *static_cast<unsigned *>(data) = RETRO_LANGUAGE_ENGLISH;
      return true;
    case RETRO_ENVIRONMENT_GET_AUDIO_VIDEO_ENABLE:
      *static_cast<int *>(data) = 3;  // video and audio
      return true;
    case RETRO_ENVIRONMENT_GET_FASTFORWARDING:
      *static_cast<bool *>(data) = false;
      return true;
    case RETRO_ENVIRONMENT_GET_TARGET_REFRESH_RATE:
      *static_cast<float *>(data) = 60.0f;
      return true;
    case RETRO_ENVIRONMENT_GET_MESSAGE_INTERFACE_VERSION:
      *static_cast<unsigned *>(data) = 0;
      return true;
    case RETRO_ENVIRONMENT_GET_INPUT_MAX_USERS:
      *static_cast<unsigned *>(data) = 1;  // one player per process
      return true;
    case RETRO_ENVIRONMENT_SET_FRAME_TIME_CALLBACK:
      frame_time_ = *static_cast<const retro_frame_time_callback *>(data);
      return true;
    case RETRO_ENVIRONMENT_SHUTDOWN:
      shutdown_requested_ = true;
      return true;
    case RETRO_ENVIRONMENT_GET_INPUT_BITMASKS:
    case RETRO_ENVIRONMENT_SET_MESSAGE:
    case RETRO_ENVIRONMENT_SET_MESSAGE_EXT:
    case RETRO_ENVIRONMENT_SET_PERFORMANCE_LEVEL:
    case RETRO_ENVIRONMENT_SET_INPUT_DESCRIPTORS:
    case RETRO_ENVIRONMENT_SET_SUPPORT_NO_GAME:
    case RETRO_ENVIRONMENT_SET_CONTROLLER_INFO:
    case RETRO_ENVIRONMENT_SET_SUBSYSTEM_INFO:
    case RETRO_ENVIRONMENT_SET_MEMORY_MAPS:
    case RETRO_ENVIRONMENT_SET_SERIALIZATION_QUIRKS:
    case RETRO_ENVIRONMENT_SET_SUPPORT_ACHIEVEMENTS:
    case RETRO_ENVIRONMENT_SET_CONTENT_INFO_OVERRIDE:
      return true;
    default:
      return false;
  }
}

// ---- core options ----------------------------------------------------------------------

template <class Def>
void Core::define_options(const Def *defs) {
  for (; defs && defs->key; defs++) {
    std::vector<std::string> values;
    for (const auto &v : defs->values) {
      if (!v.value) break;
      values.push_back(v.value);
    }
    define_option(defs->key, values, defs->default_value);
  }
}

void Core::define_option(const std::string &key, const std::vector<std::string> &values,
                         const char *default_value) {
  std::string value = default_value ? default_value : (values.empty() ? "" : values.front());
  if (auto it = overrides_.find(key); it != overrides_.end() &&
      std::find(values.begin(), values.end(), it->second) != values.end())
    value = it->second;
  set_option(key, value);
}

void Core::set_option(const std::string &key, const std::string &value) {
  option_values_[key] = value;
  options_dirty_ = true;
}

}  // namespace mt
