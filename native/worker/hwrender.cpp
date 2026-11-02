#include "hwrender.hpp"

#include <algorithm>
#include <cstring>
#include <stdexcept>

#ifdef MULTITAP_HAVE_EGL
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GL/glcorearb.h>
#endif

namespace mt {

namespace {
HwRender *g_hw = nullptr;  // libretro's HW callbacks carry no user pointer either
}

#ifdef MULTITAP_HAVE_EGL

namespace {

bool has_ext(const char *list, const char *name) {
  if (!list) return false;
  size_t n = std::strlen(name);
  for (const char *p = list; (p = std::strstr(p, name)); p += n)
    if ((p == list || p[-1] == ' ') && (p[n] == ' ' || p[n] == '\0')) return true;
  return false;
}

std::string egl_error(const char *what) {
  char buf[96];
  std::snprintf(buf, sizeof buf, "%s failed (EGL error 0x%04X)", what, unsigned(eglGetError()));
  return buf;
}

// Prefer a real GPU through EGL_EXT_platform_device, then Mesa's surfaceless platform, then
// whatever the default display is (needs a display server).
EGLDisplay open_display() {
  const char *client = eglQueryString(EGL_NO_DISPLAY, EGL_EXTENSIONS);
  auto get_platform_display = reinterpret_cast<PFNEGLGETPLATFORMDISPLAYEXTPROC>(
      eglGetProcAddress("eglGetPlatformDisplayEXT"));
  EGLint major, minor;
  if (get_platform_display && has_ext(client, "EGL_EXT_platform_device")) {
    auto query_devices =
        reinterpret_cast<PFNEGLQUERYDEVICESEXTPROC>(eglGetProcAddress("eglQueryDevicesEXT"));
    auto device_string = reinterpret_cast<PFNEGLQUERYDEVICESTRINGEXTPROC>(
        eglGetProcAddress("eglQueryDeviceStringEXT"));
    EGLDeviceEXT devices[16];
    EGLint count = 0;
    if (query_devices && device_string && query_devices(16, devices, &count)) {
      for (bool software : {false, true}) {
        for (EGLint i = 0; i < count; i++) {
          const char *exts = device_string(devices[i], EGL_EXTENSIONS);
          if (has_ext(exts, "EGL_MESA_device_software") != software) continue;
          EGLDisplay d = get_platform_display(EGL_PLATFORM_DEVICE_EXT, devices[i], nullptr);
          if (d != EGL_NO_DISPLAY && eglInitialize(d, &major, &minor)) return d;
        }
      }
    }
  }
  if (get_platform_display && has_ext(client, "EGL_MESA_platform_surfaceless")) {
    EGLDisplay d = get_platform_display(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, nullptr);
    if (d != EGL_NO_DISPLAY && eglInitialize(d, &major, &minor)) return d;
  }
  EGLDisplay d = eglGetDisplay(EGL_DEFAULT_DISPLAY);
  if (d != EGL_NO_DISPLAY && eglInitialize(d, &major, &minor)) return d;
  return EGL_NO_DISPLAY;
}

}  // namespace

// The few GL entry points the frontend itself needs, loaded through EGL so the worker works
// with desktop GL and GLES alike without linking either.
struct HwRender::Gl {
#define MT_GL_FNS(X)                                                  \
  X(PFNGLGETSTRINGPROC, GetString)                                    \
  X(PFNGLGETINTEGERVPROC, GetIntegerv)                                \
  X(PFNGLGENFRAMEBUFFERSPROC, GenFramebuffers)                        \
  X(PFNGLDELETEFRAMEBUFFERSPROC, DeleteFramebuffers)                  \
  X(PFNGLBINDFRAMEBUFFERPROC, BindFramebuffer)                        \
  X(PFNGLCHECKFRAMEBUFFERSTATUSPROC, CheckFramebufferStatus)          \
  X(PFNGLFRAMEBUFFERTEXTURE2DPROC, FramebufferTexture2D)              \
  X(PFNGLFRAMEBUFFERRENDERBUFFERPROC, FramebufferRenderbuffer)        \
  X(PFNGLGENTEXTURESPROC, GenTextures)                                \
  X(PFNGLDELETETEXTURESPROC, DeleteTextures)                          \
  X(PFNGLBINDTEXTUREPROC, BindTexture)                                \
  X(PFNGLTEXIMAGE2DPROC, TexImage2D)                                  \
  X(PFNGLTEXPARAMETERIPROC, TexParameteri)                            \
  X(PFNGLGENRENDERBUFFERSPROC, GenRenderbuffers)                      \
  X(PFNGLDELETERENDERBUFFERSPROC, DeleteRenderbuffers)                \
  X(PFNGLBINDRENDERBUFFERPROC, BindRenderbuffer)                      \
  X(PFNGLRENDERBUFFERSTORAGEPROC, RenderbufferStorage)                \
  X(PFNGLPIXELSTOREIPROC, PixelStorei)                                \
  X(PFNGLREADPIXELSPROC, ReadPixels)                                  \
  X(PFNGLBINDBUFFERPROC, BindBuffer)                                  \
  X(PFNGLCLEARCOLORPROC, ClearColor)                                  \
  X(PFNGLCLEARPROC, Clear)
#define MT_GL_DECLARE(type, name) type name = nullptr;
  MT_GL_FNS(MT_GL_DECLARE)
#undef MT_GL_DECLARE
  void load() {
#define MT_GL_LOAD(type, name)                                                     \
  name = reinterpret_cast<type>(eglGetProcAddress("gl" #name));                    \
  if (!name) throw std::runtime_error("OpenGL is missing gl" #name);
    MT_GL_FNS(MT_GL_LOAD)
#undef MT_GL_LOAD
  }
#undef MT_GL_FNS
};

HwRender::HwRender() { g_hw = this; }

HwRender::~HwRender() {
  notify_destroy();
  release();
  if (g_hw == this) g_hw = nullptr;
}

bool HwRender::supported() { return true; }

bool HwRender::accept(retro_hw_render_callback *cb) {
  switch (cb->context_type) {
    case RETRO_HW_CONTEXT_OPENGL:
    case RETRO_HW_CONTEXT_OPENGL_CORE:
      gles_ = false;
      break;
    case RETRO_HW_CONTEXT_OPENGLES2:
    case RETRO_HW_CONTEXT_OPENGLES3:
    case RETRO_HW_CONTEXT_OPENGLES_VERSION:
      gles_ = true;
      break;
    default:
      return false;  // Vulkan, Direct3D: the core may ask again with another API
  }
  cb->get_current_framebuffer = &HwRender::get_current_framebuffer;
  cb->get_proc_address = &HwRender::get_proc_address;
  cb_ = *cb;
  requested_ = true;
  return true;
}

void HwRender::start(unsigned max_w, unsigned max_h) {
  if (!requested_ || active_) return;
  EGLDisplay dpy = open_display();
  if (dpy == EGL_NO_DISPLAY) throw std::runtime_error("OpenGL is not available (no EGL display)");
  display_ = dpy;

  EGLint renderable = EGL_OPENGL_BIT;
  if (gles_) {
    unsigned major = cb_.context_type == RETRO_HW_CONTEXT_OPENGLES2 ? 2
                     : cb_.context_type == RETRO_HW_CONTEXT_OPENGLES3 ? 3
                                                                      : cb_.version_major;
    renderable = major >= 3 ? EGL_OPENGL_ES3_BIT : EGL_OPENGL_ES2_BIT;
  }
  if (!eglBindAPI(gles_ ? EGL_OPENGL_ES_API : EGL_OPENGL_API))
    throw std::runtime_error(egl_error("eglBindAPI"));
  EGLint cfg_attrs[] = {EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, renderable,
                        EGL_RED_SIZE,     8,               EGL_GREEN_SIZE,      8,
                        EGL_BLUE_SIZE,    8,               EGL_NONE};
  EGLConfig config = nullptr;
  EGLint n = 0;
  if (!eglChooseConfig(dpy, cfg_attrs, &config, 1, &n) || n < 1) {
    cfg_attrs[1] = 0;  // surfaceless displays may have no pbuffer configs
    if (!eglChooseConfig(dpy, cfg_attrs, &config, 1, &n) || n < 1)
      throw std::runtime_error("OpenGL is not available (no EGL config for this API)");
  }

  std::vector<EGLint> attrs;
  if (gles_) {
    unsigned major = cb_.context_type == RETRO_HW_CONTEXT_OPENGLES2 ? 2
                     : cb_.context_type == RETRO_HW_CONTEXT_OPENGLES3 ? 3
                                                                      : cb_.version_major;
    attrs = {EGL_CONTEXT_MAJOR_VERSION, EGLint(major), EGL_CONTEXT_MINOR_VERSION,
             EGLint(cb_.context_type == RETRO_HW_CONTEXT_OPENGLES_VERSION ? cb_.version_minor : 0)};
  } else if (cb_.context_type == RETRO_HW_CONTEXT_OPENGL_CORE) {
    attrs = {EGL_CONTEXT_MAJOR_VERSION, EGLint(std::max(3u, cb_.version_major)),
             EGL_CONTEXT_MINOR_VERSION, EGLint(cb_.version_major >= 3 ? cb_.version_minor : 2),
             EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT};
  } else if (cb_.version_major >= 3) {  // a versioned compatibility context
    attrs = {EGL_CONTEXT_MAJOR_VERSION, EGLint(cb_.version_major), EGL_CONTEXT_MINOR_VERSION,
             EGLint(cb_.version_minor), EGL_CONTEXT_OPENGL_PROFILE_MASK,
             EGL_CONTEXT_OPENGL_COMPATIBILITY_PROFILE_BIT};
  }
  if (cb_.debug_context) attrs.insert(attrs.end(), {EGL_CONTEXT_OPENGL_DEBUG, EGL_TRUE});
  attrs.push_back(EGL_NONE);
  EGLContext ctx = eglCreateContext(dpy, config, EGL_NO_CONTEXT, attrs.data());
  if (ctx == EGL_NO_CONTEXT) throw std::runtime_error(egl_error("Creating the OpenGL context"));
  context_ = ctx;

  EGLSurface surface = EGL_NO_SURFACE;
  if (!has_ext(eglQueryString(dpy, EGL_EXTENSIONS), "EGL_KHR_surfaceless_context")) {
    EGLint pb[] = {EGL_WIDTH, 1, EGL_HEIGHT, 1, EGL_NONE};
    surface = eglCreatePbufferSurface(dpy, config, pb);
    surface_ = surface;
  }
  if (!eglMakeCurrent(dpy, surface, surface, ctx))
    throw std::runtime_error(egl_error("eglMakeCurrent"));

  gl_ = std::make_unique<Gl>();
  gl_->load();
  auto str = [&](GLenum e) {
    auto *s = reinterpret_cast<const char *>(gl_->GetString(e));
    return std::string(s ? s : "?");
  };
  description_ = (gles_ ? "OpenGL ES " : "OpenGL ") + str(GL_VERSION) + " (" + str(GL_RENDERER) + ")";
  make_framebuffer(std::max(1u, max_w), std::max(1u, max_h));
  active_ = true;
  if (cb_.context_reset) cb_.context_reset();
}

void HwRender::make_framebuffer(unsigned w, unsigned h) {
  Gl &gl = *gl_;
  if (!fbo_) {
    gl.GenFramebuffers(1, &fbo_);
    gl.GenTextures(1, &color_);
    if (cb_.depth) gl.GenRenderbuffers(1, &depth_);
  }
  gl.BindTexture(GL_TEXTURE_2D, color_);
  gl.TexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
  gl.TexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
  gl.TexImage2D(GL_TEXTURE_2D, 0, gles_ ? GL_RGBA : GL_RGBA8, GLsizei(w), GLsizei(h), 0, GL_RGBA,
                GL_UNSIGNED_BYTE, nullptr);
  gl.BindTexture(GL_TEXTURE_2D, 0);
  gl.BindFramebuffer(GL_FRAMEBUFFER, fbo_);
  gl.FramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, color_, 0);
  if (depth_) {
    gl.BindRenderbuffer(GL_RENDERBUFFER, depth_);
    gl.RenderbufferStorage(GL_RENDERBUFFER, cb_.stencil ? GL_DEPTH24_STENCIL8 : GL_DEPTH_COMPONENT24,
                           GLsizei(w), GLsizei(h));
    gl.BindRenderbuffer(GL_RENDERBUFFER, 0);
    gl.FramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_RENDERBUFFER, depth_);
    if (cb_.stencil)
      gl.FramebufferRenderbuffer(GL_FRAMEBUFFER, GL_STENCIL_ATTACHMENT, GL_RENDERBUFFER, depth_);
  }
  GLenum status = gl.CheckFramebufferStatus(GL_FRAMEBUFFER);
  if (status != GL_FRAMEBUFFER_COMPLETE) {
    char buf[64];
    std::snprintf(buf, sizeof buf, "framebuffer incomplete (0x%04X)", unsigned(status));
    throw std::runtime_error(buf);
  }
  // Start black, as RetroArch does.
  gl.ClearColor(0, 0, 0, 1);
  gl.Clear(GL_COLOR_BUFFER_BIT | (depth_ ? GL_DEPTH_BUFFER_BIT : 0) |
           (cb_.stencil ? GL_STENCIL_BUFFER_BIT : 0));
  gl.BindFramebuffer(GL_FRAMEBUFFER, 0);
  fb_w_ = w;
  fb_h_ = h;
}

void HwRender::ensure_size(unsigned w, unsigned h) {
  if (!active_ || (w <= fb_w_ && h <= fb_h_)) return;
  make_framebuffer(std::max(w, fb_w_), std::max(h, fb_h_));
}

void HwRender::notify_destroy() {
  if (!active_ || destroyed_) return;
  destroyed_ = true;
  if (cb_.context_destroy) cb_.context_destroy();
}

void HwRender::release() {
  if (!display_) return;
  auto dpy = static_cast<EGLDisplay>(display_);
  if (active_ && gl_) {
    if (fbo_) gl_->DeleteFramebuffers(1, &fbo_);
    if (color_) gl_->DeleteTextures(1, &color_);
    if (depth_) gl_->DeleteRenderbuffers(1, &depth_);
    fbo_ = color_ = depth_ = 0;
  }
  active_ = false;
  eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
  if (surface_) eglDestroySurface(dpy, static_cast<EGLSurface>(surface_));
  if (context_) eglDestroyContext(dpy, static_cast<EGLContext>(context_));
  eglTerminate(dpy);
  surface_ = context_ = display_ = nullptr;
}

const uint8_t *HwRender::read_frame(unsigned w, unsigned h) {
  if (!active_) return nullptr;
  Gl &gl = *gl_;
  w = std::min(w, fb_w_);
  h = std::min(h, fb_h_);
  size_t row = size_t(w) * 4;
  pixels_.resize(row * h);

  // Leave the core's state as it was: it may keep a pack buffer or other framebuffer bound.
  GLint prev_fb = 0, prev_align = 4, prev_pack = 0;
  gl.GetIntegerv(GL_FRAMEBUFFER_BINDING, &prev_fb);
  gl.GetIntegerv(GL_PACK_ALIGNMENT, &prev_align);
  bool has_pbo = !gles_ || cb_.context_type != RETRO_HW_CONTEXT_OPENGLES2;
  if (has_pbo) {
    gl.GetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &prev_pack);
    if (prev_pack) gl.BindBuffer(GL_PIXEL_PACK_BUFFER, 0);
  }
  gl.BindFramebuffer(GL_FRAMEBUFFER, fbo_);
  gl.PixelStorei(GL_PACK_ALIGNMENT, 4);
  if (gles_) {
    gl.ReadPixels(0, 0, GLsizei(w), GLsizei(h), GL_RGBA, GL_UNSIGNED_BYTE, pixels_.data());
    for (size_t i = 0; i < pixels_.size(); i += 4) std::swap(pixels_[i], pixels_[i + 2]);  // BGRA
  } else {
    gl.ReadPixels(0, 0, GLsizei(w), GLsizei(h), GL_BGRA, GL_UNSIGNED_INT_8_8_8_8_REV,
                  pixels_.data());
  }
  gl.PixelStorei(GL_PACK_ALIGNMENT, prev_align);
  gl.BindFramebuffer(GL_FRAMEBUFFER, GLuint(prev_fb));
  if (prev_pack) gl.BindBuffer(GL_PIXEL_PACK_BUFFER, GLuint(prev_pack));

