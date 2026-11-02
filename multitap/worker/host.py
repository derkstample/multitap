"""Host-side handles for worker processes (no Qt dependency)."""

from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass, field

from .main import run_worker
from .shm import InputShm, VideoShm, make_path

_ctx = mp.get_context("spawn")


@dataclass
class WorkerHandle:
    player: int
    video: VideoShm
    input: InputShm
    process: mp.Process
    conn: object
    loaded: bool = False
    error: str = ""
    exited: bool = False
    info: dict = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)

    def send(self, *msg) -> None:
        if self.exited:
            return
        try:
            self.conn.send(msg)
        except (BrokenPipeError, OSError):
            self.exited = True

    def poll(self) -> list[tuple]:
        """Drain pending messages from the worker and update state."""
        out = []
        try:
            while self.conn.poll():
                msg = self.conn.recv()
                out.append(msg)
                kind = msg[0]
                if kind == "loaded":
                    self.loaded, self.info = True, msg[1]
                elif kind == "error":
                    self.error = msg[1]
                elif kind == "log":
                    self.logs = (self.logs + [msg[1]])[-50:]
                elif kind == "exited":
                    self.exited = True
        except (EOFError, OSError):
            self.exited = True
        if not self.process.is_alive():
            self.exited = True
        if self.exited and not self.loaded and not self.error:
            self.error = f"Emulator process exited unexpectedly (code {self.process.exitcode})"
        return out


def spawn_worker(player: int, cfg: dict, tag: str = "") -> WorkerHandle:
    """Create shm blocks and start a worker. cfg needs core_path, rom_path, system_dir, save_dir."""
    tag = tag or f"p{player + 1}"
    video = VideoShm(make_path(f"{tag}-video"), create=True)
    inp = InputShm(make_path(f"{tag}-input"), create=True)
    parent, child = _ctx.Pipe()
    full = dict(cfg, player=player, video_path=video.path, input_path=inp.path)
    proc = _ctx.Process(target=run_worker, args=(full, child), daemon=True,
                        name=f"multitap-p{player + 1}")
    proc.start()
    child.close()
    return WorkerHandle(player, video, inp, proc, parent)


def stop_workers(handles: list[WorkerHandle], timeout: float = 3.0) -> None:
    """Ask every worker to quit (flushing SRAM), then clean up processes and shm."""
    for h in handles:
        h.send("quit")
    for h in handles:
        h.process.join(timeout)
        if h.process.is_alive():
            h.process.terminate()
            h.process.join(1.0)
        if h.process.is_alive():
            h.process.kill()
            h.process.join(1.0)
        try:
            h.conn.close()
        except OSError:
            pass
        h.video.close(unlink=True)
        h.input.close(unlink=True)
