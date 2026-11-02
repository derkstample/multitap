"""Per-player input configuration: rebind RetroPad buttons and test them live."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QPushButton, QVBoxLayout, QWidget)

from ..colors import player_color
from ..input.devices import GamepadManager, active_elements
from ..input.mapping import (BUTTON_ORDER, KEYBOARD_PRESETS, PAD_DEFAULTS, REQUIRED, RESERVED_KEYS,
                             device_label, element_name, key_name, keyboard_bindings,
                             pad_bindings)


class _Light(QFrame):
    def __init__(self):
        super().__init__()
        self.setFixedSize(14, 14)
        self.set_on(False)

    def set_on(self, on: bool):
        self.setStyleSheet(f"background:{'#4CAF50' if on else '#3a3a3a'};border-radius:7px;")


class InputDialog(QDialog):
    def __init__(self, player_index: int, player, pads: GamepadManager, parent=None):
        super().__init__(parent)
        self.player = player
        self.pads = pads
        self.is_kbd = bool(player.device) and player.device.get("kind") == "keyboard"
        self.dev_key = None
        if player.device and not self.is_kbd:
            self.dev_key = f"pad:{player.device['guid']}:{player.device.get('ordinal', 0)}"
        self.bindings = keyboard_bindings(player) if self.is_kbd else pad_bindings(player)
        self._capturing: str | None = None
        self._held_keys: set[int] = set()

        self.setWindowTitle(f"Player {player_index + 1} input")
        self.setMinimumWidth(560)
        root = QVBoxLayout(self)
        head = QLabel(f"<b style='color:{player_color(player_index)}'>Player {player_index + 1}</b>"
                      f" &nbsp;·&nbsp; {device_label(player.device)}")
        root.addWidget(head)
        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        hint = QLabel("Press buttons to test them (lights turn green). "
                      "Click a binding, then press the new key/button to change it.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")
        root.addWidget(hint)

        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setHorizontalSpacing(10)
        self.lights: dict[str, _Light] = {}
        self.bind_buttons: dict[str, QPushButton] = {}
        half = (len(BUTTON_ORDER) + 1) // 2
        for i, btn in enumerate(BUTTON_ORDER):
            row, col = i % half, (i // half) * 3
            light = _Light()
            label = QLabel(btn + (" *" if btn in REQUIRED else ""))
            bind = QPushButton()
            bind.setMinimumWidth(150)
            bind.clicked.connect(lambda _=False, b=btn: self._start_capture(b))
            grid.addWidget(light, row, col)
            grid.addWidget(label, row, col + 1)
            grid.addWidget(bind, row, col + 2)
            self.lights[btn] = light
            self.bind_buttons[btn] = bind
        root.addWidget(grid_host)
        note = QLabel("* required. Esc, R and P are reserved for MultiTap hotkeys.")
        note.setStyleSheet("color: palette(placeholder-text);")
        root.addWidget(note)

        row = QHBoxLayout()
        reset = QPushButton("Reset to defaults")
        reset.clicked.connect(self._reset_defaults)
        clear = QPushButton("Clear binding")
        clear.setToolTip("Click a binding first, then this to unbind it")
        clear.clicked.connect(self._clear_capture)
        row.addWidget(reset)
        row.addWidget(clear)
        row.addStretch()
        root.addLayout(row)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        for b in box.buttons():
            b.setFocusPolicy(Qt.NoFocus)
        root.addWidget(box)

        for b in self.bind_buttons.values():
            b.setFocusPolicy(Qt.NoFocus)
        reset.setFocusPolicy(Qt.NoFocus)
        clear.setFocusPolicy(Qt.NoFocus)

        if self.dev_key:
            pads.activity.connect(self._on_pad_activity)
            pads.devicesChanged.connect(self._refresh_status)
            self._poll = QTimer(self, interval=30, timeout=self._poll_pad)
            self._poll.start()
        self._refresh()
        self._refresh_status()

    # ---- results ---------------------------------------------------------
    def result_bindings(self) -> dict:
        return dict(self.bindings)

    # ---- display ---------------------------------------------------------
    def _label_for(self, btn: str) -> str:
        if btn not in self.bindings:
            return "— unbound —"
        v = self.bindings[btn]
        return key_name(v) if self.is_kbd else element_name(v)

    def _refresh(self):
        for btn, w in self.bind_buttons.items():
            if btn == self._capturing:
                w.setText("Press… (Esc cancels)")
                w.setStyleSheet("font-weight:bold;")
            else:
                w.setText(self._label_for(btn))
                w.setStyleSheet("color:#E53935;" if btn in REQUIRED and btn not in self.bindings else "")

    def _refresh_status(self):
        if not self.player.device:
            self.status.setText("<span style='color:#E53935'>No device selected.</span>")
        elif self.is_kbd:
            self.status.setText("<span style='color:#43A047'>● Keyboard ready</span>")
        elif self.dev_key in self.pads.connected_keys():
            self.status.setText("<span style='color:#43A047'>● Controller connected</span>")
        else:
            self.status.setText("<span style='color:#E53935'>● Controller not connected — "
                                "plug it in to test it</span>")

    def _set_lights(self, active_bindings: set[str]):
        for btn, light in self.lights.items():
            light.set_on(btn in active_bindings)

    # ---- capture ---------------------------------------------------------
    def _start_capture(self, btn: str):
        self._capturing = btn
        self._refresh()

    def _clear_capture(self):
        if self._capturing:
            self.bindings.pop(self._capturing, None)
            self._capturing = None
            self._refresh()

    def _assign(self, value):
        btn = self._capturing
        # A value can only drive one RetroPad button: steal it from any other.
        for other, v in list(self.bindings.items()):
            if v == value and other != btn:
                del self.bindings[other]
        self.bindings[btn] = value
        self._capturing = None
        self._refresh()
        self._refresh_status()

    def _reset_defaults(self):
        if self.is_kbd:
            self.bindings = dict(KEYBOARD_PRESETS.get(self.player.device.get("set", "A"),
                                                      KEYBOARD_PRESETS["A"]))
        else:
            self.bindings = dict(PAD_DEFAULTS)
        self._capturing = None
        self._refresh()

    # ---- events ----------------------------------------------------------
    def event(self, e):
        # Catch Tab/Backtab before Qt uses them for focus navigation.
        if e.type() == QEvent.KeyPress and self.is_kbd and e.key() in (Qt.Key_Tab, Qt.Key_Backtab):
            self.keyPressEvent(e)
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        key = int(e.key())
        if self._capturing:
            if key == int(Qt.Key_Escape):
                self._capturing = None
                self._refresh()
            elif self.is_kbd:
                if key in RESERVED_KEYS:
                    self.status.setText(f"<span style='color:#E53935'>{key_name(key)} is reserved "
                                        f"for {RESERVED_KEYS[key]}.</span>")
                else:
                    self._assign(key)
            return
        if self.is_kbd and not e.isAutoRepeat():
            self._held_keys.add(key)
            self._update_kbd_lights()
            return
        if key == int(Qt.Key_Escape):
            self.reject()

    def keyReleaseEvent(self, e):
        if self.is_kbd and not e.isAutoRepeat():
            self._held_keys.discard(int(e.key()))
            self._update_kbd_lights()

    def _update_kbd_lights(self):
        self._set_lights({b for b, k in self.bindings.items() if k in self._held_keys})

    def _on_pad_activity(self, key: str, element: str):
        if key == self.dev_key and self._capturing:
            self._assign(element)

    def _poll_pad(self):
        buttons, axes = self.pads.state(self.dev_key)
        active = active_elements(buttons, axes)
        self._set_lights({b for b, el in self.bindings.items() if el in active})

    def done(self, r):
        if self.dev_key:
            self._poll.stop()
            try:
                self.pads.activity.disconnect(self._on_pad_activity)
                self.pads.devicesChanged.disconnect(self._refresh_status)
            except (RuntimeError, TypeError):
                pass
        super().done(r)
