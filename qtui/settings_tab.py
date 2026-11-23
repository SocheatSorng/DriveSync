"""The Settings tab: Google accounts (add - choosing the browser profile to sign in with -, cancel a
sign-in, sign in again, remove, the browser profile per account, import credentials.json), syncing,
internet use, updates, appearance, quit and uninstall. Texts and rules come from ui_model, as in the
Tk window, and options are saved to the same config.json keys.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

import browsers
import ui_model
import updater
from qtui import style
from qtui.widgets import Switch, button, dot, frame, label, safe, scroll, segments
from version import __version__

STATUS_ROLES = {"muted": "muted", "error": "error"}


class AddAccountDialog(QDialog):
    """Asks which browser profile to sign in with."""

    def __init__(self, parent: QWidget | None, choices: dict[str, str | None]):
        super().__init__(parent)
        self.setWindowTitle(ui_model.ADD_ACCOUNT_TITLE)
        self.choices = choices
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(label("Sign in with", "name"))
        lay.addWidget(label(ui_model.ADD_ACCOUNT_TEXT, "muted", wrap=True))
        self.menu = QComboBox()
        self.menu.addItems(list(choices))
        self.menu.setMinimumWidth(420)
        lay.addWidget(self.menu)
        lay.addWidget(label(ui_model.ADD_ACCOUNT_HINT, "muted", wrap=True))
        btns = QHBoxLayout()
        btns.addStretch(1)
        ok = button("Open sign-in", self.accept, "primary")
        ok.setDefault(True)
        btns.addWidget(ok)
        btns.addWidget(button("Cancel", self.reject))
        lay.addLayout(btns)

    @classmethod
    def ask(cls, parent: QWidget | None, choices: dict[str, str | None]) -> tuple[bool, str | None]:
        """(confirmed, profile id) - the profile id is None for the default browser."""
        d = cls(parent, choices)
        if d.exec() != QDialog.DialogCode.Accepted:
            return False, None
        return True, choices.get(d.menu.currentText())


class SettingsTab(QWidget):
    def __init__(self, app, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self.account_rows: list[QFrame] = []
        inner = QWidget()
        self.sections = QVBoxLayout(inner)
        self.sections.setContentsMargins(0, 0, 6, 0)
        self.sections.setSpacing(10)
        self._build_accounts()
        self._build_options()
        self.sections.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(inner))

    def section(self, title: str) -> QVBoxLayout:
        box = frame("panel")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(8)
        lay.addWidget(label(title, "heading"))
        self.sections.addWidget(box)
        return lay

    # ------------------------------------------------------------- Google accounts
    def _build_accounts(self) -> None:
        acct = self.section("Google accounts")
        self.accounts_help = label("", "muted", wrap=True)
        acct.addWidget(self.accounts_help)
        self.accounts_box = QVBoxLayout()
        self.accounts_box.setSpacing(6)
        acct.addLayout(self.accounts_box)
        row = QHBoxLayout()
        self.add_account_btn = button("+  Add Google account", self.add_account, "primary")
        row.addWidget(self.add_account_btn)
        # Shown only while waiting: closing the browser can't be detected, so this ends the wait.
        self.cancel_btn = button("Cancel", lambda: self.app.cancel_sign_in())
        self.cancel_btn.hide()
        row.addWidget(self.cancel_btn)
        row.addWidget(button("Import credentials.json…", self.import_credentials))
        row.addWidget(button("Setup guide", lambda: self.app.open_readme()))
        row.addStretch(1)
        acct.addLayout(row)

    def refresh_accounts(self) -> None:
        app = self.app
        ready, info, busy = app.credentials_ready(), app.accounts_info(), app.signing_in
        self.accounts_help.setText(ui_model.accounts_help(ready, busy, bool(info)))
        enabled, text = ui_model.add_account_button(ready, busy)
        self.add_account_btn.setEnabled(enabled)
        self.add_account_btn.setText(text)
        self.cancel_btn.setVisible(bool(busy))
        for w in self.account_rows:
            self.accounts_box.removeWidget(w)
            w.deleteLater()
        choices = ui_model.profile_choices(browsers.list_profiles()) if info else {}
        self.account_rows = [self._account_row(a, choices, ready and not busy) for a in info]
        for w in self.account_rows:
            self.accounts_box.addWidget(w)

    def _account_row(self, a: dict, choices: dict[str, str | None], can_sign_in: bool) -> QFrame:
        row = ui_model.account_row(a)
        r = frame("card")
        g = QGridLayout(r)
        g.setContentsMargins(12, 8, 8, 10)
        g.setHorizontalSpacing(8)
        g.addWidget(dot(row.dot), 0, 0, 2, 1, Qt.AlignmentFlag.AlignVCenter)
        g.addWidget(label(row.email, "name", selectable=True), 0, 1)
        g.addWidget(label(row.status, STATUS_ROLES[row.tone], wrap=True), 1, 1)
        btns = QHBoxLayout()
        if row.needs_signin:
            again = button("Sign in again", lambda e=row.email: self.app.add_account(e), "primary")
            again.setEnabled(can_sign_in)
            btns.addWidget(again)
        btns.addWidget(button("Remove", lambda e=row.email, n=a["folders"]: self.remove_account(e, n), "danger"))
        g.addLayout(btns, 0, 2, 2, 1, Qt.AlignmentFlag.AlignVCenter)

        pr = QHBoxLayout()
        pr.addWidget(label(ui_model.PROFILE_LABEL, "muted"))
        menu = QComboBox()
        labels = list(choices)
        current = ui_model.profile_label(self.app.account_profile(a["email"]), choices)
        if current not in labels:
            labels.append(current)
        menu.addItems(labels)
        menu.setCurrentText(current)
        menu.setMinimumWidth(240)
        menu.currentTextChanged.connect(
            lambda text, e=row.email: self.app.set_account_profile(e, choices.get(text)))
        pr.addWidget(menu)
        pr.addStretch(1)
        g.addLayout(pr, 2, 1, 1, 2)
        g.setColumnStretch(1, 1)
        return r

    # ------------------------------------------------------------- account actions
    def add_account(self) -> None:
        ok, profile_id = AddAccountDialog.ask(self, ui_model.profile_choices(browsers.list_profiles()))
        if ok:
            self.app.add_account(browser_profile=profile_id)

    def remove_account(self, email: str, n_folders: int) -> None:
        question = ui_model.remove_account_question(email, n_folders)
        if QMessageBox.question(self, "Drive Sync", safe(question)) == QMessageBox.StandardButton.Yes:
            self.app.set_account_profile(email, None)
            self.app.remove_account(email)

    def import_credentials(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Choose the credentials.json you downloaded",
                                                    os.path.expanduser("~/Downloads"),
                                                    "JSON files (*.json);;All files (*.*)")
        if not path:
            return
        try:
            self.app.import_credentials(Path(path))
        except ValueError as e:
            QMessageBox.critical(self, "Drive Sync", safe(str(e)))
            return
        self.refresh_accounts()
        if not self.app.accounts_info() or QMessageBox.question(
                self, "Drive Sync", ui_model.IMPORTED_ASK) == QMessageBox.StandardButton.Yes:
            self.add_account()

    # ------------------------------------------------------------- options
    def _build_options(self) -> None:
        o = ui_model.option_labels(self.app.cfg)

        sync = self.section("Syncing")
        self.watch_sw = self._switch(sync, ui_model.WATCH_LABEL, o.watch, self.save_options)
        sync.addWidget(self._help(ui_model.WATCH_HELP))
        self.poll_menu = self._menu(sync, ui_model.POLL_LABEL, list(ui_model.POLLS), o.poll)
        sync.addWidget(self._help(ui_model.POLL_HELP))
        self.interval_menu = self._menu(sync, ui_model.INTERVAL_LABEL, o.intervals, o.interval)
        sync.addWidget(self._help(ui_model.INTERVAL_HELP))
        self.notify_sw = self._switch(sync, ui_model.NOTIFY_LABEL, o.notify, self.save_options)
        self.startup_sw = self._switch(sync, ui_model.STARTUP_LABEL, self.app.startup_enabled(),
                                       self.toggle_startup)

        net = self.section("Internet use")
        row = QHBoxLayout()
        row.addWidget(label(ui_model.PRIORITY_LABEL))
        box, self.priority = segments(ui_model.PRIORITIES, o.priority, lambda _name: self.save_options())
        row.addWidget(box)
        row.addStretch(1)
        net.addLayout(row)
        net.addWidget(self._help(ui_model.PRIORITY_HELP))
        self.limit_menu = self._menu(net, ui_model.LIMIT_LABEL, list(ui_model.LIMITS), o.limit)
        net.addWidget(self._help(ui_model.LIMIT_HELP))
        self.metered_sw = self._switch(net, ui_model.METERED_LABEL, o.metered, self.save_options)
        net.addWidget(self._help(ui_model.METERED_HELP))

        upd = self.section("Updates")
        self.update_lbl = label("", "muted", wrap=True)
        upd.addWidget(self.update_lbl)
        self.auto_update_sw = self._switch(upd, ui_model.AUTO_UPDATE_LABEL, o.auto_update, self.save_options)
        upd.addWidget(self._help(ui_model.UPDATE_HELP))
        row = QHBoxLayout()
        row.addWidget(button("Check now", lambda: self.app.check_updates_now()))
        self.update_btn = button("Update now", lambda: self.app.install_update(), "primary")
        row.addWidget(self.update_btn)
        row.addStretch(1)
        upd.addLayout(row)
        self.refresh_update()

        look = self.section("Appearance")
        box, self.appearance = segments(ui_model.APPEARANCES, o.appearance, self.set_appearance)
        row = QHBoxLayout()
        row.addWidget(box)
        row.addStretch(1)
        look.addLayout(row)

        bottom = QHBoxLayout()
        bottom.addWidget(label(ui_model.CLOSE_HINT, "muted", wrap=True), 1)
        if updater.installed_copy():
            bottom.addWidget(button("Uninstall…", lambda: self.app.uninstall()))
        bottom.addWidget(button("Quit Drive Sync", self.confirm_quit, "danger"))
        self.sections.addLayout(bottom)

    @staticmethod
    def _help(text: str):
        return label(text, "muted", wrap=True)

    @staticmethod
    def _switch(parent: QVBoxLayout, text: str, on: bool, on_change) -> Switch:
        sw = Switch(text, on)
        sw.toggled.connect(lambda _on: on_change())
        parent.addWidget(sw)
        return sw

    def _menu(self, parent: QVBoxLayout, text: str, items: list[str], current: str) -> QComboBox:
        row = QHBoxLayout()
        row.addWidget(label(text))
        menu = QComboBox()
        menu.addItems(items if current in items else [current, *items])
        menu.setCurrentText(current)
        menu.setMinimumWidth(180)
        menu.currentTextChanged.connect(lambda _t: self.save_options())
        row.addWidget(menu)
        row.addStretch(1)
        parent.addLayout(row)
        return menu

    def save_options(self) -> None:
        cfg = ui_model.options_cfg(
            self.app.cfg, interval=self.interval_menu.currentText(), poll=self.poll_menu.currentText(),
            limit=self.limit_menu.currentText(), priority=self.priority.checkedButton().text(),
            watch=self.watch_sw.isChecked(), notify=self.notify_sw.isChecked(),
            metered=self.metered_sw.isChecked(), auto_update=self.auto_update_sw.isChecked())
        if cfg != self.app.cfg:
            self.app.save_config(cfg)

    def set_appearance(self, value: str) -> None:
        self.app.save_config(dict(self.app.cfg, appearance=value))
        style.apply(QApplication.instance(), value)

    def toggle_startup(self) -> None:
        try:
            self.app.set_startup(self.startup_sw.isChecked())
        except Exception as e:  # noqa: BLE001 - shortcut / permissions problems
            QMessageBox.critical(self, "Drive Sync", safe(f"Couldn't change the startup setting:\n{e}"))
        self.startup_sw.set_checked_quietly(self.app.startup_enabled())   # what Windows really has now

    def refresh_update(self) -> None:
        text, button_text = ui_model.update_line(__version__, updater.installed_copy(), self.app.update_status,
                                                 self.app.update)
        self.update_lbl.setText(text)
        if button_text:
            self.update_btn.setText(button_text)
            self.update_btn.show()
        else:
            self.update_btn.hide()

    def confirm_quit(self) -> None:
        if QMessageBox.question(self, "Drive Sync", ui_model.QUIT_QUESTION) == QMessageBox.StandardButton.Yes:
            self.app.quit()
