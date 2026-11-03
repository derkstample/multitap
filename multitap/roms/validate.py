"""Quick checks that a file is plausible content for a core (the trial load is the final word)."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ARCHIVE_EXTS = {"zip"}

GB_LOGO = bytes.fromhex(
    "CEED6666CC0D000B03730083000C000D0008111F8889000EDCCC6EE6DDDDD999BBBB67636E0EECCCDDDC999FBBB9333E")


@dataclass
class Check:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def ext_of(path: str | Path) -> str:
    return Path(path).suffix.lower().lstrip(".")


def archive_members(path: str | Path, core_exts: list[str]) -> list[str]:
    """Names inside a zip that the core can load."""
    try:
        with zipfile.ZipFile(path) as zf:
            return [n for n in zf.namelist() if not n.endswith("/") and ext_of(n) in core_exts]
    except (zipfile.BadZipFile, OSError):
        return []


def needs_extraction(path: str | Path, core_exts: list[str], block_extract: bool = False) -> bool:
    ext = ext_of(path)
    return ext in ARCHIVE_EXTS and ext not in core_exts and not block_extract


def _read_head(path: Path, size: int = 0x200) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(size)


def _sniff_nes(head: bytes, size: int) -> str | None:
    if head[:4] in (b"NES\x1a", b"UNIF", b"FDS\x1a"):
        return None
    return "Missing iNES header: not an NES ROM"


def _sniff_gb(head: bytes, size: int) -> str | None:
    if size < 0x150:
        return "File too small to be a Game Boy ROM"
    logo_ok = head[0x104:0x134] == GB_LOGO
    chk = 0
    for b in head[0x134:0x14D]:
        chk = (chk - b - 1) & 0xFF
    if not logo_ok and chk != head[0x14D]:
        return "No valid Game Boy cartridge header"
    return None


def _sniff_gba(head: bytes, size: int) -> str | None:
    if size < 0xC0 or head[0xB2] != 0x96:
        return "No valid Game Boy Advance cartridge header"
    return None


def _sniff_genesis(head: bytes, size: int) -> str | None:
    if size < 0x200 or b"SEGA" not in head[0x100:0x110]:
        return "No SEGA header: not a Mega Drive/Genesis ROM"
    return None


def _sniff_snes(head: bytes, size: int) -> str | None:
    if size < 0x8000 or size % 1024 not in (0, 512):
        return "Unexpected size for a SNES ROM"
    return None


SNIFFERS = {
    "nes": _sniff_nes, "unf": _sniff_nes, "unif": _sniff_nes, "fds": _sniff_nes,
    "gb": _sniff_gb, "gbc": _sniff_gb, "sgb": _sniff_gb,
    "gba": _sniff_gba, "agb": _sniff_gba,
    "md": _sniff_genesis, "gen": _sniff_genesis,
    "sfc": _sniff_snes, "smc": _sniff_snes,
}


def quick_check(path: str | Path, core_exts: list[str], block_extract: bool = False,
                firmware: list[dict] | None = None, system_dir: str | Path | None = None) -> Check:
    """Extension, archive contents, magic bytes and firmware presence. No core is loaded."""
    path = Path(path)
    check = Check(ok=True)
    if not path.is_file():
        return Check(False, ["File not found"])
    if path.stat().st_size == 0:
        return Check(False, ["File is empty"])

    ext = ext_of(path)
    inner_ext, head, size = ext, None, path.stat().st_size
    if ext in core_exts:
        head = _read_head(path)
    elif ext in ARCHIVE_EXTS and not block_extract:
        members = archive_members(path, core_exts)
        if not members:
            return Check(False, ["Archive contains no file this emulator can load"])
        with zipfile.ZipFile(path) as zf:
            info = zf.getinfo(members[0])
            size = info.file_size
            with zf.open(info) as fh:
                head = fh.read(0x200)
        inner_ext = ext_of(members[0])
    else:
        exts = ", ".join("." + e for e in core_exts) or "(none)"
        return Check(False, [f".{ext} files aren't supported by this emulator. Supported: {exts}"])

    sniff = SNIFFERS.get(inner_ext)
    if sniff:
        err = sniff(head, size)
        if err:
            check.ok = False
            check.errors.append(err)

    if firmware and system_dir:
        for fw in firmware:
            if not (Path(system_dir) / fw["path"]).is_file():
                label = fw.get("desc") or fw["path"]
                if fw.get("optional", True):
                    check.warnings.append(f"Optional firmware missing: {label}")
                else:
                    check.warnings.append(f"Required firmware missing: {label} "
                                          f"(place it in {system_dir})")
    return check
