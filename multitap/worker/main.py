"""Per-player worker process: runs one libretro core and talks to the host via shm + a pipe.

Commands received (tuples): ("pause",) ("resume",) ("reset",) ("audible", bool) ("quit",)
Messages sent:              ("loaded", info) ("error", text) ("log", text) ("exited",)
"""

from __future__ import annotations

import signal
import tempfile
import time
import traceback
from array import array
from pathlib import Path

from ..libretro.core import CoreError, LibretroCore
from ..roms.validate import prepare_content
from .shm import (STATE_ERROR, STATE_EXITED, STATE_LOADING, STATE_PAUSED, STATE_RUNNING,
                  InputShm, VideoShm)

SRAM_INTERVAL_S = 30.0
AUDIO_TARGET_MS = 60.0
AUDIO_ADJUST = 0.005  # max pacing correction while audible (0.5%)


class Worker:
    def __init__(self, cfg: dict, conn):
        self.cfg = cfg
        self.conn = conn
        self.video = VideoShm(cfg["video_path"])
        self.input = InputShm(cfg["input_path"])
        self.paused = bool(cfg.get("start_paused", False))
        self.audible = bool(cfg.get("audible", False))
        self.running = True
        self.core: LibretroCore | None = None
        self.audio = None
        self.sram_path: Path | None = None
        self.last_sram: bytes | None = None
        self.tmp = tempfile.TemporaryDirectory(prefix="multitap-")

    def send(self, *msg) -> None:
        try:
            self.conn.send(msg)
        except (BrokenPipeError, OSError):
            self.running = False

    # ---- setup -----------------------------------------------------------
    def load(self) -> None:
        cfg = self.cfg
        self.video.set_info(state=STATE_LOADING)
        save_dir = Path(cfg["save_dir"])
        save_dir.mkdir(parents=True, exist_ok=True)
        Path(cfg["system_dir"]).mkdir(parents=True, exist_ok=True)
        core = LibretroCore(cfg["core_path"], cfg["system_dir"], save_dir,
                            username=cfg.get("username", ""),
                            log=lambda m: self.send("log", m))
        self.core = core
        content = prepare_content(cfg["rom_path"], core.system_info["valid_extensions"],
                                  self.tmp.name, core.system_info["block_extract"])
        core.on_video = self.on_video
        core.on_av_change = self.on_av_change
        core.load_game(content)

        self.sram_path = save_dir / (Path(cfg["rom_path"]).stem + ".srm")
        if self.sram_path.is_file():
            core.write_sram(self.sram_path.read_bytes())
        self.last_sram = core.read_sram()

        if not cfg.get("no_audio"):
            try:
                from .audio import AudioOut
                self.audio = AudioOut(core.sample_rate,
                                      f"MultiTap P{cfg.get('player', 0) + 1}")
            except Exception as exc:
                self.send("log", f"audio disabled: {exc}")

        self.video.set_info(state=STATE_PAUSED if self.paused else STATE_RUNNING,
                            aspect=core.aspect, fps=0.0, sample_rate=core.sample_rate)
        self.send("loaded", {
            "name": core.system_info["library_name"],
            "fps": core.fps,
            "aspect": core.aspect,
            "sample_rate": core.sample_rate,
        })

    # ---- callbacks -------------------------------------------------------
    def on_video(self, data, width, height, pitch, fmt):
        if data:  # None = duplicate frame; keep showing the previous one
            self.video.write_frame(data, width, height, pitch, fmt)

    def on_av_change(self):
        core = self.core
        self.video.set_info(aspect=core.aspect, sample_rate=core.sample_rate)
        if self.audio:
            self.audio.set_source_rate(core.sample_rate)

    # ---- main loop -------------------------------------------------------
    def handle(self, msg) -> None:
        cmd = msg[0]
        if cmd == "pause":
            self.paused = True
            if self.audio:
                self.audio.clear()
            self.video.set_info(state=STATE_PAUSED)
        elif cmd == "resume":
            self.paused = False
            self.video.set_info(state=STATE_RUNNING)
        elif cmd == "reset":
            self.core.reset()
        elif cmd == "audible":
            self.audible = bool(msg[1])
            if self.audio and not self.audible:
                self.audio.clear()
        elif cmd == "quit":
            self.running = False

    def poll_commands(self, timeout: float = 0.0) -> None:
        try:
            while self.running and self.conn.poll(timeout):
                self.handle(self.conn.recv())
                timeout = 0.0
        except (EOFError, OSError):
            self.running = False  # host went away

    def save_sram(self) -> None:
        if not self.core or not self.sram_path:
            return
        data = self.core.read_sram()
        if data and data != self.last_sram:
            tmp = self.sram_path.with_suffix(".srm.tmp")
            tmp.write_bytes(data)
            tmp.replace(self.sram_path)
            self.last_sram = data

    def loop(self) -> None:
        core = self.core
        clock = time.perf_counter
        next_frame = clock()
        last_frame = next_frame
        last_sram = next_frame
        fps_t0, fps_frames = next_frame, 0
        while self.running:
            if self.paused:
                self.poll_commands(0.05)
                next_frame = last_frame = clock()
                continue
            self.poll_commands()
            if not self.running or self.paused:
                continue

            buttons, analog = self.input.read()
            core.input_bits = buttons
            core.analog = analog
            now = clock()
            if core.frame_time_cb:
                core.call_frame_time(int((now - last_frame) * 1e6) or core.frame_time_reference)
            last_frame = now
            core.run()
            if core.shutdown_requested:
                break

            period = 1.0 / core.fps
            samples = core.audio
            core.audio = array("h")
            if self.audio and self.audible:
                self.audio.push(samples)
                err = (self.audio.queued_ms() - AUDIO_TARGET_MS) / AUDIO_TARGET_MS
                period *= 1.0 + max(-AUDIO_ADJUST, min(AUDIO_ADJUST, err * AUDIO_ADJUST))

            fps_frames += 1
            now = clock()
            if now - fps_t0 >= 1.0:
                self.video.set_info(fps=fps_frames / (now - fps_t0))
                fps_t0, fps_frames = now, 0
            if now - last_sram >= SRAM_INTERVAL_S:
                self.save_sram()
                last_sram = now

            next_frame += period
            delay = next_frame - clock()
            if delay > 0.002:
                time.sleep(delay - 0.001)
            while clock() < next_frame:
                pass
            if clock() - next_frame > 0.1:  # fell far behind (e.g. suspended); resync
                next_frame = clock()

    def shutdown(self) -> None:
        try:
            self.save_sram()
        except Exception as exc:
            self.send("log", f"SRAM save failed: {exc}")
        if self.audio:
            self.audio.close()
        if self.core:
            try:
                self.core.close()
            except Exception:
                pass
        self.video.set_info(state=STATE_EXITED)
        self.tmp.cleanup()


def run_worker(cfg: dict, conn) -> None:
    """multiprocessing entry point."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl+C in the terminal is the host's to handle
    worker = Worker(cfg, conn)
    try:
        worker.load()
    except (CoreError, ValueError, OSError) as exc:
        worker.video.set_info(state=STATE_ERROR)
        worker.send("error", str(exc))
        conn.close()
        return
    except Exception:
        worker.video.set_info(state=STATE_ERROR)
        worker.send("error", traceback.format_exc(limit=3))
        conn.close()
        return
    try:
        worker.loop()
    except Exception:
        worker.send("error", traceback.format_exc(limit=3))
    finally:
        worker.shutdown()
        worker.send("exited")
        conn.close()
