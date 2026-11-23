"""The Space tab (Qt): what's using space on this PC or on Google Drive, and clearing it out safely.
Everything except drawing - scanning, the selection, moving items to the Recycle Bin / Drive trash and
removing old versions - is space_model.py, shared with the Tk window."""
from __future__ import annotations

import os
import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

import space
import space_model as sm
from progress import human_size
from qtui.widgets import button, frame, label, safe, scroll


def _in_background(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True, name=f"space-{fn.__name__}").start()


class SpaceTab(QWidget):
    def __init__(self, app, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self.st = sm.state_of(app)
        self.run_in_background = _in_background      # (tests run the work directly)
        self.delete_btn = None
        self.old_btn = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.source = QComboBox()
        self.source.addItems(sm.sources(app.accounts_info()))
        self.source.setMinimumWidth(300)
        bar.addWidget(self.source)
        self.scan_btn = button("Scan", self.scan, "primary")
        bar.addWidget(self.scan_btn)
        self.stop_btn = button("Stop", self.st.stop.set)
        bar.addWidget(self.stop_btn)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.status_lbl = label("", "muted", wrap=True)
        lay.addWidget(self.status_lbl)
        self.area = scroll(QWidget())
        lay.addWidget(self.area, 1)
        self.render()

    # ------------------------------------------------------------- scanning
    def scan(self) -> None:
        choice = self.source.currentText()
        picked = None
        if choice == sm.PICK:
            picked = QFileDialog.getExistingDirectory(self, "Folder to look at")
            if not picked:
                return
        target = sm.scan_target(choice, self.app.cfg, picked)
        if target is None:
            QMessageBox.information(self, "Drive Sync", sm.NO_SYNCED_FOLDERS)
            return
        sm.start_scan(self.st)
        self.render()
        self.run_in_background(sm.run_scan, self.app, self.st, target)

    # ------------------------------------------------------------- drawing
    def render(self) -> None:
        """Redraw from the state (on the window's thread)."""
        st = self.st
        self.scan_btn.setEnabled(not st.busy)
        self.stop_btn.setVisible(st.busy)
        bar = self.area.verticalScrollBar()
        keep = bar.value()
        inner = QWidget()
        self.list = QVBoxLayout(inner)
        self.list.setContentsMargins(0, 4, 6, 4)
        self.list.setSpacing(10)
        self.delete_btn = self.old_btn = None
        r = st.report
        if r is None:
            self.status_lbl.setText(st.status or sm.INTRO)
        else:
            self.status_lbl.setText(st.status)
            self._summary(r)
            self._safe(r)
            self._duplicates(r)
            if r.where == "drive":
                self._old_versions(r)
            self._biggest("Biggest folders", r.biggest_folders, r, select=False)
            self._biggest("Biggest files", r.biggest_files, r, select=True)
        self.list.addStretch(1)
        self.area.setWidget(inner)
        bar.setValue(keep)

    def show_progress(self) -> None:
        """Cheap update while moving files (redrawing every list would be slow)."""
        self.status_lbl.setText(self.st.status)
        if self.st.report is not None and self.delete_btn is not None:
            self._refresh_button()

    def card(self, title: str, note: str = "") -> QVBoxLayout:
        c = frame("panel")
        lay = QVBoxLayout(c)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(4)
        lay.addWidget(label(title, "heading"))
        if note:
            lay.addWidget(label(note, "muted", wrap=True))
        self.list.addWidget(c)
        return lay

    def _summary(self, r: space.Report) -> None:
        s = sm.summary(r)
        c = self.card(s.title, s.text)
        row = QHBoxLayout()
        self.delete_btn = button("", self.delete_selected, "primary")
        row.addWidget(self.delete_btn)
        self._refresh_button()
        if s.trash_button:
            row.addWidget(button(s.trash_button, lambda: self.app.open_url(sm.DRIVE_TRASH_URL, self.st.email)))
        row.addStretch(1)
        c.addLayout(row)
        c.addWidget(label(s.note, "muted", wrap=True))

    def _check(self, parent: QVBoxLayout, it: space.Item, text: str, selection: set[str]) -> QCheckBox:
        cb = QCheckBox(text)
        cb.setChecked(it.key in selection)
        cb.setEnabled(not self.st.busy)

        def toggled(on: bool) -> None:
            (selection.add if on else selection.discard)(it.key)
            if selection is self.st.selected:
                self._refresh_button()
            else:
                self._refresh_old_button()
        cb.toggled.connect(toggled)
        parent.addWidget(cb)
        return cb

    def _refresh_button(self) -> None:
        text, enabled = sm.delete_button(self.st)
        self.delete_btn.setText(text)
        self.delete_btn.setEnabled(enabled)

    def _safe(self, r: space.Report) -> None:
        items = r.safe()
        if not items:
            return
        c = self.card(f"Safe to delete - {human_size(r.safe_bytes())}", sm.SAFE_NOTE)
        for it, text in sm.safe_rows(r):
            self._check(c, it, text, self.st.selected)
        self._more(c, len(items))

    def _duplicates(self, r: space.Report) -> None:
        if not r.duplicates:
            return
        c = self.card(sm.dup_title(r), sm.DUP_NOTE)
        for g in r.duplicates[:sm.GROUPS]:
            c.addWidget(label(sm.dup_group_title(g), "name"))
            for i, it in enumerate(g.items):
                self._check(c, it, sm.dup_row(i, it), self.st.selected)
        self._more(c, len(r.duplicates), sm.GROUPS, "groups")

    def _old_versions(self, r: space.Report) -> None:
        if not r.old_versions:
            return
        c = self.card(sm.old_title(r), sm.OLD_NOTE)
        for it in r.old_versions[:sm.ROWS]:
            self._check(c, it, sm.old_row(it), self.st.old_selected)
        self.old_btn = button("", self.delete_old_versions, "danger-filled")
        c.addWidget(self.old_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self._refresh_old_button()

    def _refresh_old_button(self) -> None:
        text, enabled = sm.old_button(self.st)
        self.old_btn.setText(text)
        self.old_btn.setEnabled(enabled)

    def _biggest(self, title: str, items: list[space.Item], r: space.Report, select: bool) -> None:
        if not items:
            return
        c = self.card(title)
        for it in items:
            row = QHBoxLayout()
            text = sm.biggest_row(r, it, select)
            holder = QVBoxLayout()
            if select:
                self._check(holder, it, text, self.st.selected)
            else:
                holder.addWidget(label(text))
            row.addLayout(holder, 1)
            row.addWidget(button("Open", lambda i=it: self.open_item(i)))
            c.addLayout(row)

    @staticmethod
    def _more(c: QVBoxLayout, n: int, shown: int = sm.ROWS, what: str = "items") -> None:
        text = sm.more_text(n, shown, what)
        if text:
            c.addWidget(label(text, "muted"))

    # ------------------------------------------------------------- actions
    def open_item(self, it: space.Item) -> None:
        kind, target = sm.open_target(self.st.report, it)
        if kind == "folder":
            if target:
                os.startfile(target)  # noqa: S606 - a folder the scan found on this PC
        else:
            self.app.open_url(target, self.st.email)

    def delete_selected(self) -> None:
        st = self.st
        if st.busy or st.report is None:
            return
        question, warn = sm.delete_question(st)
        ask = QMessageBox.warning if warn else QMessageBox.question
        buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        if ask(self, "Drive Sync", safe(question), buttons) != QMessageBox.StandardButton.Yes:
            return
        keys = sm.start_delete(st)
        self.render()
        self.run_in_background(sm.run_delete, self.app, st, keys)

    def delete_old_versions(self) -> None:
        st = self.st
        if st.busy or st.report is None:
            return
        items, question = sm.old_question(st)
        buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        if QMessageBox.warning(self, "Drive Sync", safe(question), buttons) != QMessageBox.StandardButton.Yes:
            return
        sm.start_old(st)
        self.render()
        self.run_in_background(sm.run_old, self.app, st, items)
