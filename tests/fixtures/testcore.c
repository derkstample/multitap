/* Tiny libretro test core for MultiTap.
 *
 * Content: any file with extension .tst (its first byte tints the background).
 * Video:   320x240 XRGB8888. Background hue from content; a white bar whose width
 *          is the frame counter mod 320; 16 squares light up for pressed RetroPad buttons.
 * Audio:   440 Hz square wave at 44100 Hz while any button is held.
 * SRAM:    64 bytes: [0]=0xAB marker, [1..2]=last button mask, [4..7]=frames since reset,
 *          [8..11]=reset count.
 * A file whose content starts with "BAD" is rejected by retro_load_game.
 */
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>

#define RETRO_API_VERSION 1
#define RETRO_ENVIRONMENT_SET_PIXEL_FORMAT 10
#define RETRO_ENVIRONMENT_GET_INPUT_BITMASKS (51 | 0x10000)
#define RETRO_DEVICE_JOYPAD 1
#define RETRO_DEVICE_ID_JOYPAD_MASK 256
#define W 320
#define H 240

struct retro_system_info { const char *library_name, *library_version, *valid_extensions; bool need_fullpath, block_extract; };
struct retro_game_geometry { unsigned base_width, base_height, max_width, max_height; float aspect_ratio; };
struct retro_system_timing { double fps, sample_rate; };
struct retro_system_av_info { struct retro_game_geometry geometry; struct retro_system_timing timing; };
struct retro_game_info { const char *path; const void *data; size_t size; const char *meta; };

typedef bool (*env_t)(unsigned, void *);
typedef void (*video_t)(const void *, unsigned, unsigned, size_t);
typedef void (*sample_t)(int16_t, int16_t);
typedef size_t (*batch_t)(const int16_t *, size_t);
typedef void (*poll_t)(void);
typedef int16_t (*state_t)(unsigned, unsigned, unsigned, unsigned);

static env_t env_cb; static video_t video_cb; static sample_t sample_cb;
static batch_t batch_cb; static poll_t poll_cb; static state_t state_cb;
static uint32_t fb[W * H];
static uint8_t sram[64];
static uint32_t tint, frames, resets, phase;
static bool bitmasks;

void retro_set_environment(env_t cb) { env_cb = cb; }
void retro_set_video_refresh(video_t cb) { video_cb = cb; }
void retro_set_audio_sample(sample_t cb) { sample_cb = cb; }
void retro_set_audio_sample_batch(batch_t cb) { batch_cb = cb; }
void retro_set_input_poll(poll_t cb) { poll_cb = cb; }
void retro_set_input_state(state_t cb) { state_cb = cb; }
unsigned retro_api_version(void) { return RETRO_API_VERSION; }
void retro_init(void) { memset(sram, 0, sizeof sram); sram[0] = 0xAB; }
void retro_deinit(void) {}
void retro_set_controller_port_device(unsigned port, unsigned device) { (void)port; (void)device; }

void retro_get_system_info(struct retro_system_info *info) {
    info->library_name = "MultiTap Test Core";
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
    if (!env_cb(RETRO_ENVIRONMENT_SET_PIXEL_FORMAT, &fmt)) return false;
    bitmasks = env_cb(RETRO_ENVIRONMENT_GET_INPUT_BITMASKS, NULL);
    if (!game || !game->data || game->size < 1) return false;
    if (game->size >= 3 && memcmp(game->data, "BAD", 3) == 0) return false;
    uint8_t b = ((const uint8_t *)game->data)[0];
    tint = ((uint32_t)(b * 37 & 0x7F) << 16) | ((uint32_t)(b * 91 & 0x7F) << 8) | (b * 53 & 0x7F);
    return true;
}

void retro_unload_game(void) {}
void retro_reset(void) { frames = 0; resets++; }

void retro_run(void) {
    poll_cb();
    uint16_t mask = 0;
    if (bitmasks) mask = (uint16_t)state_cb(0, RETRO_DEVICE_JOYPAD, 0, RETRO_DEVICE_ID_JOYPAD_MASK);
    else for (unsigned i = 0; i < 16; i++) if (state_cb(0, RETRO_DEVICE_JOYPAD, 0, i)) mask |= 1u << i;

    for (int i = 0; i < W * H; i++) fb[i] = tint;
    unsigned bar = frames % W;
    for (int y = 0; y < 8; y++) for (unsigned x = 0; x < bar; x++) fb[y * W + x] = 0xFFFFFF;
    for (int b = 0; b < 16; b++) {
        uint32_t c = (mask >> b) & 1 ? 0x00FF00 : 0x202020;
        int x0 = 16 + (b % 8) * 36, y0 = 100 + (b / 8) * 40;
        for (int y = 0; y < 28; y++) for (int x = 0; x < 28; x++) fb[(y0 + y) * W + x0 + x] = c;
    }
    video_cb(fb, W, H, W * 4);

    int16_t buf[735 * 2];
    for (int i = 0; i < 735; i++) {
        int16_t v = mask ? ((phase++ / 50) % 2 ? 3000 : -3000) : 0;
        buf[2 * i] = buf[2 * i + 1] = v;
    }
    batch_cb(buf, 735);

    frames++;
    sram[1] = mask & 0xFF; sram[2] = mask >> 8;
    memcpy(sram + 4, &frames, 4);
    memcpy(sram + 8, &resets, 4);
}

size_t retro_serialize_size(void) { return 0; }
bool retro_serialize(void *d, size_t s) { (void)d; (void)s; return false; }
bool retro_unserialize(const void *d, size_t s) { (void)d; (void)s; return false; }
void retro_cheat_reset(void) {}
void retro_cheat_set(unsigned i, bool e, const char *c) { (void)i; (void)e; (void)c; }
bool retro_load_game_special(unsigned t, const struct retro_game_info *i, size_t n) { (void)t; (void)i; (void)n; return false; }
unsigned retro_get_region(void) { return 0; }
void *retro_get_memory_data(unsigned id) { return id == 0 ? sram : NULL; }
size_t retro_get_memory_size(unsigned id) { return id == 0 ? sizeof sram : 0; }
