"""Config menu dialogs: graphics and audio. Each edits a copy and hands it back on OK."""

from __future__ import annotations

import ctypes.util
import os
import warnings
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton, QSlider,
                               QSpinBox, QVBoxLayout, QWidget)

from ..settings import AudioSettings, GraphicsSettings

ASPECT_LABELS = {
    "core": "Core provided", "4:3": "4:3", "16:9": "16:9", "square": "Square pixels (1:1)",
    "stretch": "Stretch to fill", "custom": "Custom",
}

def renderer_note() -> str:
    """Why some graphics options won't apply, or "" if they all will."""
    from . import native_session
    if not native_session.available():
        return ("Shader presets and vertical sync take effect with the native renderer "
                "(multitap-session), which isn't built.")
    if not ctypes.util.find_library("rashader"):
        return ("Shader presets need librashader (librashader.so), which isn't installed; "
                "games show without the preset until it is.")
    return ""


class GraphicsDialog(QDialog):
    def __init__(self, graphics: GraphicsSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Graphics")
        self.setMinimumWidth(460)
        g = self.graphics = replace(graphics)
        form = QFormLayout()
        form.setHorizontalSpacing(14)

        self.filter = QComboBox()
        self.filter.addItem("Nearest (sharp pixels)", "nearest")
        self.filter.addItem("Bilinear (smooth)", "bilinear")
        self.filter.setCurrentIndex(self.filter.findData(g.filter))
        form.addRow("Scaling filter", self.filter)

        self.integer = QCheckBox("Integer scaling")
        self.integer.setToolTip("Scale by whole multiples only, so every pixel is the same size")
        self.integer.setChecked(g.integer_scale)
        form.addRow("", self.integer)

        self.aspect = QComboBox()
        for key, label in ASPECT_LABELS.items():
            self.aspect.addItem(label, key)
        self.aspect.setCurrentIndex(self.aspect.findData(g.aspect))
        self.custom = QDoubleSpinBox()
        self.custom.setRange(0.2, 5.0)
        self.custom.setDecimals(3)
        self.custom.setSingleStep(0.01)
        self.custom.setValue(g.custom_aspect)
        aspect_row = QHBoxLayout()
        aspect_row.addWidget(self.aspect, 1)
        aspect_row.addWidget(self.custom)
        form.addRow("Aspect ratio", aspect_row)
        self.aspect.currentIndexChanged.connect(self._sync)

        self.overscan = QCheckBox("Crop overscan")
        self.overscan.setToolTip("Hide the 8 rows at the top and bottom that old TVs cut off")
        self.overscan.setChecked(g.crop_overscan)
        form.addRow("", self.overscan)

        self.shader = QComboBox()
        self.shader.addItem("None", "")
        if g.shader_preset:
            self.shader.addItem(Path(g.shader_preset).name, g.shader_preset)
            self.shader.setCurrentIndex(1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_shader)
        shader_row = QHBoxLayout()
        shader_row.addWidget(self.shader, 1)
        shader_row.addWidget(browse)
        self.shader.setToolTip("A RetroArch shader preset (.slangp), applied with librashader")
        form.addRow("Shader preset", shader_row)

        self.vsync = QCheckBox("Vertical sync")
        self.vsync.setToolTip("Wait for the display's refresh: no tearing, slightly more latency")
        self.vsync.setChecked(g.vsync)
        form.addRow("", self.vsync)

        note = QLabel(renderer_note())
        note.setObjectName("caption")
        note.setWordWrap(True)
        note.setVisible(bool(note.text()))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(note)
        root.addWidget(buttons)
        self._sync()

    def _sync(self):
        self.custom.setEnabled(self.aspect.currentData() == "custom")

    def _browse_shader(self):
        start = str(Path(self.shader.currentData()).parent) if self.shader.currentData() else ""
        path, _ = QFileDialog.getOpenFileName(self, "Shader preset", start,
                                              "RetroArch shader presets (*.slangp)")
        if path:
            i = self.shader.findData(path)
            if i < 0:
                self.shader.addItem(Path(path).name, path)
                i = self.shader.count() - 1
            self.shader.setCurrentIndex(i)

    def result_settings(self) -> GraphicsSettings:
        return replace(self.graphics, filter=self.filter.currentData(),
                       integer_scale=self.integer.isChecked(), aspect=self.aspect.currentData(),
                       custom_aspect=self.custom.value(), crop_overscan=self.overscan.isChecked(),
                       shader_preset=self.shader.currentData() or "", vsync=self.vsync.isChecked())


def _sdl():
    os.environ.setdefault("SDL_NO_SIGNAL_HANDLERS", "1")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import sdl2
    return sdl2


def audio_drivers() -> list[str]:
    """SDL audio drivers compiled in, e.g. pipewire, pulseaudio, alsa."""
    try:
        sdl = _sdl()
        names = [sdl.SDL_GetAudioDriver(i) for i in range(sdl.SDL_GetNumAudioDrivers())]
    except Exception:
        return []
    return [n.decode() for n in names if n and n not in (b"disk", b"dummy")]


def audio_devices(driver: str) -> list[str]:
    """Output devices of one driver ("auto" = SDL's choice); empty if it can't start."""
    try:
        sdl = _sdl()
        if sdl.SDL_AudioInit(None if driver == "auto" else driver.encode()) != 0:
            return []
        try:
            names = [sdl.SDL_GetAudioDeviceName(i, 0) for i in range(sdl.SDL_GetNumAudioDevices(0))]
        finally:
            sdl.SDL_AudioQuit()
    except Exception:
        return []
    return [n.decode(errors="replace") for n in names if n]


class AudioDialog(QDialog):
    def __init__(self, audio: AudioSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Audio")
        self.setMinimumWidth(460)
        a = self.audio = replace(audio)
        form = QFormLayout()
        form.setHorizontalSpacing(14)

        self.driver = QComboBox()
        self.driver.addItem("Automatic", "auto")
        for name in audio_drivers():
            self.driver.addItem(name, name)
        if self.driver.findData(a.driver) < 0:
            self.driver.addItem(f"{a.driver} (not available)", a.driver)
        self.driver.setCurrentIndex(self.driver.findData(a.driver))
        form.addRow("Backend", self.driver)

        self.device = QComboBox()
        form.addRow("Output device", self.device)
        self.driver.currentIndexChanged.connect(self._fill_devices)

        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(a.volume)
        self.volume_label = QLabel()
        self.volume_label.setMinimumWidth(42)
        self.volume.valueChanged.connect(lambda v: self.volume_label.setText(f"{v}%"))
        self.volume_label.setText(f"{a.volume}%")
        vol_row = QWidget()
        vl = QHBoxLayout(vol_row)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.addWidget(self.volume, 1)
        vl.addWidget(self.volume_label)
        form.addRow("Volume", vol_row)

        self.rotation = QSpinBox()
        self.rotation.setRange(5, 3600)
        self.rotation.setSuffix(" s")
        self.rotation.setValue(a.rotation_s)
        self.rotation.setToolTip("Only one player is audible at a time; this is how long each "
                                 "player's turn lasts")
        form.addRow("Switch audible player every", self.rotation)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(buttons)
        self._fill_devices()

    def _fill_devices(self):
        want = self.device.currentData() if self.device.count() else self.audio.device
        self.device.clear()
        self.device.addItem("Default", "")
        for name in audio_devices(self.driver.currentData()):
            self.device.addItem(name, name)
        if want and self.device.findData(want) < 0:
            self.device.addItem(f"{want} (not found)", want)
        self.device.setCurrentIndex(max(0, self.device.findData(want or "")))

    def result_settings(self) -> AudioSettings:
        return replace(self.audio, driver=self.driver.currentData(),
                       device=self.device.currentData() or "", volume=self.volume.value(),
                       rotation_s=self.rotation.value())
