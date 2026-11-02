// RetroArch .slangp shader presets through librashader, loaded at runtime (librashader.so is
// optional: without it, presets are reported as unavailable and tiles use plain scaling).
#pragma once

#include <cstdint>
#include <memory>
#include <string>

namespace mt::session {

class ShaderChain {
 public:
  ~ShaderChain();
  // Load `preset` for the current GL context; nullptr and `error` set on failure.
  static std::unique_ptr<ShaderChain> create(const std::string &preset, std::string &error);
  // Whether librashader.so could be loaded at all.
  static bool available();

  // Run the chain on `in` (a GL_RGBA8 texture, first row = top of the image) into `out`.
  bool frame(uint32_t in, int in_w, int in_h, uint32_t out, int out_w, int out_h,
             uint64_t frame_count, float fps, std::string &error);

 private:
  ShaderChain() = default;
  void *chain_ = nullptr;
};

}  // namespace mt::session
