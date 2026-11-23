"""Choose what a synced folder skips (Qt): tick common items, or files and folders in a tree that shows
both the PC folder and its Google Drive folder, so things only on Drive can be kept off this PC.
The state and rules are skip_model.SkipModel; this file draws them.

Folders are only read when opened, so big folders stay fast. Drive listings come from a background
thread and arrive on the window's thread through a Qt signal.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qtui import style
from qtui.widgets import button, label
from skip_model import COMMON, INHERITED, ONLY_DRIVE, SkipModel, split_patterns

ROW = Qt.ItemDataRole.UserRole      # a row's (rel, is folder, only on Drive); notes have none


class _Results(QObject):
    done = Signal(object, object)    # (rel_dir, Drive listing or exception)


class SkipPickerDialog(QDialog):
    """self.result = the chosen patterns (None if cancelled); self.result_where = which side each is on."""

    def __init__(self, parent: QWidget | None, local: str, patterns: list[str], always: list[str] = (),
                 drive_list=None, drive_label: str = "", where: dict | None = None, run_now: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Choose what to skip")
        self.resize(620, 640)
        self.setMinimumSize(480, 460)
        self.model = SkipModel(local, patterns, always, drive_list is not None, where)
        self.drive_list = drive_list
        self.run_now = run_now
        self.result: list[str] | None = None
        self.result_where: dict[str, str] = {}
        self.item_of: dict[str, QTreeWidgetItem] = {}
        self.loaded: set[str] = set()                  # folders whose contents are in the tree
        self.loading_row: dict[str, QTreeWidgetItem] = {}
        self._results = _Results()
        self._results.done.connect(self._add_drive)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.addWidget(label("Common", "name"))
        grid = QGridLayout()
        for i, (p, what) in enumerate(COMMON):
            cb = QCheckBox(f"{p}  ·  {what}")
            cb.setChecked(self.model.common[p])
            cb.toggled.connect(lambda on, p=p: self._set_common(p, on))
            grid.addWidget(cb, i // 2, i % 2)
        lay.addLayout(grid)
        where_text = f"{local} and {drive_label}" if drive_list and drive_label else local
        heading = label(f"In {where_text}", "name", wrap=True)
        heading.setContentsMargins(0, 10, 0, 0)
        lay.addWidget(heading)
        help_text = ("Click a file or folder to skip it (and everything in it). Skipped items aren't synced either "
                     "way and are left alone on both sides.")
        if drive_list:
            help_text += f" Items marked “{ONLY_DRIVE}” are never downloaded to this PC if you skip them."
        help_text += f"  {INHERITED} = skipped by a rule above or a skipped folder."
        lay.addWidget(label(help_text, "muted", wrap=True))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemExpanded.connect(self._on_expand)
        self.tree.itemClicked.connect(self._on_click)
        lay.addWidget(self.tree, 1)
        self.other_lbl = label("", "muted", wrap=True)
        lay.addWidget(self.other_lbl)
        bottom = QHBoxLayout()
        self.count_lbl = label("", "muted")
        bottom.addWidget(self.count_lbl)
        bottom.addStretch(1)
        self.done_btn = button("Done", self.done_picking, "primary")
        bottom.addWidget(self.done_btn)
        bottom.addWidget(button("Cancel", self.reject))
        lay.addLayout(bottom)

        root = self.tree.invisibleRootItem()
        if self.model.can_show():
            self._fill(root, "")
            for rel in self.model.ticked_rels:
                self._reveal(rel)
        else:
            root.addChild(self._note("Choose the folder on this PC first."))
        self.refresh()

    # ------------------------------------------------------------- the tree
    @staticmethod
    def _note(text: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([text])
        item.setForeground(0, QBrush(QColor(style.color("muted"))))
        return item

    def _parent_of(self, rel_dir: str) -> QTreeWidgetItem | None:
        return self.tree.invisibleRootItem() if rel_dir == "" else self.item_of.get(rel_dir)

    def _add_row(self, parent: QTreeWidgetItem, rel: str, is_dir: bool, only_drive: bool) -> QTreeWidgetItem:
        item = QTreeWidgetItem([rel.rsplit("/", 1)[-1]])
        item.setData(0, ROW, (rel, is_dir, only_drive))
        if is_dir:
            item.addChild(QTreeWidgetItem(["…"]))       # placeholder so it can be opened
        parent.addChild(item)
        self.item_of[rel] = item
        return item

    def _fill(self, parent: QTreeWidgetItem, rel_dir: str) -> None:
        """Put one folder's contents in the tree: what's on this PC now, what's on Drive when it arrives."""
        self.loaded.add(rel_dir)
        parent.takeChildren()
        entries, more, error = self.model.list_pc(rel_dir)
        if error:
            parent.addChild(self._note(error))
        for name, is_dir in entries:
            self._add_row(parent, f"{rel_dir}/{name}" if rel_dir else name, is_dir, False)
        if more:
            parent.addChild(self._note(f"…and {more:,} more (type a pattern in the box to skip those)"))
        self._ask_drive(rel_dir)

    def _ask_drive(self, rel_dir: str) -> None:
        ask, folder_id = self.model.drive_to_ask(rel_dir)
        if not ask:
            return
        parent = self._parent_of(rel_dir)
        row = self._note("Loading Google Drive…")
        parent.addChild(row)
        self.loading_row[rel_dir] = row

        def work():
            try:
                out = self.drive_list(folder_id)
            except Exception as e:  # noqa: BLE001 - shown in the tree
                out = e
            if self.run_now:
                self._add_drive(rel_dir, out)
            else:
                self._results.done.emit(rel_dir, out)
        if self.run_now:
            work()
        else:
            threading.Thread(target=work, daemon=True, name="skip-drive").start()

    def _add_drive(self, rel_dir: str, out) -> None:
        parent = self._parent_of(rel_dir)
        row = self.loading_row.pop(rel_dir, None)
        if parent is None:
            return
        if row is not None:
            parent.removeChild(row)
        if isinstance(out, Exception):
            parent.addChild(self._note(f"Couldn't read Google Drive: {out}"))
            return
        rows = [parent.child(i) for i in range(parent.childCount()) if parent.child(i).data(0, ROW)]
        here = {r.data(0, ROW)[0].rsplit("/", 1)[-1].lower(): r.data(0, ROW)[0] for r in rows}
        for rel, is_dir in self.model.merge_drive(rel_dir, here, out):
            self._add_row(parent, rel, is_dir, True)
        # Keep folders first, then files, by name (notes stay at the end).
        children = parent.takeChildren()
        data_rows = [c for c in children if c.data(0, ROW)]
        data_rows.sort(key=lambda c: (not c.data(0, ROW)[1], c.data(0, ROW)[0].rsplit("/", 1)[-1].lower()))
        parent.addChildren(data_rows + [c for c in children if not c.data(0, ROW)])
        self.model.adopt(c.data(0, ROW)[0] for c in data_rows)
        for c in data_rows:                            # subfolders opened before their Drive ids were known
            rel = c.data(0, ROW)[0]
            if rel in self.loaded:
                self._ask_drive(rel)
        self.refresh()

    def expand(self, rel: str) -> None:
        item = self.item_of[rel]
        if rel not in self.loaded:
            self._fill(item, rel)
        item.setExpanded(True)
        self.refresh()

    def _reveal(self, rel: str) -> None:
        """Open the folders above an item ticked before, so it's in view."""
        parts = rel.split("/")
        for i in range(1, len(parts)):
            sub = "/".join(parts[:i])
            if sub not in self.item_of:
                return
            self.expand(sub)

    def _on_expand(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, ROW)
        if data and data[1] and data[0] not in self.loaded:
            self._fill(item, data[0])
            self.refresh()

    def _on_click(self, item: QTreeWidgetItem, _column: int) -> None:
        data = item.data(0, ROW)
        if data:
            self.toggle(data[0])

    # ------------------------------------------------------------- state
    def _set_common(self, pattern: str, on: bool) -> None:
        self.model.common[pattern] = on
        self.refresh()

    def toggle(self, rel: str) -> None:
        if self.model.toggle(rel):
            self.refresh()

    def refresh(self) -> None:
        grey, blue = QColor(style.color("muted")), QColor(style.tone("link"))

        def walk(parent: QTreeWidgetItem) -> None:
            for i in range(parent.childCount()):
                item = parent.child(i)
                data = item.data(0, ROW)
                if not data:
                    continue
                rel, is_dir, only_drive = data
                text, kind = self.model.row_text(rel, is_dir, only_drive)
                item.setText(0, text)
                if kind == "inherited":
                    item.setForeground(0, QBrush(grey))
                elif kind == "drive":
                    item.setForeground(0, QBrush(blue))
                else:
                    item.setData(0, Qt.ItemDataRole.ForegroundRole, None)
                if rel in self.loaded:
                    walk(item)
        walk(self.tree.invisibleRootItem())
        self.count_lbl.setText(self.model.count_text())
        self.other_lbl.setText(self.model.other_text())

    def done_picking(self) -> None:
        self.result, self.result_where = self.model.finish(list(self.item_of))
        self.accept()


def pick_skips(parent: QWidget | None, app, local: str, exclude_text: str, email: str | None, drive_text: str,
               drive_id: str | None, chosen_label: str | None, where: dict):
    """The folder dialog's skips "Choose…": (patterns, sides) or None if cancelled."""
    from drive_sync import DEFAULT_EXCLUDES
    from skip_model import drive_side
    lister, drive_label = drive_side(app, email, local, drive_text, drive_id, chosen_label)
    d = SkipPickerDialog(parent, local, split_patterns(exclude_text), always=DEFAULT_EXCLUDES,
                         drive_list=lister, drive_label=drive_label, where=where)
    d.exec()
    return None if d.result is None else (d.result, d.result_where)
