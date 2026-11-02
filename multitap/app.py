"""Application controller: swaps between the launcher and a fullscreen session."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from . import settings as settings_mod
from .input.devices import GamepadManager
from .session_config import SessionConfig
from .ui import native_session, theme
from .ui.launcher import LauncherWindow
from .ui.native_session import EXIT_QUIT_APP, NativeSession
from .ui.session import SessionWindow

DOUBLE_ESC_S = 0.5

log = logging.getLogger(__name__)


def use_native_session() -> bool:
    """The C++ session unless it isn't built or MULTITAP_PYTHON_SESSION=1 asks for the old one."""
    return native_session.available() and os.environ.get("MULTITAP_PYTHON_SESSION") != "1"


class App(QObject):
    def __init__(self, qapp: QApplication):
        super().__init__()
        self.qapp = qapp
        self.settings = settings_mod.load()
        self.pads = GamepadManager()
        self.pads.start()
        self.launcher = LauncherWindow(self.settings, self.pads)
        self.launcher.startRequested.connect(self.start_session)
        self.session: SessionWindow | None = None
        self.native: NativeSession | None = None
        self.stopping: threading.Thread | None = None
        self.last_esc: float | None = None  # time.monotonic() of the Esc that ended a session
        qapp.installEventFilter(self)
        qapp.aboutToQuit.connect(self.shutdown)

    def run(self) -> int:
        self.launcher.show()
        return self.qapp.exec()

    # ---- session switching ------------------------------------------------
    def start_session(self, cfg: SessionConfig) -> None:
        if self.native or self.session:
            return
        if self.stopping:
            self.stopping.join(5.0)  # previous session still flushing saves
            self.stopping = None
        self.last_esc = None
        if use_native_session() and self._start_native(cfg):
            return
        session = SessionWindow(cfg, self.pads)
        session.exitRequested.connect(self.end_session)
        session.setGeometry(self.launcher.screen().geometry())
        session.showFullScreen()
        session.activateWindow()
        session.setFocus()
        session.start()
        self.session = session
        self.launcher.hide()

    def _start_native(self, cfg: SessionConfig) -> bool:
        g = self.launcher.screen().geometry()
        native = NativeSession(cfg, (g.x(), g.y(), g.width(), g.height()), self)
        native.finished.connect(self._native_finished)
        if not native.start():
            log.warning("multitap-session did not start; using the Python session")
            native.deleteLater()
            return False
        self.native = native
        self.launcher.hide()
        return True

    def _native_finished(self, code: int, report: dict) -> None:
        native, self.native = self.native, None
        if native is None:
            return
        native.deleteLater()
        if isinstance(report.get("osd_enabled"), bool):
            self.launcher.set_osd_enabled(report["osd_enabled"])
        if code == EXIT_QUIT_APP:
            self.qapp.quit()
            return
        esc_ns = report.get("esc_ns") or 0
        # The session's clock is CLOCK_MONOTONIC, the same one time.monotonic() reads.
        self.last_esc = esc_ns / 1e9 if esc_ns else None
        self._show_launcher()
        if code != 0:
            details = "\n".join(native.tail) or "No output."
            box = QMessageBox(QMessageBox.Warning, "MultiTap",
                              "The game session ended unexpectedly.", QMessageBox.Ok, self.launcher)
            box.setDetailedText(details)
            box.open()

    def end_session(self, esc_timestamp: int = 0) -> None:
        self.last_esc = time.monotonic()
        session, self.session = self.session, None
        if session is None:
            return
        self.stopping = session.stop()
        session.close()
        session.deleteLater()
        self._show_launcher()

    def _show_launcher(self) -> None:
        self.launcher.show()
        self.launcher.raise_()
        self.launcher.activateWindow()

    # ---- double-Esc quits from anywhere -----------------------------------
    def eventFilter(self, obj, event):
        if (event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape
                and not event.isAutoRepeat() and self.last_esc is not None
                and time.monotonic() - self.last_esc <= DOUBLE_ESC_S):
            self.last_esc = None
            self.qapp.quit()
            return True
        return False

    def shutdown(self) -> None:
        if self.native:
            native, self.native = self.native, None
            native.stop()
        if self.session:
            self.session.stop().join(5.0)
            self.session = None
        if self.stopping:
            self.stopping.join(5.0)
        self.launcher.flush_settings()
        self.pads.stop()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    QApplication.setApplicationName("MultiTap")
    QApplication.setDesktopFileName("multitap")
    qapp = QApplication(sys.argv)
    theme.apply(qapp)
    return App(qapp).run()
