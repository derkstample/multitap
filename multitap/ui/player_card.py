"""One player's tile in the launcher: name, device, input check and a live controller view."""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QPainter, QPen, QTransform
from PySide6.QtWidgets import (QApplication, QBoxLayout, QComboBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSizePolicy, QToolButton, QVBoxLayout,
                               QWidget)

from ..colors import player_color
from . import theme
from .controllers import CONTROLLERS, Controller

MIME = "application/x-multitap-slot"


class ControllerView(QWidget):
    """Controller artwork scaled to fit; pressed RetroPad buttons light up red."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.controller: Controller = CONTROLLERS["generic"]
        self.pressed: frozenset[str] = frozenset()
        self.dimmed = False
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(120, 70)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_controller(self, controller: Controller) -> None:
        self.controller = controller
        self.setToolTip(controller.title)
        self.update()

    def set_pressed(self, pressed: frozenset[str]) -> None:
        if pressed != self.pressed:
            self.pressed = pressed
            self.update()

    def set_dimmed(self, dimmed: bool) -> None:
        if dimmed != self.dimmed:
            self.dimmed = dimmed
            self.update()

    def sizeHint(self) -> QSize:
        return QSize(260, 150)

    def paintEvent(self, _e):
        img = self.controller.pixmap_image()
        if img.isNull():
            return
        iw, ih = img.width(), img.height()
        area = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        s = min(area.width() / iw, area.height() / ih)
        target = QRectF(0, 0, iw * s, ih * s)
        target.moveCenter(area.center())
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        if self.pressed:  # under the line art, so the button outlines stay crisp
            to_view = QTransform().translate(target.x(), target.y()).scale(s, s)
            glow = QColor(theme.PRESSED)
            glow.setAlpha(110)
            fill = QColor(theme.PRESSED)
            fill.setAlpha(235)
            for btn in self.pressed:
                for shape in self.controller.buttons.get(btn, ()):
                    path = to_view.map(shape(iw, ih))
                    p.setPen(QPen(glow, 5))
                    p.setBrush(fill)
                    p.drawPath(path)
        p.setOpacity(0.4 if self.dimmed else 1.0)
        p.drawImage(target, img)


class PlayerCard(QFrame):
    nameEdited = Signal(int, str)
    closeClicked = Signal(int)
    configureClicked = Signal(int)

    def __init__(self, slot: int, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.color = player_color(slot)
        self._press: QPoint | None = None
        self.setObjectName("playerCard")
        self.setFocusPolicy(Qt.ClickFocus)
        on = theme.text_on(self.color)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = QWidget()
        self.header.setObjectName("cardHeader")
        self.header.setCursor(Qt.OpenHandCursor)
        self.header.setToolTip("Drag to move or swap this player")
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(14, 6, 6, 6)
        hl.setSpacing(8)
        badge = QLabel(f"P{slot + 1}")
        badge.setObjectName("badge")
        self.name = QLineEdit()
        self.name.setObjectName("nameEdit")
        self.name.setPlaceholderText(f"Player {slot + 1}")
        self.name.setMaxLength(24)
        self.name.setFocusPolicy(Qt.ClickFocus)
        self.name.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.name.setMinimumWidth(80)
        self.name.setMaximumWidth(260)
        self.close_btn = QToolButton()
        self.close_btn.setObjectName("closeButton")
        self.close_btn.setText("✕")
        self.close_btn.setToolTip("Remove this player")
        self.close_btn.setFocusPolicy(Qt.NoFocus)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        hl.addWidget(badge)
        hl.addWidget(self.name, 1)
        hl.addStretch()
        hl.addWidget(self.close_btn)
        root.addWidget(self.header)

        body = QWidget()
        self.lower = QBoxLayout(QBoxLayout.LeftToRight, body)
        self.lower.setContentsMargins(14, 12, 14, 12)
        self.lower.setSpacing(12)
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(10)
        self.device = QComboBox()
        self.device.setFocusPolicy(Qt.NoFocus)
        self.device.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.device.setMinimumContentsLength(8)
        self.configure = QPushButton("Configure")
        self.configure.setObjectName("configureButton")
        self.configure.setFocusPolicy(Qt.NoFocus)
        self.configure.setCursor(Qt.PointingHandCursor)
        self.configure.setToolTip("Rebind buttons and test them")
        self.status = QLabel()
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        sl.addWidget(self.device)
        sl.addWidget(self.configure, 0, Qt.AlignLeft)
        sl.addWidget(self.status)
        sl.addStretch()
        self.side = side
        self.view = ControllerView()
        self.lower.addWidget(side)
        self.lower.addWidget(self.view, 1)
        root.addWidget(body, 1)

        self._flash = QTimer(self, singleShot=True, interval=260,
                             timeout=lambda: self._apply_style(False))
        self._apply_style(False)

        self.name.textEdited.connect(lambda t: self.nameEdited.emit(self.slot, t.strip()))
        self.name.returnPressed.connect(self.name.clearFocus)
        self.close_btn.clicked.connect(lambda: self.closeClicked.emit(self.slot))
        self.configure.clicked.connect(lambda: self.configureClicked.emit(self.slot))

    # ---- appearance -------------------------------------------------------
    def _apply_style(self, lit: bool):
        c, on = self.color, theme.text_on(self.color)
        body = theme.mix(theme.SURFACE, c, 0.10).name()
        edge = theme.mix(theme.BORDER, c, 0.85 if lit else 0.35).name()
        hover = theme.mix(c, "#ffffff", 0.15).name()
        self.setStyleSheet(f"""
            #playerCard {{ background: {body}; border: {2 if lit else 1}px solid {edge};
                           border-radius: 16px; }}
            #cardHeader {{ background: {c}; border-top-left-radius: 15px;
                           border-top-right-radius: 15px; }}
            #badge {{ color: {on}; background: rgba(0,0,0,0.18); border-radius: 8px;
                      padding: 2px 7px; font-weight: 800; }}
            #nameEdit {{ background: transparent; color: {on}; border: none;
                         border-bottom: 2px solid {"rgba(0,0,0,0.35)" if on != "#ffffff" else "rgba(255,255,255,0.55)"};
                         border-radius: 0; padding: 2px 2px; font-size: 17px; font-weight: 700; }}
            #closeButton {{ color: {on}; background: transparent; border: none; border-radius: 13px;
                            font-size: 15px; font-weight: 800; min-width: 26px; min-height: 26px; }}
            #closeButton:hover {{ background: rgba(0,0,0,0.2); }}
            #configureButton {{ background: {c}; color: {on}; border: none; border-radius: 15px;
                                padding: 7px 18px; }}
            #configureButton:hover {{ background: {hover}; }}
            #status {{ color: {theme.MUTED}; }}
        """)

    def flash(self) -> None:
        self._apply_style(True)
        self._flash.start()

    def set_status(self, ok: bool, problems: list[str], unassigned: bool) -> None:
        if ok:
            color, text = theme.OK, "Ready"
        else:
            color = theme.MUTED if unassigned else theme.DANGER
            text = problems[0] if problems else ""
            if len(problems) > 1:
                text += f" (+{len(problems) - 1})"
        self.status.setText(f"<span style='color:{color}'>●</span> {text}")
        self.status.setToolTip("Ready to play" if ok else "\n".join(problems))
        self.view.set_dimmed(unassigned)

    def set_can_close(self, can: bool) -> None:
        self.close_btn.setVisible(can)

    def resizeEvent(self, e):
        # Wide tiles: controls on the left, controller beside them. Tall tiles: stacked.
        wide = self.width() >= 1.15 * self.height() and self.width() > 380
        self.lower.setDirection(QBoxLayout.LeftToRight if wide else QBoxLayout.TopToBottom)
        if wide:
            self.side.setFixedWidth(max(190, min(300, int(self.width() * 0.4))))
        else:
            self.side.setMinimumWidth(0)
            self.side.setMaximumWidth(16777215)
        super().resizeEvent(e)

    # ---- drag to move/swap --------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.position().toPoint()
        self.setFocus()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press is None or not (e.buttons() & Qt.LeftButton):
            return
        if (e.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        self._press = None
        mime = QMimeData()
        mime.setData(MIME, str(self.slot).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        shot = self.grab().scaledToWidth(min(220, self.width()), Qt.SmoothTransformation)
        drag.setPixmap(shot)
        drag.setHotSpot(QPoint(shot.width() // 2, 20))
        drag.exec(Qt.MoveAction)

    def mouseReleaseEvent(self, e):
        self._press = None
        super().mouseReleaseEvent(e)
