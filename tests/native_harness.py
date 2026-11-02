"""Drive the C++ multitap-worker from Python: spawn it, talk over its control socket and read
its shared memory. The block layouts are documented in native/common/shm.hpp."""

from __future__ import annotations

import json
import mmap
import os
import socket
import struct
import subprocess
import time
from array import array
from dataclasses import dataclass, field
from pathlib import Path

SHM_ROOT = Path("/dev/shm")
NONE = 0xFFFFFFFF
STATE_LOADING, STATE_RUNNING, STATE_PAUSED, STATE_ERROR, STATE_EXITED = range(5)

VIDEO_HEADER = struct.Struct("<IIIIQIIffdd")
SLOT_HEADER = struct.Struct("<IIIIIfQ")  # seq w h pitch fmt aspect frame_no
U32 = struct.Struct("<I")
U64 = struct.Struct("<Q")


def wait_for(cond, timeout: float = 5.0, interval: float = 0.01) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(interval)
    return False


def _map(path: Path) -> mmap.mmap:
    fd = os.open(path, os.O_RDWR)
    try:
        return mmap.mmap(fd, os.fstat(fd).st_size)
    finally:
        os.close(fd)


@dataclass
class Frame:
    frame_no: int
    width: int
    height: int
    pitch: int
    fmt: int
    aspect: float
    pixels: bytes

    def pixel(self, x: int, y: int) -> int:
        """XRGB8888 pixel value."""
        return U32.unpack_from(self.pixels, y * self.pitch + x * 4)[0] & 0xFFFFFF


class VideoReader:
    def __init__(self, path: Path):
        self.mm = _map(path)

    def header(self) -> dict:
        (magic, version, header_size, slot_count, stride, capacity, state, aspect,
         measured_fps, core_fps, sample_rate) = VIDEO_HEADER.unpack_from(self.mm, 0)
        return {"magic": magic, "version": version, "header_size": header_size,
                "slot_count": slot_count, "slot_stride": stride, "slot_capacity": capacity,
                "state": state, "aspect": aspect, "measured_fps": measured_fps,
                "core_fps": core_fps, "sample_rate": sample_rate,
                "latest": U32.unpack_from(self.mm, 64)[0],
                "frame_no": self.frame_no()}

    def frame_no(self) -> int:
        return U64.unpack_from(self.mm, 192)[0]

    def state(self) -> int:
        return U32.unpack_from(self.mm, 28)[0]

    def read_frame(self) -> Frame | None:
        """Copy the newest frame. Python has no atomics, so instead of claiming the slot
        this checks that the slot's seq is even and unchanged across the copy."""
        header_size = U32.unpack_from(self.mm, 8)[0]
        stride = U64.unpack_from(self.mm, 16)[0]
        for _ in range(20):
            latest = U32.unpack_from(self.mm, 64)[0]
            if latest == NONE:
                return None
            off = header_size + latest * stride
            seq, w, h, pitch, fmt, aspect, frame_no = SLOT_HEADER.unpack_from(self.mm, off)
            if seq & 1:
                continue
            pixels = self.mm[off + 64:off + 64 + h * pitch]
            if U32.unpack_from(self.mm, off)[0] == seq:
                return Frame(frame_no, w, h, pitch, fmt, aspect, pixels)
        return None

    def close(self) -> None:
        self.mm.close()


class InputWriter:
    """Session side of the input block (single writer, as in the real session)."""

    def __init__(self, path: Path):
        self.mm = _map(path)

    def set_held(self, mask: int) -> None:
        prev = U32.unpack_from(self.mm, 8)[0]
        U32.pack_into(self.mm, 8, mask)
        if mask & ~prev:
            self.tap(mask & ~prev)
        self._bump()

    def tap(self, mask: int) -> None:
        """Latch a press without holding it (as if released before the worker polled)."""
        U32.pack_into(self.mm, 12, U32.unpack_from(self.mm, 12)[0] | mask)
        self._bump()

    def pressed(self) -> int:
        return U32.unpack_from(self.mm, 12)[0]

    def set_analog(self, index: int, value: int) -> None:
        struct.pack_into("<i", self.mm, 16 + 4 * index, value)
        self._bump()

    def _bump(self) -> None:
        U32.pack_into(self.mm, 32, (U32.unpack_from(self.mm, 32)[0] + 1) & 0xFFFFFFFF)

    def close(self) -> None:
        self.mm.close()


