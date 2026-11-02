"""Gamepad discovery and live state via SDL2's GameController API, on a background thread."""

from __future__ import annotations

import ctypes
import os
import threading
import warnings
from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal

from .mapping import AXIS_THRESHOLD, RETROPAD

os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
# SDL otherwise swallows SIGINT/SIGTERM, so `kill` would not stop the app.
os.environ.setdefault("SDL_NO_SIGNAL_HANDLERS", "1")
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import sdl2


@dataclass
class Pad:
    key: str
    name: str
    guid: str
    ordinal: int
    instance_id: int
    handle: object
    buttons: set[str] = field(default_factory=set)
    axes: dict[str, int] = field(default_factory=dict)

    def as_device(self) -> dict:
        return {"kind": "pad", "guid": self.guid, "ordinal": self.ordinal, "name": self.name}


def pad_state_bits(buttons: set[str], axes: dict[str, int],
                   bindings: dict[str, str]) -> tuple[int, tuple[int, int, int, int]]:
    """Convert raw controller state to (RetroPad bitmask, analog tuple)."""
    active = active_elements(buttons, axes)
    bits = 0
    for btn, element in bindings.items():
        if element in active:
            bits |= 1 << RETROPAD[btn]
    # The left stick always doubles as a d-pad (handy for 8/16-bit games).
    lx, ly = axes.get("leftx", 0), axes.get("lefty", 0)
    if lx < -AXIS_THRESHOLD:
        bits |= 1 << RETROPAD["Left"]
    if lx > AXIS_THRESHOLD:
        bits |= 1 << RETROPAD["Right"]
    if ly < -AXIS_THRESHOLD:
        bits |= 1 << RETROPAD["Up"]
    if ly > AXIS_THRESHOLD:
        bits |= 1 << RETROPAD["Down"]
    analog = (lx, ly, axes.get("rightx", 0), axes.get("righty", 0))
    return bits, analog


def active_elements(buttons: set[str], axes: dict[str, int]) -> set[str]:
    out = {f"button:{b}" for b in buttons}
    for name, v in axes.items():
        if v > AXIS_THRESHOLD:
            out.add(f"axis:{name}+")
        elif v < -AXIS_THRESHOLD:
            out.add(f"axis:{name}-")
    return out


class _SdlThread(QThread):
    def __init__(self, manager: "GamepadManager"):
        super().__init__()
        self.m = manager
        self.running = True

    def run(self):
        m = self.m
        if sdl2.SDL_Init(sdl2.SDL_INIT_GAMECONTROLLER) != 0:
            m.error = sdl2.SDL_GetError().decode()
            m.devicesChanged.emit()
            return
        ev = sdl2.SDL_Event()
        while self.running:
            if not sdl2.SDL_WaitEventTimeout(ctypes.byref(ev), 50):
                continue
            changed: set[str] = set()
            while True:
                m._handle(ev, changed)
                if not sdl2.SDL_PollEvent(ctypes.byref(ev)):
                    break
            for key in changed:
                m._notify(key)
        with m._lock:
            for pad in m._pads.values():
                sdl2.SDL_GameControllerClose(pad.handle)
            m._pads.clear()
        sdl2.SDL_QuitSubSystem(sdl2.SDL_INIT_GAMECONTROLLER)


