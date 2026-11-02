#include "renderer.hpp"

#include <algorithm>
#include <cmath>

#include "shaders.hpp"

namespace mt::session {

namespace {

constexpr size_t kIntervalHistory = 120;

// A quad from gl_VertexID; v = 0 is the top of the image (frames are uploaded top row first).
const char *kVertex = R"(#version 330 core
uniform bool u_flip;
out vec2 v_uv;
void main() {
  vec2 p = vec2(gl_VertexID & 1, gl_VertexID >> 1);
  v_uv = vec2(p.x, u_flip ? p.y : 1.0 - p.y);
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
)";

// The X byte of XRGB8888 (and the 1 of 0RGB1555) is undefined: always draw opaque.
const char *kFragment = R"(#version 330 core
uniform sampler2D u_tex;
in vec2 v_uv;
out vec4 color;
void main() { color = vec4(texture(u_tex, v_uv).rgb, 1.0); }
)";

GLuint compile(GLenum type, const char *src, std::string &err) {
  GLuint s = glCreateShader(type);
  glShaderSource(s, 1, &src, nullptr);
  glCompileShader(s);
  GLint ok = 0;
  glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
  if (!ok) {
    char log[1024];
    glGetShaderInfoLog(s, sizeof log, nullptr, log);
    err = std::string("shader: ") + log;
  }
  return s;
}

struct PixelFormat {
  GLenum format, type;
  int bpp;
};

bool pixel_format(int fmt, PixelFormat &out) {
  switch (fmt) {
    case 0: out = {GL_BGRA, GL_UNSIGNED_SHORT_1_5_5_5_REV, 2}; return true;  // 0RGB1555
    case 1: out = {GL_BGRA, GL_UNSIGNED_INT_8_8_8_8_REV, 4}; return true;    // XRGB8888
    case 2: out = {GL_RGB, GL_UNSIGNED_SHORT_5_6_5, 2}; return true;         // RGB565
    default: return false;
  }
}

GLuint make_texture(int w, int h, GLint filter) {
  GLuint t;
  glGenTextures(1, &t);
  glBindTexture(GL_TEXTURE_2D, t);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, filter);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, filter);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
  glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
  return t;
}

}  // namespace

Renderer::Renderer(const Graphics &g, size_t tiles) : g_(g), tiles_(tiles) {}

Renderer::~Renderer() {
  for (auto &t : tiles_) {
    t.chain.reset();
    if (t.tex) glDeleteTextures(1, &t.tex);
    if (t.out_tex) glDeleteTextures(1, &t.out_tex);
  }
  if (fbo_) glDeleteFramebuffers(1, &fbo_);
  if (fbo_tex_) glDeleteTextures(1, &fbo_tex_);
  if (vao_) glDeleteVertexArrays(1, &vao_);
  if (prog_) glDeleteProgram(prog_);
}

std::string Renderer::init() {
  std::string err;
  GLuint vs = compile(GL_VERTEX_SHADER, kVertex, err);
  GLuint fs = compile(GL_FRAGMENT_SHADER, kFragment, err);
  prog_ = glCreateProgram();
  glAttachShader(prog_, vs);
  glAttachShader(prog_, fs);
  glLinkProgram(prog_);
  glDeleteShader(vs);
  glDeleteShader(fs);
  GLint ok = 0;
  glGetProgramiv(prog_, GL_LINK_STATUS, &ok);
  if (!ok && err.empty()) err = "shader link failed";
  if (!err.empty()) return err;
  u_flip_ = glGetUniformLocation(prog_, "u_flip");
  glGenVertexArrays(1, &vao_);

  if (!g_.shader_preset.empty()) {
    if (!ShaderChain::available()) shader_warning_ = "Shader preset off: librashader is not installed";
    else shaders_on_ = true;
  }
  return "";
}

void Renderer::begin(int width, int height, bool offscreen) {
  width_ = width;
  height_ = height;
  offscreen_ = offscreen;
  if (offscreen) {
    if (!fbo_ || fbo_w_ != width || fbo_h_ != height) {
      if (fbo_tex_) glDeleteTextures(1, &fbo_tex_);
      if (!fbo_) glGenFramebuffers(1, &fbo_);
      fbo_tex_ = make_texture(width, height, GL_NEAREST);
      glBindFramebuffer(GL_FRAMEBUFFER, fbo_);
      glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, fbo_tex_, 0);
      fbo_w_ = width;
      fbo_h_ = height;
    }
    glBindFramebuffer(GL_FRAMEBUFFER, fbo_);
  } else {
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
  }
  glDisable(GL_SCISSOR_TEST);
  glViewport(0, 0, width, height);
  glClearColor(0, 0, 0, 1);
  glClear(GL_COLOR_BUFFER_BIT);
}

