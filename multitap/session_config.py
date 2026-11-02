"""What the launcher hands to a play session, and the JSON the native multitap-session reads
(parsed by native/session/config.cpp)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths
from .colors import player_color
from .input.mapping import qt_to_sdl_key
from .layout.model import Layout
from .settings import AudioSettings, GraphicsSettings, OsdSettings


@dataclass
class SessionPlayer:
    index: int
    name: str
    device: dict
    bindings: dict


@dataclass
class SessionConfig:
    core_path: str
    core_id: str
    rom_path: str
    system_dir: str
    layout: Layout
    players: list[SessionPlayer] = field(default_factory=list)
    graphics: GraphicsSettings = field(default_factory=GraphicsSettings)
    osd: OsdSettings = field(default_factory=OsdSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)


def native_config(cfg: SessionConfig, screen: tuple[int, int, int, int] | None = None) -> dict:
    """The session config as multitap-session expects it.

    Keyboard bindings become SDL keycodes (keys SDL can't name are dropped); `screen` is the
    launcher's screen geometry, so the session opens on the same display.
    """
    players = []
    for p in cfg.players:
        if p.device.get("kind") == "keyboard":
            bindings = {b: k for b, k in ((b, qt_to_sdl_key(k)) for b, k in p.bindings.items())
                        if k is not None}
        else:
            bindings = dict(p.bindings)
        players.append({
            "index": p.index, "name": p.name, "color": player_color(p.index),
            "save_dir": str(paths.slot_dir(p.index, cfg.core_id) / "saves"),
            "device": dict(p.device), "bindings": bindings,
        })
    out = {
        "core_path": cfg.core_path, "rom_path": cfg.rom_path, "system_dir": cfg.system_dir,
        "layout": cfg.layout.to_dict(), "players": players,
        "graphics": asdict(cfg.graphics), "osd": asdict(cfg.osd), "audio": asdict(cfg.audio),
    }
    if screen:
        out["screen"] = list(screen)
    return out


def write_native_config(cfg: SessionConfig, path: Path,
                        screen: tuple[int, int, int, int] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(native_config(cfg, screen), indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path
