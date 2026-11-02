"""RetroPad buttons, default bindings and pre-launch validation rules."""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

# RetroPad button name -> libretro joypad id
RETROPAD = {
    "B": 0, "Y": 1, "Select": 2, "Start": 3, "Up": 4, "Down": 5, "Left": 6, "Right": 7,
    "A": 8, "X": 9, "L": 10, "R": 11, "L2": 12, "R2": 13, "L3": 14, "R3": 15,
}
BUTTON_ORDER = ["Up", "Down", "Left", "Right", "B", "A", "Y", "X",
                "L", "R", "L2", "R2", "L3", "R3", "Select", "Start"]
REQUIRED = ("Up", "Down", "Left", "Right", "B", "A", "Start", "Select")

RESERVED_KEYS = {int(Qt.Key_Escape): "Esc (menu)", int(Qt.Key_R): "R (reset)",
                 int(Qt.Key_P): "P (pause)", int(Qt.Key_F1): "F1 (on-screen display)"}

K = Qt.Key
KEYBOARD_PRESETS: dict[str, dict[str, int]] = {
    "A": {"Up": K.Key_W, "Down": K.Key_S, "Left": K.Key_A, "Right": K.Key_D,
          "B": K.Key_J, "A": K.Key_K, "Y": K.Key_U, "X": K.Key_I,
          "L": K.Key_Q, "R": K.Key_E, "L2": K.Key_1, "R2": K.Key_3,
          "Select": K.Key_C, "Start": K.Key_V},
    "B": {"Up": K.Key_Up, "Down": K.Key_Down, "Left": K.Key_Left, "Right": K.Key_Right,
          "B": K.Key_Comma, "A": K.Key_Period, "Y": K.Key_L, "X": K.Key_Semicolon,
          "L": K.Key_BracketLeft, "R": K.Key_BracketRight, "L2": K.Key_9, "R2": K.Key_0,
          "Select": K.Key_Backspace, "Start": K.Key_Return},
}
KEYBOARD_PRESETS = {s: {b: int(k) for b, k in m.items()} for s, m in KEYBOARD_PRESETS.items()}

# SDL GameController names. RetroPad follows the SNES layout (B south, A east, Y west, X north).
PAD_DEFAULTS: dict[str, str] = {
    "Up": "button:dpup", "Down": "button:dpdown", "Left": "button:dpleft", "Right": "button:dpright",
    "B": "button:a", "A": "button:b", "Y": "button:x", "X": "button:y",
    "L": "button:leftshoulder", "R": "button:rightshoulder",
    "L2": "axis:lefttrigger+", "R2": "axis:righttrigger+",
    "L3": "button:leftstick", "R3": "button:rightstick",
    "Select": "button:back", "Start": "button:start",
}

AXIS_THRESHOLD = 16000


# Qt key codes -> SDL2 keycodes (SDL_Keycode), for the native session. Printable keys are the
# same ASCII code except letters, which SDL keeps lowercase. Qt reports the shifted symbol of a
# US layout ("!" for Shift+1); SDL reports the unshifted key, so those map back to it.
_SDL_SCANCODE_MASK = 1 << 30
_QT_SPECIAL_TO_SDL_SCANCODE = {
    K.Key_CapsLock: 57, K.Key_Print: 70, K.Key_ScrollLock: 71, K.Key_Pause: 72,
    K.Key_Insert: 73, K.Key_Home: 74, K.Key_PageUp: 75, K.Key_End: 77, K.Key_PageDown: 78,
    K.Key_Right: 79, K.Key_Left: 80, K.Key_Down: 81, K.Key_Up: 82, K.Key_NumLock: 83,
    K.Key_Enter: 88, K.Key_Menu: 101,
    K.Key_Control: 224, K.Key_Shift: 225, K.Key_Alt: 226, K.Key_Meta: 227, K.Key_AltGr: 230,
}
_QT_SPECIAL_TO_SDL = {
    K.Key_Escape: 27, K.Key_Tab: 9, K.Key_Backtab: 9, K.Key_Backspace: 8, K.Key_Return: 13,
    K.Key_Delete: 127,
}
_US_SHIFTED = dict(zip('!@#$%^&*()_+{}|:"<>?~', "1234567890-=[]\\;',./`"))


def qt_to_sdl_key(key: int) -> int | None:
    """SDL keycode for a Qt key code, or None if SDL has no equivalent."""
    key = int(key)
    if int(K.Key_F1) <= key <= int(K.Key_F12):
        return _SDL_SCANCODE_MASK | (58 + key - int(K.Key_F1))
    if int(K.Key_F13) <= key <= int(K.Key_F24):
        return _SDL_SCANCODE_MASK | (104 + key - int(K.Key_F13))
    for table, mask in ((_QT_SPECIAL_TO_SDL_SCANCODE, _SDL_SCANCODE_MASK),
                        (_QT_SPECIAL_TO_SDL, 0)):
        for qt, code in table.items():
            if key == int(qt):
                return mask | code
    if 0x20 <= key <= 0x7E:
        ch = chr(key)
        ch = _US_SHIFTED.get(ch, ch)
        return ord(ch.lower())
    if 0xA0 <= key <= 0xFF:  # Latin-1 letters (e.g. a German keyboard's ö)
        return ord(chr(key).lower())
    return None


