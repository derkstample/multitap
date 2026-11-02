"""SDL2 audio output for one worker: resamples the core's rate to the device rate."""

from __future__ import annotations

import ctypes
import os
import warnings
from array import array

OUT_RATE = 48000
BYTES_PER_FRAME = 4  # stereo int16
MAX_QUEUE_MS = 120


def _import_sdl():
    os.environ.setdefault("SDL_AUDIO_DEVICE_APP_NAME", "MultiTap")
    os.environ.setdefault("SDL_NO_SIGNAL_HANDLERS", "1")  # keep terminate() working
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import sdl2
    return sdl2


class AudioOut:
    def __init__(self, src_rate: float, app_name: str = "MultiTap", volume: int = 100,
                 driver: str = "auto", device: str = ""):
        os.environ["SDL_AUDIO_DEVICE_APP_NAME"] = app_name
        if driver and driver != "auto":
            os.environ["SDL_AUDIODRIVER"] = driver
        self.sdl = sdl = _import_sdl()
        if sdl.SDL_InitSubSystem(sdl.SDL_INIT_AUDIO) != 0:
            raise RuntimeError(sdl.SDL_GetError().decode())
        want = sdl.SDL_AudioSpec(OUT_RATE, sdl.AUDIO_S16SYS, 2, 1024)
        have = sdl.SDL_AudioSpec(0, 0, 0, 0)
        self.dev = sdl.SDL_OpenAudioDevice(device.encode() if device else None, 0, want, have, 0)
        if not self.dev and device:  # the saved device is gone; use the default instead
            self.dev = sdl.SDL_OpenAudioDevice(None, 0, want, have, 0)
        if not self.dev:
            raise RuntimeError(sdl.SDL_GetError().decode())
        self.stream = None
        self._out = ctypes.create_string_buffer(65536)
        self._mixed = ctypes.create_string_buffer(65536)
        u8 = ctypes.POINTER(ctypes.c_ubyte)
        self._out_u8, self._mixed_u8 = ctypes.cast(self._out, u8), ctypes.cast(self._mixed, u8)
        self.volume = round(max(0, min(100, volume)) * sdl.SDL_MIX_MAXVOLUME / 100)
        self.set_source_rate(src_rate)
        sdl.SDL_PauseAudioDevice(self.dev, 0)

    def set_source_rate(self, src_rate: float) -> None:
        sdl = self.sdl
        if self.stream:
            sdl.SDL_FreeAudioStream(self.stream)
        rate = int(round(src_rate)) if src_rate and src_rate > 0 else OUT_RATE
        self.stream = sdl.SDL_NewAudioStream(sdl.AUDIO_S16SYS, 2, rate,
                                             sdl.AUDIO_S16SYS, 2, OUT_RATE)

    def queued_ms(self) -> float:
        return self.sdl.SDL_GetQueuedAudioSize(self.dev) / (OUT_RATE * BYTES_PER_FRAME) * 1000

    def push(self, samples: array) -> None:
        if not samples or not self.stream:
            return
        sdl = self.sdl
        addr, count = samples.buffer_info()
        sdl.SDL_AudioStreamPut(self.stream, ctypes.c_void_p(addr), count * 2)
        while True:
            n = sdl.SDL_AudioStreamGet(self.stream, self._out, len(self._out))
            if n <= 0:
                break
            if self.queued_ms() >= MAX_QUEUE_MS:
                continue
            buf = self._out
            if self.volume < sdl.SDL_MIX_MAXVOLUME:
                ctypes.memset(self._mixed, 0, n)
                sdl.SDL_MixAudioFormat(self._mixed_u8, self._out_u8, sdl.AUDIO_S16SYS, n,
                                       self.volume)
                buf = self._mixed
            sdl.SDL_QueueAudio(self.dev, buf, n)

    def clear(self) -> None:
        self.sdl.SDL_ClearQueuedAudio(self.dev)
        if self.stream:
            self.sdl.SDL_AudioStreamClear(self.stream)

    def close(self) -> None:
        sdl = self.sdl
        if self.stream:
            sdl.SDL_FreeAudioStream(self.stream)
            self.stream = None
        sdl.SDL_CloseAudioDevice(self.dev)
        sdl.SDL_QuitSubSystem(sdl.SDL_INIT_AUDIO)
