"""Find libretro cores from system packages, flatpak RetroArch, MultiTap's own dir and custom paths."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import paths
from .probe import probe_argv, probe_env

HOME = Path.home()
FLATPAK_RA = "org.libretro.RetroArch"
FLATPAK_ROOTS = [Path("/var/lib/flatpak/app") / FLATPAK_RA / "current/active/files",
                 HOME / ".local/share/flatpak/app" / FLATPAK_RA / "current/active/files"]


def core_dirs() -> list[tuple[Path, str]]:
    dirs = [
        (paths.cores_dir(), "MultiTap"),
        (Path("/usr/lib/x86_64-linux-gnu/libretro"), "system"),
        (Path("/usr/lib/libretro"), "system"),
        (Path("/usr/lib64/libretro"), "system"),
        (Path("/usr/local/lib/libretro"), "system"),
        (HOME / ".config/retroarch/cores", "RetroArch"),
        (HOME / ".var/app" / FLATPAK_RA / "config/retroarch/cores", "flatpak"),
    ]
    for root in FLATPAK_ROOTS:
        dirs += [(root / "lib/retroarch/cores", "flatpak"), (root / "lib/libretro", "flatpak")]
    return dirs


def info_dirs() -> list[Path]:
    dirs = [paths.info_dir(), Path("/usr/share/libretro/info"),
            Path("/usr/lib/x86_64-linux-gnu/libretro"), Path("/usr/local/share/libretro/info"),
            HOME / ".config/retroarch/info", HOME / ".config/retroarch/cores",
            HOME / ".var/app" / FLATPAK_RA / "config/retroarch/info",
            HOME / ".var/app" / FLATPAK_RA / "config/retroarch/cores"]
    for root in FLATPAK_ROOTS:
        dirs += [root / "share/libretro/info", root / "lib/retroarch/cores"]
    return dirs


@dataclass
class CoreInfo:
    path: str
    core_id: str
    display_name: str
    systems: str = ""
    extensions: list[str] = field(default_factory=list)
    firmware: list[dict] = field(default_factory=list)
    source: str = "custom"
    need_fullpath: bool = False
    block_extract: bool = False
    hw_render: bool = False  # needs a GPU API (see hw_api)
    hw_api: str = ""  # the .info file's required_hw_api, e.g. "OpenGL Core >= 3.3 | Vulkan >= 1.0"
    error: str = ""

    @property
    def label(self) -> str:
        sys_part = f" ({self.systems})" if self.systems else ""
        return f"{self.display_name}{sys_part} — {self.source}"


def core_id_for(path: str | Path) -> str:
    stem = Path(path).name.split(".so")[0]
    return stem[:-len("_libretro")] if stem.endswith("_libretro") else stem


def parse_info(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"')
    return out


def info_to_fields(info: dict[str, str]) -> dict:
    exts = [e.strip().lower() for e in info.get("supported_extensions", "").split("|") if e.strip()]
    firmware = []
    try:
        count = int(info.get("firmware_count", "0"))
    except ValueError:
        count = 0
    for i in range(count):
        p = info.get(f"firmware{i}_path")
        if p:
            firmware.append({"path": p, "desc": info.get(f"firmware{i}_desc", p),
                             "optional": info.get(f"firmware{i}_opt", "true").lower() == "true"})
    hw = info.get("required_hw_api", "").strip()
    return {
        "display_name": info.get("display_name") or info.get("corename", ""),
        "systems": info.get("systemname", ""),
        "extensions": exts,
        "firmware": firmware,
        "hw_render": bool(hw) and "software" not in hw.lower(),
        "hw_api": hw,
    }


def can_host(hw_api: str) -> bool:
    """Whether MultiTap can run a core needing `hw_api`: OpenGL and OpenGL ES, through the
    native worker. Vulkan- or Direct3D-only cores can't run."""
    from .. import native
    return "opengl" in hw_api.lower() and native.find("multitap-worker") is not None


