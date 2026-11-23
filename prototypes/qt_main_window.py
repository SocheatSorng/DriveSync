"""Drive Sync's main window rebuilt in PySide6 (Qt), to compare with the CustomTkinter one.

A prototype: FAKE DATA ONLY. It never reads your settings, Google accounts or sync state, and
none of its buttons sync anything ("Sync now" just plays a pretend sync).

Why Qt: in CustomTkinter every label and button is its own Windows control (the real window had
~3,000), and Windows moves and repaints each one, so dragging and scrolling lagged. Qt draws all
its widgets inside ONE Windows window, so it stays smooth however many rows are shown.

Run (PySide6 comes from a throwaway environment; the app's .venv is not touched):
    python -m uv run --no-project --with pyside6 prototypes/qt_main_window.py [--light | --dark]
"""
from __future__ import annotations

import random
import sys
import time
from string import Template

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# --------------------------------------------------------------------------- look
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
BADGES = {  # kind: (label, colour) - the same badges as the Activity tab today
    "error": ("Problem", "#d93025"), "conflict": ("Conflict", "#e37400"),
    "skipped": ("Skipped", "#e37400"), "uploaded": ("Uploaded", "#1e8e3e"),
    "updated": ("Updated", "#1a73e8"), "moved": ("Moved", "#1a73e8"),
    "downloaded": ("Downloaded", "#00838f"), "deleted_local": ("Recycle Bin", "#80868b"),
    "trashed": ("Drive trash", "#80868b"), "folder": ("New folder", "#9334e6"),
}
STATE_COLORS = {"idle": "#1a73e8", "syncing": "#f9ab00", "error": "#d93025", "paused": "#80868b"}
DETAILS_SHOWN = 200      # file rows per Activity card, as today

QSS = Template("""
* { font-family: "Segoe UI"; font-size: 10pt; color: $text; }
QMainWindow, #root { background: $window; }
QLabel { background: transparent; }
QLabel[role="title"] { font-size: 16pt; font-weight: 600; }
QLabel[role="heading"] { font-size: 11pt; font-weight: 600; }
QLabel[role="name"] { font-size: 10.5pt; font-weight: 600; }
QLabel[role="muted"] { color: $muted; font-size: 9pt; }
QLabel[role="day"] { color: $muted; font-size: 8pt; font-weight: 700; }
QLabel[role="error"] { color: $red; font-size: 9pt; }
QLabel[role="waiting"] { color: $amber; font-size: 9pt; }
QLabel[role="chip"] { background: $chip; border-radius: 8px; padding: 2px 8px; font-size: 9pt; }
QLabel[role="badge"] { color: white; border-radius: 6px; padding: 1px 0px; font-size: 8pt;
                       font-weight: 700; }
$badges
QFrame[role="panel"] { background: $panel; border-radius: 12px; }
QFrame[role="card"] { background: $card; border-radius: 10px; }
QFrame[role="inset"] { background: $panel; border-radius: 8px; }
QFrame[role="segments"] { background: $chip; border-radius: 8px; }
QPushButton { background: transparent; border: 1px solid $border; border-radius: 8px;
              padding: 5px 14px; }
QPushButton:hover { background: $hover; }
QPushButton:disabled { color: $muted; }
QPushButton[role="primary"] { background: $primary; color: white; border: none; font-weight: 600; }
QPushButton[role="primary"]:hover { background: $primary_hover; }
QPushButton[role="primary"]:disabled { background: $track; color: $muted; }
QPushButton[role="danger"] { border: none; color: $red; }
QPushButton[role="link"] { border: none; color: $link; padding: 2px 2px; text-align: left; }
QPushButton[role="link"]:hover { background: transparent; text-decoration: underline; }
QPushButton[role="segment"] { border: none; border-radius: 6px; padding: 4px 12px; color: $muted; }
QPushButton[role="segment"]:checked { background: $panel; color: $text; font-weight: 600; }
QLineEdit, QComboBox { background: $panel; border: 1px solid $border; border-radius: 8px;
                       padding: 5px 10px; }
QLineEdit:focus, QComboBox:focus { border: 1px solid $primary; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: $panel; border: 1px solid $border; outline: none;
                              selection-background-color: $hover; selection-color: $text; }
QProgressBar { background: $track; border: none; border-radius: 3px; max-height: 6px;
               min-height: 6px; }
QProgressBar::chunk { background: $primary; border-radius: 3px; }
QProgressBar[role="usage"] { max-height: 8px; min-height: 8px; border-radius: 4px; }
QProgressBar[role="usage"]::chunk { border-radius: 4px; }
QProgressBar[full="true"]::chunk { background: $red; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $scroll; border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: $muted; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; color: $muted; padding: 6px 16px; margin: 0 4px 8px 0;
               border-radius: 8px; }
QTabBar::tab:selected { background: $panel; color: $text; font-weight: 600; }
QTabBar::tab:hover:!selected { background: $hover; }
QToolTip { background: $panel; color: $text; border: 1px solid $border; padding: 4px; }
""")

