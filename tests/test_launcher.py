import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication

from multitap.input.devices import GamepadManager
from multitap.input.mapping import RETROPAD
from multitap.layout.model import Rect
from multitap.settings import Settings
from multitap.ui.controllers import CONTROLLERS, controller_for
from multitap.ui.player_card import MIME


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def launcher(qapp, xdg, testcore):
    from multitap.ui.launcher import LauncherWindow
    s = Settings(core_path=str(testcore), extra_core_paths=[str(testcore)])
    s.players[0].device = {"kind": "keyboard", "set": "A"}
    s.players[1].device = {"kind": "keyboard", "set": "B"}
    w = LauncherWindow(s, GamepadManager())
    w.resize(1100, 760)
    w.show()
    qapp.processEvents()
    yield w
    w.close()


def test_add_and_remove_players(launcher):
    w = launcher
    assert w.s.active == [0, 1] and w.board._column.isVisible()  # full 2x1 grid: add column
    w.board.addRequested.emit(None)
    assert w.s.active == [0, 1, 2]
    assert (w.layout_model.cols, w.layout_model.rows) == (2, 2)
    assert w.layout_model.free_cells() == [(1, 1)] and not w.board._column.isVisible()
    w.board.addRequested.emit((1, 1))
    assert w.s.active == [0, 1, 2, 3] and len(w.cards) == 4

    w.s.players[1].name = "Eve"
    w.cards[1].close_btn.click()
    assert w.s.active == [0, 2, 3] and 1 not in w.cards
    assert w.s.players[1].name == ""  # a removed player starts fresh
    w.board.addRequested.emit(w.layout_model.free_cells()[0])
    assert w.s.active == [0, 2, 3, 1]  # lowest free slot keeps P2's color
    assert len(w.layout_model.rects) == 4 and w.layout_model.is_valid()


def test_last_player_cannot_be_removed(launcher):
    w = launcher
    w.cards[1].close_btn.click()
    assert w.s.active == [0] and w.cards[0].close_btn.isHidden()
    w.cards[0].close_btn.click()
    assert w.s.active == [0]


def test_dropping_a_card_on_another_swaps_them(launcher, qapp):
    w = launcher
    board = w.board
    mime = QMimeData()
    mime.setData(MIME, b"0")
    target = QPointF(w.cards[1].geometry().center())
    ev = QDropEvent(target, Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
    board.dropEvent(ev)
    assert w.layout_model.rects == [Rect(0, 1), Rect(0, 0)]
    assert w.cards[0].geometry().x() > w.cards[1].geometry().x()
    assert w.s.layout["rects"][0] == [0, 1, 1, 1]  # persisted


def test_keyboard_input_lights_the_controller(launcher):
    w = launcher
    w._held = {int(Qt.Key_J), int(Qt.Key_W), int(Qt.Key_Return)}
    w._poll_inputs()
    assert w.cards[0].view.pressed == {"B", "Up"}  # keyboard A
    assert w.cards[1].view.pressed == {"Start"}  # keyboard B


def test_controller_artwork_matches_the_core():
    assert controller_for("fceumm").key == "nes"
    assert controller_for("genesis_plus_gx", rom_path="/g/sonic.gg").key == "gamegear"
    assert controller_for("genesis_plus_gx", rom_path="/g/sonic.md").key == "genesis"
    assert controller_for("mgba", rom_path="/g/tetris.gb").key == "gb"
    assert controller_for("some_core", systems="Super Nintendo Entertainment System").key == "snes"
    assert controller_for("testcore").key == "generic"


def test_every_controller_loads_and_maps_real_buttons(qapp):
    for c in CONTROLLERS.values():
        img = c.pixmap_image()
        assert not img.isNull(), c.key
        assert set(c.buttons) <= set(RETROPAD), c.key
        for shapes in c.buttons.values():
            for shape in shapes:
                box = shape(img.width(), img.height()).boundingRect()
                assert img.rect().adjusted(-5, -5, 5, 5).contains(box.toRect()), c.key


def test_start_passes_slots_in_layout_order(launcher, content):
    w = launcher
    w.board.addRequested.emit(None)
    w.cards[1].close_btn.click()  # active is now [0, 2]
    w.s.players[2].device = {"kind": "keyboard", "set": "B"}
    w.game_combo.setEditText(str(content))
    w.rom_state = ("ok", "Playable")
    got = []
    w.startRequested.connect(got.append)
    w._start()
    cfg = got[0]
    assert [p.index for p in cfg.players] == [0, 2]
    assert cfg.layout.count == 2 and cfg.layout.is_valid()
