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


def _menu(w, title):
    for act in w.menuBar().actions():
        if act.text().replace("&", "") == title:
            return act.menu()
    raise AssertionError(f"no {title} menu")


def _open(menu):
    menu.aboutToShow.emit()
    return {a.text().replace("&", ""): a for a in menu.actions() if not a.isSeparator()}


def test_menu_bar_replaces_the_gear(launcher):
    from PySide6.QtWidgets import QToolButton
    titles = [a.text().replace("&", "") for a in launcher.menuBar().actions()]
    assert titles == ["File", "Inputs", "Config"]
    assert not launcher.findChildren(QToolButton, "iconButton")
    config = _open(_menu(launcher, "Config"))
    assert {"Graphics…", "On-screen display", "Audio…", "BIOS / firmware folder…"} <= set(config)


def test_file_menu_mirrors_core_and_game_pickers(launcher, content):
    w = launcher
    file = _open(_menu(w, "File"))
    emulators = _open(file["Emulator"].menu())
    assert any(a.isChecked() for a in emulators.values())
    assert file["Start"].isEnabled() == w.start_btn.isEnabled()

    w.s.add_recent(w.current_core().core_id, str(content))
    recents = _open(file["Recent games"].menu())
    next(a for t, a in recents.items() if content.name in t).trigger()
    assert w.rom_path() == str(content)


def test_inputs_menu_has_one_entry_per_player(launcher):
    w = launcher
    inputs = _open(_menu(w, "Inputs"))
    players = [t for t in inputs if t.startswith("Player")]
    assert players == ["Player 1", "Player 2"]
    p2 = _open(inputs["Player 2"].menu())
    assert p2["Configure buttons…"].isEnabled()
    devices = _open(p2["Device"].menu())
    checked = [t for t, a in devices.items() if a.isChecked()]
    assert checked == [w.cards[1].device.currentText()]
    devices["No input device"].trigger()
    assert w.s.players[1].device is None
    p2 = _open(_open(_menu(w, "Inputs"))["Player 2"].menu())
    assert not p2["Configure buttons… (choose a device first)"].isEnabled()


def test_osd_menu_toggles_settings(launcher):
    w = launcher
    osd = _open(_open(_menu(w, "Config"))["On-screen display"].menu())
    assert not osd["Frame rate"].isEnabled()  # greyed out until the OSD is on
    osd["Show on-screen display"].trigger()
    assert w.s.osd.enabled
    osd = _open(_open(_menu(w, "Config"))["On-screen display"].menu())
    osd["Input display"].trigger()
    assert w.s.osd.inputs


def test_config_dialogs_return_edited_copies(qapp):
    from multitap.settings import AudioSettings, GraphicsSettings
    from multitap.ui.config_dialogs import AudioDialog, GraphicsDialog
    g = GraphicsSettings()
    dlg = GraphicsDialog(g)
    dlg.filter.setCurrentIndex(dlg.filter.findData("bilinear"))
    dlg.aspect.setCurrentIndex(dlg.aspect.findData("custom"))
    assert dlg.custom.isEnabled()
    dlg.custom.setValue(1.25)
    out = dlg.result_settings()
    assert (out.filter, out.aspect, out.custom_aspect) == ("bilinear", "custom", 1.25)
    assert g == GraphicsSettings()  # the original is untouched

    dlg = AudioDialog(AudioSettings(device="Gone Speakers"))
    assert dlg.device.currentData() == "Gone Speakers"  # a missing saved device is kept
    dlg.volume.setValue(40)
    dlg.rotation.setValue(30)
    out = dlg.result_settings()
    assert (out.volume, out.rotation_s, out.device) == (40, 30, "Gone Speakers")
