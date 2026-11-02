"""Persistent launcher settings (JSON in ~/.config/multitap/settings.json)."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import paths
from .colors import MAX_PLAYERS

MAX_RECENTS = 10


@dataclass
class PlayerSettings:
    name: str = ""
    # {"kind": "keyboard", "set": "A"} or {"kind": "pad", "guid": str, "ordinal": int, "name": str}
    device: dict | None = None
    # RetroPad button name -> SDL element ("button:a", "axis:lefttrigger+", ...). Empty = defaults.
    pad_bindings: dict[str, str] = field(default_factory=dict)
    # RetroPad button name -> Qt key code. Empty = defaults of the chosen keyboard set.
    kbd_bindings: dict[str, int] = field(default_factory=dict)


@dataclass
class Settings:
    core_path: str = ""
    extra_core_paths: list[str] = field(default_factory=list)
    last_rom: dict[str, str] = field(default_factory=dict)
    recents: dict[str, list[dict]] = field(default_factory=dict)
    # Player slots (0-7) in play, in the order of layout["rects"]. A slot keeps its color and saves.
    active: list[int] = field(default_factory=lambda: [0, 1])
    players: list[PlayerSettings] = field(
        default_factory=lambda: [PlayerSettings() for _ in range(MAX_PLAYERS)])
    layout: dict = field(default_factory=dict)
    audio_rotation_s: int = 120
    system_dir: str = ""
    smooth_scaling: bool = False

    # ---- recents -------------------------------------------------------
    def add_recent(self, core_id: str, rom_path: str) -> None:
        items = [r for r in self.recents.get(core_id, []) if r.get("path") != rom_path]
        items.insert(0, {"path": rom_path, "played": time.time()})
        self.recents[core_id] = items[:MAX_RECENTS]
        self.last_rom[core_id] = rom_path

    def recent_for(self, core_id: str) -> list[dict]:
        return list(self.recents.get(core_id, []))

    def effective_system_dir(self) -> Path:
        return Path(self.system_dir) if self.system_dir else paths.default_system_dir()

    # ---- (de)serialisation ---------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Settings":
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in raw.items() if k in known and k != "players"}
        s = cls(**kwargs)
        pknown = {f.name for f in fields(PlayerSettings)}
        players = []
        for p in raw.get("players", [])[:MAX_PLAYERS]:
            if isinstance(p, dict):
                players.append(PlayerSettings(**{k: v for k, v in p.items() if k in pknown}))
        while len(players) < MAX_PLAYERS:
            players.append(PlayerSettings())
        s.players = players
        active: list[int] = []
        for slot in s.active if isinstance(s.active, list) else []:
            if isinstance(slot, int) and 0 <= slot < MAX_PLAYERS and slot not in active:
                active.append(slot)
        s.active = active or [0]
        if not isinstance(s.layout, dict):
            s.layout = {}
        s.audio_rotation_s = max(5, int(s.audio_rotation_s))
        return s


def load(path: Path | None = None) -> Settings:
    path = path or paths.settings_file()
    try:
        raw = json.loads(path.read_text())
        if isinstance(raw, dict):
            return Settings.from_dict(raw)
    except (OSError, ValueError, TypeError):
        pass
    return Settings()


def save(settings: Settings, path: Path | None = None) -> None:
    path = path or paths.settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings.to_dict(), indent=2))
    os.replace(tmp, path)
