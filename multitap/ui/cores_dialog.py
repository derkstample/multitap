"""'Get cores…' dialog: download curated cores from the libretro buildbot."""

from __future__ import annotations

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem,
                               QProgressBar, QVBoxLayout)

from ..cores import downloader


class _DownloadThread(QThread):
    progress = Signal(str, int, int)  # label, done, total
    finished_ok = Signal(list)
    failed = Signal(str)

    def __init__(self, names: list[str]):
        super().__init__()
        self.names = names

    def run(self):
        done = []
        try:
            self.progress.emit("Core info files", 0, 0)
            downloader.download_info(lambda d, t: self.progress.emit("Core info files", d, t))
            for name in self.names:
                downloader.download_core(name, lambda d, t, n=name: self.progress.emit(n, d, t))
                done.append(name)
        except Exception as exc:
            self.failed.emit(f"{exc}")
            return
        self.finished_ok.emit(done)


class CoresDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Get cores")
        self.setMinimumSize(520, 460)
        self.downloaded: list[str] = []
        root = QVBoxLayout(self)
        intro = QLabel("Choose emulator cores to download from the official libretro buildbot "
                       "(buildbot.libretro.com). They're installed for MultiTap only.")
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.list = QListWidget()
        for name, label, systems in downloader.CURATED:
            installed = downloader.installed_path(name).is_file()
            item = QListWidgetItem(f"{label} — {systems}" + ("   (installed)" if installed else ""))
            item.setData(Qt.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.list.addItem(item)
        root.addWidget(self.list)
        self.status = QLabel()
        root.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.hide()
        root.addWidget(self.bar)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.get = self.buttons.addButton("Download", QDialogButtonBox.AcceptRole)
        self.buttons.accepted.connect(self._start)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self._thread: _DownloadThread | None = None

    def _checked(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def _start(self):
        names = self._checked()
        if not names:
            self.status.setText("Tick at least one core.")
            return
        self.get.setEnabled(False)
        self.list.setEnabled(False)
        self.bar.show()
        self._thread = _DownloadThread(names)
        self._thread.progress.connect(self._on_progress)
        self._thread.finished_ok.connect(self._on_done)
        self._thread.failed.connect(self._on_failed)
        self._thread.start()

    def _on_progress(self, label: str, done: int, total: int):
        self.status.setText(f"Downloading {label}…")
        self.bar.setRange(0, total or 0)
        if total:
            self.bar.setValue(done)

    def _on_done(self, names: list[str]):
        self.downloaded = names
        self.bar.hide()
        self.status.setText(f"Installed {len(names)} core(s).")
        self.accept()

    def _on_failed(self, msg: str):
        self.bar.hide()
        self.get.setEnabled(True)
        self.list.setEnabled(True)
        self.status.setText(f"<span style='color:#E53935'>Download failed: {msg}</span>")

    def reject(self):
        if self._thread and self._thread.isRunning():
            return  # let the download finish rather than leaving a half-written core
        super().reject()
