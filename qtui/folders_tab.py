"""The Folders tab: one card per synced folder, with the same texts as the Tk window (ui_model),
and adding / editing folders (qtui/folder_dialog.py)."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

import ui_model
from qtui.widgets import button, frame, label, safe, scroll

LINE_ROLES = {"muted": "muted", "error": "error", "waiting": "waiting"}   # ui_model tone -> label role


class FoldersTab(QWidget):
    def __init__(self, app, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self.cards: list[QFrame] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.addWidget(label(ui_model.FOLDERS_HEADING, "heading"), 1)
        top.addWidget(button("Open Google Drive", lambda: self.open_drive(None)))
        top.addWidget(button("+  Add folder", self.add_folder, "primary"))
        lay.addLayout(top)
        self.area = scroll(QWidget())
        lay.addWidget(self.area, 1)

    # ------------------------------------------------------------- building
    def refresh(self) -> None:
        """Rebuild the cards from the settings (cheap in Qt, even with many folders)."""
        bar = self.area.verticalScrollBar()
        keep = bar.value()
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 8, 6, 0)
        lay.setSpacing(8)
        self.cards = []
        folders = self.app.cfg.get("folders", [])
        if not folders:
            lay.addWidget(self._empty())
        else:
            info = self.app.accounts_info()
            known = [a["email"] for a in info]
            signin = {a["email"] for a in info if a["needs_signin"]}
            for i, f in enumerate(folders):
                card = self._card(i, f, known, signin)
                self.cards.append(card)
                lay.addWidget(card)
        lay.addStretch(1)
        self.area.setWidget(inner)
        bar.setValue(keep)

    def _empty(self) -> QFrame:
        box = frame("panel")
        b = QVBoxLayout(box)
        b.setContentsMargins(24, 32, 24, 32)
        b.addWidget(label(ui_model.EMPTY_FOLDERS_TITLE, "heading"), 0, Qt.AlignmentFlag.AlignHCenter)
        hint = label(ui_model.EMPTY_FOLDERS_TEXT, "muted", wrap=True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        b.addWidget(hint)
        b.addWidget(button("+  Add folder", self.add_folder, "primary"), 0, Qt.AlignmentFlag.AlignHCenter)
        return box

    def _card(self, i: int, f: dict, known: list[str], signin: set[str]) -> QFrame:
        local = f.get("local", "")
        email = self.app.folder_account(f, known)
        card = ui_model.folder_card(f, email, known, signin, self.app.is_waiting(f), os.path.isdir(local))
        c = frame("panel")
        g = QGridLayout(c)
        g.setContentsMargins(16, 12, 12, 12)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(3)
        g.addWidget(label(card.title, "name", wrap=True), 0, 0)
        btns = QHBoxLayout()
        btns.setSpacing(6)
        if card.waiting:   # one time only: the folder's first sync runs when this is pressed
            start = button("Start syncing", lambda: self.start_folder(f), "primary")
            start.setEnabled(card.can_start)
            btns.addWidget(start)
        btns.addWidget(button("Open", lambda: self.open_local(i)))
        btns.addWidget(button("Drive", lambda: self.open_drive(i)))
        btns.addWidget(button("Edit", lambda: self.edit_folder(i)))
        btns.addWidget(button("Remove", lambda: self.remove_folder(i), "danger"))
        g.addLayout(btns, 0, 1, Qt.AlignmentFlag.AlignTop)
        row = 1
        if card.location:
            g.addWidget(label(card.location, "muted", wrap=True, selectable=True), row, 0, 1, 2)
            row += 1
        g.addWidget(label(card.line, LINE_ROLES[card.tone], wrap=True), row, 0, 1, 2)
        row += 1
        skips = f.get("exclude") or []
        if skips:
            g.addLayout(self._skip_row(skips, local, f.get("exclude_where", {}), lambda: self.edit_folder(i)),
                        row, 0, 1, 2)
        g.setColumnStretch(0, 1)
        return c

    @staticmethod
    def _skip_row(skips: list[str], local: str, where: dict, on_click) -> QHBoxLayout:
        """'Skips  [Big folder · Google Drive] [*.log · Rule] [+3 more]', the full list on hover."""
        model = ui_model.skip_chips(skips, local, where)
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(label("Skips", "muted"))
        for chip in [*model.shown, *([model.more] if model.more else [])]:
            w = label(chip.text, "chip")
            if chip.tone:
                w.setProperty("tone", chip.tone)
            w.setToolTip(safe(chip.tooltip))   # skipped paths: never HTML
            w.setCursor(Qt.CursorShape.PointingHandCursor)
            w.mousePressEvent = lambda _event: on_click()   # click a chip to change what's skipped
            row.addWidget(w)
        row.addStretch(1)
        return row

    # ------------------------------------------------------------- actions
    def add_folder(self) -> None:
        from qtui.folder_dialog import FolderDialog
        result = FolderDialog.ask(self, self.app, "Add folder")
        if not result:
            return
        known = [a["email"] for a in self.app.accounts_info()]
        # A new folder waits: its first sync runs when "Start syncing" on its card is pressed.
        cfg = ui_model.add_folder(self.app.cfg, result, known, self.app.folder_account)
        if cfg is None:
            QMessageBox.information(self, "Drive Sync", ui_model.DUPLICATE_FOLDER)
            return
        self.app.save_config(cfg)
        self.refresh()

    def edit_folder(self, i: int) -> None:
        from qtui.folder_dialog import FolderDialog
        cfg = self.app.cfg
        result = FolderDialog.ask(self, self.app, "Edit folder", cfg["folders"][i])
        if result:
            self.app.save_config(ui_model.replace_folder(cfg, i, result))
            self.refresh()

    def start_folder(self, folder: dict) -> None:
        self.app.start_folder(folder)
        self.refresh()

    def remove_folder(self, i: int) -> None:
        cfg = self.app.cfg
        answer = QMessageBox.question(self, "Drive Sync", safe(ui_model.remove_folder_question(cfg["folders"][i])))
        if answer == QMessageBox.StandardButton.Yes:
            self.app.save_config(ui_model.without_folder(cfg, i))
            self.refresh()

    def open_local(self, i: int) -> None:
        path = self.app.cfg["folders"][i]["local"]
        if os.path.isdir(path):
            os.startfile(path)  # noqa: S606 - opens the user's own folder in Explorer

    def open_drive(self, i: int | None) -> None:
        known = [a["email"] for a in self.app.accounts_info()]
        f = self.app.cfg["folders"][i] if i is not None else None
        email = self.app.folder_account(f, known) if f is not None else None
        url = ui_model.drive_url(f, email, known, self.app.remote_ids)   # the right account inside the profile
        self.app.open_url(url, ui_model.drive_url_email(email, known))   # the right browser profile
