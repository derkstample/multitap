"""Shared-memory blocks between the host and a worker, backed by files in /dev/shm.

Video block:   header (64 B) + 2 frame slots. Each slot = slot header (16 B) + pixels.
  header:  seq u64 | active u32 | state u32 | aspect f32 | fps f32 | sample_rate f32
  slot:    width u32 | height u32 | pitch u32 | pixel_format u32 | pixels...
Input block:   buttons u16 | pad u16 | lx ly rx ry i16 | seq u32
"""

from __future__ import annotations

import ctypes
import mmap
import os
import struct
from pathlib import Path

SHM_ROOT = Path("/dev/shm") if Path("/dev/shm").is_dir() else Path("/tmp")

VIDEO_HEADER = struct.Struct("<QIIfff")
VIDEO_HEADER_SIZE = 64
SLOT_HEADER = struct.Struct("<IIII")
SLOT_HEADER_SIZE = 16
MAX_FRAME_BYTES = 1024 * 1024 * 4
SLOT_SIZE = SLOT_HEADER_SIZE + MAX_FRAME_BYTES
VIDEO_SIZE = VIDEO_HEADER_SIZE + 2 * SLOT_SIZE

INPUT = struct.Struct("<HHhhhhI")
INPUT_SIZE = 32

STATE_LOADING, STATE_RUNNING, STATE_PAUSED, STATE_ERROR, STATE_EXITED = range(5)


def _map(path: str | Path, size: int, create: bool) -> mmap.mmap:
    flags = os.O_RDWR | (os.O_CREAT | os.O_TRUNC if create else 0)
    fd = os.open(path, flags, 0o600)
    try:
        if create:
            os.ftruncate(fd, size)  # sparse on tmpfs: pages appear only when written
        return mmap.mmap(fd, size)
    finally:
        os.close(fd)


def make_path(tag: str) -> Path:
    return SHM_ROOT / f"multitap-{os.getpid()}-{tag}"


class VideoShm:
    def __init__(self, path: str | Path, create: bool = False):
        self.path = str(path)
        self.mm = _map(path, VIDEO_SIZE, create)
        self._buf = (ctypes.c_char * VIDEO_SIZE).from_buffer(self.mm)
        self.base = ctypes.addressof(self._buf)
        self._seq = 0
        self._active = 0

    # ---- worker side -----------------------------------------------------
    def write_frame(self, data_ptr: int, width: int, height: int, pitch: int, fmt: int) -> None:
        if pitch <= 0 or width <= 0 or height <= 0:
            return
        height = min(height, MAX_FRAME_BYTES // pitch)
        slot = 1 - self._active
        off = VIDEO_HEADER_SIZE + slot * SLOT_SIZE
        ctypes.memmove(self.base + off + SLOT_HEADER_SIZE, data_ptr, height * pitch)
        SLOT_HEADER.pack_into(self.mm, off, width, height, pitch, fmt)
        self._active = slot
        self._seq += 1
        struct.pack_into("<QI", self.mm, 0, self._seq, slot)

    def set_info(self, state: int | None = None, aspect: float | None = None,
                 fps: float | None = None, sample_rate: float | None = None) -> None:
        _, _, cur_state, cur_aspect, cur_fps, cur_rate = VIDEO_HEADER.unpack_from(self.mm, 0)
        struct.pack_into("<Ifff", self.mm, 12,
                         cur_state if state is None else state,
                         cur_aspect if aspect is None else aspect,
                         cur_fps if fps is None else fps,
                         cur_rate if sample_rate is None else sample_rate)

    # ---- host side -------------------------------------------------------
    def header(self) -> tuple[int, int, int, float, float, float]:
        """(seq, active, state, aspect, fps, sample_rate)"""
        return VIDEO_HEADER.unpack_from(self.mm, 0)

    def read_frame(self) -> tuple[int, int, int, int, int, bytes] | None:
        """Copy out the latest frame: (seq, width, height, pitch, fmt, pixels)."""
        seq, active = struct.unpack_from("<QI", self.mm, 0)
        if seq == 0:
            return None
        off = VIDEO_HEADER_SIZE + active * SLOT_SIZE
        w, h, pitch, fmt = SLOT_HEADER.unpack_from(self.mm, off)
        start = off + SLOT_HEADER_SIZE
        return seq, w, h, pitch, fmt, self.mm[start:start + h * pitch]

    def close(self, unlink: bool = False) -> None:
        del self._buf
        self.mm.close()
        if unlink:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass


class InputShm:
    def __init__(self, path: str | Path, create: bool = False):
        self.path = str(path)
        self.mm = _map(path, INPUT_SIZE, create)
        self._seq = 0

    def write(self, buttons: int, analog: tuple[int, int, int, int] = (0, 0, 0, 0)) -> None:
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        INPUT.pack_into(self.mm, 0, buttons & 0xFFFF, 0, *analog, self._seq)

    def read(self) -> tuple[int, list[int]]:
        buttons, _, lx, ly, rx, ry, _ = INPUT.unpack_from(self.mm, 0)
        return buttons, [lx, ly, rx, ry]

    def close(self, unlink: bool = False) -> None:
        self.mm.close()
        if unlink:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