THEME = dict(THEMES["light"])   # the colours in use (custom-painted widgets read them)


def label(text: str, role: str | None = None, wrap: bool = False, selectable: bool = False) -> QLabel:
    w = QLabel(text)
    if role:
        w.setProperty("role", role)
    if wrap:
        w.setWordWrap(True)
    if selectable:   # only where copying helps (paths, messages): selectable labels draw slower
        w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return w


def button(text: str, role: str | None = None, on_click=None) -> QPushButton:
    b = QPushButton(text)
    if role:
        b.setProperty("role", role)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click:
        b.clicked.connect(on_click)
    return b


def frame(role: str) -> QFrame:
    f = QFrame()
    f.setProperty("role", role)
    return f


def dot(color: str, size: int = 10) -> QLabel:
    d = QLabel()
    d.setFixedSize(size, size)
    d.setStyleSheet(f"background: {color}; border-radius: {size // 2}px;")
    return d


class Switch(QAbstractButton):
    """An on/off switch with a sliding knob (Qt has no ready-made one)."""

    def __init__(self, text: str, checked: bool = False):
        super().__init__()
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

    def sizeHint(self) -> QSize:
        return QSize(46 + self.fontMetrics().horizontalAdvance(self.text()), 26)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off, on = QColor(THEME["switch_off"]), QColor(THEME["primary"])
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
        p.setPen(QColor(THEME["text"]))
        p.drawText(QRectF(46, 0, self.width() - 46, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())


def scroll(inner: QWidget) -> QScrollArea:
    s = QScrollArea()
    s.setWidgetResizable(True)
    s.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    s.setWidget(inner)
    return s


# --------------------------------------------------------------------------- fake data
EMAIL, EMAIL2 = "teacher@example.com", "family.photos@example.com"
FOLDERS = [
    {"name": "Image", "local": r"D:\Storage\Image", "target": "My Drive › Backups › Image",
     "account": EMAIL, "two_way": True, "skips": [("Thumbs.db", "Rule"), ("Raw exports", "Google Drive")]},
    {"name": "EBC", "local": r"D:\EBC", "target": "My Drive › School › EBC", "account": EMAIL,
     "two_way": True, "skips": []},
    {"name": "Lesson plans for the 2026–2027 school year (all classes)",
     "local": r"C:\Users\Teacher\Documents\School\Lesson plans for the 2026–2027 school year (all classes)",
     "target": "Shared drive › Staff › Lesson plans", "account": EMAIL2, "two_way": False,
     "skips": [("*.tmp", "Rule"), ("Old drafts", "This PC"), ("Scans", "PC + Drive"), ("Video", "Rule")]},
    {"name": "Phone camera", "local": r"E:\Phone camera", "target": "My Drive › Phone camera",
     "account": EMAIL2, "two_way": True, "skips": [], "waiting": True},
]


def fake_activity(seed: int = 7) -> list[dict]:
    """Newest first: a big run with a problem today, then smaller runs over three days."""
    rnd = random.Random(seed)
    now = time.time()
    out = []
    items = [{"kind": "error", "path": "Micomet", "message": "Couldn't read folder: [WinError 3] "
              "The system cannot find the path specified: 'D:\\Storage\\Image\\Micomet '"}]
    items += [{"kind": "downloaded", "path": f"Photos/Trip 2026/IMG_{i:04d}.jpg"} for i in range(199)]
    out.append({"folder": "Image", "ts": now - 300, "items": items, "trigger": "when Drive Sync started"})
    kinds = ["uploaded", "updated", "downloaded", "moved", "deleted_local", "trashed", "conflict"]
    for n in range(1, 30):
        k = rnd.choice(kinds)
        files = [{"kind": rnd.choice([k, "uploaded"]),
                  "path": f"{rnd.choice(['Notes', 'Class 3B', 'Reports', 'Photos'])}/file_{n}_{j}.docx"}
                 for j in range(rnd.randint(1, 6))]
        if k == "conflict":
            files[0]["message"] = "Edited on both this PC and Drive - your version was kept as a copy"
        out.append({"folder": rnd.choice(["Image", "EBC", "Lesson plans", "Phone camera"]),
                    "ts": now - n * 3600 * 2.3, "items": files,
                    "trigger": rnd.choice(["a change on this PC", "a change on Drive", "the timer"])})
    return out


ACTIVITY = fake_activity()


def summary(items: list[dict]) -> str:
    counts: dict[str, int] = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    words = {"error": "problem", "conflict": "conflict", "uploaded": "uploaded", "updated": "updated",
             "downloaded": "downloaded", "moved": "moved", "deleted_local": "to Recycle Bin",
             "trashed": "to Drive trash", "folder": "new folder", "skipped": "skipped"}
    parts = []
    for kind, n in sorted(counts.items(), key=lambda kv: list(BADGES).index(kv[0])):
        w = words[kind]
        parts.append(f"{n} {w}{'s' if n != 1 and kind in ('error', 'conflict') else ''}")
    return " · ".join(parts)


def day_label(ts: float) -> str:
    days = (time.localtime().tm_yday - time.localtime(ts).tm_yday) % 366
    return "Today" if days == 0 else "Yesterday" if days == 1 else time.strftime("%A %d %B", time.localtime(ts))


# --------------------------------------------------------------------------- tabs
class FoldersTab(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.addWidget(label("Each folder is kept the same on this PC and Google Drive.", "muted"), 1)
        top.addWidget(button("+  Add folder", "primary"))
        lay.addLayout(top)
        inner = QWidget()
        self.list = QVBoxLayout(inner)
        self.list.setContentsMargins(0, 8, 6, 0)
        self.list.setSpacing(8)
        for f in FOLDERS:
            self.list.addWidget(self._card(f))
        self.list.addStretch(1)
        lay.addWidget(scroll(inner), 1)

    @staticmethod
    def _card(f: dict) -> QFrame:
        c = frame("card")
        g = QGridLayout(c)
        g.setContentsMargins(16, 12, 12, 12)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(3)
        title = label(f["name"], "name", wrap=True)
        g.addWidget(title, 0, 0)
        btns = QHBoxLayout()
        btns.setSpacing(6)
        if f.get("waiting"):
            btns.addWidget(button("Start syncing", "primary"))
        for text in ("Open", "Drive", "Edit"):
            btns.addWidget(button(text))
        btns.addWidget(button("Remove", "danger"))
        g.addLayout(btns, 0, 1, Qt.AlignmentFlag.AlignTop)
        g.addWidget(label(f["local"], "muted", wrap=True, selectable=True), 1, 0, 1, 2)
        arrow = "↔" if f["two_way"] else "→"
        line = f"{arrow}  {f['account']} · {f['target']}" + ("" if f["two_way"] else " · upload only")
        if f.get("waiting"):
            g.addWidget(label(f"Waiting - press Start syncing   ·   {line}", "waiting", wrap=True), 2, 0, 1, 2)
        else:
            g.addWidget(label(line, "muted", wrap=True), 2, 0, 1, 2)
        if f["skips"]:
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(label("Skips", "muted"))
            for name, side in f["skips"]:
                chip = label(f"{name}  ·  {side}", "chip")
                chip.setToolTip(f"{name}\nNot synced ({side}).\n\nClick to change what's skipped.")
                chip.setCursor(Qt.CursorShape.PointingHandCursor)
                row.addWidget(chip)
            row.addStretch(1)
            g.addLayout(row, 3, 0, 1, 2)
        g.setColumnStretch(0, 1)
        return c


class SettingsTab(QWidget):
    def __init__(self, on_appearance):
        super().__init__()
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(10)

        acct = self._section(lay, "Google accounts")
        for email, folders, used, total in ((EMAIL, 2, 6.1, 15), (EMAIL2, 2, 13.9, 15)):
            row = frame("card")
            r = QGridLayout(row)
            r.setContentsMargins(12, 8, 8, 8)
            r.addWidget(dot(THEME["green"] if used < 14 else "#e37400"), 0, 0, 2, 1)
            r.addWidget(label(email, "name"), 0, 1)
            near = used / total > 0.9
            r.addWidget(label(f"{folders} folders  ·  Drive: {used} GB of {total} GB used"
                              + ("  -  nearly full" if near else ""), "error" if near else "muted"), 1, 1)
            r.addWidget(button("Remove", "danger"), 0, 2, 2, 1)
            r.setColumnStretch(1, 1)
            acct.addWidget(row)
        btns = QHBoxLayout()
        for text, role in (("+  Add Google account", "primary"), ("Import credentials.json…", None),
                           ("Setup guide", None)):
            btns.addWidget(button(text, role))
        btns.addStretch(1)
        acct.addLayout(btns)

        sync = self._section(lay, "Syncing")
        sync.addLayout(self._combo_row("Look for missed changes", ["Every 5 minutes", "Every 15 minutes",
                                       "Every 30 minutes", "Every hour", "Once a day"], 1))
        for text, on in (("Sync changes as soon as they happen", True),
                         ("Show a notification when files are uploaded", True),
                         ("Start Drive Sync when I sign in to Windows", True)):
            sync.addWidget(Switch(text, on))

        net = self._section(lay, "Network")
        net.addLayout(self._combo_row("Upload speed limit", ["No limit", "5 MB/s", "2 MB/s", "1 MB/s"], 0))
        net.addWidget(Switch("Pause on metered connections (phone hotspot, capped Wi-Fi)", True))
        net.addWidget(label("Syncing waits until you're back on an unmetered network. "
                            "“Sync now” still works.", "muted", wrap=True))

        look = self._section(lay, "Appearance")
        theme = QComboBox()
        theme.addItems(["Same as Windows", "Light", "Dark"])
        theme.currentTextChanged.connect(on_appearance)
        look.addLayout(self._combo_row("Theme", combo=theme))

        upd = self._section(lay, "Updates")
        r = QHBoxLayout()
        r.addWidget(label("Version 1.1.0 (installed)  ·  Up to date", "muted"), 1)
        r.addWidget(button("Check now"))
        upd.addLayout(r)
        lay.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(inner))

    @staticmethod
    def _section(parent: QVBoxLayout, title: str) -> QVBoxLayout:
        box = frame("panel")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(8)
        lay.addWidget(label(title, "heading"))
        parent.addWidget(box)
        return lay

    @staticmethod
    def _combo_row(text: str, items: list[str] | None = None, current: int = 0,
                   combo: QComboBox | None = None) -> QHBoxLayout:
        r = QHBoxLayout()
        r.addWidget(label(text))
        if combo is None:
            combo = QComboBox()
            combo.addItems(items or [])
            combo.setCurrentIndex(current)
        combo.setMinimumWidth(180)
        r.addWidget(combo)
        r.addStretch(1)
        return r


class ActivityTab(QWidget):
    def __init__(self):
        super().__init__()
        self.view, self.query, self.open_cards = "All", "", {0}   # the problem run starts open
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        seg = frame("segments")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        group = QButtonGroup(self)
        for name in ("All", "Changes", "Problems"):
            b = button(name, "segment", lambda _=False, n=name: self._set_view(n))
            b.setCheckable(True)
            b.setChecked(name == "All")
            group.addButton(b)
            sl.addWidget(b)
        bar.addWidget(seg)
        search = QLineEdit()
        search.setPlaceholderText("Search files, folders, accounts…")
        search.setClearButtonEnabled(True)
        search.textChanged.connect(self._set_query)
        bar.addWidget(search, 1)
        bar.addWidget(button("Clear"))
        bar.addWidget(button("Log file"))
        lay.addLayout(bar)
        lay.addWidget(label("Last check 17:51  ·  Checks that changed nothing aren't listed.  ·  "
                            "1 recent problem - see Problems", "muted"))
        self.area = scroll(QWidget())
        lay.addWidget(self.area, 1)
        self.render()

    def _set_view(self, name: str) -> None:
        self.view = name
        self.render()

    def _set_query(self, text: str) -> None:
        self.query = text.strip().lower()
        self.render()

    def _shown(self) -> list[tuple[int, dict]]:
        out = []
        for i, e in enumerate(ACTIVITY):
            if self.view == "Problems" and not any(it["kind"] in ("error", "conflict") for it in e["items"]):
                continue
            if self.query and self.query not in e["folder"].lower() and not any(
                    self.query in it["path"].lower() for it in e["items"]):
                continue
            out.append((i, e))
        return out

    def render(self) -> None:
        """Rebuilt from scratch on every filter change - cheap in Qt, even with hundreds of rows."""
        bar = self.area.verticalScrollBar()
        keep = bar.value()
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 6, 6, 6)
        lay.setSpacing(6)
        day = None
        shown = self._shown()
        for i, e in shown:
            d = day_label(e["ts"])
            if d != day:
                day = d
                lay.addWidget(label(d.upper(), "day"))
            lay.addWidget(self._card(i, e))
        if not shown:
            box = frame("card")
            b = QVBoxLayout(box)
            b.setContentsMargins(16, 18, 16, 18)
            b.addWidget(label("Nothing found", "heading"), 0, Qt.AlignmentFlag.AlignHCenter)
            b.addWidget(label("No activity matches this filter.", "muted"), 0, Qt.AlignmentFlag.AlignHCenter)
            lay.addWidget(box)
        lay.addStretch(1)
        self.area.setWidget(inner)
        QTimer.singleShot(0, lambda: bar.setValue(keep))

    def _card(self, i: int, e: dict) -> QFrame:
        c = frame("card")
        g = QGridLayout(c)
        g.setContentsMargins(14, 10, 12, 10)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(2)
        kinds = {it["kind"] for it in e["items"]}
        color = (THEME["red"] if "error" in kinds else "#e37400" if kinds & {"conflict", "skipped"}
                 else THEME["green"])
        g.addWidget(dot(color, 9), 0, 0, Qt.AlignmentFlag.AlignVCenter)
        g.addWidget(label(e["folder"], "name"), 0, 1)
        g.addWidget(label(time.strftime("%H:%M", time.localtime(e["ts"])), "muted"), 0, 2)
        g.addWidget(label(f"{summary(e['items'])} · {e['trigger']} · {EMAIL}", "muted", wrap=True), 1, 1, 1, 2)
        is_open = i in self.open_cards
        n = len(e["items"])
        toggle = button("Hide details ▴" if is_open else f"Show {n} item{'s' if n != 1 else ''} ▾", "link",
                        lambda: self._toggle(i))
        g.addWidget(toggle, 2, 1, Qt.AlignmentFlag.AlignLeft)
        if is_open:   # rows are only made for cards that are open
            g.addWidget(self._details(e), 3, 0, 1, 3)
        g.setColumnStretch(1, 1)
        return c

    def _toggle(self, i: int) -> None:
        self.open_cards ^= {i}
        self.render()

    @staticmethod
    def _details(e: dict) -> QFrame:
        box = frame("inset")
        g = QGridLayout(box)
        g.setContentsMargins(10, 8, 10, 8)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(4)
        items = sorted(e["items"], key=lambda it: list(BADGES).index(it["kind"]))[:DETAILS_SHOWN]
        row = 0
        for it in items:
            text = BADGES[it["kind"]][0]
            badge = label(text, "badge")
            badge.setProperty("kind", it["kind"])
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedWidth(84)
            g.addWidget(badge, row, 0, Qt.AlignmentFlag.AlignTop)
            g.addWidget(label(it["path"], wrap=True, selectable=True), row, 1)
            row += 1
            if it.get("message"):
                g.addWidget(label(it["message"], "error" if it["kind"] == "error" else "muted", wrap=True,
                                  selectable=True), row, 1)
                row += 1
        g.setColumnStretch(1, 1)
        return box


