"""Fullscreen play session: one tile per player, overlays, hotkeys and audio rotation."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from .. import paths
from ..colors import player_color
from ..input.devices import GamepadManager, pad_state_bits
from ..input.mapping import RETROPAD, device_key
from ..layout.model import Layout
from ..worker.host import WorkerHandle, spawn_worker, stop_workers
from ..worker.shm import STATE_ERROR

QIMAGE_FORMATS = {0: QImage.Format_RGB555, 1: QImage.Format_RGB32, 2: QImage.Format_RGB16}
GAP = 4


@dataclass
class SessionPlayer:
    index: int
    name: str
    device: dict
    bindings: dict


@dataclass
class SessionConfig:
    core_path: str
    core_id: str
    rom_path: str
    system_dir: str
    layout: Layout
    players: list[SessionPlayer] = field(default_factory=list)
    audio_rotation_s: int = 120
    smooth: bool = False


class SessionWindow(QOpenGLWidget):
    exitRequested = Signal(int)  # key event timestamp of the Esc press

    def __init__(self, cfg: SessionConfig, pads: GamepadManager):
        super().__init__()
        self.cfg = cfg
        self.pads = pads
        self.setWindowTitle("MultiTap")
        self.setCursor(Qt.BlankCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.handles: list[WorkerHandle] = []
        self.frames: list[tuple[int, QImage | None, bytes | None]] = []
        self.paused = False
        self.auto_paused = False
        self.audible = 0
        self.disconnected: set[int] = set()
        self.toast = ("", 0.0)
        self._held: set[int] = set()
        self._kbd_map: dict[int, list[tuple[int, int]]] = {}  # qt key -> [(player slot, bit)]
        self._kbd_bits = [0] * len(cfg.players)
        self._pad_players: dict[str, list[int]] = {}
        self._stopped = False

        for slot, p in enumerate(cfg.players):
            if p.device.get("kind") == "keyboard":
                for btn, key in p.bindings.items():
                    self._kbd_map.setdefault(int(key), []).append((slot, 1 << RETROPAD[btn]))
            else:
                self._pad_players.setdefault(device_key(p.device), []).append(slot)

        self._rotate = QTimer(self, interval=max(5, cfg.audio_rotation_s) * 1000,
                              timeout=self._rotate_audio)
        self._poll = QTimer(self, interval=100, timeout=self._poll_workers)
        self.frameSwapped.connect(self.update)

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        cfg = self.cfg
        for slot, p in enumerate(cfg.players):
            save_dir = paths.slot_dir(p.index, cfg.core_id) / "saves"
            h = spawn_worker(p.index, {
                "core_path": cfg.core_path, "rom_path": cfg.rom_path,
                "system_dir": cfg.system_dir, "save_dir": str(save_dir),
                "username": p.name or f"Player {p.index + 1}",
                "audible": slot == self.audible,
            })
            self.handles.append(h)
            self.frames.append((0, None, None))
        self.pads.add_listener(self._on_pad_state)
        self.pads.devicesChanged.connect(self._check_devices)
        for key in self._pad_players:
            self._on_pad_state(key, *self.pads.state(key))
        self._poll.start()
        if len(self.handles) > 1:
            self._rotate.start()
        self._check_devices()

    def stop(self) -> threading.Thread:
        """Stop workers in the background (they flush SRAM); returns the thread doing it."""
        if self._stopped:
            return threading.Thread()
        self._stopped = True
        self._rotate.stop()
        self._poll.stop()
        self.pads.remove_listener(self._on_pad_state)
        try:
            self.pads.devicesChanged.disconnect(self._check_devices)
        except (RuntimeError, TypeError):
            pass
        handles = self.handles
        t = threading.Thread(target=stop_workers, args=(handles,), daemon=True)
        t.start()
        return t

    # ---- control ---------------------------------------------------------
    def _broadcast(self, *msg) -> None:
        for h in self.handles:
            h.send(*msg)

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self._broadcast("pause" if paused else "resume")
        if paused:
            self._rotate.stop()
        elif len(self.handles) > 1:
            self._rotate.start()
        self.update()

    def reset_all(self) -> None:
        self._broadcast("reset")
        self.toast = ("Reset", time.monotonic() + 1.2)

    def _rotate_audio(self) -> None:
        candidates = [i for i, h in enumerate(self.handles) if h.loaded and not h.exited]
        if len(candidates) < 2:
            return
        nxt = next((i for i in candidates if i > self.audible), candidates[0])
        self.handles[self.audible].send("audible", False)
        self.handles[nxt].send("audible", True)
        self.audible = nxt

    def _poll_workers(self) -> None:
        for i, h in enumerate(self.handles):
            h.poll()
        # If the audible player failed to load, hand audio to the first healthy one.
        cur = self.handles[self.audible] if self.handles else None
        if cur and (cur.error or cur.exited):
            for i, h in enumerate(self.handles):
                if h.loaded and not h.exited:
                    h.send("audible", True)
                    self.audible = i
                    break

    def _check_devices(self) -> None:
        connected = self.pads.connected_keys()
        missing = {slot for key, slots in self._pad_players.items() if key not in connected
                   for slot in slots}
        newly = missing - self.disconnected
        self.disconnected = missing
        for slot in newly:
            self.handles[slot].input.write(0)
        if newly and not self.paused:
            self.auto_paused = True
            self.set_paused(True)
        self.update()

    # ---- input -----------------------------------------------------------
    def _on_pad_state(self, key: str, buttons: set, axes: dict) -> None:
        # Runs on the SDL thread: compute bits and write straight into shared memory.
        for slot in self._pad_players.get(key, ()):
            bits, analog = pad_state_bits(buttons, axes, self.cfg.players[slot].bindings)
            if slot < len(self.handles):
                self.handles[slot].input.write(bits, analog)

    def _write_kbd(self) -> None:
        bits = [0] * len(self.cfg.players)
        for key in self._held:
            for slot, bit in self._kbd_map.get(key, ()):
                bits[slot] |= bit
        for slot, b in enumerate(bits):
            if b != self._kbd_bits[slot] and self.cfg.players[slot].device.get("kind") == "keyboard":
                self.handles[slot].input.write(b)
        self._kbd_bits = bits

    def event(self, e):
        # Tab is a legitimate binding; don't let Qt use it for focus changes.
        if e.type() == e.Type.KeyPress and e.key() in (Qt.Key_Tab, Qt.Key_Backtab):
            self.keyPressEvent(e)
            return True
        if e.type() == e.Type.KeyRelease and e.key() in (Qt.Key_Tab, Qt.Key_Backtab):
            self.keyReleaseEvent(e)
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        key = int(e.key())
        if e.isAutoRepeat():
            return
        if key == int(Qt.Key_Escape):
            self.exitRequested.emit(e.timestamp())
            return
        if key == int(Qt.Key_P):
            self.auto_paused = False
            self.set_paused(not self.paused)
            return
        if key == int(Qt.Key_R):
            self.reset_all()
            return
        self._held.add(key)
        self._write_kbd()

    def keyReleaseEvent(self, e):
        if e.isAutoRepeat():
            return
        self._held.discard(int(e.key()))
        self._write_kbd()

    def focusOutEvent(self, e):
        self._held.clear()
        self._write_kbd()
        super().focusOutEvent(e)

    # ---- painting --------------------------------------------------------
    def _latest(self, slot: int) -> QImage | None:
        seq, img, buf = self.frames[slot]
        h = self.handles[slot]
        cur = h.video.header()[0]
        if cur != seq:
            got = h.video.read_frame()
            if got:
                seq, w, hh, pitch, fmt, pixels = got
                qfmt = QIMAGE_FORMATS.get(fmt)
                if qfmt is not None and w and hh:
                    img, buf = QImage(pixels, w, hh, pitch, qfmt), pixels
                self.frames[slot] = (seq, img, buf)
        return img

    def paintGL(self):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        if not self.handles:
            p.end()
            return
        rects = self.cfg.layout.pixel_rects(self.width(), self.height(), GAP)
        p.setRenderHint(QPainter.SmoothPixmapTransform, self.cfg.smooth)
        tiles = []
        for slot, (x, y, w, h) in enumerate(rects[:len(self.handles)]):
            tile = QRectF(x, y, w, h)
            p.fillRect(tile, QColor(10, 10, 12))
            handle = self.handles[slot]
            img = self._latest(slot)
            if img is not None:
                aspect = handle.video.header()[3] or img.width() / max(1, img.height())
                tw, th = (w, w / aspect) if w / h < aspect else (h * aspect, h)
                target = QRectF(x + (w - tw) / 2, y + (h - th) / 2, tw, th)
                p.drawImage(target, img)
            tiles.append((slot, tile, handle, img is not None))
        # XRGB8888 frames carry an undefined (often zero) X byte that the GL engine copies into
        # the framebuffer's alpha. Force everything opaque so the window never turns see-through.
        p.setCompositionMode(QPainter.CompositionMode_DestinationOver)
        p.fillRect(self.rect(), Qt.black)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        for slot, tile, handle, has_frame in tiles:
            self._paint_tile_overlay(p, slot, tile, handle, has_frame)

        if self.paused:
            self._banner(p, "PAUSED", "P resume  ·  R reset  ·  Esc menu (twice to quit)")
        text, until = self.toast
        if text and time.monotonic() < until:
            self._banner(p, text, "", small=True)
        p.end()

    def _paint_tile_overlay(self, p: QPainter, slot: int, tile: QRectF,
                            handle: WorkerHandle, has_frame: bool) -> None:
        player = self.cfg.players[slot]
        size = max(12.0, min(tile.height() / 20, 28.0))
        font = QFont(self.font())
        font.setPixelSize(int(size))
        font.setBold(True)
        p.setFont(font)
        pad = size * 0.4
        color = QColor(player_color(player.index))

        msg = ""
        if handle.error or handle.video.header()[2] == STATE_ERROR:
            msg = f"Player {player.index + 1}: failed to start\n{handle.error}"
        elif not has_frame:
            msg = "Loading…"
        if slot in self.disconnected:
            msg = f"Player {player.index + 1}: controller disconnected\nReconnect, then press P"
        if msg:
            p.setPen(QColor(230, 230, 230))
            p.drawText(tile.adjusted(20, 20, -20, -20), Qt.AlignCenter | Qt.TextWordWrap, msg)

        if player.name:
            fm = p.fontMetrics()
            text = fm.elidedText(player.name, Qt.ElideRight, int(tile.width() * 0.6))
            box = QRectF(tile.left() + pad, tile.bottom() - pad - fm.height() - pad,
                         fm.horizontalAdvance(text) + 2 * pad, fm.height() + pad)
            path = QPainterPath()
            path.addRoundedRect(box, pad, pad)
            p.fillPath(path, QColor(0, 0, 0, 170))
            p.setPen(color)
            p.drawText(box, Qt.AlignCenter, text)

        if slot == self.audible and len(self.handles) > 1:
            s = size * 1.5
            box = QRectF(tile.right() - pad - s - pad, tile.bottom() - pad - s - pad / 2,
                         s + pad, s + pad / 2)
            path = QPainterPath()
            path.addRoundedRect(box, pad, pad)
            p.fillPath(path, QColor(0, 0, 0, 170))
            self._speaker(p, box.adjusted(pad / 2, pad / 4, -pad / 2, -pad / 4))

    @staticmethod
    def _speaker(p: QPainter, r: QRectF) -> None:
        w, h = r.width(), r.height()
        body = QPainterPath()
        body.moveTo(r.left(), r.top() + h * 0.35)
        body.lineTo(r.left() + w * 0.22, r.top() + h * 0.35)
        body.lineTo(r.left() + w * 0.5, r.top() + h * 0.1)
        body.lineTo(r.left() + w * 0.5, r.top() + h * 0.9)
        body.lineTo(r.left() + w * 0.22, r.top() + h * 0.65)
        body.lineTo(r.left(), r.top() + h * 0.65)
        body.closeSubpath()
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.fillPath(body, Qt.white)
        pen = QPen(Qt.white, max(1.5, w * 0.07), Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        c = QPointF(r.left() + w * 0.5, r.center().y())
        for rad in (w * 0.2, w * 0.38):
            p.drawArc(QRectF(c.x() - rad, c.y() - rad, 2 * rad, 2 * rad), -45 * 16, 90 * 16)
        p.restore()

    def _banner(self, p: QPainter, title: str, sub: str, small: bool = False) -> None:
        font = QFont(self.font())
        font.setBold(True)
        font.setPixelSize(int(self.height() / (22 if small else 12)))
        p.setFont(font)
        fm = p.fontMetrics()
        sub_font = QFont(self.font())
        sub_font.setPixelSize(max(14, int(self.height() / 45)))
        sub_h = (fm.height() * 0.6) if sub else 0
        w = max(fm.horizontalAdvance(title), 0) + 80
        if sub:
            p.setFont(sub_font)
            w = max(w, p.fontMetrics().horizontalAdvance(sub) + 80)
        h = fm.height() + sub_h + 30
        box = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        path = QPainterPath()
        path.addRoundedRect(box, 14, 14)
        p.fillPath(path, QColor(0, 0, 0, 200))
        p.setFont(font)
        p.setPen(Qt.white)
        p.drawText(QRectF(box.left(), box.top() + 10, w, fm.height()), Qt.AlignCenter, title)
        if sub:
            p.setFont(sub_font)
            p.setPen(QColor(200, 200, 200))
            p.drawText(QRectF(box.left(), box.top() + 10 + fm.height(), w, sub_h + 10),
                       Qt.AlignCenter, sub)
