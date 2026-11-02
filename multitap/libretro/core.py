"""A minimal software-rendering libretro frontend core wrapper (one core per process)."""

from __future__ import annotations

import ctypes
from array import array
from ctypes import POINTER, byref, c_bool, c_char_p, c_float, c_int, c_uint, cast
from pathlib import Path
from typing import Callable

from . import api


class CoreError(RuntimeError):
    pass


def _open_library(core_path: str | Path) -> ctypes.CDLL:
    try:
        lib = ctypes.CDLL(str(core_path), mode=ctypes.RTLD_LOCAL)
    except OSError as exc:
        raise CoreError(f"Cannot load core: {exc}") from exc
    try:
        api.declare(lib)
    except AttributeError as exc:
        raise CoreError(f"Not a libretro core (missing symbol): {exc}") from exc
    if lib.retro_api_version() != api.RETRO_API_VERSION:
        raise CoreError("Unsupported libretro API version")
    return lib


def read_system_info(core_path: str | Path) -> dict:
    """Query name/version/extensions without initialising the core."""
    lib = _open_library(core_path)
    info = api.retro_system_info()
    lib.retro_get_system_info(byref(info))
    return _system_info_dict(info)


def _system_info_dict(info: api.retro_system_info) -> dict:
    dec = lambda b: (b or b"").decode("utf-8", "replace")
    exts = [e.strip().lower() for e in dec(info.valid_extensions).split("|") if e.strip()]
    return {
        "library_name": dec(info.library_name),
        "library_version": dec(info.library_version),
        "valid_extensions": exts,
        "need_fullpath": bool(info.need_fullpath),
        "block_extract": bool(info.block_extract),
    }


