"""Out-of-process core probing with multitap-worker, so a broken core can't crash the launcher.

    multitap-worker --probe info  <core.so>
    multitap-worker --probe trial <core.so> <rom> <system_dir>

Each prints one JSON object on the last stdout line.
"""

from __future__ import annotations

import os

from .. import native


def probe_argv(*args: str) -> list[str]:
    """Command line for a probe. Raises FileNotFoundError if the worker isn't built."""
    return [str(native.require("multitap-worker")), "--probe", *args]


def probe_env() -> dict[str, str]:
    return os.environ.copy()
