"""Launcher window: emulator and game pickers, player cards tiled like the screen, and START."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QEvent, QProcess, QProcessEnvironment, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup, QColor, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QComboBox, QFileDialog, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QPushButton,
                               QSizePolicy, QStyledItemDelegate, QVBoxLayout, QWidget)

from .. import settings as settings_mod
from ..colors import MAX_PLAYERS, player_color
from ..cores import discovery
from ..cores.discovery import CoreInfo
from ..cores.downloader import installed_path
from ..cores.probe import probe_argv, probe_env
from ..input.devices import GamepadManager, pad_state_bits
from ..input.mapping import (RETROPAD, device_key, device_label, keyboard_bindings, pad_bindings,
                             validate_players)
from ..layout.model import PRESETS, Layout
from ..roms.validate import ARCHIVE_EXTS, quick_check
from ..settings import OSD_POSITIONS, PlayerSettings, Settings
from . import theme
from .board import PlayerBoard
from .config_dialogs import AudioDialog, GraphicsDialog
from .controllers import CONTROLLERS, Controller, controller_for
from .cores_dialog import CoresDialog
from .input_dialog import InputDialog
from .player_card import PlayerCard
from .session import SessionConfig, SessionPlayer

TRIAL_TIMEOUT_MS = 10000
GRID_LABELS = {(2, 1): "Side by side", (1, 2): "Stacked"}
NO_DEVICE = "No input device"


OSD_ITEMS = [("fps", "Frame rate"), ("frame_time", "Frame time"), ("inputs", "Input display"),
             ("audio", "Audible player"), ("core_info", "Core and game")]


def _swatch(color: str, size: int = 12) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(QColor(color))
    return QIcon(pm)


class _RecentDelegate(QStyledItemDelegate):
    """Recent games list: show "name — folder" instead of the full path, grey if missing."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        path = index.data(Qt.UserRole) or ""
        if path:
            p = Path(path)
            option.text = f"{p.name}    —    {p.parent}"
            if not p.is_file():
                option.palette.setColor(option.palette.ColorRole.Text, QColor(theme.MUTED))


