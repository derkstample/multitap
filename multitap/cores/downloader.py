"""Fetch cores and .info files from the official libretro buildbot."""

from __future__ import annotations

import io
import os
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from .. import paths

BUILDBOT = "https://buildbot.libretro.com"
CORE_URL = BUILDBOT + "/nightly/linux/x86_64/latest/{name}_libretro.so.zip"
INFO_URL = BUILDBOT + "/assets/frontend/info.zip"
SYSTEM_URL = BUILDBOT + "/assets/system/{name}.zip"

# (buildbot name, display name, systems) — software-rendered cores.
CURATED: list[tuple[str, str, str]] = [
    ("fceumm", "FCEUmm", "NES / Famicom"),
    ("nestopia", "Nestopia UE", "NES / Famicom"),
    ("mesen", "Mesen", "NES / Famicom"),
    ("snes9x", "Snes9x", "Super Nintendo"),
    ("bsnes", "bsnes", "Super Nintendo"),
    ("genesis_plus_gx", "Genesis Plus GX", "Mega Drive / Master System / Game Gear"),
    ("picodrive", "PicoDrive", "Mega Drive / 32X / Master System"),
    ("gambatte", "Gambatte", "Game Boy / Game Boy Color"),
    ("sameboy", "SameBoy", "Game Boy / Game Boy Color"),
    ("mgba", "mGBA", "Game Boy Advance"),
    ("mednafen_pce_fast", "Beetle PCE Fast", "PC Engine / TurboGrafx-16"),
    ("mednafen_ngp", "Beetle NeoPop", "Neo Geo Pocket / Color"),
    ("mednafen_wswan", "Beetle Cygne", "WonderSwan / Color"),
    ("handy", "Handy", "Atari Lynx"),
    ("stella", "Stella", "Atari 2600"),
    ("prosystem", "ProSystem", "Atari 7800"),
]

# OpenGL cores: each player's core renders offscreen through EGL in its own worker. Their .info
# files list an OpenGL or OpenGL ES API, so discovery.can_host accepts them. (Not PPSSPP: its
# GLEW only initialises in a GLX context.)
CURATED_GL: list[tuple[str, str, str]] = [
    ("mupen64plus_next", "Mupen64Plus-Next", "Nintendo 64"),
    ("parallel_n64", "ParaLLEl N64", "Nintendo 64"),
    ("flycast", "Flycast", "Dreamcast / NAOMI"),
    ("mednafen_psx_hw", "Beetle PSX HW", "PlayStation"),
    ("swanstation", "SwanStation", "PlayStation"),
    ("melondsds", "melonDS DS", "Nintendo DS"),
    ("citra", "Citra", "Nintendo 3DS"),
    ("yabasanshiro", "YabaSanshiro", "Saturn"),
    ("dolphin", "Dolphin", "GameCube / Wii"),
]

# Cores that need the buildbot's system files (the same ones RetroArch's "Core System Files"
# downloader installs) in the system folder: core -> zip name. Dolphin won't boot without
# dolphin-emu/Sys.
SYSTEM_FILES: dict[str, str] = {
    "dolphin": "Dolphin",
}

Progress = Callable[[int, int], None]  # (bytes done, total or 0)


def _fetch(url: str, progress: Progress | None = None, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "MultiTap/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        buf = io.BytesIO()
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            buf.write(chunk)
            if progress:
                progress(buf.tell(), total)
        return buf.getvalue()


def installed_path(name: str) -> Path:
    return paths.cores_dir() / f"{name}_libretro.so"


def download_core(name: str, progress: Progress | None = None) -> Path:
    data = _fetch(CORE_URL.format(name=name), progress)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        member = next((n for n in zf.namelist() if n.endswith("_libretro.so")), None)
        if not member:
            raise ValueError("Download did not contain a core")
        target = paths.ensure(paths.cores_dir()) / Path(member).name
        tmp = target.with_suffix(".so.part")
        tmp.write_bytes(zf.read(member))
        os.chmod(tmp, 0o755)
        tmp.replace(target)
    return target


def download_system_files(name: str, system_dir: str | Path,
                          progress: Progress | None = None) -> int:
    """Install a core's system files into `system_dir`; returns the number of files written
    (0 when the core needs none)."""
    zip_name = SYSTEM_FILES.get(name)
    if not zip_name:
        return 0
    data = _fetch(SYSTEM_URL.format(name=zip_name), progress)
    root = paths.ensure(Path(system_dir)).resolve()
    count = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.infolist():
            target = (root / member.filename).resolve()
            if member.is_dir() or not target.is_relative_to(root):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(member))
            count += 1
    return count


def download_info(progress: Progress | None = None) -> int:
    """Install the buildbot's .info bundle; returns the number of files written."""
    data = _fetch(INFO_URL, progress)
    out = paths.ensure(paths.info_dir())
    count = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            if name.endswith(".info") and "/" not in name.strip("/"):
                (out / name).write_bytes(zf.read(name))
                count += 1
    return count
