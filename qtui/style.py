"""The Qt window's look: light and dark colours, one stylesheet (QSS) for every widget, and the
colours for ui_model's tones.

Widgets pick their look with a "role" property (e.g. label(..., role="muted")) instead of their own
stylesheets: one shared stylesheet is far quicker for Qt to draw than one per widget.
"""
from __future__ import annotations

import sys
from pathlib import Path
from string import Template

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QWidget

# The white tick drawn in checked boxes (inside the .exe when frozen).
_RESOURCES = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
CHECK_IMAGE = (_RESOURCES / "assets" / "check.png").as_posix()

THEMES = {
    "light": {
        "window": "#eef0f2", "panel": "#ffffff", "card": "#f6f7f8", "text": "#1f1f1f",
        "muted": "#5f6368", "border": "#dadce0", "hover": "#e8eaed", "chip": "#e8eaed",
        "primary": "#1a73e8", "primary_hover": "#1765cc", "link": "#1a73e8", "red": "#d93025",
        "amber": "#b06000", "green": "#1e8e3e", "track": "#e3e3e3", "scroll": "#c4c7c5",
        "switch_off": "#bdc1c6",
    },
    "dark": {
        "window": "#1b1b1b", "panel": "#262626", "card": "#2f2f2f", "text": "#e3e3e3",
        "muted": "#9aa0a6", "border": "#4a4d50", "hover": "#3c4043", "chip": "#3c4043",
        "primary": "#1a73e8", "primary_hover": "#3b82f6", "link": "#8ab4f8", "red": "#f28b82",
        "amber": "#fdd663", "green": "#81c995", "track": "#3c4043", "scroll": "#5f6368",
        "switch_off": "#5f6368",
    },
}
# Segoe UI first; it has no Khmer letters, so Khmer comes from Khmer UI (else Qt borrows them from a
# font that draws Khmer at half the size). Both ship with Windows.
FONT_FAMILIES = ["Segoe UI", "Khmer UI", "Leelawadee UI"]
# The status dot (same colours as the tray icon's states and the Tk window).
STATE_COLORS = {"idle": "#1a73e8", "syncing": "#f9ab00", "error": "#d93025", "paused": "#80868b"}
# ui_model's tones -> theme colour names
TONE_KEYS = {"error": "red", "waiting": "amber", "muted": "muted", "link": "link", "ok": "green"}

QSS = Template("""
* { font-family: $fonts; font-size: 10pt; color: $text; }
QMainWindow, #root { background: $window; }
QLabel { background: transparent; }
QLabel[role="title"] { font-size: 16pt; font-weight: 700; }   /* bold, like the Tk window */
QLabel[role="heading"] { font-size: 11pt; font-weight: 600; }
QLabel[role="name"] { font-size: 10.5pt; font-weight: 600; }
QLabel[role="muted"] { color: $muted; font-size: 9pt; }
QLabel[role="day"] { color: $muted; font-size: 8pt; font-weight: 700; }
QLabel[role="error"] { color: $red; font-size: 9pt; }
QLabel[role="waiting"] { color: $amber; font-size: 9pt; }
QLabel[role="chip"] { background: $chip; border-radius: 8px; padding: 2px 8px; font-size: 9pt; }
QLabel[role="chip"][tone="link"] { color: $link; }
QLabel[role="chip"][tone="muted"] { color: $muted; }
QLabel[role="badge"] { color: white; border-radius: 6px; padding: 1px 0px; font-size: 8pt; font-weight: 700; }
QLabel[role="dot"] { border-radius: 6px; }
QLabel[role="smalldot"] { border-radius: 5px; }
$dots
QFrame[role="panel"] { background: $panel; border-radius: 12px; }
QFrame[role="card"] { background: $card; border-radius: 10px; }
QFrame[role="inset"] { background: $panel; border-radius: 8px; }
QFrame[role="segments"] { background: $chip; border-radius: 8px; }
QPushButton { background: transparent; border: 1px solid $border; border-radius: 8px; padding: 5px 14px; }
QPushButton:hover { background: $hover; }
QPushButton:disabled { color: $muted; }
QPushButton[role="primary"] { background: $primary; color: white; border: none; font-weight: 600; }
QPushButton[role="primary"]:hover { background: $primary_hover; }
QPushButton[role="primary"]:disabled { background: $track; color: $muted; }
QPushButton[role="danger"] { border: none; color: $red; }
QPushButton[role="danger-filled"] { background: #d93025; color: white; border: none; font-weight: 600; }
QPushButton[role="danger-filled"]:hover { background: #b3261e; }
QPushButton[role="danger-filled"]:disabled { background: $track; color: $muted; }
QPushButton[role="link"] { border: none; color: $link; padding: 2px 2px; text-align: left; }
QPushButton[role="link"]:hover { background: transparent; text-decoration: underline; }
/* the Folders tab's sync-order arrows: a one-colour font (so disabled ones grey out), big enough to read */
QPushButton[role="arrow"] { padding: 5px 9px; font-family: "Segoe UI Symbol"; font-size: 15px; font-weight: 700; }
QPushButton[role="row"] { border: none; border-radius: 6px; padding: 6px 10px; text-align: left; }
QPushButton[role="segment"] { border: none; border-radius: 6px; padding: 4px 12px; color: $muted; }
QPushButton[role="segment"]:checked { background: $panel; color: $text; }   /* (bold would clip: same width) */
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid $muted; border-radius: 3px; background: $panel; }
QCheckBox::indicator:checked { background: $primary; border: 1px solid $primary; image: url($check); }
QCheckBox::indicator:disabled { border-color: $border; }
QCheckBox::indicator:checked:disabled { background: $track; border-color: $track; }
QLineEdit, QComboBox { background: $panel; border: 1px solid $border; border-radius: 8px; padding: 5px 10px; }
QLineEdit:focus, QComboBox:focus { border: 1px solid $primary; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: $panel; border: 1px solid $border; outline: none;
                              selection-background-color: $hover; selection-color: $text; }
QProgressBar { background: $track; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; }
QProgressBar::chunk { background: $primary; border-radius: 3px; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $scroll; border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: $muted; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; color: $muted; padding: 6px 16px; margin: 0 4px 8px 0; border-radius: 8px; }
QTabBar::tab:selected { background: $panel; color: $text; font-weight: 600; }
QTabBar::tab:hover:!selected { background: $hover; }
QToolTip { background: $panel; color: $text; border: 1px solid $border; padding: 4px; }
""")