  // GL rows start at the bottom; most cores draw that way (bottom_left_origin).
  if (cb_.bottom_left_origin) {
    std::vector<uint8_t> tmp(row);
    for (unsigned y = 0; y < h / 2; y++) {
      uint8_t *a = pixels_.data() + y * row, *b = pixels_.data() + (h - 1 - y) * row;
      std::memcpy(tmp.data(), a, row);
      std::memcpy(a, b, row);
      std::memcpy(b, tmp.data(), row);
    }
  }
  return pixels_.data();
}

uintptr_t HwRender::get_current_framebuffer() { return g_hw ? g_hw->fbo_ : 0; }

retro_proc_address_t HwRender::get_proc_address(const char *sym) {
  return reinterpret_cast<retro_proc_address_t>(eglGetProcAddress(sym));
}

#else  // no EGL in this build: hardware-rendered cores are refused

struct HwRender::Gl {};
HwRender::HwRender() { g_hw = this; }
HwRender::~HwRender() {
  if (g_hw == this) g_hw = nullptr;
}
bool HwRender::supported() { return false; }
bool HwRender::accept(retro_hw_render_callback *) { return false; }
void HwRender::start(unsigned, unsigned) {}
void HwRender::ensure_size(unsigned, unsigned) {}
void HwRender::notify_destroy() {}
void HwRender::release() {}
const uint8_t *HwRender::read_frame(unsigned, unsigned) { return nullptr; }
uintptr_t HwRender::get_current_framebuffer() { return 0; }
retro_proc_address_t HwRender::get_proc_address(const char *) { return nullptr; }

#endif

}  // namespace mt
