"""The Activity tab: sync runs and events as cards, newest first, grouped by day, with filters,
search and each run's file list. Texts and rules come from ui_model, as in the Tk window.

A run's file list uses one small label per badge / path / time: in Qt these are drawn inside the one
window (not separate Windows controls as in Tk), so even 200 rows scroll smoothly.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

import activity
import ui_model
from qtui.widgets import button, dot, frame, label, scroll

SEARCH_DELAY_MS = 250   # wait for a pause in typing before searching


class ActivityTab(QWidget):
    def __init__(self, app, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self.entries: list[dict] = app.activity.load()     # oldest first
        self.view, self.query, self.limit = "All", "", ui_model.PAGE
        self.expanded: set[str] = set()
        self.collapsed: set[str] = set()                   # problem runs the user closed
        self.cards: list[QFrame] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        seg = frame("segments")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self.view_group = QButtonGroup(self)
        for name in ui_model.VIEWS:
            b = button(name, lambda n=name: self.set_view(n), "segment")
            b.setCheckable(True)
            b.setChecked(name == "All")
            self.view_group.addButton(b)
            sl.addWidget(b)
        bar.addWidget(seg)
        self.search = QLineEdit()
        self.search.setPlaceholderText(ui_model.SEARCH_HINT)
        self.search.setClearButtonEnabled(True)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self.apply_search)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        bar.addWidget(self.search, 1)
        bar.addWidget(button("Clear", self.clear))
        bar.addWidget(button("Log file", lambda: self.app.open_log()))
        lay.addLayout(bar)
        self.header_lbl = label("", "muted", wrap=True)
        lay.addWidget(self.header_lbl)
        self.area = scroll(QWidget())
        lay.addWidget(self.area, 1)
        self.render()

    # ------------------------------------------------------------- data
    def add(self, entry: dict) -> None:
        self.entries.append(entry)
        if len(self.entries) > activity.KEEP:
            del self.entries[: len(self.entries) - activity.KEEP]
        if activity.matches(entry, self.view, self.query):
            self.render()
        else:
            self.refresh_header()

    def clear(self) -> None:
        if not self.entries:
            return
        if QMessageBox.question(self, "Drive Sync", ui_model.CLEAR_QUESTION) == QMessageBox.StandardButton.Yes:
            self.app.activity.clear()
            self.entries.clear()
            self.render()

    # ------------------------------------------------------------- controls
    def set_view(self, view: str) -> None:
        self.view, self.limit = view, ui_model.PAGE
        self.render()

    def apply_search(self) -> None:
        query = self.search.text()
        if query != self.query:
            self.query, self.limit = query, ui_model.PAGE
            self.render()

    def refresh_header(self) -> None:
        self.header_lbl.setText(ui_model.activity_header(self.app.last_check, self.entries, self.view))

    # ------------------------------------------------------------- building
    def render(self) -> None:
        bar = self.area.verticalScrollBar()
        keep = bar.value()
        self.refresh_header()
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 6, 6, 6)
        lay.setSpacing(6)
        self.cards = []
        shown = [e for e in reversed(self.entries) if activity.matches(e, self.view, self.query)]
        if not shown:
            lay.addWidget(self._empty())
        day = None
        for e in shown[: self.limit]:
            d = activity.day_label(e["ts"])
            if d != day:
                day = d
                lay.addWidget(label(d.upper(), "day"))
            card = self._run_card(e) if e["type"] == "run" else self._card(ui_model.event_card(e))
            self.cards.append(card)
            lay.addWidget(card)
        rest = len(shown) - self.limit
        if rest > 0:
            more = button(f"Show older activity ({rest} more)", self._more)
            lay.addWidget(more, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)
        self.area.setWidget(inner)
        QTimer.singleShot(0, lambda: bar.setValue(keep))

    def _more(self) -> None:
        self.limit += ui_model.PAGE
        self.render()

    def _empty(self) -> QFrame:
        title, text = ui_model.activity_empty(bool(self.entries), self.query, self.view)
        box = frame("card")
        b = QVBoxLayout(box)
        b.setContentsMargins(16, 18, 16, 18)
        b.addWidget(label(title, "heading"), 0, Qt.AlignmentFlag.AlignHCenter)
        t = label(text, "muted", wrap=True)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        b.addWidget(t)
        return box

    @staticmethod
    def _card(c: ui_model.ActivityCard) -> QFrame:
        card = frame("card")
        g = QGridLayout(card)
        g.setContentsMargins(14, 10, 12, 10)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(2)
        g.addWidget(dot(c.dot), 0, 0, Qt.AlignmentFlag.AlignVCenter)
        g.addWidget(label(c.title, "name", wrap=True), 0, 1)
        g.addWidget(label(c.when, "muted"), 0, 2, Qt.AlignmentFlag.AlignTop)
        if c.subtitle:
            g.addWidget(label(c.subtitle, "muted", wrap=True, selectable=True), 1, 1, 1, 2)
        g.setColumnStretch(1, 1)
        return card

    def _run_card(self, e: dict) -> QFrame:
        c = ui_model.run_card(e)
        card = self._card(c)
        if not c.items:
            return card
        g: QGridLayout = card.layout()
        eid = ui_model.entry_id(e)
        is_open = ui_model.details_open(e, self.expanded, self.collapsed)
        g.addWidget(button(ui_model.details_toggle(is_open, c.items), lambda: self._toggle(eid, is_open), "link"),
                    2, 1, Qt.AlignmentFlag.AlignLeft)
        if is_open:   # rows are only made for cards that are open
            g.addWidget(self._details(e), 3, 0, 1, 3)
        return card

    def _toggle(self, eid: str, is_open: bool) -> None:
        if is_open:
            self.expanded.discard(eid)
            self.collapsed.add(eid)
        else:
            self.expanded.add(eid)
            self.collapsed.discard(eid)
        self.render()

    def _details(self, e: dict) -> QFrame:
        shown, times, hidden = ui_model.detail_rows(e, self.query)
        box = frame("inset")
        g = QGridLayout(box)
        g.setContentsMargins(10, 8, 10, 8)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(4)
        row = 0
        for it, when in zip(shown, times, strict=True):
            text = ui_model.badge(it["kind"])[0]
            b = label(text, "badge")
            b.setProperty("kind", it["kind"] if it["kind"] in ui_model.BADGES else "other")
            b.setAlignment(Qt.AlignmentFlag.AlignCenter)
            b.setFixedWidth(84)
            g.addWidget(b, row, 0, Qt.AlignmentFlag.AlignTop)
            path = it["path"] if it["path"] != "." else "(whole folder)"
            g.addWidget(label(path, wrap=True, selectable=True), row, 1)
            if when:   # when the file finished; blank while it's the same minute as the row above
                g.addWidget(label(when, "muted"), row, 2, Qt.AlignmentFlag.AlignTop)
            row += 1
            if it.get("message"):
                role = "error" if it["kind"] == "error" else "muted"
                g.addWidget(label(it["message"], role, wrap=True, selectable=True), row, 1, 1, 2)
                row += 1
        if hidden:
            g.addWidget(label(f"…and {hidden} more (full list in the log file)", "muted"), row, 1, 1, 2)
        g.setColumnStretch(1, 1)
        return box
