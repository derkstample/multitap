"""ctypes bindings for the libretro API (subset needed by a software-rendering frontend)."""

from ctypes import (CFUNCTYPE, POINTER, Structure, c_bool, c_char_p, c_double, c_float,
                    c_int, c_int16, c_size_t, c_uint, c_void_p, c_int64)

RETRO_API_VERSION = 1

# Devices / joypad ids
RETRO_DEVICE_NONE = 0
RETRO_DEVICE_JOYPAD = 1
RETRO_DEVICE_ANALOG = 5
RETRO_DEVICE_ID_JOYPAD_MASK = 256
RETRO_DEVICE_INDEX_ANALOG_LEFT = 0
RETRO_DEVICE_INDEX_ANALOG_RIGHT = 1
RETRO_DEVICE_ID_ANALOG_X = 0
RETRO_DEVICE_ID_ANALOG_Y = 1

# Memory
RETRO_MEMORY_SAVE_RAM = 0

# Pixel formats
PIXEL_0RGB1555 = 0
PIXEL_XRGB8888 = 1
PIXEL_RGB565 = 2
BYTES_PER_PIXEL = {PIXEL_0RGB1555: 2, PIXEL_XRGB8888: 4, PIXEL_RGB565: 2}

# Environment commands (low 16 bits; EXPERIMENTAL/PRIVATE flags are masked off)
ENV_SET_ROTATION = 1
ENV_GET_OVERSCAN = 2
ENV_GET_CAN_DUPE = 3
ENV_SET_MESSAGE = 6
ENV_SHUTDOWN = 7
ENV_SET_PERFORMANCE_LEVEL = 8
ENV_GET_SYSTEM_DIRECTORY = 9
ENV_SET_PIXEL_FORMAT = 10
ENV_SET_INPUT_DESCRIPTORS = 11
ENV_SET_HW_RENDER = 14
ENV_GET_VARIABLE = 15
ENV_SET_VARIABLES = 16
ENV_GET_VARIABLE_UPDATE = 17
ENV_SET_SUPPORT_NO_GAME = 18
ENV_GET_LIBRETRO_PATH = 19
ENV_SET_FRAME_TIME_CALLBACK = 21
ENV_GET_LOG_INTERFACE = 27
ENV_GET_CORE_ASSETS_DIRECTORY = 30
ENV_GET_SAVE_DIRECTORY = 31
ENV_SET_SYSTEM_AV_INFO = 32
ENV_SET_SUBSYSTEM_INFO = 34
ENV_SET_CONTROLLER_INFO = 35
ENV_SET_MEMORY_MAPS = 36
ENV_SET_GEOMETRY = 37
ENV_GET_USERNAME = 38
ENV_GET_LANGUAGE = 39
ENV_SET_SUPPORT_ACHIEVEMENTS = 42
ENV_SET_SERIALIZATION_QUIRKS = 44
ENV_GET_AUDIO_VIDEO_ENABLE = 47
ENV_GET_FASTFORWARDING = 49
ENV_GET_TARGET_REFRESH_RATE = 50
ENV_GET_INPUT_BITMASKS = 51
ENV_GET_CORE_OPTIONS_VERSION = 52
ENV_GET_PREFERRED_HW_RENDER = 56
ENV_GET_MESSAGE_INTERFACE_VERSION = 59
ENV_SET_MESSAGE_EXT = 60
ENV_GET_INPUT_MAX_USERS = 61
ENV_SET_CONTENT_INFO_OVERRIDE = 65
ENV_SET_VARIABLE = 70

ENV_CMD_MASK = 0xFFFF


class retro_system_info(Structure):
    _fields_ = [
        ("library_name", c_char_p),
        ("library_version", c_char_p),
        ("valid_extensions", c_char_p),
        ("need_fullpath", c_bool),
        ("block_extract", c_bool),
    ]


class retro_game_geometry(Structure):
    _fields_ = [
        ("base_width", c_uint),
        ("base_height", c_uint),
        ("max_width", c_uint),
        ("max_height", c_uint),
        ("aspect_ratio", c_float),
    ]


class retro_system_timing(Structure):
    _fields_ = [("fps", c_double), ("sample_rate", c_double)]


class retro_system_av_info(Structure):
    _fields_ = [("geometry", retro_game_geometry), ("timing", retro_system_timing)]


class retro_game_info(Structure):
    _fields_ = [
        ("path", c_char_p),
        ("data", c_void_p),
        ("size", c_size_t),
        ("meta", c_char_p),
    ]


class retro_variable(Structure):
    _fields_ = [("key", c_char_p), ("value", c_char_p)]


# Callback signatures
environment_t = CFUNCTYPE(c_bool, c_uint, c_void_p)
video_refresh_t = CFUNCTYPE(None, c_void_p, c_uint, c_uint, c_size_t)
audio_sample_t = CFUNCTYPE(None, c_int16, c_int16)
audio_sample_batch_t = CFUNCTYPE(c_size_t, POINTER(c_int16), c_size_t)
input_poll_t = CFUNCTYPE(None)
input_state_t = CFUNCTYPE(c_int16, c_uint, c_uint, c_uint, c_uint)
# The real log callback is variadic; on x86-64 SysV the fixed args still arrive correctly.
log_printf_t = CFUNCTYPE(None, c_int, c_char_p)
frame_time_t = CFUNCTYPE(None, c_int64)


class retro_log_callback(Structure):
    _fields_ = [("log", log_printf_t)]


class retro_frame_time_callback(Structure):
    _fields_ = [("callback", frame_time_t), ("reference", c_int64)]


def declare(lib) -> None:
    """Attach argument/return types to the exported retro_* functions of a loaded core."""
    sig = {
        "retro_set_environment": (None, [environment_t]),
        "retro_set_video_refresh": (None, [video_refresh_t]),
        "retro_set_audio_sample": (None, [audio_sample_t]),
        "retro_set_audio_sample_batch": (None, [audio_sample_batch_t]),
        "retro_set_input_poll": (None, [input_poll_t]),
        "retro_set_input_state": (None, [input_state_t]),
        "retro_init": (None, []),
        "retro_deinit": (None, []),
        "retro_api_version": (c_uint, []),
        "retro_get_system_info": (None, [POINTER(retro_system_info)]),
        "retro_get_system_av_info": (None, [POINTER(retro_system_av_info)]),
        "retro_set_controller_port_device": (None, [c_uint, c_uint]),
        "retro_reset": (None, []),
        "retro_run": (None, []),
        "retro_load_game": (c_bool, [POINTER(retro_game_info)]),
        "retro_unload_game": (None, []),
        "retro_get_memory_data": (c_void_p, [c_uint]),
        "retro_get_memory_size": (c_size_t, [c_uint]),
    }
    for name, (restype, argtypes) in sig.items():
        fn = getattr(lib, name)
        fn.restype = restype
        fn.argtypes = argtypes