class LibretroCore:
    """Loads a core and exposes run/reset/SRAM. Video/audio/input are routed through hooks.

    on_video(ptr_or_None, width, height, pitch, pixel_format)
    input_bits / analog are set by the owner before each retro_run (read in input_state).
    Audio produced during a frame accumulates in `audio` (interleaved int16 stereo).
    """

    def __init__(self, core_path: str | Path, system_dir: str | Path, save_dir: str | Path,
                 username: str = "", log: Callable[[str], None] = print,
                 options: dict[str, str] | None = None):
        self.path = str(core_path)
        self.lib = _open_library(core_path)
        self.log = log
        self.pixel_format = api.PIXEL_0RGB1555
        self.hw_render_requested = False
        self.shutdown_requested = False
        self.av_info = api.retro_system_av_info()
        self.options: dict[str, str] = {}
        self._option_overrides = dict(options or {})
        self._option_bytes: dict[str, bytes] = {}
        self._options_dirty = False
        self.frame_time_cb = None
        self.on_video: Callable | None = None
        self.on_av_change: Callable | None = None
        self.input_bits = 0
        self.analog = [0, 0, 0, 0]  # left x, left y, right x, right y
        self.audio = array("h")
        self._game_buffer = None
        self._loaded = False

        # Keep byte strings handed to the core alive for the core's lifetime.
        self._sysdir_b = str(system_dir).encode()
        self._savedir_b = str(save_dir).encode()
        self._username_b = (username or "Player").encode()
        self._path_b = self.path.encode()

        self._cb_env = api.environment_t(self._environment)
        self._cb_video = api.video_refresh_t(self._video_refresh)
        self._cb_sample = api.audio_sample_t(self._audio_sample)
        self._cb_batch = api.audio_sample_batch_t(self._audio_batch)
        self._cb_poll = api.input_poll_t(lambda: None)
        self._cb_state = api.input_state_t(self._input_state)
        self._cb_log = api.log_printf_t(self._log_printf)
        self._log_struct = api.retro_log_callback(self._cb_log)

        lib = self.lib
        lib.retro_set_environment(self._cb_env)
        lib.retro_set_video_refresh(self._cb_video)
        lib.retro_set_audio_sample(self._cb_sample)
        lib.retro_set_audio_sample_batch(self._cb_batch)
        lib.retro_set_input_poll(self._cb_poll)
        lib.retro_set_input_state(self._cb_state)
        lib.retro_init()

        info = api.retro_system_info()
        lib.retro_get_system_info(byref(info))
        self.system_info = _system_info_dict(info)

    # ---- lifecycle -------------------------------------------------------
    def load_game(self, rom_path: str | Path) -> None:
        rom_path = str(rom_path)
        game = api.retro_game_info()
        game.path = rom_path.encode()
        game.meta = None
        if self.system_info["need_fullpath"]:
            game.data = None
            game.size = 0
        else:
            data = Path(rom_path).read_bytes()
            self._game_buffer = ctypes.create_string_buffer(data, len(data))
            game.data = ctypes.addressof(self._game_buffer)
            game.size = len(data)
        self._game_info = game
        ok = self.lib.retro_load_game(byref(game))
        if not ok:
            if self.hw_render_requested:
                raise CoreError("This core needs hardware (OpenGL/Vulkan) rendering, "
                                "which MultiTap v1 does not support")
            raise CoreError("The core rejected this file")
        self._loaded = True
        self.lib.retro_get_system_av_info(byref(self.av_info))
        self.lib.retro_set_controller_port_device(0, api.RETRO_DEVICE_JOYPAD)

    def run(self) -> None:
        self.lib.retro_run()

    def reset(self) -> None:
        self.lib.retro_reset()

    def close(self) -> None:
        if self._loaded:
            self.lib.retro_unload_game()
            self._loaded = False
        self.lib.retro_deinit()

    # ---- properties ------------------------------------------------------
    @property
    def fps(self) -> float:
        fps = self.av_info.timing.fps
        return fps if fps > 1 else 60.0

    @property
    def sample_rate(self) -> float:
        return self.av_info.timing.sample_rate

    @property
    def aspect(self) -> float:
        g = self.av_info.geometry
        if g.aspect_ratio > 0:
            return float(g.aspect_ratio)
        return g.base_width / g.base_height if g.base_height else 4 / 3

    # ---- SRAM ------------------------------------------------------------
    def read_sram(self) -> bytes | None:
        size = self.lib.retro_get_memory_size(api.RETRO_MEMORY_SAVE_RAM)
        ptr = self.lib.retro_get_memory_data(api.RETRO_MEMORY_SAVE_RAM)
        if not size or not ptr:
            return None
        return ctypes.string_at(ptr, size)

    def write_sram(self, data: bytes) -> bool:
        size = self.lib.retro_get_memory_size(api.RETRO_MEMORY_SAVE_RAM)
        ptr = self.lib.retro_get_memory_data(api.RETRO_MEMORY_SAVE_RAM)
        if not size or not ptr or not data:
            return False
        ctypes.memmove(ptr, data, min(size, len(data)))
        return True

    # ---- callbacks -------------------------------------------------------
    def _video_refresh(self, data, width, height, pitch):
        if self.on_video:
            self.on_video(data, width, height, pitch, self.pixel_format)

    def _audio_sample(self, left, right):
        self.audio.append(left)
        self.audio.append(right)

    def _audio_batch(self, data, frames):
        if frames:
            self.audio.frombytes(ctypes.string_at(data, frames * 4))
        return frames

    def _input_state(self, port, device, index, ident):
        if port != 0:
            return 0
        device &= 0xFF
        if device == api.RETRO_DEVICE_JOYPAD:
            if ident == api.RETRO_DEVICE_ID_JOYPAD_MASK:
                v = self.input_bits & 0xFFFF
                return v - 0x10000 if v >= 0x8000 else v
            return (self.input_bits >> ident) & 1
        if device == api.RETRO_DEVICE_ANALOG and index in (0, 1) and ident in (0, 1):
            return self.analog[index * 2 + ident]
        return 0

    def _log_printf(self, level, fmt):
        if fmt and level >= 2:  # warn / error only
            self.log(fmt.decode("utf-8", "replace").rstrip())

    def _environment(self, cmd, data):
        try:
            return self._handle_env(cmd & api.ENV_CMD_MASK, data)
        except Exception as exc:  # never let a Python error unwind into C
            self.log(f"env {cmd}: {exc!r}")
            return False

    def _handle_env(self, cmd, data):
        if cmd == api.ENV_GET_CAN_DUPE:
            cast(data, POINTER(c_bool))[0] = True
        elif cmd == api.ENV_GET_OVERSCAN:
            cast(data, POINTER(c_bool))[0] = False
        elif cmd in (api.ENV_GET_SYSTEM_DIRECTORY, api.ENV_GET_CORE_ASSETS_DIRECTORY):
            cast(data, POINTER(c_char_p))[0] = self._sysdir_b
        elif cmd == api.ENV_GET_SAVE_DIRECTORY:
            cast(data, POINTER(c_char_p))[0] = self._savedir_b
        elif cmd == api.ENV_GET_LIBRETRO_PATH:
            cast(data, POINTER(c_char_p))[0] = self._path_b
        elif cmd == api.ENV_GET_USERNAME:
            cast(data, POINTER(c_char_p))[0] = self._username_b
        elif cmd == api.ENV_SET_PIXEL_FORMAT:
            fmt = cast(data, POINTER(c_uint))[0]
            if fmt not in api.BYTES_PER_PIXEL:
                return False
            self.pixel_format = fmt
        elif cmd == api.ENV_GET_LOG_INTERFACE:
            cast(data, POINTER(api.retro_log_callback))[0] = self._log_struct
        elif cmd == api.ENV_SET_VARIABLES:
            self._set_variables(data)
        elif cmd == api.ENV_GET_VARIABLE:
            var = cast(data, POINTER(api.retro_variable)).contents
            key = (var.key or b"").decode()
            if key not in self._option_bytes:
                var.value = None
                return False
            var.value = self._option_bytes[key]
        elif cmd == api.ENV_SET_VARIABLE:
            if not data:
                return True
            var = cast(data, POINTER(api.retro_variable)).contents
            if var.key and var.value:
                self._set_option(var.key.decode(), var.value.decode())
        elif cmd == api.ENV_GET_VARIABLE_UPDATE:
            cast(data, POINTER(c_bool))[0] = self._options_dirty
            self._options_dirty = False
        elif cmd == api.ENV_SET_HW_RENDER:
            self.hw_render_requested = True
            return False
        elif cmd == api.ENV_SET_SYSTEM_AV_INFO:
            self.av_info = api.retro_system_av_info.from_buffer_copy(
                cast(data, POINTER(api.retro_system_av_info))[0])
            if self.on_av_change:
                self.on_av_change()
        elif cmd == api.ENV_SET_GEOMETRY:
            self.av_info.geometry = api.retro_game_geometry.from_buffer_copy(
                cast(data, POINTER(api.retro_game_geometry))[0])
            if self.on_av_change:
                self.on_av_change()
        elif cmd == api.ENV_GET_LANGUAGE:
            cast(data, POINTER(c_uint))[0] = 0
        elif cmd == api.ENV_GET_AUDIO_VIDEO_ENABLE:
            cast(data, POINTER(c_int))[0] = 3
        elif cmd == api.ENV_GET_FASTFORWARDING:
            cast(data, POINTER(c_bool))[0] = False
        elif cmd == api.ENV_GET_TARGET_REFRESH_RATE:
            cast(data, POINTER(c_float))[0] = 60.0
        elif cmd == api.ENV_GET_MESSAGE_INTERFACE_VERSION:
            cast(data, POINTER(c_uint))[0] = 0
        elif cmd == api.ENV_SET_FRAME_TIME_CALLBACK:
            cb = api.retro_frame_time_callback.from_buffer_copy(
                cast(data, POINTER(api.retro_frame_time_callback))[0])
            self.frame_time_cb = (cb.callback, cb.reference)
        elif cmd == api.ENV_SHUTDOWN:
            self.shutdown_requested = True
        elif cmd in (api.ENV_GET_INPUT_BITMASKS, api.ENV_SET_MESSAGE, api.ENV_SET_MESSAGE_EXT,
                     api.ENV_SET_PERFORMANCE_LEVEL, api.ENV_SET_INPUT_DESCRIPTORS,
                     api.ENV_SET_SUPPORT_NO_GAME, api.ENV_SET_CONTROLLER_INFO,
                     api.ENV_SET_SUBSYSTEM_INFO, api.ENV_SET_MEMORY_MAPS,
                     api.ENV_SET_SERIALIZATION_QUIRKS, api.ENV_SET_SUPPORT_ACHIEVEMENTS,
                     api.ENV_SET_CONTENT_INFO_OVERRIDE):
            pass
        else:
            # Includes GET_CORE_OPTIONS_VERSION: reporting no support makes cores
            # fall back to the legacy SET_VARIABLES interface.
            return False
        return True

    # ---- core options (legacy interface) ---------------------------------
    def _set_variables(self, data):
        arr = cast(data, POINTER(api.retro_variable))
        i = 0
        while arr[i].key:
            key = arr[i].key.decode()
            desc = (arr[i].value or b"").decode("utf-8", "replace")
            values = desc.split(";", 1)[1].strip().split("|") if ";" in desc else []
            default = values[0] if values else ""
            override = self._option_overrides.get(key)
            self._set_option(key, override if override in values else default)
            i += 1

    def _set_option(self, key: str, value: str):
        self.options[key] = value
        self._option_bytes[key] = value.encode()
        self._options_dirty = True

    def call_frame_time(self, usec: int) -> None:
        if self.frame_time_cb:
            self.frame_time_cb[0](usec)

    @property
    def frame_time_reference(self) -> int:
        return self.frame_time_cb[1] if self.frame_time_cb else 0
