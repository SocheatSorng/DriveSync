"""Add / Edit one synced folder (Qt): the PC folder, the Google account, the Drive folder (typed, or
chosen in the Drive browser), what to skip, and the sync direction. The rules for what gets saved are
ui_model.folder_result, shared with the Tk dialog."""
from __future__ import annotations

import os

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

import ui_model
from qtui.drive_browser import DriveBrowser
from qtui.widgets import button, label, segments


class FolderDialog(QDialog):
    """self.result is the folder's settings, or None if cancelled."""

    def __init__(self, parent: QWidget | None, app, title: str, data: dict | None = None,
                 auto_browse: bool = True):
        super().__init__(parent)
        self.app = app
        self.data = data or {}
        self.result = None
        self.setWindowTitle(title)
        self.setMinimumWidth(560)
        accounts = [a["email"] for a in app.accounts_info()]
        current = app.folder_account(self.data, accounts) if self.data else None
        if current and current not in accounts:
            accounts.append(current)   # keep showing a removed account rather than silently switching
        # A folder chosen in the Drive browser is kept by id (+ its path, for display).
        self.drive_id: str | None = self.data.get("drive_folder_id")
        self.chosen_label: str | None = self.data.get("drive_folder") if self.drive_id else None
        self.exclude_where: dict[str, str] = dict(self.data.get("exclude_where", {}))

        g = QGridLayout()
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(4)
        row = 0

        def heading(text: str, gap: bool = True) -> None:
            nonlocal row
            h = label(text, "name")
            if gap:
                h.setContentsMargins(0, 12, 0, 0)
            g.addWidget(h, row, 0, 1, 2)
            row += 1

        def hint(text: str) -> None:
            nonlocal row
            g.addWidget(label(text, "muted", wrap=True), row, 0, 1, 2)
            row += 1

        heading("Folder on this PC", gap=False)
        self.local = QLineEdit(self.data.get("local", ""))
        self.browse_btn = button("Browse…", self.browse)
        g.addWidget(self.local, row, 0)
        g.addWidget(self.browse_btn, row, 1)
        row += 1

        heading("Google account")
        self.account = QComboBox()
        self.account.addItems(accounts)
        if current or accounts:
            self.account.setCurrentText(current or accounts[0])
        self.account.currentTextChanged.connect(lambda _t: self._forget_choice())
        g.addWidget(self.account, row, 0, 1, 2)
        row += 1
        if not accounts:
            self.account.hide()
            hint(ui_model.NO_ACCOUNT_YET)

        heading("Google Drive folder")
        self.drive = QLineEdit(ui_model.drive_text_for(self.data))
        self.choose_drive_btn = button("Choose…", self.choose_drive_folder, "primary")
        g.addWidget(self.drive, row, 0)
        g.addWidget(self.choose_drive_btn, row, 1)
        row += 1
        hint(ui_model.DRIVE_FOLDER_HINT)

        heading("Skip files or folders matching")
        self.exclude = QLineEdit(", ".join(self.data.get("exclude", [])))
        self.exclude.setPlaceholderText("*.log, node_modules, .git")
        self.skips_btn = button("Choose…", self.choose_skips)
        try:
            import qtui.skip_picker  # noqa: F401 - (the picker arrives in task 12)
        except ImportError:
            self.skips_btn.hide()
        g.addWidget(self.exclude, row, 0)
        g.addWidget(self.skips_btn, row, 1)
        row += 1
        hint(ui_model.SKIPS_HINT)

        heading("Sync direction")
        current_mode = next(k for k, v in ui_model.MODES.items() if v == self.data.get("mode", "two_way"))
        box, self.mode = segments(list(ui_model.MODES), current_mode, lambda _name: self._show_mode())
        mode_row = QHBoxLayout()
        mode_row.addWidget(box)
        mode_row.addStretch(1)
        g.addLayout(mode_row, row, 0, 1, 2)
        row += 1
        self.mode_help = label("", "muted", wrap=True)
        g.addWidget(self.mode_help, row, 0, 1, 2)
        row += 1
        self.mirror = QCheckBox(ui_model.MIRROR_LABEL)
        self.mirror.setChecked(bool(self.data.get("delete")))
        g.addWidget(self.mirror, row, 0, 1, 2)
        row += 1
        self.mirror_help = label(ui_model.MIRROR_HELP, "muted", wrap=True)
        g.addWidget(self.mirror_help, row, 0, 1, 2)
        g.setColumnStretch(0, 1)
        self._show_mode()

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.save_btn = button("Save", self.ok, "primary")
        self.save_btn.setDefault(True)
        btns.addWidget(self.save_btn)
        btns.addWidget(button("Cancel", self.reject))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addLayout(g)
        lay.addSpacing(16)
        lay.addLayout(btns)
        if auto_browse and not self.local.text():
            QTimer.singleShot(300, self.browse)   # a new folder: start by choosing the PC folder

    @classmethod
    def ask(cls, parent: QWidget | None, app, title: str, data: dict | None = None) -> dict | None:
        d = cls(parent, app, title, data)
        d.exec()
        return d.result

    # ------------------------------------------------------------- the form
    def mode_value(self) -> str:
        return ui_model.MODES[self.mode.checkedButton().text()]

    def set_mode(self, mode: str) -> None:
        name = next(k for k, v in ui_model.MODES.items() if v == mode)
        next(b for b in self.mode.buttons() if b.text() == name).setChecked(True)
        self._show_mode()

    def _show_mode(self) -> None:
        mode = self.mode_value()
        self.mode_help.setText(ui_model.mode_help(mode))
        self.mirror.setVisible(mode == "upload")
        self.mirror_help.setVisible(mode == "upload")

    def _forget_choice(self) -> None:
        """A chosen Drive folder belongs to one account; switching accounts means choosing again."""
        if self.drive_id:
            self.drive_id, self.chosen_label = None, None
            self.drive.setText("")

    def _email(self) -> str | None:
        return None if self.account.isHidden() else (self.account.currentText() or None)

    # ------------------------------------------------------------- actions
    def browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a folder to sync",
                                                self.local.text() or os.path.expanduser("~"))
        if path:
            path = os.path.normpath(path)
            self.local.setText(path)
            if not self.drive.text():
                self.drive.setText(ui_model.default_drive_text(path))

    def choose_drive_folder(self) -> None:
        email = self._email()
        if not email:
            QMessageBox.information(self, "Drive Sync", ui_model.NEED_ACCOUNT)
            return
        picked = DriveBrowser.ask(self, self.app, email)
        if picked:
            self.drive_id, self.chosen_label = picked
            self.drive.setText(self.chosen_label)

    def choose_skips(self) -> None:
        from qtui.skip_picker import pick_skips
        picked = pick_skips(self, self.app, self.local.text().strip(), self.exclude.text(), self._email(),
                            self.drive.text().strip(), self.drive_id, self.chosen_label, self.exclude_where)
        if picked is not None:
            patterns, where = picked
            self.exclude.setText(", ".join(patterns))
            self.exclude_where = where

    def ok(self) -> None:
        result = ui_model.folder_result(
            self.data, local=self.local.text(), account=self._email(), mode=self.mode_value(),
            delete=self.mirror.isChecked(), exclude_text=self.exclude.text(), exclude_where=self.exclude_where,
            drive_text=self.drive.text(), drive_id=self.drive_id, chosen_label=self.chosen_label)
        if result is None:
            QMessageBox.critical(self, "Drive Sync", ui_model.NEED_PC_FOLDER)
            return
        self.result = result
        self.accept()
