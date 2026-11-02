"""Application controller: swaps between the launcher and a fullscreen session."""

from __future__ import annotations

import sys
import threading

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication

from . import settings as settings_mod
from .input.devices import GamepadManager
from .ui import theme
from .ui.launcher import LauncherWindow
from .ui.session import SessionConfig, SessionWindow

DOUBLE_ESC_MS = 500


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
        self.stopping: threading.Thread | None = None
        self.last_esc_ts: int | None = None
        qapp.installEventFilter(self)
        qapp.aboutToQuit.connect(self.shutdown)

    def run(self) -> int:
        self.launcher.show()
        return self.qapp.exec()

    # ---- session switching ------------------------------------------------
    def start_session(self, cfg: SessionConfig) -> None:
        if self.stopping:
            self.stopping.join(5.0)  # previous session still flushing saves
            self.stopping = None
        self.last_esc_ts = None
        session = SessionWindow(cfg, self.pads)
        session.exitRequested.connect(self.end_session)
        session.setGeometry(self.launcher.screen().geometry())
        session.showFullScreen()
        session.activateWindow()
        session.setFocus()
        session.start()
        self.session = session
        self.launcher.hide()

    def end_session(self, esc_timestamp: int = 0) -> None:
        self.last_esc_ts = esc_timestamp
        session, self.session = self.session, None
        if session is None:
            return
        self.stopping = session.stop()
        session.close()
        session.deleteLater()
        self.launcher.show()
        self.launcher.raise_()
        self.launcher.activateWindow()

    # ---- double-Esc quits from anywhere -----------------------------------
    def eventFilter(self, obj, event):
        if (event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape
                and not event.isAutoRepeat() and self.last_esc_ts is not None
                and 0 < event.timestamp() - self.last_esc_ts <= DOUBLE_ESC_MS):
            self.last_esc_ts = None
            self.qapp.quit()
            return True
        return False

    def shutdown(self) -> None:
        if self.session:
            self.session.stop().join(5.0)
            self.session = None
        if self.stopping:
            self.stopping.join(5.0)
        self.launcher.flush_settings()
        self.pads.stop()


def main() -> int:
    QApplication.setApplicationName("MultiTap")
    QApplication.setDesktopFileName("multitap")
    qapp = QApplication(sys.argv)
    theme.apply(qapp)
    return App(qapp).run()
