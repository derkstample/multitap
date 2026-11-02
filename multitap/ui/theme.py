"""Dark launcher theme: Fusion palette, shared colors and the application stylesheet."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

BG = "#121419"
SURFACE = "#1b1e25"
SURFACE_2 = "#242832"
BORDER = "#2f3440"
TEXT = "#e8eaf0"
MUTED = "#8b91a0"
ACCENT = "#22c55e"
DANGER = "#ff4d4f"
WARN = "#f5a524"
OK = "#3ecf6e"
PRESSED = "#ff2b2b"  # live input highlight
CONTROLLER_LINES = "#c4c9d4"  # controller line art
ICONS = (Path(__file__).resolve().parent.parent / "assets" / "icons").as_posix()


def mix(a: str, b: str, t: float) -> QColor:
    """Blend color a toward b by t (0..1)."""
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * t),
                  round(ca.green() + (cb.green() - ca.green()) * t),
                  round(ca.blue() + (cb.blue() - ca.blue()) * t))


def text_on(color: str) -> str:
    """Black or white, whichever reads better on `color`."""
    c = QColor(color)
    return "#15171c" if (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) > 170 else "#ffffff"


STYLESHEET = f"""
QWidget {{ color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 6px; }}

QLabel#fieldLabel {{ color: {MUTED}; font-weight: 600; }}
QLabel#caption {{ color: {MUTED}; }}

QComboBox, QLineEdit, QSpinBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 9px;
    padding: 6px 10px; min-height: 22px; selection-background-color: #3b4252;
}}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover {{ border-color: #454b5a; }}
QComboBox:focus, QLineEdit:focus {{ border-color: #5b6475; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url({ICONS}/chevron-down.svg); width: 12px; height: 8px; }}
QSpinBox::up-button, QSpinBox::down-button {{ border: none; width: 18px; }}
QSpinBox::up-arrow {{ image: url({ICONS}/chevron-up.svg); width: 10px; height: 7px; }}
QSpinBox::down-arrow {{ image: url({ICONS}/chevron-down.svg); width: 10px; height: 7px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {BORDER}; padding: 4px; outline: 0;
    selection-background-color: {SURFACE_2};
}}

QPushButton {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 9px;
    padding: 7px 14px; font-weight: 600;
}}
QPushButton:hover {{ background: #2d323d; }}
QPushButton:pressed {{ background: #1f232b; }}
QPushButton:disabled {{ color: #5d6372; }}

QPushButton#startButton {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #34d399, stop:1 #16a34a);
    color: #06210f; border: none; border-radius: 22px; padding: 10px 34px;
    font-size: 17px; font-weight: 800; letter-spacing: 1px;
}}
QPushButton#startButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #4ade80, stop:1 #22c55e);
}}
QPushButton#startButton:disabled {{ background: #2a2e37; color: #626877; }}

QMenuBar {{ background: {BG}; padding: 2px 8px; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 6px; background: transparent; }}
QMenuBar::item:selected {{ background: {SURFACE_2}; }}

QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; padding: 6px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {SURFACE_2}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}

QListWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 9px; }}
QProgressBar {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: #3a3f4b; border-radius: 5px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    roles = {
        QPalette.Window: BG, QPalette.WindowText: TEXT, QPalette.Base: SURFACE,
        QPalette.AlternateBase: SURFACE_2, QPalette.Text: TEXT, QPalette.Button: SURFACE_2,
        QPalette.ButtonText: TEXT, QPalette.ToolTipBase: SURFACE_2, QPalette.ToolTipText: TEXT,
        QPalette.Highlight: "#3b82f6", QPalette.HighlightedText: "#ffffff",
        QPalette.PlaceholderText: MUTED, QPalette.Mid: BORDER, QPalette.Link: "#60a5fa",
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor("#5d6372"))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET)
