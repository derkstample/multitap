"""Locate MultiTap's native binaries.

`pip install -e .` builds them with CMake (scikit-build-core) and installs them into
`multitap/bin/`. MULTITAP_BIN_DIR overrides the search, e.g. to point at a CMake build dir.

    python -m multitap.native     # report what was found
"""

from __future__ import annotations

import os
import sys
import sysconfig
from pathlib import Path

import multitap

BINARIES = ("multitap-worker",)


def bin_dirs() -> list[Path]:
    """Folders searched for binaries, in order."""
    dirs = []
    if os.environ.get("MULTITAP_BIN_DIR"):
        dirs.append(Path(os.environ["MULTITAP_BIN_DIR"]))
    dirs += [Path(p) / "bin" for p in multitap.__path__]
    # An editable install imports the sources in place but installs build outputs into
    # site-packages.
    for key in ("platlib", "purelib"):
        d = Path(sysconfig.get_path(key)) / "multitap" / "bin"
        if d not in dirs:
            dirs.append(d)
    return dirs


def find(name: str) -> Path | None:
    for d in bin_dirs():
        path = d / name
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def missing() -> list[str]:
    return [name for name in BINARIES if find(name) is None]


def require(name: str) -> Path:
    path = find(name)
    if path is None:
        searched = ", ".join(str(d) for d in bin_dirs())
        raise FileNotFoundError(f"{name} is not built (searched {searched}). "
                                "Run: pip install -e .")
    return path


def main() -> int:
    for name in BINARIES:
        print(f"{name}: {find(name) or 'MISSING'}")
    return 1 if missing() else 0


if __name__ == "__main__":
    sys.exit(main())