def _find_info(core_id: str) -> dict[str, str] | None:
    for d in info_dirs():
        f = d / f"{core_id}_libretro.info"
        if f.is_file():
            try:
                return parse_info(f.read_text(errors="replace"))
            except OSError:
                continue
    return None


# ---- probing (out of process, results cached) -------------------------------
_CACHE_FILE = "core_cache.json"


def _load_cache() -> dict:
    try:
        return json.loads((paths.data_dir() / _CACHE_FILE).read_text())
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        paths.ensure(paths.data_dir())
        (paths.data_dir() / _CACHE_FILE).write_text(json.dumps(cache))
    except OSError:
        pass


def probe_system_info(core_path: str | Path, timeout: float = 10.0) -> dict:
    """Run retro_get_system_info in a subprocess so a broken core can't crash the launcher."""
    try:
        res = subprocess.run(probe_argv("info", str(core_path)), env=probe_env(),
                             capture_output=True, text=True, timeout=timeout)
        lines = [l for l in res.stdout.splitlines() if l.startswith("{")]
        if lines:
            return json.loads(lines[-1])
        return {"error": (res.stderr.strip().splitlines() or ["probe failed"])[-1]}
    except subprocess.TimeoutExpired:
        return {"error": "Core did not respond"}
    except FileNotFoundError as exc:  # the native runtime isn't built
        return {"error": str(exc), "uncached": True}


def describe(core_path: str | Path, source: str = "custom", cache: dict | None = None) -> CoreInfo:
    core_path = Path(core_path)
    cid = core_id_for(core_path)
    own_cache = cache is None
    cache = _load_cache() if cache is None else cache
    try:
        mtime = core_path.stat().st_mtime
    except OSError:
        return CoreInfo(str(core_path), cid, cid, source=source, error="File not found")

    key = str(core_path)
    probe = cache.get(key)
    if not probe or probe.get("mtime") != mtime:
        probe = probe_system_info(core_path)
        probe["mtime"] = mtime
        if not probe.pop("uncached", False):
            cache[key] = probe
            if own_cache:
                _save_cache(cache)

    core = CoreInfo(str(core_path), cid, probe.get("library_name") or cid, source=source)
    if probe.get("error"):
        core.error = probe["error"]
    core.extensions = probe.get("valid_extensions", [])
    core.need_fullpath = probe.get("need_fullpath", False)
    core.block_extract = probe.get("block_extract", False)
    info = _find_info(cid)
    if info:
        f = info_to_fields(info)
        core.display_name = f["display_name"] or core.display_name
        core.systems = f["systems"]
        core.firmware = f["firmware"]
        core.hw_render = f["hw_render"]
        core.hw_api = f["hw_api"]
        if not core.extensions:
            core.extensions = f["extensions"]
    if core.hw_render and not core.error and not can_host(core.hw_api):
        if "opengl" in core.hw_api.lower():
            core.error = "Needs OpenGL rendering: build the native runtime (pip install -e .)"
        else:
            api = core.hw_api.split("|")[0].split(">")[0].strip()  # "Vulkan >= 1.0" -> Vulkan
            core.error = f"Needs {api} rendering, which MultiTap doesn't support"
    return core


def scan(extra_paths: list[str] | None = None) -> list[CoreInfo]:
    cache = _load_cache()
    seen: set[str] = set()
    found: list[CoreInfo] = []

    def add(p: Path, source: str):
        try:
            real = str(p.resolve())
        except OSError:
            return
        if real in seen:
            return
        seen.add(real)
        found.append(describe(p, source, cache))

    for d, source in core_dirs():
        if d.is_dir():
            for p in sorted(d.glob("*_libretro.so")):
                add(p, source)
    for extra in extra_paths or []:
        p = Path(extra)
        if p.is_file():
            add(p, "custom")
    _save_cache(cache)
    found.sort(key=lambda c: (bool(c.error), c.display_name.lower()))
    return found


def to_dict(core: CoreInfo) -> dict:
    return asdict(core)
