#include "shaders.hpp"

#include <SDL.h>

#define LIBRA_RUNTIME_OPENGL
#include "librashader_ld.h"

namespace mt::session {

namespace {

constexpr uint32_t kGL_RGBA8 = 0x8058;

libra_instance_t &lib() {
  static libra_instance_t instance = librashader_load_instance();
  return instance;
}

const void *gl_loader(const char *name) { return SDL_GL_GetProcAddress(name); }

// Turns a librashader error into text and frees it.
std::string take_error(libra_error_t err) {
  std::string out = "unknown librashader error";
  char *msg = nullptr;
  if (lib().error_write(err, &msg) == 0 && msg) {
    out = msg;
    lib().error_free_string(&msg);
  }
  lib().error_free(&err);
  return out;
}

}  // namespace

bool ShaderChain::available() { return lib().instance_loaded; }

std::unique_ptr<ShaderChain> ShaderChain::create(const std::string &preset, std::string &error) {
  if (!available()) {
    error = "librashader is not installed";
    return nullptr;
  }
  libra_shader_preset_t p = nullptr;
  if (libra_error_t err = lib().preset_create(preset.c_str(), &p)) {
    error = "cannot load " + preset + ": " + take_error(err);
    return nullptr;
  }
  filter_chain_gl_opt_t opt{};
  opt.version = LIBRASHADER_CURRENT_VERSION;
  opt.glsl_version = 330;
  opt.use_dsa = false;
  opt.force_no_mipmaps = false;
  opt.disable_cache = true;  // the cache needs DSA (GL 4.5)
  libra_gl_filter_chain_t chain = nullptr;
  libra_error_t err = lib().gl_filter_chain_create(&p, gl_loader, &opt, &chain);
  lib().preset_free(&p);  // a no-op if the chain took it
  if (err) {
    error = "cannot compile " + preset + ": " + take_error(err);
    return nullptr;
  }
  std::unique_ptr<ShaderChain> out(new ShaderChain);
  out->chain_ = chain;
  return out;
}

ShaderChain::~ShaderChain() {
  if (chain_) {
    auto chain = static_cast<libra_gl_filter_chain_t>(chain_);
    lib().gl_filter_chain_free(&chain);
  }
}

bool ShaderChain::frame(uint32_t in, int in_w, int in_h, uint32_t out, int out_w, int out_h,
                        uint64_t frame_count, float fps, std::string &error) {
  auto chain = static_cast<libra_gl_filter_chain_t>(chain_);
  libra_image_gl_t src{in, kGL_RGBA8, uint32_t(in_w), uint32_t(in_h)};
  libra_image_gl_t dst{out, kGL_RGBA8, uint32_t(out_w), uint32_t(out_h)};
  libra_viewport_t vp{0, 0, uint32_t(out_w), uint32_t(out_h)};
  frame_gl_opt_t opt{};
  opt.version = LIBRASHADER_CURRENT_VERSION;
  opt.frame_direction = 1;
  opt.total_subframes = 1;
  opt.current_subframe = 1;
  opt.frames_per_second = fps;
  if (libra_error_t err = lib().gl_filter_chain_frame(&chain, size_t(frame_count), src, dst, &vp,
                                                      nullptr, &opt)) {
    error = take_error(err);
    return false;
  }
  return true;
}

}  // namespace mt::session
