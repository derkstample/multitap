/* Tiny hardware-rendered libretro test core for MultiTap.
 *
 * Requests an OpenGL 3.3 core context and renders 320x240 into the frontend's framebuffer:
 * the whole frame is cleared to blue-grey (#336699), or red (#FF0000) while any RetroPad button
 * is held, and the bottom-left quadrant (GL coordinates, bottom_left_origin) is green
 * (#00FF00), so a frontend that forgets to flip shows the green at the top.
 * Content: any .tst file. Without an OpenGL context retro_load_game fails.
 */
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>

#define RETRO_API_VERSION 1
#define RETRO_ENVIRONMENT_SET_PIXEL_FORMAT 10
#define RETRO_ENVIRONMENT_SET_HW_RENDER 14
#define RETRO_ENVIRONMENT_GET_INPUT_BITMASKS (51 | 0x10000)
#define RETRO_HW_CONTEXT_OPENGL_CORE 3
#define RETRO_HW_FRAME_BUFFER_VALID ((void *)-1)
#define RETRO_DEVICE_JOYPAD 1
#define RETRO_DEVICE_ID_JOYPAD_MASK 256
#define W 320
#define H 240

#define GL_COLOR_BUFFER_BIT 0x4000
#define GL_SCISSOR_TEST 0x0C11
#define GL_FRAMEBUFFER 0x8D40

struct retro_system_info { const char *library_name, *library_version, *valid_extensions; bool need_fullpath, block_extract; };
struct retro_game_geometry { unsigned base_width, base_height, max_width, max_height; float aspect_ratio; };
struct retro_system_timing { double fps, sample_rate; };
struct retro_system_av_info { struct retro_game_geometry geometry; struct retro_system_timing timing; };
struct retro_game_info { const char *path; const void *data; size_t size; const char *meta; };

typedef void (*proc_t)(void);
struct retro_hw_render_callback {
    unsigned context_type;
    void (*context_reset)(void);
    uintptr_t (*get_current_framebuffer)(void);
    proc_t (*get_proc_address)(const char *sym);
    bool depth, stencil, bottom_left_origin;
    unsigned version_major, version_minor;
    bool cache_context;
    void (*context_destroy)(void);
    bool debug_context;
};

typedef bool (*env_t)(unsigned, void *);
typedef void (*video_t)(const void *, unsigned, unsigned, size_t);
typedef void (*sample_t)(int16_t, int16_t);
typedef size_t (*batch_t)(const int16_t *, size_t);
typedef void (*poll_t)(void);
typedef int16_t (*state_t)(unsigned, unsigned, unsigned, unsigned);

static env_t env_cb; static video_t video_cb; static sample_t sample_cb;
static batch_t batch_cb; static poll_t poll_cb; static state_t state_cb;
static bool bitmasks;
static struct retro_hw_render_callback hw;

static void (*glBindFramebuffer)(unsigned, unsigned);
static void (*glViewport)(int, int, int, int);
static void (*glScissor)(int, int, int, int);
static void (*glEnable)(unsigned);
static void (*glDisable)(unsigned);
static void (*glClearColor)(float, float, float, float);
static void (*glClear)(unsigned);

static void context_reset(void) {
    glBindFramebuffer = (void (*)(unsigned, unsigned))hw.get_proc_address("glBindFramebuffer");
    glViewport = (void (*)(int, int, int, int))hw.get_proc_address("glViewport");
    glScissor = (void (*)(int, int, int, int))hw.get_proc_address("glScissor");
    glEnable = (void (*)(unsigned))hw.get_proc_address("glEnable");
    glDisable = (void (*)(unsigned))hw.get_proc_address("glDisable");
    glClearColor = (void (*)(float, float, float, float))hw.get_proc_address("glClearColor");
    glClear = (void (*)(unsigned))hw.get_proc_address("glClear");
}

static void context_destroy(void) { glClear = NULL; }

