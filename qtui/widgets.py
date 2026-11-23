"""Small helpers every Qt screen uses: labels, buttons and frames styled by role (qtui/style.py).

Security: Qt reads text that looks like HTML as HTML (labels, tooltips, message boxes). Names from
Google Drive are untrusted - anyone can share a folder with you, and Drive allows < > " / in names - and
an <img src="file://host/share/x.png"> shown as HTML makes Windows connect to that host and send the
user's login hash (NTLM). So labels are always plain text, and tooltip / message box text goes
through safe().
"""
from __future__ import annotations

import html

from PySide6 import QtGui
from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QWidget,
)


def safe(text: str) -> str:
    """Text for a tooltip or message box that Qt must show exactly as written, never as HTML."""
    if not QtGui.Qt.mightBeRichText(text):
        return text                    # Qt shows it as plain text already
    return '<p style="white-space: pre-wrap">' + html.escape(text) + "</p>"


def label(text: str, role: str | None = None, wrap: bool = False, selectable: bool = False) -> QLabel:
    w = QLabel(text)
    w.setTextFormat(Qt.TextFormat.PlainText)   # never HTML: labels show file and folder names (see above)
    if role:
        w.setProperty("role", role)
    w.setWordWrap(wrap)
    if selectable:   # only where copying helps (paths, messages): selectable labels draw slower
        w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return w


def button(text: str, on_click, role: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if role:
        b.setProperty("role", role)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(lambda _checked=False: on_click())
    return b


def frame(role: str) -> QFrame:
    f = QFrame()
    f.setProperty("role", role)
    return f


def scroll(inner: QWidget) -> QScrollArea:
    s = QScrollArea()
    s.setWidgetResizable(True)
    s.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    s.setWidget(inner)
    return s


def dot(state: str, role: str = "smalldot", size: int = 10) -> QLabel:
    """A round coloured dot; its colour comes from the stylesheet rule for (role, state)."""
    d = QLabel()
    d.setFixedSize(size, size)
    d.setProperty("role", role)
    d.setProperty("state", state)
    return d


class Switch(QAbstractButton):
    """An on/off switch with a sliding knob (Qt has no ready-made one). Colours follow the theme."""

    def __init__(self, text: str, checked: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setText(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._knob = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._slide)

    def _get_knob(self) -> float:
        return self._knob

    def _set_knob(self, v: float) -> None:
        self._knob = v
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def _slide(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._knob)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def set_checked_quietly(self, on: bool) -> None:
        """Show a value without it counting as a click (no toggled signal, no animation)."""
        self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(False)
        self._anim.stop()
        self._set_knob(1.0 if on else 0.0)

    def sizeHint(self) -> QSize:
        return QSize(46 + self.fontMetrics().horizontalAdvance(self.text()), 26)

    def paintEvent(self, _event) -> None:
        from qtui import style
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off, on = QColor(style.color("switch_off")), QColor(style.color("primary"))
        k = self._knob
        mix = QColor.fromRgbF(off.redF() + (on.redF() - off.redF()) * k,
                              off.greenF() + (on.greenF() - off.greenF()) * k,
                              off.blueF() + (on.blueF() - off.blueF()) * k)
        track = QRectF(0, (self.height() - 20) / 2, 36, 20)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mix)
        p.drawRoundedRect(track, 10, 10)
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(track.x() + 2 + k * 16, track.y() + 2, 16, 16))
        p.setPen(QColor(style.color("text")))
        p.drawText(QRectF(46, 0, self.width() - 46, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())


def segments(names, current: str, on_pick) -> tuple[QFrame, QButtonGroup]:
    """A row of buttons where one is chosen (like "System | Light | Dark")."""
    box = frame("segments")
    lay = QHBoxLayout(box)
    lay.setContentsMargins(3, 3, 3, 3)
    lay.setSpacing(2)
    group = QButtonGroup(box)
    for name in names:
        b = button(name, lambda n=name: on_pick(n), "segment")
        b.setCheckable(True)
        b.setChecked(name == current)
        group.addButton(b)
        lay.addWidget(b)
    return box, group