def key_name(key: int) -> str:
    return QKeySequence(key).toString() or f"0x{key:x}"


def element_name(element: str) -> str:
    kind, _, name = element.partition(":")
    if kind == "axis":
        sign = name[-1] if name[-1] in "+-" else ""
        base = name.rstrip("+-")
        pretty = {"lefttrigger": "LT", "righttrigger": "RT", "leftx": "Left stick X",
                  "lefty": "Left stick Y", "rightx": "Right stick X", "righty": "Right stick Y"}
        return pretty.get(base, base) + ("" if base.endswith("trigger") else f" {sign}")
    pretty = {"a": "A / Cross", "b": "B / Circle", "x": "X / Square", "y": "Y / Triangle",
              "back": "Back / Select", "start": "Start", "guide": "Guide",
              "leftshoulder": "LB", "rightshoulder": "RB", "leftstick": "L3", "rightstick": "R3",
              "dpup": "D-pad Up", "dpdown": "D-pad Down", "dpleft": "D-pad Left",
              "dpright": "D-pad Right"}
    return pretty.get(name, name)


def keyboard_bindings(player) -> dict[str, int]:
    """Effective keyboard bindings for a PlayerSettings with a keyboard device."""
    preset = KEYBOARD_PRESETS.get((player.device or {}).get("set", "A"), KEYBOARD_PRESETS["A"])
    return dict(player.kbd_bindings) if player.kbd_bindings else dict(preset)


def pad_bindings(player) -> dict[str, str]:
    return dict(player.pad_bindings) if player.pad_bindings else dict(PAD_DEFAULTS)


def device_key(device: dict | None) -> str | None:
    if not device:
        return None
    if device.get("kind") == "keyboard":
        return f"kbd:{device.get('set', 'A')}"
    return f"pad:{device.get('guid')}:{device.get('ordinal', 0)}"


def device_label(device: dict | None) -> str:
    if not device:
        return "— none —"
    if device.get("kind") == "keyboard":
        return f"Keyboard {device.get('set', 'A')}"
    name = device.get("name") or "Controller"
    return name if not device.get("ordinal") else f"{name} #{device['ordinal'] + 1}"


@dataclass
class PlayerStatus:
    ok: bool
    problems: list[str] = field(default_factory=list)


def validate_players(players, slots: list[int], connected: set[str]) -> dict[int, PlayerStatus]:
    """Check the players in `slots` (indexes into `players`); keyed by slot.

    `connected` holds the device_key()s that are present.
    """
    out = {i: PlayerStatus(True) for i in slots}
    owners: dict[str, int] = {}
    key_owner: dict[int, int] = {}
    for i in slots:
        p, st = players[i], out[i]
        key = device_key(p.device)
        same_device = False
        if key is None:
            st.problems.append("No input device assigned")
        else:
            if key in owners:
                same_device = True
                st.problems.append(f"Same device as Player {owners[key] + 1}")
                out[owners[key]].problems.append(f"Same device as Player {i + 1}")
            else:
                owners[key] = i
            if key.startswith("pad:") and key not in connected:
                st.problems.append("Controller not connected")

        if p.device and p.device.get("kind") == "keyboard":
            binds = keyboard_bindings(p)
            missing = [b for b in REQUIRED if b not in binds]
            shared: dict[int, list[str]] = {}
            for btn, code in binds.items():
                if code in RESERVED_KEYS:
                    st.problems.append(f"{btn} uses reserved key {RESERVED_KEYS[code]}")
                other = key_owner.setdefault(code, i)
                if other != i:
                    shared.setdefault(other, []).append(key_name(code))
            if not same_device:
                for other, names in shared.items():
                    keys = ", ".join(names[:4]) + ("…" if len(names) > 4 else "")
                    st.problems.append(f"Shares keys with Player {other + 1}: {keys}")
                    out[other].problems.append(f"Shares keys with Player {i + 1}: {keys}")
            if len(set(binds.values())) != len(binds):
                st.problems.append("Two buttons share the same key")
        elif p.device:
            binds = pad_bindings(p)
            missing = [b for b in REQUIRED if b not in binds]
        else:
            missing = []
        if missing:
            st.problems.append("Unbound: " + ", ".join(missing))

    for st in out.values():
        st.problems = list(dict.fromkeys(st.problems))
        st.ok = not st.problems
    return out