class AudioReader:
    """Consumer side of the audio ring."""

    def __init__(self, path: Path):
        self.mm = _map(path)
        self.capacity = U32.unpack_from(self.mm, 12)[0]

    def sample_rate(self) -> float:
        return struct.unpack_from("<d", self.mm, 16)[0]

    def fill(self) -> int:
        return U64.unpack_from(self.mm, 64)[0] - U64.unpack_from(self.mm, 128)[0]

    def skip_all(self) -> None:
        U64.pack_into(self.mm, 128, U64.unpack_from(self.mm, 64)[0])

    def read(self) -> array:
        """Every queued stereo frame, interleaved."""
        w = U64.unpack_from(self.mm, 64)[0]
        r = U64.unpack_from(self.mm, 128)[0]
        out = array("h")
        for pos in range(r, w):
            i = 256 + (pos % self.capacity) * 4
            out.frombytes(self.mm[i:i + 4])
        U64.pack_into(self.mm, 128, w)
        return out

    def close(self) -> None:
        self.mm.close()


@dataclass
class NativeWorker:
    binary: Path
    slot: int
    proc: subprocess.Popen
    sock: socket.socket
    prefix: str
    loaded: bool = False
    exited: bool = False
    error: str = ""
    info: dict = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    stats: list[dict] = field(default_factory=list)
    video: VideoReader | None = None
    input: InputWriter | None = None
    audio: AudioReader | None = None
    _buf: bytes = b""

    @classmethod
    def spawn(cls, binary: Path, slot: int, core: Path, rom: Path, system_dir: Path,
              save_dir: Path, audible: bool = False, options: dict | None = None) -> NativeWorker:
        parent, child = socket.socketpair()
        prefix = f"multitap-{os.getpid()}-{slot}"
        argv = [str(binary), "--control-fd", str(child.fileno()), "--core", str(core),
                "--rom", str(rom), "--system-dir", str(system_dir), "--save-dir", str(save_dir),
                "--slot", str(slot), "--shm-prefix", prefix]
        for k, v in (options or {}).items():
            argv += ["--option", f"{k}={v}"]
        if audible:
            argv.append("--audible")
        proc = subprocess.Popen(argv, pass_fds=(child.fileno(),), stdout=subprocess.DEVNULL)
        child.close()
        parent.setblocking(False)
        return cls(binary, slot, proc, parent, prefix)

    def shm_path(self, kind: str) -> Path:
        return SHM_ROOT / f"{self.prefix}-{kind}"

    def send(self, type_: str, **fields) -> None:
        try:
            self.sock.sendall((json.dumps({"type": type_, **fields}) + "\n").encode())
        except OSError:
            self.exited = True

    def start(self, t0_ns: int | None = None) -> None:
        self.send("start", **({} if t0_ns is None else {"t0_ns": t0_ns}))

    def poll(self) -> list[dict]:
        """Drain pending messages and update state."""
        out = []
        while True:
            try:
                chunk = self.sock.recv(65536)
            except BlockingIOError:
                break
            except OSError:
                chunk = b""
            if not chunk:
                self.exited = True
                break
            self._buf += chunk
        *lines, self._buf = self._buf.split(b"\n")
        for line in lines:
            msg = json.loads(line)
            out.append(msg)
            kind = msg["type"]
            if kind == "loaded":
                self.loaded, self.info = True, msg
                self.video = VideoReader(self.shm_path("video"))
                self.input = InputWriter(self.shm_path("input"))
                self.audio = AudioReader(self.shm_path("audio"))
            elif kind == "error":
                self.error = msg["message"]
            elif kind == "log":
                self.logs = (self.logs + [msg["message"]])[-50:]
            elif kind == "stats":
                self.stats.append(msg)
            elif kind == "exited":
                self.exited = True
        return out

    def wait_loaded(self, timeout: float = 5.0) -> bool:
        return wait_for(lambda: (self.poll(), self.loaded or self.error or self.exited)[1],
                        timeout) and self.loaded

    def stop(self, timeout: float = 3.0) -> None:
        """Ask the worker to quit (flushing SRAM), then clean up the process and shm."""
        self.send("quit")
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(1.0)
        self.poll()
        self.sock.close()
        for reader in (self.video, self.input, self.audio):
            if reader:
                reader.close()
        for kind in ("video", "input", "audio"):
            self.shm_path(kind).unlink(missing_ok=True)


def stop_all(workers: list[NativeWorker]) -> None:
    for w in workers:
        w.send("quit")
    for w in workers:
        w.stop()