void retro_set_environment(env_t cb) { env_cb = cb; }
void retro_set_video_refresh(video_t cb) { video_cb = cb; }
void retro_set_audio_sample(sample_t cb) { sample_cb = cb; }
void retro_set_audio_sample_batch(batch_t cb) { batch_cb = cb; }
void retro_set_input_poll(poll_t cb) { poll_cb = cb; }
void retro_set_input_state(state_t cb) { state_cb = cb; }
unsigned retro_api_version(void) { return RETRO_API_VERSION; }
void retro_init(void) {}
void retro_deinit(void) {}
void retro_set_controller_port_device(unsigned port, unsigned device) { (void)port; (void)device; }

void retro_get_system_info(struct retro_system_info *info) {
    info->library_name = "MultiTap GL Test Core";
    info->library_version = "1.0";
    info->valid_extensions = "tst";
    info->need_fullpath = false;
    info->block_extract = false;
}

void retro_get_system_av_info(struct retro_system_av_info *info) {
    info->geometry.base_width = W; info->geometry.base_height = H;
    info->geometry.max_width = W; info->geometry.max_height = H;
    info->geometry.aspect_ratio = 4.0f / 3.0f;
    info->timing.fps = 60.0; info->timing.sample_rate = 44100.0;
}

bool retro_load_game(const struct retro_game_info *game) {
    unsigned fmt = 1; /* XRGB8888 */
    if (!game || !env_cb(RETRO_ENVIRONMENT_SET_PIXEL_FORMAT, &fmt)) return false;
    bitmasks = env_cb(RETRO_ENVIRONMENT_GET_INPUT_BITMASKS, NULL);
    memset(&hw, 0, sizeof hw);
    hw.context_type = RETRO_HW_CONTEXT_OPENGL_CORE;
    hw.version_major = 3;
    hw.version_minor = 3;
    hw.context_reset = context_reset;
    hw.context_destroy = context_destroy;
    hw.depth = true;
    hw.bottom_left_origin = true;
    return env_cb(RETRO_ENVIRONMENT_SET_HW_RENDER, &hw);
}

void retro_unload_game(void) {}
void retro_reset(void) {}

void retro_run(void) {
    poll_cb();
    uint16_t mask = 0;
    if (bitmasks) mask = (uint16_t)state_cb(0, RETRO_DEVICE_JOYPAD, 0, RETRO_DEVICE_ID_JOYPAD_MASK);
    else for (unsigned i = 0; i < 16; i++) if (state_cb(0, RETRO_DEVICE_JOYPAD, 0, i)) mask |= 1u << i;

    if (glClear) {
        glBindFramebuffer(GL_FRAMEBUFFER, (unsigned)hw.get_current_framebuffer());
        glViewport(0, 0, W, H);
        glDisable(GL_SCISSOR_TEST);
        if (mask) glClearColor(1.0f, 0.0f, 0.0f, 1.0f);
        else glClearColor(0.2f, 0.4f, 0.6f, 1.0f);
        glClear(GL_COLOR_BUFFER_BIT);
        glEnable(GL_SCISSOR_TEST);
        glScissor(0, 0, W / 2, H / 2);
        glClearColor(0.0f, 1.0f, 0.0f, 1.0f);
        glClear(GL_COLOR_BUFFER_BIT);
        glDisable(GL_SCISSOR_TEST);
    }
    video_cb(RETRO_HW_FRAME_BUFFER_VALID, W, H, 0);

    int16_t buf[735 * 2] = {0};
    batch_cb(buf, 735);
}

size_t retro_serialize_size(void) { return 0; }
bool retro_serialize(void *data, size_t size) { (void)data; (void)size; return false; }
bool retro_unserialize(const void *data, size_t size) { (void)data; (void)size; return false; }
void retro_cheat_reset(void) {}
void retro_cheat_set(unsigned i, bool e, const char *c) { (void)i; (void)e; (void)c; }
bool retro_load_game_special(unsigned t, const struct retro_game_info *i, size_t n) { (void)t; (void)i; (void)n; return false; }
unsigned retro_get_region(void) { return 0; }
void *retro_get_memory_data(unsigned id) { (void)id; return NULL; }
size_t retro_get_memory_size(unsigned id) { (void)id; return 0; }