class GamepadManager(QObject):
    """Owns all controllers. Thread-safe queries; Qt signals for hotplug and button activity."""

    devicesChanged = Signal()
    activity = Signal(str, str)  # device key, element (e.g. "button:a", "axis:lefttrigger+")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._pads: dict[int, Pad] = {}  # by SDL instance id
        self._unmapped: dict[int, str] = {}  # joysticks SDL has no controller mapping for
        self._listeners: list[Callable[[str, set[str], dict[str, int]], None]] = []
        self.error = ""
        self._thread = _SdlThread(self)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._thread.running = False
        self._thread.wait(2000)

    # ---- queries ---------------------------------------------------------
    def pads(self) -> list[Pad]:
        with self._lock:
            return sorted(self._pads.values(), key=lambda p: (p.name, p.ordinal))

    def unmapped_names(self) -> list[str]:
        with self._lock:
            return list(self._unmapped.values())

    def connected_keys(self) -> set[str]:
        with self._lock:
            return {p.key for p in self._pads.values()}

    def state(self, key: str) -> tuple[set[str], dict[str, int]]:
        with self._lock:
            for p in self._pads.values():
                if p.key == key:
                    return set(p.buttons), dict(p.axes)
        return set(), {}

    def add_listener(self, fn: Callable[[str, set[str], dict[str, int]], None]) -> None:
        """fn(key, buttons, axes) is called on the SDL thread whenever a pad's state changes."""
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    # ---- SDL thread internals ---------------------------------------------
    def _notify(self, key: str) -> None:
        with self._lock:
            listeners = list(self._listeners)
            pad = next((p for p in self._pads.values() if p.key == key), None)
            snap = (set(pad.buttons), dict(pad.axes)) if pad else (set(), {})
        for fn in listeners:
            try:
                fn(key, *snap)
            except Exception:
                pass

    def _handle(self, ev, changed: set[str]) -> None:
        t = ev.type
        if t == sdl2.SDL_CONTROLLERDEVICEADDED:
            self._open(ev.cdevice.which)
        elif t == sdl2.SDL_CONTROLLERDEVICEREMOVED:
            with self._lock:
                pad = self._pads.pop(ev.cdevice.which, None)
            if pad:
                sdl2.SDL_GameControllerClose(pad.handle)
                changed.add(pad.key)
                self.devicesChanged.emit()
        elif t == sdl2.SDL_JOYDEVICEADDED:
            idx = ev.jdevice.which
            if not sdl2.SDL_IsGameController(idx):
                name = (sdl2.SDL_JoystickNameForIndex(idx) or b"Joystick").decode("utf-8", "replace")
                with self._lock:
                    self._unmapped[sdl2.SDL_JoystickGetDeviceInstanceID(idx)] = name
                self.devicesChanged.emit()
        elif t == sdl2.SDL_JOYDEVICEREMOVED:
            with self._lock:
                gone = self._unmapped.pop(ev.jdevice.which, None)
            if gone:
                self.devicesChanged.emit()
        elif t in (sdl2.SDL_CONTROLLERBUTTONDOWN, sdl2.SDL_CONTROLLERBUTTONUP):
            name = sdl2.SDL_GameControllerGetStringForButton(ev.cbutton.button)
            if not name:
                return
            name = name.decode()
            with self._lock:
                pad = self._pads.get(ev.cbutton.which)
                if not pad:
                    return
                if t == sdl2.SDL_CONTROLLERBUTTONDOWN:
                    pad.buttons.add(name)
                else:
                    pad.buttons.discard(name)
                key = pad.key
            changed.add(key)
            if t == sdl2.SDL_CONTROLLERBUTTONDOWN:
                self.activity.emit(key, f"button:{name}")
        elif t == sdl2.SDL_CONTROLLERAXISMOTION:
            name = sdl2.SDL_GameControllerGetStringForAxis(ev.caxis.axis)
            if not name:
                return
            name = name.decode()
            with self._lock:
                pad = self._pads.get(ev.caxis.which)
                if not pad:
                    return
                old = pad.axes.get(name, 0)
                new = ev.caxis.value
                pad.axes[name] = new
                key = pad.key
            changed.add(key)
            for sign, crossed in (("+", old <= AXIS_THRESHOLD < new),
                                  ("-", old >= -AXIS_THRESHOLD > new)):
                if crossed:
                    self.activity.emit(key, f"axis:{name}{sign}")

    def _open(self, index: int) -> None:
        handle = sdl2.SDL_GameControllerOpen(index)
        if not handle:
            return
        joy = sdl2.SDL_GameControllerGetJoystick(handle)
        iid = sdl2.SDL_JoystickInstanceID(joy)
        buf = ctypes.create_string_buffer(64)
        sdl2.SDL_JoystickGetGUIDString(sdl2.SDL_JoystickGetGUID(joy), buf, 64)
        guid = buf.value.decode()
        name = (sdl2.SDL_GameControllerName(handle) or b"Controller").decode("utf-8", "replace")
        with self._lock:
            if iid in self._pads:
                sdl2.SDL_GameControllerClose(handle)
                return
            self._unmapped.pop(iid, None)
            used = {p.ordinal for p in self._pads.values() if p.guid == guid}
            ordinal = next(i for i in range(len(used) + 1) if i not in used)
            self._pads[iid] = Pad(f"pad:{guid}:{ordinal}", name, guid, ordinal, iid, handle)
        self.devicesChanged.emit()