_current = {"name": "light"}


def theme_name(appearance: str, system_dark: bool) -> str:
    """"light" or "dark" for the Appearance setting ("System", "Light", "Dark")."""
    choice = (appearance or "").strip().lower()
    if choice in ("light", "dark"):
        return choice
    return "dark" if system_dark else "light"


def system_is_dark() -> bool:
    from PySide6.QtCore import Qt
    return QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def stylesheet(name: str) -> str:
    import ui_model
    rules = [f'QLabel[role="dot"][state="{s}"] {{ background: {c}; }}' for s, c in STATE_COLORS.items()]
    rules += [f'QLabel[role="smalldot"][state="{s}"] {{ background: {c}; }}' for s, c in ui_model.DOT_COLORS.items()]
    badges = {**{k: c for k, (_label, c) in ui_model.BADGES.items()}, "other": "#80868b"}
    rules += [f'QLabel[role="badge"][kind="{k}"] {{ background: {c}; }}' for k, c in badges.items()]
    fonts = ", ".join(f'"{f}"' for f in FONT_FAMILIES)
    return QSS.substitute(THEMES[name], dots="\n".join(rules), check=CHECK_IMAGE, fonts=fonts)


def apply(qapp: QApplication, appearance: str) -> str:
    """Use the light or dark look everywhere; returns which."""
    name = theme_name(appearance, system_is_dark())
    _current["name"] = name
    if qapp.style().name().lower() != "fusion":
        qapp.setStyle("Fusion")    # the same look on every Windows version; colours come from the stylesheet
    if qapp.font().families()[:len(FONT_FAMILIES)] != FONT_FAMILIES:
        font = QFont(qapp.font())
        font.setFamilies(FONT_FAMILIES)   # also for what the stylesheet doesn't reach (e.g. tree rows)
        font.setPointSize(10)
        qapp.setFont(font)
    sheet = stylesheet(name)
    if qapp.styleSheet() != sheet:  # setting it restyles every widget: only when it really changes
        qapp.setPalette(palette(name))   # what Fusion draws itself: check boxes, menu arrows, text cursor
        qapp.setStyleSheet(sheet)
    return name


def palette(name: str) -> QPalette:
    """Qt's colour palette for a theme, so the parts Fusion draws match the stylesheet."""
    t = THEMES[name]
    p = QPalette()
    R = QPalette.ColorRole
    for role, key in ((R.Window, "window"), (R.WindowText, "text"), (R.Base, "panel"), (R.AlternateBase, "card"),
                      (R.Text, "text"), (R.Button, "card"), (R.ButtonText, "text"), (R.ToolTipBase, "panel"),
                      (R.ToolTipText, "text"), (R.PlaceholderText, "muted"), (R.Highlight, "primary"),
                      (R.Link, "link"), (R.Mid, "border"), (R.Dark, "muted")):   # Dark: check box outlines
        p.setColor(role, QColor(t[key]))
    p.setColor(R.HighlightedText, QColor("white"))
    for role in (R.Text, R.WindowText, R.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(t["muted"]))
    return p


def current_name() -> str:
    return _current["name"]


def color(key: str) -> str:
    return THEMES[_current["name"]][key]


def tone(t: str | None) -> str | None:
    """A ui_model tone's colour in the current theme (None: the normal text colour)."""
    return color(TONE_KEYS[t]) if t else None


def set_prop(w: QWidget, name: str, value) -> None:
    """Change a property the stylesheet looks at (role, state) and redraw with it."""
    if w.property(name) == value:
        return
    w.setProperty(name, value)
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()
