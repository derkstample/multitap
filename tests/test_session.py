import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time

import pytest
from PySide6.QtWidgets import QApplication

from multitap.input.devices import GamepadManager
from multitap.layout.model import Layout
from multitap.ui.session import SessionConfig, SessionPlayer, SessionWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def run_until(qapp, cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def make_session(testcore, rom, tmp_path, n=2):
    players = [SessionPlayer(i, f"P{i + 1}", {"kind": "keyboard", "set": "AB"[i]}, {})
               for i in range(n)]
    cfg = SessionConfig(str(testcore), "testcore", str(rom), str(tmp_path / "system"),
                        Layout.from_dict({}, n), players)
    return SessionWindow(cfg, GamepadManager())


def test_session_releases_all_players_together(qapp, xdg, testcore, content, tmp_path):
    w = make_session(testcore, content, tmp_path)
    w.start()
    try:
        assert run_until(qapp, lambda: w._started)
        assert all(h.loaded for h in w.handles)
        assert run_until(qapp, lambda: all(h.video.header()[0] > 10 for h in w.handles))
        seqs = [h.video.header()[0] for h in w.handles]
        assert max(seqs) - min(seqs) <= 3
    finally:
        w.stop().join(5.0)


def test_pause_during_loading_holds_everyone(qapp, xdg, testcore, content, tmp_path):
    w = make_session(testcore, content, tmp_path)
    w.start()
    w.set_paused(True)  # pressed P before the cores finished loading
    try:
        assert run_until(qapp, lambda: w._started)
        time.sleep(0.3)
        assert [h.video.header()[0] for h in w.handles] == [0, 0]
        w.set_paused(False)
        assert run_until(qapp, lambda: all(h.video.header()[0] > 5 for h in w.handles))
    finally:
        w.stop().join(5.0)


def test_frame_geometry():
    from multitap.settings import GraphicsSettings
    from multitap.ui.session import frame_geometry
    g = GraphicsSettings()
    # NES 256x240 at 4:3 in a 1000x600 tile: height-limited, centred.
    src, (x, y, w, h) = frame_geometry(1000, 600, 256, 240, 4 / 3, g)
    assert src == (0, 0, 256, 240) and (w, h) == (800, 600) and x == 100

    g.integer_scale = True  # 600 // 240 = 2x
    _, (x, y, w, h) = frame_geometry(1000, 600, 256, 240, 4 / 3, g)
    assert h == 480 and round(w) == 640 and y == 60

    g = GraphicsSettings(aspect="square", crop_overscan=True)
    src, (_, _, w, h) = frame_geometry(1000, 600, 256, 240, 4 / 3, g)
    assert src == (0, 8, 256, 224) and h == 600 and round(w) == round(600 * 256 / 224)

    g = GraphicsSettings(aspect="stretch")
    assert frame_geometry(1000, 600, 256, 240, 4 / 3, g)[1] == (0, 0, 1000, 600)
