"""The launcher's main area: player cards tiled like the real screen, with blank "add" cells.

Drag a card onto another to swap, or onto a blank cell to move it. Hovering a card shows arrows
that grow it into neighbouring blank cells; right-click a card to grow or shrink it.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QMenu, QSizePolicy, QWidget

from ..colors import MAX_PLAYERS
from ..layout.model import Layout
from . import theme
from .player_card import MIME, PlayerCard

GAP = 14
ADD_COLUMN_W = 58
DIRS = ("up", "down", "left", "right")


class AddTile(QWidget):
    """Dashed placeholder with a plus: a blank cell, or the thin column right of a full grid."""
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cell: tuple[int, int] | None = None
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Add a player")

    def enterEvent(self, _e):
        self._hover = True
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setBrush(QColor(255, 255, 255, 14 if self._hover else 6))
        pen = QPen(QColor("#596071" if self._hover else "#363b47"), 2, Qt.DashLine)
        pen.setDashPattern([4, 4])
        p.setPen(pen)
        p.drawRoundedRect(r, 16, 16)
        size = max(24.0, min(56.0, min(r.width(), r.height()) * 0.22))
        thick = max(5.0, size / 4.5)
        c = r.center()
        plus = QPainterPath()
        plus.addRoundedRect(QRectF(c.x() - size / 2, c.y() - thick / 2, size, thick), thick / 2, thick / 2)
        plus.addRoundedRect(QRectF(c.x() - thick / 2, c.y() - size / 2, thick, size), thick / 2, thick / 2)
        p.fillPath(plus.simplified(), QColor("#c9ceda" if self._hover else "#7d8494"))


class _GrowHandle(QWidget):
    clicked = Signal(str)
    W, H = 34, 18

    def __init__(self, direction: str, parent=None):
        super().__init__(parent)
        self.direction = direction
        self._hover = False
        vertical = direction in ("up", "down")
        self.resize(self.W, self.H) if vertical else self.resize(self.H, self.W)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"Grow {direction} into the blank space")
        self.hide()

    def enterEvent(self, _e):
        self._hover = True
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.direction)

    def paintEvent(self, _e):
        w, h = self.width(), self.height()
        pts = {
            "down": [(3, 3), (w - 3, 3), (w / 2, h - 3)],
            "up": [(3, h - 3), (w - 3, h - 3), (w / 2, 3)],
            "right": [(3, 3), (3, h - 3), (w - 3, h / 2)],
            "left": [(w - 3, 3), (w - 3, h - 3), (3, h / 2)],
        }[self.direction]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(0, 0, 0, 120), 1.5))
        p.setBrush(QColor("#ffffff" if self._hover else "#b9bfcc"))
        p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))


class _DropOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.target: QRect | None = None
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _e):
        if self.target is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#ffffff"), 2.5, Qt.DashLine)
        pen.setDashPattern([3, 3])
        p.setPen(pen)
        p.setBrush(QColor(255, 255, 255, 28))
        p.drawRoundedRect(QRectF(self.target).adjusted(2, 2, -2, -2), 16, 16)


class PlayerBoard(QWidget):
    addRequested = Signal(object)  # (row, col) of a blank cell, or None for the add column
    layoutEdited = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_model = Layout.default(1)
        self.slots: list[int] = []
        self.cards: dict[int, PlayerCard] = {}
        self._tiles: list[AddTile] = []
        self._column = AddTile(self)
        self._column.setToolTip("Add a player (adds a column)")
        self._column.clicked.connect(lambda: self.addRequested.emit(None))
        self._handles = {d: _GrowHandle(d, self) for d in DIRS}
        for h in self._handles.values():
            h.clicked.connect(self._grow_hovered)
        self._hovered: int | None = None
        self._dragging = False
        self._overlay = _DropOverlay(self)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(480, 300)
        self._hover_timer = QTimer(self, interval=60, timeout=self._update_handles)
        self._hover_timer.start()

    # ---- state ---------------------------------------------------------------
    def set_players(self, layout: Layout, slots: list[int], cards: dict[int, PlayerCard]) -> None:
        self.layout_model = layout
        self.slots = list(slots)
        for slot, card in cards.items():
            if card.parent() is not self:
                card.setParent(self)
                card.installEventFilter(self)
            card.set_can_close(len(slots) > 1)
            card.show()
        self.cards = cards
        self.relayout()

    # ---- geometry ------------------------------------------------------------
    def _needs_column(self) -> bool:
        return not self.layout_model.free_cells() and self.layout_model.count < MAX_PLAYERS

    def _grid_size(self) -> tuple[int, int]:
        w = self.width() - (ADD_COLUMN_W + GAP if self._needs_column() else 0)
        return max(1, w), max(1, self.height())

    def relayout(self) -> None:
        lm = self.layout_model
        gw, gh = self._grid_size()
        for slot, (x, y, w, h) in zip(self.slots, lm.pixel_rects(gw, gh, GAP)):
            self.cards[slot].setGeometry(x, y, w, h)
        free = lm.free_cells()
        while len(self._tiles) < len(free):
            tile = AddTile(self)
            tile.clicked.connect(lambda t=tile: self.addRequested.emit(t.cell))
            self._tiles.append(tile)
        cells = lm.cell_rects(gw, gh, GAP)
        for tile, cell in zip(self._tiles, free):
            tile.cell = cell
            tile.setGeometry(*cells[cell])
            tile.show()
        for tile in self._tiles[len(free):]:
            tile.hide()
        if self._needs_column():
            self._column.setGeometry(gw + GAP, 0, ADD_COLUMN_W, gh)
            self._column.show()
        else:
            self._column.hide()
        self._overlay.setGeometry(self.rect())
        self._overlay.raise_()
        for h in self._handles.values():
            h.raise_()
        self._update_handles()

    def resizeEvent(self, _e):
        self.relayout()

    def _card_rect(self, index: int) -> QRect:
        return self.cards[self.slots[index]].geometry()

    def _cell_at(self, pos) -> tuple[int, int] | None:
        gw, gh = self._grid_size()
        if not (0 <= pos.x() < gw and 0 <= pos.y() < gh):
            return None
        lm = self.layout_model
        return (min(int(pos.y() / gh * lm.rows), lm.rows - 1),
                min(int(pos.x() / gw * lm.cols), lm.cols - 1))

    # ---- grow handles ----------------------------------------------------------
    def _update_handles(self) -> None:
        if not self.isVisible():
            return
        pos = self.mapFromGlobal(QCursor.pos())
        hovered = None
        if not self._dragging and self.rect().contains(pos):
            for i in range(len(self.slots)):
                if self._card_rect(i).adjusted(-GAP, -GAP, GAP, GAP).contains(pos):
                    hovered = i
                    break
        self._hovered = hovered
        for d, h in self._handles.items():
            if hovered is None or not self.layout_model.can_grow(hovered, d):
                h.hide()
                continue
            r = self._card_rect(hovered)
            c = r.center()
            centre = {"down": (c.x(), r.bottom() + GAP / 2 + 1), "up": (c.x(), r.top() - GAP / 2),
                      "left": (r.left() - GAP / 2, c.y()), "right": (r.right() + GAP / 2 + 1, c.y())}[d]
            h.move(int(centre[0] - h.width() / 2), int(centre[1] - h.height() / 2))
            h.show()
            h.raise_()

    def _grow_hovered(self, direction: str) -> None:
        if self._hovered is not None and self.layout_model.grow(self._hovered, direction):
            self.layoutEdited.emit()
            self.relayout()

    # ---- context menu ------------------------------------------------------------
    def eventFilter(self, obj, event):
        if event.type() == QEvent.ContextMenu and isinstance(obj, PlayerCard) \
                and obj.slot in self.slots:
            self._card_menu(self.slots.index(obj.slot), event.globalPos())
            return True
        return False

    def _card_menu(self, index: int, global_pos) -> None:
        lm = self.layout_model
        menu = QMenu(self)
        labels = {"up": "Grow up", "down": "Grow down", "left": "Grow left", "right": "Grow right"}
        for d in DIRS:
            act = menu.addAction(labels[d])
            act.setEnabled(lm.can_grow(index, d))
            act.setData(d)
        menu.addSeparator()
        shrink = menu.addAction("Shrink to one cell")
        r = lm.rects[index]
        shrink.setEnabled(r.rowspan > 1 or r.colspan > 1)
        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen is shrink:
            lm.shrink(index)
        else:
            lm.grow(index, chosen.data())
        self.layoutEdited.emit()
        self.relayout()

    # ---- drag and drop -------------------------------------------------------------
    def _drop_target(self, pos) -> tuple[tuple[int, int] | None, QRect | None]:
        cell = self._cell_at(pos)
        if cell is None:
            return None, None
        owner = self.layout_model.player_at(*cell)
        if owner is not None:
            return cell, self._card_rect(owner)
        gw, gh = self._grid_size()
        return cell, QRect(*self.layout_model.cell_rects(gw, gh, GAP)[cell])

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            self._dragging = True
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        cell, rect = self._drop_target(e.position().toPoint())
        self._overlay.target = rect
        self._overlay.update()
        if cell is None:
            e.ignore()
        else:
            e.acceptProposedAction()

    def dragLeaveEvent(self, _e):
        self._dragging = False
        self._overlay.target = None
        self._overlay.update()

    def dropEvent(self, e):
        self._dragging = False
        self._overlay.target = None
        self._overlay.update()
        try:
            slot = int(bytes(e.mimeData().data(MIME)).decode())
        except ValueError:
            return
        cell, _ = self._drop_target(e.position().toPoint())
        if cell is None or slot not in self.slots:
            return
        self.layout_model.move_to_cell(self.slots.index(slot), *cell)
        e.acceptProposedAction()
        self.layoutEdited.emit()
        self.relayout()
