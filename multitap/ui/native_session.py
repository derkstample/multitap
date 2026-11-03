"""Runs the native multitap-session for one play session and reports how it ended."""

from __future__ import annotations

import json
import logging
from collections import deque

from PySide6.QtCore import QObject, QProcess, Signal

from .. import native, paths
from ..session_config import SessionConfig, write_native_config

BINARY = "multitap-session"
EXIT_QUIT_APP = 3  # double Esc: quit MultiTap

log = logging.getLogger("multitap.session")


def available() -> bool:
    return native.find(BINARY) is not None


class NativeSession(QObject):
    # exit code (-1 if it crashed), the exit report {"esc_ns", "osd_enabled", ...} (may be empty)
    finished = Signal(int, dict)

    def __init__(self, cfg: SessionConfig, screen: tuple[int, int, int, int] | None = None,
                 parent: QObject | None = None):
        super().__init__(parent)
        self.cfg = cfg
        self.screen = screen
        self.proc = QProcess(self)
        self.proc.readyReadStandardError.connect(self._on_stderr)
        self.proc.finished.connect(self._on_finished)
        self.proc.errorOccurred.connect(self._on_error)
        self.tail: deque[str] = deque(maxlen=40)  # recent log lines, for error reports
        self._log = None
        self._stderr_buf = b""
        self._done = False

    def start(self) -> bool:
        binary = native.find(BINARY)
        if binary is None:
            self.tail.append(f"{BINARY} isn't built: run pip install -e .")
            return False
        config = write_native_config(self.cfg, paths.ensure(paths.runtime_dir()) / "session.json",
                                     self.screen)
        try:
            self._log = open(paths.ensure(paths.state_dir()) / "session.log", "w", encoding="utf-8")
        except OSError:
            self._log = None
        self.proc.start(str(binary), [str(config)])
        return self.proc.waitForStarted(5000)

    def running(self) -> bool:
        return self.proc.state() != QProcess.NotRunning

    def stop(self, timeout_ms: int = 5000) -> None:
        """Ask the session to end (it lets the workers save) and wait for it."""
        if not self.running():
            return
        self.proc.terminate()  # SIGTERM: same as Esc
        if not self.proc.waitForFinished(timeout_ms):
            self.proc.kill()
            self.proc.waitForFinished(1000)

    def _on_stderr(self) -> None:
        data = self._stderr_buf + bytes(self.proc.readAllStandardError())
        *lines, self._stderr_buf = data.split(b"\n")
        for raw in lines:
            line = raw.decode("utf-8", "replace").rstrip()
            if not line:
                continue
            self.tail.append(line)
            log.info(line.removeprefix("multitap-session: "))
            if self._log:
                self._log.write(line + "\n")
                self._log.flush()

    def _report(self) -> dict:
        out = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        for line in reversed(out.strip().splitlines()):
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("type") == "exit":
                return msg
        return {}

    def _finish(self, code: int) -> None:
        if self._done:
            return
        self._done = True
        self._on_stderr()
        if self._log:
            self._log.close()
            self._log = None
        self.finished.emit(code, self._report())

    def _on_finished(self, code: int, status: QProcess.ExitStatus) -> None:
        if status == QProcess.CrashExit:
            log.error("%s crashed", BINARY)
            code = -1
        self._finish(code)

    def _on_error(self, err: QProcess.ProcessError) -> None:
        if err == QProcess.FailedToStart:
            self.tail.append(f"{BINARY} failed to start: {self.proc.errorString()}")
            self._finish(-1)