class SpaceTab(QWidget):
    def __init__(self):
        super().__init__()
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(10)
        for email, used, total in ((EMAIL, 6.1, 15.0), (EMAIL2, 13.9, 15.0)):
            box = frame("panel")
            b = QVBoxLayout(box)
            b.setContentsMargins(16, 12, 16, 14)
            b.setSpacing(6)
            b.addWidget(label(f"Google Drive  ·  {email}", "heading"))
            bar = QProgressBar()
            bar.setProperty("role", "usage")
            bar.setTextVisible(False)
            bar.setRange(0, 1000)
            bar.setValue(int(used / total * 1000))
            bar.setProperty("full", used / total > 0.9)   # nearly full: red, as in Settings
            b.addWidget(bar)
            b.addWidget(label(f"{used} GB of {total:.0f} GB used ({used / total:.0%})", "muted"))
            b.addWidget(label("Biggest folders", "name"))
            for name, gb in (("Image", used * 0.55), ("Phone camera", used * 0.3), ("EBC", used * 0.1)):
                r = QHBoxLayout()
                n = label(name)
                n.setFixedWidth(130)
                r.addWidget(n)
                fb = QProgressBar()
                fb.setTextVisible(False)
                fb.setRange(0, 1000)
                fb.setValue(int(gb / used * 1000))
                r.addWidget(fb, 1)
                s = label(f"{gb:.1f} GB", "muted")
                s.setFixedWidth(60)
                s.setAlignment(Qt.AlignmentFlag.AlignRight)
                r.addWidget(s)
                b.addLayout(r)
            lay.addWidget(box)
        lay.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(inner))


