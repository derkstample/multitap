"""Out-of-process core probing.

    python -m multitap.cores.probe info  <core.so>
    python -m multitap.cores.probe trial <core.so> <rom> <system_dir>

Prints one JSON object on the last stdout line.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

TRIAL_FRAMES = 10


def probe_argv(*args: str) -> list[str]:
    return [sys.executable, "-m", "multitap.cores.probe", *args]


def probe_env() -> dict[str, str]:
    """Environment that makes the multitap package importable in the child."""
    env = os.environ.copy()
    root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def _emit(obj: dict) -> None:
    sys.stdout.write("\n" + json.dumps(obj) + "\n")
    sys.stdout.flush()


def cmd_info(core_path: str) -> dict:
    from ..libretro.core import CoreError, read_system_info
    try:
        return read_system_info(core_path)
    except CoreError as exc:
        return {"error": str(exc)}


def cmd_trial(core_path: str, rom: str, system_dir: str) -> dict:
    from ..libretro.core import CoreError, LibretroCore
    from ..roms.validate import prepare_content
    with tempfile.TemporaryDirectory(prefix="multitap-probe-") as tmp:
        try:
            os.makedirs(system_dir, exist_ok=True)
            frames = []
            core = LibretroCore(core_path, system_dir, tmp, log=lambda m: None)
            content = prepare_content(rom, core.system_info["valid_extensions"], tmp,
                                      core.system_info["block_extract"])
            core.on_video = lambda d, w, h, p, f: frames.append((w, h)) if d else None
            core.load_game(content)
            for _ in range(TRIAL_FRAMES):
                core.run()
            result = {"ok": True, "name": core.system_info["library_name"], "fps": core.fps,
                      "aspect": core.aspect, "frames": len(frames)}
            if not frames:
                result["warning"] = "The core produced no video in its first frames"
            return result
        except (CoreError, ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}


def main(argv: list[str]) -> int:
    # Cores may print to stdout/stderr; keep our JSON on the last line.
    if len(argv) >= 2 and argv[0] == "info":
        _emit(cmd_info(argv[1]))
        return 0
    if len(argv) >= 4 and argv[0] == "trial":
        _emit(cmd_trial(argv[1], argv[2], argv[3]))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
