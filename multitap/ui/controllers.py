"""Controller artwork per emulated system, with the outline of every button so it can light up.

The artwork is black line drawings on transparency, recoloured for the dark theme on load. Button
shapes trace the inside of each drawn outline; the highlight is painted under the lines.

Coordinates are percentages of the image: x of its width, y of its height. Radii and sizes are
percentages of the width, so circles stay round.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF, QTransform

from . import theme

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "controllers"

Shape = Callable[[float, float], QPainterPath]  # (image width, image height) -> path in px


def circle(cx: float, cy: float, r: float) -> Shape:
    return ellipse(cx, cy, r, r)


def ellipse(cx: float, cy: float, rx: float, ry: float) -> Shape:
    def make(w, h):
        path = QPainterPath()
        path.addEllipse(QPointF(cx * w / 100, cy * h / 100), rx * w / 100, ry * w / 100)
        return path
    return make


def pill(cx: float, cy: float, length: float, thick: float, angle: float = 0) -> Shape:
    """A rounded bar `length` x `thick` (both % of width) rotated `angle` degrees clockwise."""
    def make(w, h):
        lw, tw = length * w / 100, thick * w / 100
        path = QPainterPath()
        path.addRoundedRect(QRectF(-lw / 2, -tw / 2, lw, tw), tw / 2, tw / 2)
        return QTransform().translate(cx * w / 100, cy * h / 100).rotate(angle).map(path)
    return make


def rounded(cx: float, cy: float, width: float, height: float, radius: float) -> Shape:
    """A rounded rectangle; all sizes are % of width."""
    def make(w, h):
        rw, rh, r = width * w / 100, height * w / 100, radius * w / 100
        path = QPainterPath()
        path.addRoundedRect(QRectF(cx * w / 100 - rw / 2, cy * h / 100 - rh / 2, rw, rh), r, r)
        return path
    return make


def poly(*points: tuple[float, float]) -> Shape:
    """A polygon through (x, y) points, for buttons that are not circles or bars."""
    def make(w, h):
        path = QPainterPath()
        path.addPolygon(QPolygonF([QPointF(x * w / 100, y * h / 100) for x, y in points]))
        path.closeSubpath()
        return path
    return make


def petal(cx: float, cy: float, width: float, length: float, angle: float = 0) -> Shape:
    """A WonderSwan key: wide rounded end, narrowing to a point. At angle 0 the point faces down."""
    def make(w, h):
        bw, ln = width * w / 100, length * w / 100
        path = QPainterPath(QPointF(-bw / 2, -ln / 4))
        path.cubicTo(-bw / 2, -ln * 0.57, bw / 2, -ln * 0.57, bw / 2, -ln / 4)
        path.lineTo(0, ln / 2)
        path.closeSubpath()
        return QTransform().translate(cx * w / 100, cy * h / 100).rotate(angle).map(path)
    return make


def _arm(cx, cy, half, arm, angle, rot) -> Shape:
    def make(w, h):
        hw, aw = half * w / 100, arm * w / 100
        path = QPainterPath()
        path.addRoundedRect(QRectF(-aw / 2, -hw, aw, hw - aw * 0.45), aw * 0.2, aw * 0.2)
        return QTransform().translate(cx * w / 100, cy * h / 100).rotate(angle + rot).map(path)
    return make


_DIRS = {"Up": 0, "Right": 90, "Down": 180, "Left": 270}


def dpad(cx: float, cy: float, half: float, arm: float, rot: float = 0) -> dict[str, list[Shape]]:
    """Four arms of a cross: `half` = centre to arm tip, `arm` = arm width (% of width)."""
    return {d: [_arm(cx, cy, half, arm, a, rot)] for d, a in _DIRS.items()}


def _wedge(base: Shape, cx, cy, angle) -> Shape:
    def make(w, h):
        far = 2 * max(w, h)
        tri = QPolygonF([QPointF(0, 0), QPointF(-far, -far), QPointF(far, -far)])
        cut = QPainterPath()
        cut.addPolygon(tri)
        cut.closeSubpath()
        cut = QTransform().translate(cx * w / 100, cy * h / 100).rotate(angle).map(cut)
        return base(w, h).intersected(cut)
    return make


def wedges(base: Shape, cx: float, cy: float) -> dict[str, list[Shape]]:
    """Split one round or square direction pad into four quarters around (cx, cy)."""
    return {d: [_wedge(base, cx, cy, a)] for d, a in _DIRS.items()}


@dataclass
class Controller:
    key: str
    title: str
    buttons: dict[str, list[Shape]] = field(default_factory=dict)  # RetroPad name -> shapes
    image: str = ""  # file in ASSETS; empty = drawn generic pad

    _cache: QImage | None = field(default=None, init=False, repr=False)

    def pixmap_image(self) -> QImage:
        if self._cache is None:
            img = QImage(str(ASSETS / self.image)) if self.image else QImage()
            self._cache = _tint(img if not img.isNull() else _draw_generic(), theme.CONTROLLER_LINES)
        return self._cache


def _tint(img: QImage, color: str) -> QImage:
    """Recolour line art, keeping its alpha."""
    out = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    p = QPainter(out)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(out.rect(), QColor(color))
    p.end()
    return out


def _merge(*parts: dict[str, list[Shape]]) -> dict[str, list[Shape]]:
    out: dict[str, list[Shape]] = {}
    for part in parts:
        for k, v in part.items():
            out.setdefault(k, []).extend(v)
    return out


_SNES_L = [(11.9, 12), (11.9, 9.1), (14.8, 6.1), (17.8, 4.3), (20.7, 3.5), (23.6, 3.3), (32.5, 3.3),
           (32.5, 6.5), (20.7, 6.5), (17.8, 7.4), (14.8, 9.1)]
_GBA_L = [(3.1, 15.7), (3.1, 15), (5.6, 9.9), (8.1, 7.8), (10.6, 7.2), (13.1, 6.6), (15.6, 6.1),
          (18.1, 5.3), (20.6, 4.8), (18.1, 6.6), (15.6, 8.3), (13.1, 9.7), (10.6, 10.9), (8.1, 11.6),
          (5.6, 12.4)]


def _mirror(points):
    return [(100 - x, y) for x, y in points]


CONTROLLERS: dict[str, Controller] = {c.key: c for c in [
    Controller("nes", "NES controller", _merge(
        dpad(18.3, 58.5, 8.5, 5.7),
        {"Select": [pill(40.1, 69.4, 7, 2.2)], "Start": [pill(52.8, 69.4, 7, 2.2)],
         "B": [circle(69.9, 69.5, 3.95)], "A": [circle(82.8, 69.5, 3.95)]}), "nes.png"),
    Controller("snes", "Super NES controller", _merge(
        dpad(21.7, 53, 7.9, 4.7),
        {"Select": [pill(40.9, 59.5, 7.4, 2.1, -38)], "Start": [pill(51.6, 59.5, 7.4, 2.1, -38)],
         "Y": [circle(68.8, 52.8, 3.35)], "X": [circle(78.2, 37.4, 3.35)],
         "A": [circle(87.6, 52.8, 3.35)], "B": [circle(78.2, 68.7, 3.35)],
         "L": [poly(*_SNES_L)], "R": [poly(*_mirror(_SNES_L))]}), "snes.png"),
    # Genesis Plus GX / PicoDrive: B=B, A=C, Y=A, X=Y, L=X, R=Z, Select=Mode (not visible).
    Controller("genesis", "Mega Drive / Genesis 6-button pad", _merge(
        dpad(25.3, 42.7, 8.4, 3.7),
        {"Start": [pill(48.6, 38.7, 7, 2)],
         "Y": [circle(66.4, 58.2, 4.15)], "B": [circle(75.3, 49.2, 4.15)],
         "A": [circle(85, 43.7, 4.15)],
         "L": [circle(63.2, 39, 2.85)], "X": [circle(71.3, 31.8, 2.85)],
         "R": [circle(79.8, 27.9, 2.85)]}), "genesis.png"),
    # Master System: B = button 1 (also Start in-game), A = button 2.
    Controller("sms", "Master System control pad", _merge(
        wedges(rounded(26, 50.35, 23.2, 23.2, 7.5), 26, 50.35),
        {"B": [circle(69.2, 61.2, 4.6)], "A": [circle(85.6, 61.2, 4.6)]}), "sms.png"),
    Controller("gamegear", "Game Gear", _merge(
        dpad(11.7, 46, 8.2, 4.9),
        {"Start": [poly((82.7, 22), (83.7, 17.9), (84, 16.4), (85.5, 17.3), (86.5, 19.6),
                        (86.7, 22.9), (85.3, 26.2), (83.7, 27.1), (82.1, 26.1), (82, 25.4))],
         "B": [circle(83.2, 56, 5.1)], "A": [circle(91, 38.7, 5.1)]}), "gamegear.png"),
    Controller("gb", "Game Boy", _merge(
        dpad(21.9, 67.7, 11.1, 7),
        {"Select": [pill(37.2, 82.1, 10.5, 2, -26.5)], "Start": [pill(53.4, 82.1, 10.5, 2, -26.5)],
         "B": [circle(69.8, 69, 5.45)], "A": [circle(85.5, 64.6, 5.45)]}), "gb.png"),
    Controller("gba", "Game Boy Advance", _merge(
        dpad(13.5, 41.6, 6.1, 3.7),
        {"Start": [circle(20.1, 64.9, 1.25)], "Select": [circle(20.1, 74.4, 1.25)],
         "B": [circle(82, 44.4, 2.85)], "A": [circle(90.9, 39.1, 2.85)],
         "L": [poly(*_GBA_L)], "R": [poly(*_mirror(_GBA_L))]}), "gba.png"),
    # Beetle PCE: II = B, I = A, Run = Start.
    Controller("pce", "PC Engine / TurboGrafx-16 pad", _merge(
        dpad(16.6, 69, 8.1, 4),
        {"Select": [pill(44.6, 75.8, 6.2, 2.2)], "Start": [pill(56.4, 75.8, 6.2, 2.2)],
         "B": [circle(74, 76.2, 3.6)], "A": [circle(85.5, 76.2, 3.6)]}), "pce.png"),
    # The drawing has no Option button, so Start has nothing to light up.
    Controller("ngp", "Neo Geo Pocket Color", _merge(
        wedges(circle(15.7, 37.8, 4.85), 15.7, 37.8),
        {"A": [circle(81.4, 42.8, 3.35)], "B": [circle(91.6, 31.6, 3.35)]}), "ngp.png"),
    # Beetle Cygne: X pad (lower) = d-pad, Y pad (upper) = R2 / R / L2 / L (up, right, down, left).
    # Of the two keys under the screen, Sound is left and Start is right.
    Controller("wswan", "WonderSwan Color", {
        "Up": [petal(14.5, 59.6, 6, 4.8)], "Right": [petal(19.25, 67.3, 6, 4.8, 90)],
        "Down": [petal(14.4, 75.5, 6, 4.8, 180)], "Left": [petal(9.65, 67.45, 6, 4.8, 270)],
        "R2": [petal(14.5, 14.5, 6, 4.8)], "R": [petal(19.25, 22.2, 6, 4.8, 90)],
        "L2": [petal(14.4, 30.4, 6, 4.8, 180)], "L": [petal(9.65, 22.4, 6, 4.8, 270)],
        "Start": [pill(44, 83.4, 5, 1.9)],
        "A": [petal(92.75, 65.1, 6, 4.8, 90)], "B": [petal(88, 73.65, 6, 4.8, 180)]}, "wswan.png"),
    # Handy: A/B appear twice (the Lynx can be flipped); L/R = Option 1/2, Start = Pause.
    # The two keys left of the screen are On/Off.
    Controller("lynx", "Atari Lynx", _merge(
        dpad(17.85, 48.6, 4.2, 2),
        {"A": [circle(91.4, 17, 2.55), circle(91.4, 81.6, 2.55)],
         "B": [circle(84.4, 19.8, 2.55), circle(84.4, 78.8, 2.55)],
         "L": [pill(75.8, 36.2, 2.9, 0.8)], "Start": [pill(75.8, 48.9, 2.9, 0.8)],
         "R": [pill(75.8, 61.9, 2.9, 0.8)]}), "lynx.png"),
    Controller("a2600", "Atari 2600 joystick", _merge(
        wedges(circle(50, 56.15, 31.7), 50, 56.15),
        {"B": [circle(18.3, 18.4, 9)]}), "a2600.png"),
    Controller("a7800", "Atari 7800 joypad", _merge(
        dpad(17.7, 29.6, 8.3, 5.1),
        {"B": [circle(52.1, 70.75, 6.35)], "A": [circle(75.3, 70.75, 6.35)]}), "a7800.png"),
    Controller("generic", "RetroPad", _merge(
        dpad(22, 55, 10, 6),
        {"Select": [pill(42, 62, 7, 3)], "Start": [pill(58, 62, 7, 3)],
         "Y": [circle(72, 55, 4)], "X": [circle(80, 40, 4)],
         "A": [circle(88, 55, 4)], "B": [circle(80, 70, 4)],
         "L": [pill(20, 5, 16, 2.4)], "R": [pill(80, 5, 16, 2.4)]})),
]}

# Core id -> controller. Cores that play several systems pick by the game's file extension.
_BY_CORE = {
    "fceumm": "nes", "nestopia": "nes", "mesen": "nes", "quicknes": "nes",
    "snes9x": "snes", "bsnes": "snes", "snes9x2010": "snes", "bsnes_mercury_balanced": "snes",
    "genesis_plus_gx": "genesis", "genesis_plus_gx_wide": "genesis", "picodrive": "genesis",
    "gambatte": "gb", "sameboy": "gb", "gearboy": "gb", "tgbdual": "gb",
    "mgba": "gba", "vba_next": "gba", "vbam": "gba", "gpsp": "gba",
    "mednafen_pce_fast": "pce", "mednafen_pce": "pce", "mednafen_supergrafx": "pce",
    "mednafen_ngp": "ngp", "race": "ngp", "mednafen_wswan": "wswan",
    "handy": "lynx", "mednafen_lynx": "lynx", "stella": "a2600", "stella2014": "a2600",
    "prosystem": "a7800",
}
_BY_EXT = {
    "nes": "nes", "fds": "nes", "unf": "nes", "unif": "nes",
    "sfc": "snes", "smc": "snes",
    "md": "genesis", "gen": "genesis", "smd": "genesis", "32x": "genesis",
    "sms": "sms", "sg": "sms", "gg": "gamegear",
    "gb": "gb", "gbc": "gb", "gba": "gba",
    "pce": "pce", "sgx": "pce", "ngp": "ngp", "ngc": "ngp", "ws": "wswan", "wsc": "wswan",
    "lnx": "lynx", "a26": "a2600", "a78": "a7800",
}
_BY_SYSTEM = [  # substring of the core's system name, lower case
    ("game boy advance", "gba"), ("game boy", "gb"), ("super nintendo", "snes"),
    ("super famicom", "snes"), ("nintendo entertainment", "nes"), ("famicom", "nes"),
    ("game gear", "gamegear"), ("master system", "sms"), ("mega drive", "genesis"),
    ("genesis", "genesis"), ("pc engine", "pce"), ("turbografx", "pce"),
    ("neo geo pocket", "ngp"), ("wonderswan", "wswan"), ("lynx", "lynx"), ("2600", "a2600"),
    ("7800", "a7800"),
]


def controller_for(core_id: str = "", systems: str = "", rom_path: str = "") -> Controller:
    by_core = _BY_CORE.get(core_id)
    ext = Path(rom_path).suffix.lower().lstrip(".") if rom_path else ""
    by_ext = _BY_EXT.get(ext)
    if by_core and by_ext and by_ext != by_core:
        # Multi-system cores (Genesis Plus GX, mGBA, …): trust the game when the core can play it.
        family = {"genesis": {"sms", "gamegear"}, "gba": {"gb"}, "gb": set(), "pce": set()}
        if by_ext in family.get(by_core, set()):
            return CONTROLLERS[by_ext]
    if by_core:
        return CONTROLLERS[by_core]
    low = systems.lower()
    for needle, key in _BY_SYSTEM:
        if needle in low:
            return CONTROLLERS[key]
    return CONTROLLERS[by_ext] if by_ext else CONTROLLERS["generic"]


def _draw_generic() -> QImage:
    """A RetroPad line drawing, in the style of the others, used when no drawing matches the core."""
    w, h = 720, 380
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(Qt.black, 6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    body = QPainterPath()
    body.addRoundedRect(QRectF(0.02 * w, 0.08 * h, 0.96 * w, 0.80 * h), 150, 150)
    outside = QPainterPath()
    outside.addRect(QRectF(img.rect()))
    p.setClipPath(outside.subtracted(body))  # shoulders peek out from behind the body
    p.drawRoundedRect(QRectF(0.08 * w, 0.02 * h, 0.24 * w, 0.12 * h), 18, 18)
    p.drawRoundedRect(QRectF(0.68 * w, 0.02 * h, 0.24 * w, 0.12 * h), 18, 18)
    p.setClipping(False)
    p.drawPath(body)
    cross = QPainterPath()
    cx, cy, half, arm = 0.22 * w, 0.55 * h, 0.10 * w, 0.06 * w
    cross.addRoundedRect(QRectF(cx - arm / 2, cy - half, arm, 2 * half), 8, 8)
    cross.addRoundedRect(QRectF(cx - half, cy - arm / 2, 2 * half, arm), 8, 8)
    p.drawPath(cross.simplified())
    for x in (0.42, 0.58):
        p.drawRoundedRect(QRectF((x - 0.035) * w, 0.62 * h - 0.015 * w, 0.07 * w, 0.03 * w), 10, 10)
    for x, y in ((0.72, 0.55), (0.80, 0.40), (0.88, 0.55), (0.80, 0.70)):
        p.drawEllipse(QPointF(x * w, y * h), 0.04 * w, 0.04 * w)
    p.end()
    return img