bool Renderer::update(size_t i, Player &p) {
  Tile &t = tiles_[i];
  if (!p.video) return false;
  shm::FrameView f = p.video->acquire();
  if (!f || (f.slot->frame_no == t.frame.frame_no && t.frame.has_frame)) {
    p.video->release();
    return false;
  }
  PixelFormat pf;
  int w = int(f.slot->width), h = int(f.slot->height);
  if (!pixel_format(int(f.slot->fmt), pf) || !w || !h) {
    p.video->release();
    return false;
  }
  int sy = 0, sh = h;
  if (g_.crop_overscan && h > 4 * kOverscanRows) {
    sy = kOverscanRows;
    sh = h - 2 * kOverscanRows;
  }
  if (!t.tex || t.tex_w != w || t.tex_h != sh) {
    if (t.tex) glDeleteTextures(1, &t.tex);
    // A shader chain samples the source itself; nearest keeps its input exact.
    t.tex = make_texture(w, sh, g_.filter == "bilinear" && !shaders_on_ ? GL_LINEAR : GL_NEAREST);
    t.tex_w = w;
    t.tex_h = sh;
  }
  glBindTexture(GL_TEXTURE_2D, t.tex);
  glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
  glPixelStorei(GL_UNPACK_ROW_LENGTH, int(f.slot->pitch) / pf.bpp);
  glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, w, sh, pf.format, pf.type,
                  f.pixels + size_t(sy) * f.slot->pitch);
  glPixelStorei(GL_UNPACK_ROW_LENGTH, 0);
  t.frame.frame_no = f.slot->frame_no;
  p.video->release();

  int64_t now = now_ns();
  if (t.frame.last_ns) {
    t.frame.intervals_ms.push_back(float((now - t.frame.last_ns) / 1e6));
    if (t.frame.intervals_ms.size() > kIntervalHistory) t.frame.intervals_ms.pop_front();
  }
  t.frame.last_ns = now;
  t.frame.has_frame = true;
  t.frame.width = w;
  t.frame.height = h;
  t.frame.fmt = int(f.slot->fmt);
  t.shader_frames++;
  return true;
}

void Renderer::blit(GLuint tex, int x, int y, int w, int h, bool flip) {
  glViewport(x, height_ - y - h, w, h);
  glUseProgram(prog_);
  glUniform1i(u_flip_, flip);
  glActiveTexture(GL_TEXTURE0);
  glBindTexture(GL_TEXTURE_2D, tex);
  glBindVertexArray(vao_);
  glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
}

void Renderer::draw(size_t i, const IRect &tile, double core_aspect, double fps) {
  Tile &t = tiles_[i];
  // Tile background.
  glEnable(GL_SCISSOR_TEST);
  glScissor(tile.x, height_ - tile.y - tile.h, tile.w, tile.h);
  glClearColor(10 / 255.f, 10 / 255.f, 12 / 255.f, 1);
  glClear(GL_COLOR_BUFFER_BIT);
  if (!t.frame.has_frame) {
    glDisable(GL_SCISSOR_TEST);
    return;
  }
  Placement pl = frame_geometry(tile.w, tile.h, t.frame.width, t.frame.height, core_aspect, g_);
  int x = tile.x + int(std::lround(pl.dst.x)), y = tile.y + int(std::lround(pl.dst.y));
  int w = std::max(1, int(std::lround(pl.dst.w))), h = std::max(1, int(std::lround(pl.dst.h)));

  if (shaders_on_ && !t.chain && shader_warning_.empty()) {
    std::string err;
    t.chain = ShaderChain::create(g_.shader_preset, err);
    if (!t.chain) {
      shader_warning_ = "Shader preset off: " + err;
      shaders_on_ = false;
    }
  }
  if (shaders_on_ && t.chain) {
    if (!t.out_tex || t.out_w != w || t.out_h != h) {
      if (t.out_tex) glDeleteTextures(1, &t.out_tex);
      t.out_tex = make_texture(w, h, GL_NEAREST);
      t.out_w = w;
      t.out_h = h;
    }
    std::string err;
    GLint fb = 0;
    glGetIntegerv(GL_FRAMEBUFFER_BINDING, &fb);
    bool ok = t.chain->frame(t.tex, t.tex_w, t.tex_h, t.out_tex, w, h, t.shader_frames,
                             float(fps), err);
    // librashader leaves its own GL state behind.
    glBindFramebuffer(GL_FRAMEBUFFER, GLuint(fb));
    glScissor(tile.x, height_ - tile.y - tile.h, tile.w, tile.h);
    glEnable(GL_SCISSOR_TEST);
    glDisable(GL_BLEND);
    if (ok) {
      blit(t.out_tex, x, y, w, h, false);
      glDisable(GL_SCISSOR_TEST);
      return;
    }
    shader_warning_ = "Shader preset off: " + err;
    shaders_on_ = false;
    // Fall through to plain scaling with the texture's (nearest) filter.
  }
  blit(t.tex, x, y, w, h, false);
  glDisable(GL_SCISSOR_TEST);
}

std::vector<uint8_t> Renderer::finish_offscreen() {
  std::vector<uint8_t> rgb(size_t(width_) * height_ * 3);
  if (!offscreen_) return rgb;
  glBindFramebuffer(GL_READ_FRAMEBUFFER, fbo_);
  glPixelStorei(GL_PACK_ALIGNMENT, 1);
  std::vector<uint8_t> rows(rgb.size());
  glReadPixels(0, 0, width_, height_, GL_RGB, GL_UNSIGNED_BYTE, rows.data());
  size_t stride = size_t(width_) * 3;
  for (int y = 0; y < height_; y++)  // GL rows start at the bottom
    std::copy_n(rows.data() + (height_ - 1 - y) * stride, stride, rgb.data() + y * stride);
  glBindFramebuffer(GL_DRAW_FRAMEBUFFER, 0);
  glBlitFramebuffer(0, 0, width_, height_, 0, 0, width_, height_, GL_COLOR_BUFFER_BIT, GL_NEAREST);
  glBindFramebuffer(GL_FRAMEBUFFER, 0);
  return rgb;
}

}  // namespace mt::session
