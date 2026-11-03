"""The C++ multitap-session, run headless (SDL's offscreen video driver) with --screenshot."""

import json
import os
import re
import subprocess
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from dataclasses import replace

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from multitap import native
from multitap.input.mapping import KEYBOARD_PRESETS, PAD_DEFAULTS, qt_to_sdl_key
from multitap.layout.model import Layout
from multitap.session_config import SessionConfig, SessionPlayer, native_config
from multitap.settings import GraphicsSettings, OsdSettings

SESSION = native.find("multitap-session")
needs_session = pytest.mark.skipif(SESSION is None, reason="multitap-session is not built")

# testcore fills its frame with a tint derived from the content's first byte ("X").
TINT = (56, 72, 56)
TILE_BG = (10, 10, 12)


def make_config(testcore, rom, tmp_path, n=2, graphics=None, names=False):
    players = [SessionPlayer(i, f"P{i + 1}" if names else "",
                             {"kind": "keyboard", "set": "AB"[i % 2]},
                             dict(KEYBOARD_PRESETS["AB"[i % 2]])) for i in range(n)]
    return SessionConfig(str(testcore), "testcore", str(rom), str(tmp_path / "system"),
                         Layout.from_dict({}, n), players, graphics or GraphicsSettings(),
                         OsdSettings())


def run_session(cfg, tmp_path, size, frames=20):
    path = tmp_path / "session.json"
    path.write_text(json.dumps(native_config(cfg)))
    shot = tmp_path / "shot.bmp"
    env = dict(os.environ, SDL_VIDEODRIVER="offscreen", SDL_AUDIODRIVER="dummy")
    res = subprocess.run([str(SESSION), str(path), "--size", size, "--frames", str(frames),
                          "--screenshot", str(shot)],
                         capture_output=True, text=True, timeout=60, env=env)
    if res.returncode == 1 and re.search(r"SDL_Init|SDL_CreateWindow|OpenGL", res.stderr):
        pytest.skip(f"no offscreen OpenGL here: {res.stderr.strip()}")
    assert res.returncode == 0, res.stderr
    report = json.loads(res.stdout.strip().splitlines()[-1])
    return QImage(str(shot)), res.stderr, report


def rgb(img, x, y):
    c = QColor(img.pixel(x, y))
    return c.red(), c.green(), c.blue()


@pytest.fixture(autouse=True)
def isolated(xdg):
    return xdg


@needs_session
def test_two_players_start_together_and_fill_their_tiles(testcore, content, tmp_path):
    img, log, report = run_session(make_config(testcore, content, tmp_path), tmp_path, "640x240")
    assert report == {"type": "exit", "esc_ns": 0, "osd_enabled": False, "quit": False}
    assert "released together" in log
    frames = [int(n) for n in re.search(r"screenshot at frames: P1 (\d+), P2 (\d+)", log).groups()]
    assert min(frames) >= 20 and max(frames) - min(frames) <= 2
    assert (img.width(), img.height()) == (640, 240)
    # Tiles are 318 px wide with a 4 px gap; sample each one's middle, above the button row.
    assert rgb(img, 159, 60) == TINT
    assert rgb(img, 481, 60) == TINT
    assert rgb(img, 320, 60) == (0, 0, 0)  # the gap


@needs_session
def test_integer_scale_centres_a_whole_multiple(testcore, content, tmp_path):
    g = GraphicsSettings(integer_scale=True)
    img, _, _ = run_session(make_config(testcore, content, tmp_path, n=1, graphics=g),
                            tmp_path, "1000x600")
    # 320x240 at 2x = 640x480, centred: x 180..819, y 60..539.
    assert rgb(img, 181, 300) == TINT and rgb(img, 818, 300) == TINT
    assert rgb(img, 178, 300) == TILE_BG and rgb(img, 821, 300) == TILE_BG
    assert rgb(img, 500, 58) == TILE_BG and rgb(img, 500, 61) != TILE_BG


