"""XDG locations used by MultiTap."""

import os
from pathlib import Path

APP = "multitap"


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / fallback


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / APP


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / APP


def state_dir() -> Path:
    """Logs and other state worth keeping across runs but not backing up."""
    return _xdg("XDG_STATE_HOME", ".local/state") / APP


def runtime_dir() -> Path:
    """Per-login scratch space (the session config); falls back to the temp dir."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        return Path(base) / APP
    import tempfile
    return Path(tempfile.gettempdir()) / f"{APP}-{os.getuid()}"


def settings_file() -> Path:
    return config_dir() / "settings.json"


def cores_dir() -> Path:
    """Cores fetched by the built-in downloader."""
    return data_dir() / "cores"


def info_dir() -> Path:
    """Core .info files fetched by the built-in downloader."""
    return data_dir() / "info"


def default_system_dir() -> Path:
    """Shared BIOS/firmware directory handed to every core."""
    return data_dir() / "system"


def slot_dir(player: int, core_id: str) -> Path:
    """Per-player persistent profile for one core (player is 0-based)."""
    return data_dir() / "slots" / f"p{player + 1}" / core_id


def ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
