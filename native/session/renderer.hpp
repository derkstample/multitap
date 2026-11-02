// GL 3.3 compositor: one texture per tile, uploaded from the worker's video shm and scaled into
// the tile with the configured filter, aspect and integer scaling. With a shader preset and
// librashader present, each tile runs through its own librashader filter chain instead.
#pragma once

#define GL_GLEXT_PROTOTYPES 1
#include <GL/glcorearb.h>

#include <deque>
#include <memory>
#include <string>
#include <vector>

#include "config.hpp"
#include "players.hpp"

namespace mt::session {

class ShaderChain;  // shaders.hpp

struct TileFrame {
  bool has_frame = false;
  int width = 0, height = 0;  // whole frame, before the overscan crop
  int fmt = -1;
  uint64_t frame_no = 0;
  int64_t last_ns = 0;
  std::deque<float> intervals_ms;  // between new frames, newest last
};

class Renderer {
 public:
  Renderer(const Graphics &g, size_t tiles);
  ~Renderer();

  // Needs a current GL context. Returns an error message, or empty on success.
  std::string init();
  // Shader preset status for the OSD: empty when fine or not requested.
  const std::string &shader_warning() const { return shader_warning_; }

  // Bind the target (the window, or an offscreen buffer when `offscreen`) and clear it.
  void begin(int width, int height, bool offscreen);
  // Upload the newest frame of tile i if it changed. Returns true for a new frame.
  bool update(size_t i, Player &p);
  void draw(size_t i, const IRect &tile, double core_aspect, double fps);
  // After drawing an offscreen frame: copy it to the window, and return it as RGB rows (top first).
  std::vector<uint8_t> finish_offscreen();

  const TileFrame &frame(size_t i) const { return tiles_[i].frame; }

 private:
  struct Tile {
    TileFrame frame;
    GLuint tex = 0;
    int tex_w = 0, tex_h = 0;  // the cropped region the texture holds
    int src_y = 0;
    uint64_t shader_frames = 0;
    std::unique_ptr<ShaderChain> chain;
    GLuint out_tex = 0;  // shader output
    int out_w = 0, out_h = 0;
  };

  void blit(GLuint tex, int x, int y, int w, int h, bool flip);

  Graphics g_;
  std::vector<Tile> tiles_;
  GLuint prog_ = 0, vao_ = 0;
  GLint u_flip_ = -1;
  int width_ = 0, height_ = 0;
  GLuint fbo_ = 0, fbo_tex_ = 0;
  int fbo_w_ = 0, fbo_h_ = 0;
  bool offscreen_ = false;
  bool shaders_on_ = false;
  std::string shader_warning_;
};

}  // namespace mt::session