@needs_session
@pytest.mark.parametrize("flt, smooth", [("nearest", False), ("bilinear", True)])
def test_filter(testcore, content, tmp_path, flt, smooth):
    g = GraphicsSettings(filter=flt)
    img, _, _ = run_session(make_config(testcore, content, tmp_path, n=1, graphics=g),
                            tmp_path, "1000x750")
    # A row across the first row of button squares (frame y 114, scaled 3.125x).
    colors = {rgb(img, x, 356) for x in range(40, 960)}
    assert TINT in colors and (32, 32, 32) in colors
    assert (len(colors) > 2) == smooth


@needs_session
def test_a_core_that_fails_shows_its_error_and_exits(tmp_path, content):
    bad = tmp_path / "not_a_core.so"
    bad.write_bytes(b"nope")
    _, log, report = run_session(make_config(bad, content, tmp_path, n=1), tmp_path, "320x240")
    assert report["type"] == "exit"
    assert "P1 failed" in log


def test_native_config_converts_keys_and_keeps_pad_bindings(tmp_path):
    players = [SessionPlayer(0, "Ann", {"kind": "keyboard", "set": "A"}, dict(KEYBOARD_PRESETS["A"])),
               SessionPlayer(3, "", {"kind": "pad", "guid": "g", "ordinal": 1}, dict(PAD_DEFAULTS))]
    cfg = SessionConfig("/c.so", "core", "/r.nes", "/sys", Layout.from_dict({}, 2), players,
                        replace(GraphicsSettings(), filter="bilinear"))
    out = native_config(cfg, (0, 0, 1920, 1080))
    assert out["layout"] == cfg.layout.to_dict() and out["screen"] == [0, 0, 1920, 1080]
    kb, pad = out["players"]
    assert kb["bindings"]["Up"] == ord("w") and kb["color"] == "#E53935"
    assert pad["bindings"] == PAD_DEFAULTS and pad["index"] == 3 and pad["color"] == "#FDD835"
    assert pad["save_dir"].endswith("p4/core/saves")
    assert out["graphics"]["filter"] == "bilinear" and out["audio"]["rotation_s"] == 120
    json.dumps(out)  # serialisable as is


def test_qt_to_sdl_keys():
    from PySide6.QtCore import Qt
    assert qt_to_sdl_key(Qt.Key_A) == ord("a")
    assert qt_to_sdl_key(Qt.Key_Return) == 13
    assert qt_to_sdl_key(Qt.Key_Up) == (1 << 30) | 82
    assert qt_to_sdl_key(Qt.Key_F1) == (1 << 30) | 58
    assert qt_to_sdl_key(Qt.Key_Exclam) == ord("1")  # Shift+1 on a US layout
    assert qt_to_sdl_key(Qt.Key_Shift) == (1 << 30) | 225
    assert qt_to_sdl_key(Qt.Key_VolumeUp) is None
    for preset in KEYBOARD_PRESETS.values():
        assert all(qt_to_sdl_key(k) is not None for k in preset.values())


@needs_session
def test_app_hides_the_launcher_while_the_native_session_runs(testcore, content, tmp_path,
                                                              monkeypatch):
    from multitap.app import App
    monkeypatch.setenv("SDL_VIDEODRIVER", "offscreen")
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    qapp = QApplication.instance() or QApplication([])
    app = App(qapp)
    try:
        app.launcher.show()
        app.start_session(make_config(testcore, content, tmp_path))
        native = app.native
        assert native is not None and not app.launcher.isVisible()
        assert (tmp_path / "run" / "multitap" / "session.json").is_file()
        end = time.monotonic() + 20
        while time.monotonic() < end and not any("released together" in l for l in native.tail):
            qapp.processEvents()
            time.sleep(0.02)
        assert any("released together" in l for l in native.tail), list(native.tail)
        native.stop()  # SIGTERM, like closing the window: back to the launcher
        qapp.processEvents()
        assert app.native is None and app.launcher.isVisible()
        assert app.last_esc is None  # no Esc involved
        log = (tmp_path / "state" / "multitap" / "session.log").read_text()
        assert "released together" in log
    finally:
        app.shutdown()
