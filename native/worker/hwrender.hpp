// Hosts a hardware-rendered (OpenGL / OpenGL ES) libretro core: an offscreen EGL context, the
// framebuffer the core draws into, and readback of finished frames as XRGB8888 rows.
#pragma once

#include <libretro.h>

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace mt {

class HwRender {
 public:
  HwRender();
  ~HwRender();
  HwRender(const HwRender &) = delete;
  HwRender &operator=(const HwRender &) = delete;

  // Whether this build has EGL at all.
  static bool supported();
  // RETRO_ENVIRONMENT_SET_HW_RENDER: accept OpenGL / GLES requests, fill in the frontend
  // callbacks. False for APIs we can't host (Vulkan, Direct3D).
  bool accept(retro_hw_render_callback *cb);
  bool requested() const { return requested_; }
  bool active() const { return active_; }

  // After retro_load_game: create the context and a max_w x max_h framebuffer, then call
  // the core's context_reset. Throws std::runtime_error when OpenGL isn't available.
  void start(unsigned max_w, unsigned max_h);
  // The core's geometry grew: make the framebuffer at least this big.
  void ensure_size(unsigned w, unsigned h);
  // Before retro_unload_game: the core's context_destroy. The context stays current, since
  // cores may still touch GL while unloading and in retro_deinit (as RetroArch allows).
  void notify_destroy();
  // After retro_deinit: free the framebuffer and the context.
  void release();

  // Read the w x h frame the core just rendered; rows top first, XRGB8888, tightly packed.
  const uint8_t *read_frame(unsigned w, unsigned h);
  // "OpenGL 4.6 (NVIDIA ...)" once started.
  const std::string &description() const { return description_; }

 private:
  struct Gl;
  static uintptr_t get_current_framebuffer();
  static retro_proc_address_t get_proc_address(const char *sym);
  void make_framebuffer(unsigned w, unsigned h);

  retro_hw_render_callback cb_{};
  bool requested_ = false, active_ = false, destroyed_ = false, gles_ = false;
  std::unique_ptr<Gl> gl_;
  void *display_ = nullptr, *context_ = nullptr, *surface_ = nullptr;
  uint32_t fbo_ = 0, color_ = 0, depth_ = 0;
  unsigned fb_w_ = 0, fb_h_ = 0;
  std::vector<uint8_t> pixels_;
  std::string description_;
};

}  // namespace mt