# --------------------------------------------------------------------------- window
class MainWindow(QMainWindow):
    def __init__(self, theme: str | None = None):
        super().__init__()
        self.setWindowTitle("Drive Sync (Qt prototype - fake data)")
        self.resize(820, 560)
        self.setMinimumSize(680, 460)
        self.forced = theme                    # None: follow Windows
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(22, 16, 22, 16)
        lay.setSpacing(10)

        hdr = QGridLayout()
        hdr.setHorizontalSpacing(10)
        self.dot = dot(STATE_COLORS["idle"], 12)
        hdr.addWidget(self.dot, 0, 0)
        self.status = label("Up to date", "title")
        hdr.addWidget(self.status, 0, 1)
        self.pause = button("Pause", None, self._toggle_pause)
        self.pause.setFixedWidth(96)
        self.sync = button("Sync now", "primary", self._pretend_sync)
        self.sync.setFixedWidth(110)
        hdr.addWidget(self.pause, 0, 2)
        hdr.addWidget(self.sync, 0, 3)
        self.detail = label("Last sync 17:51 · watching 3 folders for changes · 1 new folder waiting - "
                            "press Start syncing", "muted", wrap=True)
        hdr.addWidget(self.detail, 1, 1, 1, 3)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.hide()
        hdr.addWidget(self.bar, 2, 1, 1, 3)
        hdr.setColumnStretch(1, 1)
        lay.addLayout(hdr)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.tabBar().setDrawBase(False)   # no line under the tabs
        tabs.addTab(FoldersTab(), "Folders")
        tabs.addTab(SettingsTab(self.set_appearance), "Settings")
        self.activity = ActivityTab()
        tabs.addTab(self.activity, "Activity")
        tabs.addTab(SpaceTab(), "Space")
        self.tabs = tabs
        lay.addWidget(tabs, 1)

        hints = QApplication.styleHints()
        hints.colorSchemeChanged.connect(lambda _s: self.apply_theme())
        self.paused = False
        self.apply_theme()

    # -- theme
    def set_appearance(self, choice: str) -> None:
        self.forced = {"Light": "light", "Dark": "dark"}.get(choice)
        self.apply_theme()

    def apply_theme(self) -> None:
        name = self.forced or ("dark" if QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
                               else "light")
        THEME.clear()
        THEME.update(THEMES[name])
        badges = "\n".join(f'QLabel[role="badge"][kind="{k}"] {{ background: {c}; }}'
                           for k, (_t, c) in BADGES.items())
        QApplication.instance().setStyleSheet(QSS.substitute(THEME, badges=badges))
        self.activity.render()             # card dots use theme colours
        for w in self.findChildren(Switch):
            w.update()

    # -- pretend actions
    def _toggle_pause(self) -> None:
        self.paused = not self.paused
        self.pause.setText("Resume" if self.paused else "Pause")
        self.status.setText("Paused" if self.paused else "Up to date")
        self.dot.setStyleSheet(f"background: {STATE_COLORS['paused' if self.paused else 'idle']}; "
                               "border-radius: 6px;")

    def _pretend_sync(self) -> None:
        self.sync.setEnabled(False)
        self.status.setText("Syncing…")
        self.dot.setStyleSheet(f"background: {STATE_COLORS['syncing']}; border-radius: 6px;")
        self.bar.setValue(0)
        self.bar.show()
        self._step = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)

    def _tick(self) -> None:
        self._step += 1
        self.bar.setValue(self._step)
        self.detail.setText(f"Uploading 3 of 12 files · {self._step}% · 4.2 MB/s · about "
                            f"{max(1, (100 - self._step) // 20)} s left\nPhotos/Trip 2026/IMG_{self._step:04d}.jpg")
        if self._step >= 100:
            self._timer.stop()
            self.bar.hide()
            self.sync.setEnabled(True)
            self.status.setText("Up to date")
            self.dot.setStyleSheet(f"background: {STATE_COLORS['idle']}; border-radius: 6px;")
            self.detail.setText(f"Last sync {time.strftime('%H:%M')} · watching 3 folders for changes")


def main(argv: list[str]) -> int:
    app = QApplication(argv)
    app.setStyle("Fusion")    # the same look on every Windows version; colours come from QSS
    theme = "dark" if "--dark" in argv else "light" if "--light" in argv else None
    w = MainWindow(theme)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
