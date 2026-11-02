import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def testcore(tmp_path_factory) -> Path:
    """Build the tiny C libretro core used by worker/probe tests."""
    if not shutil.which("gcc"):
        pytest.skip("gcc not available")
    out = tmp_path_factory.mktemp("core") / "testcore_libretro.so"
    subprocess.run(["gcc", "-O2", "-shared", "-fPIC", "-o", str(out),
                    str(FIXTURES / "testcore.c")], check=True)
    return out


@pytest.fixture
def xdg(tmp_path, monkeypatch) -> Path:
    """Point MultiTap's config/data dirs at a temp folder."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path


@pytest.fixture
def content(tmp_path) -> Path:
    p = tmp_path / "game.tst"
    p.write_bytes(b"X")
    return p