class LauncherWindow(QMainWindow):
    startRequested = Signal(object)  # SessionConfig

    def __init__(self, settings: Settings, pads: GamepadManager):
        super().__init__()
        self.s = settings
        self.pads = pads
        self.cores: list[CoreInfo] = []
        self.layout_model = (Layout.from_dict(self.s.layout, len(self.s.active)) if self.s.layout
                             else Layout.default(len(self.s.active)))
        self.cards: dict[int, PlayerCard] = {}
        self.controller: Controller = CONTROLLERS["generic"]
        self.rom_state: tuple[str, str] = ("idle", "")
        self._trial: QProcess | None = None
        self._trial_cache: dict[tuple, tuple[str, str]] = {}
        self._warnings: list[str] = []
        self._held: set[int] = set()
        self._loading = True

        self.setWindowTitle("MultiTap")
        self.setMinimumSize(860, 560)
        avail = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1240, int(avail.width() * 0.92)), min(860, int(avail.height() * 0.92)))
        self._save_timer = QTimer(self, singleShot=True, interval=400,
                                  timeout=lambda: settings_mod.save(self.s))

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)
        self.setCentralWidget(central)

        # ---- emulator + game ---------------------------------------------
        top = QGridLayout()
        top.setHorizontalSpacing(10)
        top.setVerticalSpacing(8)
        top.setColumnStretch(1, 1)
        emu_label = QLabel("Emulator")
        game_label = QLabel("Game")
        for lbl in (emu_label, game_label):
            lbl.setObjectName("fieldLabel")
            lbl.setMinimumWidth(70)
        self.core_combo = QComboBox()
        self.core_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.core_combo.setFocusPolicy(Qt.NoFocus)
        browse_core = QPushButton("Browse…")
        browse_core.setToolTip("Add a libretro core (*_libretro.so) from anywhere on disk")
        get_cores = QPushButton("Get cores…")
        get_cores.setToolTip("Download emulator cores from the libretro build server")
        self.game_combo = QComboBox()
        self.game_combo.setEditable(True)
        self.game_combo.setInsertPolicy(QComboBox.NoInsert)
        self.game_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.game_combo.lineEdit().setPlaceholderText("Choose a game file…")
        self.game_combo.setItemDelegate(_RecentDelegate(self.game_combo))
        self.game_combo.setCompleter(None)
        self.game_combo.setToolTip("Type a path, or open the list for recently played games")
        browse_rom = QPushButton("Browse…")
        for b in (browse_core, get_cores, browse_rom):
            b.setFocusPolicy(Qt.NoFocus)
        top.addWidget(emu_label, 0, 0)
        top.addWidget(self.core_combo, 0, 1)
        top.addWidget(browse_core, 0, 2)
        top.addWidget(get_cores, 0, 3)
        top.addWidget(game_label, 1, 0)
        top.addWidget(self.game_combo, 1, 1)
        top.addWidget(browse_rom, 1, 2)
        self.rom_status = QLabel()
        self.rom_status.setObjectName("caption")
        self.rom_status.setWordWrap(True)
        top.addWidget(self.rom_status, 2, 1, 1, 3)
        root.addLayout(top)

        # ---- players --------------------------------------------------------
        self.board = PlayerBoard()
        root.addWidget(self.board, 1)

        # ---- bottom bar -----------------------------------------------------
        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        auto = QPushButton("Auto-assign controllers")
        auto.setToolTip("Give each player a connected controller, then the keyboard sets")
        self.pad_note = QLabel()
        self.pad_note.setObjectName("caption")
        split_label = QLabel("Split")
        split_label.setObjectName("fieldLabel")
        self.grid_combo = QComboBox()
        self.grid_combo.setFocusPolicy(Qt.NoFocus)
        self.grid_combo.setMinimumWidth(130)
        reset_layout = QPushButton("Reset layout")
        reset_layout.setToolTip("Put every player back in a single cell, in order")
        self.issues = QLabel()
        self.issues.setStyleSheet(f"color:{theme.DANGER};")
        self.issues.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.issues.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.start_btn = QPushButton("START")
        self.start_btn.setObjectName("startButton")
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.setMinimumHeight(46)
        for w in (auto, reset_layout, self.start_btn):
            w.setFocusPolicy(Qt.NoFocus)
        bottom.addWidget(auto)
        bottom.addWidget(self.pad_note)
        bottom.addSpacing(18)
        bottom.addWidget(split_label)
        bottom.addWidget(self.grid_combo)
        bottom.addWidget(reset_layout)
        bottom.addWidget(self.issues, 1)
        bottom.addWidget(self.start_btn)
        root.addLayout(bottom)

        # ---- wiring ---------------------------------------------------------
        browse_core.clicked.connect(self._browse_core)
        get_cores.clicked.connect(self._get_cores)
        browse_rom.clicked.connect(self._browse_rom)
        self.game_combo.lineEdit().editingFinished.connect(
            lambda: self._set_rom(self.game_combo.currentText().strip()))
        self.game_combo.activated.connect(
            lambda i: self._set_rom(self.game_combo.itemData(i, Qt.UserRole) or ""))
        self.core_combo.currentIndexChanged.connect(self._on_core_changed)
        self.grid_combo.activated.connect(self._on_grid_changed)
        reset_layout.clicked.connect(self._reset_layout)
        self.board.addRequested.connect(self._add_player)
        self.board.layoutEdited.connect(self._on_layout_edited)
        auto.clicked.connect(self._auto_assign)
        self.start_btn.clicked.connect(self._start)
        pads.devicesChanged.connect(self._refresh_devices)
        pads.activity.connect(self._on_pad_activity)
        self._input_timer = QTimer(self, interval=33, timeout=self._poll_inputs)
        QApplication.instance().installEventFilter(self)
        self._build_menus()

        # ---- restore state ------------------------------------------------
        self._sync_board()
        self._refresh_devices()
        self._loading = False
        self.reload_cores(select=self.s.core_path)
        self._input_timer.start()

    # ---- menu bar -------------------------------------------------------------
    # Submenus are rebuilt each time they open, so they always match the controls on screen.
    def _build_menus(self):
        bar = self.menuBar()
        file = bar.addMenu("&File")
        self.emulator_menu = file.addMenu("&Emulator")
        self.emulator_menu.aboutToShow.connect(self._fill_emulator_menu)
        file.addAction("&Add core…", self._browse_core)
        file.addAction("&Get cores…", self._get_cores)
        file.addSeparator()
        file.addAction("&Open game…", QKeySequence.Open, self._browse_rom)
        self.recent_menu = file.addMenu("&Recent games")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        file.addSeparator()
        self.start_action = file.addAction("&Start", QKeySequence("Ctrl+Return"), self._start)
        self.start_action.setEnabled(False)
        file.addSeparator()
        file.addAction("&Quit", QKeySequence("Ctrl+Q"), QApplication.quit)

        self.inputs_menu = bar.addMenu("&Inputs")
        self.inputs_menu.aboutToShow.connect(self._fill_inputs_menu)

        config = bar.addMenu("&Config")
        config.addAction("&Graphics…", self._configure_graphics)
        self.osd_menu = config.addMenu("&On-screen display")
        self.osd_menu.aboutToShow.connect(self._fill_osd_menu)
        config.addAction("&Audio…", self._configure_audio)
        config.addSeparator()
        config.addAction("&BIOS / firmware folder…", self._choose_bios)

    @staticmethod
    def _radio(menu: QMenu, items, on_pick):
        """Add exclusive checkable actions: items are (label, value, checked, enabled)."""
        group = QActionGroup(menu)
        for label, value, checked, enabled in items:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(checked)
            act.setEnabled(enabled)
            act.triggered.connect(lambda _=False, v=value: on_pick(v))
            group.addAction(act)

    def _fill_emulator_menu(self):
        m = self.emulator_menu
        m.clear()
        if not self.cores:
            m.addAction("No cores found").setEnabled(False)
            return
        current = self.core_combo.currentIndex()
        self._radio(m, [(self.core_combo.itemText(i), i, i == current, not c.error)
                        for i, c in enumerate(self.cores)], self.core_combo.setCurrentIndex)

    def _fill_recent_menu(self):
        m = self.recent_menu
        m.clear()
        core = self.current_core()
        recents = self.s.recent_for(core.core_id) if core else []
        for r in recents:
            p = Path(r["path"])
            act = m.addAction(f"{p.name}  —  {p.parent}")
            act.setEnabled(p.is_file())
            act.triggered.connect(lambda _=False, path=str(p): self._set_rom(path))
        if not recents:
            m.addAction("No recent games").setEnabled(False)

    def _fill_inputs_menu(self):
        m = self.inputs_menu
        m.clear()
        for slot in self.s.active:
            card = self.cards.get(slot)
            if card is None:
                continue
            name = self.s.players[slot].name
            sub = m.addMenu(_swatch(player_color(slot)),
                            f"Player {slot + 1}" + (f" — {name}" if name else ""))
            devices = sub.addMenu("Device")
            combo = card.device
            self._radio(devices, [(combo.itemText(j), j, j == combo.currentIndex(), True)
                                  for j in range(combo.count())], combo.setCurrentIndex)
            configure = sub.addAction("Configure buttons…",
                                      lambda s=slot: self._configure_player(s))
            if not self.s.players[slot].device:
                configure.setEnabled(False)
                configure.setText("Configure buttons… (choose a device first)")
        m.addSeparator()
        m.addAction("Auto-assign controllers", self._auto_assign)

    def _fill_osd_menu(self):
        m = self.osd_menu
        m.clear()
        osd = self.s.osd
        note = m.addAction("Shown by the native renderer (not available yet)")
        note.setEnabled(False)
        m.addSeparator()

        def toggle(label, attr):
            act = m.addAction(label)
            act.setCheckable(True)
            act.setChecked(getattr(osd, attr))
            act.toggled.connect(lambda on: self._set_osd(attr, on))
            return act

        toggle("Show on-screen display", "enabled")
        m.addSeparator()
        for attr, label in OSD_ITEMS:
            toggle(label, attr).setEnabled(osd.enabled)
        pos = m.addMenu("Position")
        pos.setEnabled(osd.enabled)

        def set_position(v):
            osd.position = v
            self._save_later()
        self._radio(pos, [(p.replace("-", " ").capitalize(), p, p == osd.position, True)
                          for p in OSD_POSITIONS], set_position)

    def _set_osd(self, attr: str, on: bool):
        setattr(self.s.osd, attr, on)
        self._save_later()

    def _configure_graphics(self):
        dlg = GraphicsDialog(self.s.graphics, self)
        if dlg.exec():
            self.s.graphics = dlg.result_settings()
            self._save_later()

    def _configure_audio(self):
        dlg = AudioDialog(self.s.audio, self)
        if dlg.exec():
            self.s.audio = dlg.result_settings()
            self._save_later()

    # ---- persistence ------------------------------------------------------
    def _save_later(self):
        if not self._loading:
            self._save_timer.start()

    def flush_settings(self):
        self._save_timer.stop()
        settings_mod.save(self.s)

    # ---- cores --------------------------------------------------------------
    def current_core(self) -> CoreInfo | None:
        i = self.core_combo.currentIndex()
        return self.cores[i] if 0 <= i < len(self.cores) else None

    def reload_cores(self, select: str = ""):
        self.setCursor(Qt.WaitCursor)
        try:
            self.cores = discovery.scan(self.s.extra_core_paths)
        finally:
            self.unsetCursor()
        self.core_combo.blockSignals(True)
        self.core_combo.clear()
        model = self.core_combo.model()
        idx = -1
        for i, c in enumerate(self.cores):
            self.core_combo.addItem(c.label)
            item = model.item(i)
            if c.error:
                item.setForeground(QColor("#6b7080"))
                item.setToolTip(c.error)
            else:
                exts = ", ".join("." + e for e in c.extensions[:14])
                item.setToolTip(f"{c.path}\nPlays: {exts or '?'}")
            if c.path == select or (idx < 0 and not select and not c.error):
                idx = i
        if not self.cores:
            self.core_combo.addItem("No cores found — use “Get cores…”")
        self.core_combo.setCurrentIndex(max(idx, 0))
        self.core_combo.blockSignals(False)
        self._on_core_changed()

    def _on_core_changed(self, *_):
        core = self.current_core()
        if core:
            self.s.core_path = core.path
            self.core_combo.setToolTip(core.error or core.path)
        self._refresh_recent()
        self._update_controller()
        self._validate_rom()
        self._save_later()

    def _browse_core(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a libretro core", str(Path.home()),
                                              "libretro cores (*_libretro.so *.so)")
        if not path:
            return
        if path not in self.s.extra_core_paths:
            self.s.extra_core_paths.append(path)
        self.reload_cores(select=path)

    def _get_cores(self):
        dlg = CoresDialog(self)
        if dlg.exec() and dlg.downloaded:
            self.reload_cores(select=str(installed_path(dlg.downloaded[0])))

    # ---- game ---------------------------------------------------------------
    def rom_path(self) -> str:
        return self.game_combo.currentText().strip()

    def _refresh_recent(self):
        core = self.current_core()
        self.game_combo.blockSignals(True)
        self.game_combo.clear()
        if core:
            for r in self.s.recent_for(core.core_id):
                p = r.get("path", "")
                self.game_combo.addItem(p, p)
                self.game_combo.setItemData(self.game_combo.count() - 1,
                                            p if os.path.isfile(p) else p + "\n(file missing)",
                                            Qt.ToolTipRole)
        self.game_combo.setEditText(self.s.last_rom.get(core.core_id, "") if core else "")
        self.game_combo.blockSignals(False)

    def _browse_rom(self):
        core = self.current_core()
        exts = list(core.extensions) if core else []
        if core and not core.block_extract:
            exts += [e for e in ARCHIVE_EXTS if e not in exts]
        pattern = " ".join(f"*.{e}" for e in exts) or "*"
        start = self.rom_path() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a game", str(Path(start).parent if Path(start).is_file() else start),
            f"Games for this emulator ({pattern});;All files (*)")
        if path:
            self._set_rom(path)

    def _set_rom(self, path: str):
        if not path and not self.rom_path():
            return
        self.game_combo.setEditText(path)
        core = self.current_core()
        if core:
            self.s.last_rom[core.core_id] = path
            self._save_later()
        self._update_controller()
        self._validate_rom()

    def _set_rom_state(self, state: str, msg: str):
        self.rom_state = (state, msg)
        icon = {"ok": "✓", "bad": "✕", "checking": "…", "idle": ""}[state]
        color = {"ok": theme.OK, "bad": theme.DANGER, "checking": theme.WARN,
                 "idle": theme.MUTED}[state]
        self.rom_status.setText(f"<span style='color:{color}'><b>{icon}</b> {msg}</span>"
                                if msg else "")
        self.rom_status.setVisible(bool(msg))
        notes = [w for w in self._warnings if w.startswith("Optional")] if state == "ok" else []
        self.rom_status.setToolTip("\n".join(notes))
        self._update_start()

    def _validate_rom(self):
        if self._trial:
            self._trial.kill()
            self._trial = None
        core = self.current_core()
        rom = self.rom_path()
        if not core:
            self._set_rom_state("idle", "Install an emulator core with “Get cores…”, or Browse "
                                        "to a *_libretro.so file.")
            return
        if core.error:
            self._set_rom_state("bad", "This emulator core can't be used: " + core.error)
            return
        if not rom:
            self._set_rom_state("idle", "")
            return
        check = quick_check(rom, core.extensions, core.block_extract, core.firmware,
                            self.s.effective_system_dir())
        if not check.ok:
            self._set_rom_state("bad", "; ".join(check.errors))
            return
        self._warnings = check.warnings
        try:
            st = os.stat(rom)
            key = (core.path, rom, st.st_mtime, st.st_size)
        except OSError:
            key = None
        if key in self._trial_cache:
            self._set_rom_state(*self._trial_cache[key])
            return
        self._set_rom_state("checking", "Checking that the game loads…")
        proc = QProcess(self)
        qenv = QProcessEnvironment()
        for k, v in probe_env().items():
            qenv.insert(k, v)
        proc.setProcessEnvironment(qenv)
        argv = probe_argv("trial", core.path, rom, str(self.s.effective_system_dir()))
        proc.finished.connect(lambda *_: self._trial_done(proc, key))
        QTimer.singleShot(TRIAL_TIMEOUT_MS, lambda: proc.state() != QProcess.NotRunning
                          and proc.kill())
        self._trial = proc
        proc.start(argv[0], argv[1:])

    def _trial_done(self, proc: QProcess, key):
        if proc is not self._trial:
            return
        self._trial = None
        out = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
        lines = [l for l in out.splitlines() if l.startswith("{")]
        result = None
        if lines:
            try:
                result = json.loads(lines[-1])
            except ValueError:
                pass
        if result is None:
            state = ("bad", "The emulator crashed or timed out while loading this game")
        elif result.get("ok"):
            # Optional firmware is common and harmless; keep it to the tooltip.
            extra = [w for w in self._warnings if not w.startswith("Optional")]
            if result.get("warning"):
                extra.append(result["warning"])
            state = ("ok", "Playable" + (" — " + "; ".join(extra) if extra else ""))
        else:
            state = ("bad", result.get("error") or "Not playable with this emulator")
        if key:
            self._trial_cache[key] = state
        self._set_rom_state(*state)

    def _update_controller(self):
        core = self.current_core()
        ctrl = (controller_for(core.core_id, core.systems, self.rom_path()) if core
                else CONTROLLERS["generic"])
        self.controller = ctrl
        for card in self.cards.values():
            card.view.set_controller(ctrl)

    # ---- players / layout ---------------------------------------------------
    def _make_card(self, slot: int) -> PlayerCard:
        card = PlayerCard(slot)
        card.name.setText(self.s.players[slot].name)
        card.view.set_controller(self.controller)
        card.nameEdited.connect(self._on_name_edited)
        card.closeClicked.connect(self._remove_player)
        card.configureClicked.connect(self._configure_player)
        card.device.currentIndexChanged.connect(lambda _=0, s=slot: self._on_device_changed(s))
        self._fill_devices(card)
        return card

    def _sync_board(self):
        """Create/destroy cards to match the active slots, then hand everything to the board."""
        for slot in list(self.cards):
            if slot not in self.s.active:
                self.cards.pop(slot).deleteLater()
        for slot in self.s.active:
            if slot not in self.cards:
                self.cards[slot] = self._make_card(slot)
        self.board.set_players(self.layout_model, self.s.active, self.cards)
        self._on_layout_edited()

    def _refresh_grid_combo(self):
        n = len(self.s.active)
        grids = list(PRESETS[n])
        cur = (self.layout_model.cols, self.layout_model.rows)
        if cur not in grids:
            grids.append(cur)
        self.grid_combo.blockSignals(True)
        self.grid_combo.clear()
        for cols, rows in grids:
            label = GRID_LABELS.get((cols, rows)) if n == 2 else None
            self.grid_combo.addItem(label or f"{cols} × {rows}", (cols, rows))
        self.grid_combo.setCurrentIndex(grids.index(cur))
        self.grid_combo.setEnabled(len(grids) > 1)
        self.grid_combo.blockSignals(False)

    def _on_grid_changed(self, *_):
        grid = self.grid_combo.currentData()
        if grid and tuple(grid) != (self.layout_model.cols, self.layout_model.rows):
            self.layout_model = Layout.default(len(self.s.active), tuple(grid))
            self._sync_board()

    def _reset_layout(self):
        n = len(self.s.active)
        cur = (self.layout_model.cols, self.layout_model.rows)
        self.layout_model = Layout.default(n, cur if cur in PRESETS[n] else None)
        self._sync_board()

    def _on_layout_edited(self):
        self.s.layout = self.layout_model.to_dict()
        self._refresh_grid_combo()
        self._update_start()
        self._save_later()

    def _add_player(self, cell):
        free = [s for s in range(MAX_PLAYERS) if s not in self.s.active]
        if not free or not self.layout_model.add_player(cell):
            return
        slot = free[0]
        self.s.active.append(slot)
        player = self.s.players[slot]
        if player.device is None:
            player.device = self._free_device()
            player.kbd_bindings = {}
        self._sync_board()

    def _remove_player(self, slot: int):
        if slot not in self.s.active or len(self.s.active) <= 1:
            return
        self.layout_model.remove_player(self.s.active.index(slot))
        self.s.active.remove(slot)
        self.s.players[slot] = PlayerSettings()
        self._sync_board()

    def _free_device(self) -> dict | None:
        used = {device_key(self.s.players[s].device) for s in self.s.active}
        for opt in self._device_options():
            if opt and device_key(opt) not in used:
                return opt
        return None

    def _on_name_edited(self, slot: int, name: str):
        self.s.players[slot].name = name
        self._save_later()

    def _choose_bios(self):
        d = QFileDialog.getExistingDirectory(self, "BIOS / firmware folder",
                                             str(self.s.effective_system_dir()))
        if d:
            self.s.system_dir = d
            self._save_later()
            self._validate_rom()

    # ---- devices -------------------------------------------------------------
    def _device_options(self) -> list[dict | None]:
        opts: list[dict | None] = [{"kind": "keyboard", "set": "A"},
                                   {"kind": "keyboard", "set": "B"}]
        pads = [p.as_device() for p in self.pads.pads()]
        return [None] + pads + opts

    def _fill_devices(self, card: PlayerCard):
        options = self._device_options()
        connected = {device_key(o) for o in options if o}
        saved = self.s.players[card.slot].device
        combo = card.device
        combo.blockSignals(True)
        combo.clear()
        for o in options:
            combo.addItem(device_label(o) if o else NO_DEVICE, o)
        if saved and device_key(saved) not in connected:
            combo.addItem(device_label(saved) + " (disconnected)", saved)
        for j in range(combo.count()):
            if device_key(combo.itemData(j)) == device_key(saved):
                combo.setCurrentIndex(j)
                break
        combo.blockSignals(False)

    def _refresh_devices(self):
        pads = self.pads.pads()
        unmapped = self.pads.unmapped_names()
        if self.pads.error:
            note = f"<span style='color:{theme.DANGER}'>Controllers unavailable</span>"
            self.pad_note.setToolTip(self.pads.error)
        else:
            note = (f"{len(pads)} controller{'s' if len(pads) != 1 else ''} connected" if pads
                    else "No controllers connected")
            if unmapped:
                note += f" · <span style='color:{theme.WARN}'>{len(unmapped)} unmapped</span>"
            self.pad_note.setToolTip("No button mapping for: " + ", ".join(unmapped)
                                     if unmapped else "")
        self.pad_note.setText(note)
        for card in self.cards.values():
            self._fill_devices(card)
        self._update_start()

    def _on_device_changed(self, slot: int):
        card = self.cards.get(slot)
        if card is None:
            return
        dev = card.device.currentData()
        player = self.s.players[slot]
        if device_key(player.device) != device_key(dev) and (dev or {}).get("kind") == "keyboard":
            player.kbd_bindings = {}
        player.device = dev
        self._update_start()
        self._save_later()

    def _auto_assign(self):
        pads = self.pads.pads()
        keyboards = [{"kind": "keyboard", "set": k} for k in "AB"]
        for i, slot in enumerate(self.s.active):
            player = self.s.players[slot]
            if i < len(pads):
                player.device = pads[i].as_device()
            elif i - len(pads) < len(keyboards):
                player.device = keyboards[i - len(pads)]
                player.kbd_bindings = {}
        self._refresh_devices()
        self._save_later()

    def _on_pad_activity(self, key: str, _element: str):
        for slot in self.s.active:
            if device_key(self.s.players[slot].device) == key and slot in self.cards:
                self.cards[slot].flash()

    def _configure_player(self, slot: int):
        player = self.s.players[slot]
        if not player.device:
            self.cards[slot].device.showPopup()
            return
        dlg = InputDialog(slot, player, self.pads, self)
        if dlg.exec():
            if player.device.get("kind") == "keyboard":
                player.kbd_bindings = dlg.result_bindings()
            else:
                player.pad_bindings = dlg.result_bindings()
            self._update_start()
            self._save_later()

    # ---- live input preview ----------------------------------------------------
    def _poll_inputs(self):
        if not self.isVisible():
            return
        connected = self.pads.connected_keys()
        for slot in self.s.active:
            card = self.cards.get(slot)
            if card is None:
                continue
            player = self.s.players[slot]
            dev = player.device
            pressed: set[str] = set()
            if dev and dev.get("kind") == "keyboard":
                pressed = {b for b, k in keyboard_bindings(player).items() if k in self._held}
            elif dev and device_key(dev) in connected:
                buttons, axes = self.pads.state(device_key(dev))
                bits, _ = pad_state_bits(buttons, axes, pad_bindings(player))
                pressed = {b for b, i in RETROPAD.items() if bits >> i & 1}
            card.view.set_pressed(frozenset(pressed))

    def eventFilter(self, obj, e):
        t = e.type()
        if t in (QEvent.KeyPress, QEvent.KeyRelease) and not e.isAutoRepeat():
            if t == QEvent.KeyRelease:
                self._held.discard(int(e.key()))
            elif self.isActiveWindow() and not self._typing():
                self._held.add(int(e.key()))
        elif t == QEvent.WindowDeactivate and obj is self:
            self._held.clear()
        return False

    @staticmethod
    def _typing() -> bool:
        w = QApplication.focusWidget()
        return isinstance(w, (QLineEdit, QAbstractSpinBox)) or (
            isinstance(w, QComboBox) and w.isEditable())

    def focusNextPrevChild(self, _next: bool) -> bool:
        return False  # Tab is a valid key binding; don't move focus with it

    # ---- start ----------------------------------------------------------------
    def _blocking_issues(self) -> list[str]:
        issues = []
        core = self.current_core()
        if not core:
            issues.append("Choose an emulator core")
        elif core.error:
            issues.append("The selected emulator core can't be used")
        state = self.rom_state[0]
        if state == "idle":
            issues.append("Choose a game")
        elif state == "checking":
            issues.append("Still checking the game…")
        elif state == "bad":
            issues.append("The game isn't playable with this emulator")
        statuses = validate_players(self.s.players, self.s.active, self.pads.connected_keys())
        for slot, st in statuses.items():
            if slot in self.cards:
                self.cards[slot].set_status(st.ok, st.problems, self.s.players[slot].device is None)
            for prob in st.problems:
                issues.append(f"Player {slot + 1}: {prob}")
        return issues

    def _update_start(self):
        if self._loading:
            return
        issues = self._blocking_issues()
        self.start_btn.setEnabled(not issues)
        self.start_action.setEnabled(not issues)
        self.start_btn.setToolTip("\n".join(issues) if issues else "Launch the game for every player")
        more = f"  (+{len(issues) - 1} more)" if len(issues) > 1 else ""
        self.issues.setText(issues[0] + more if issues else "")
        self.issues.setToolTip("\n".join(issues))

    def _start(self):
        if self._blocking_issues():
            return
        core = self.current_core()
        rom = self.rom_path()
        players = []
        for slot in self.s.active:
            p = self.s.players[slot]
            binds = keyboard_bindings(p) if p.device.get("kind") == "keyboard" else pad_bindings(p)
            players.append(SessionPlayer(slot, p.name, dict(p.device), binds))
        self.s.add_recent(core.core_id, rom)
        self._refresh_recent()
        self.flush_settings()
        cfg = SessionConfig(core.path, core.core_id, rom, str(self.s.effective_system_dir()),
                            Layout.from_dict(self.layout_model.to_dict(), len(players)), players,
                            replace(self.s.graphics), replace(self.s.osd), replace(self.s.audio))
        self.startRequested.emit(cfg)

    def closeEvent(self, e):
        self.flush_settings()
        super().closeEvent(e)
