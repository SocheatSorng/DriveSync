"""The Qt main window: the status header, then the tabs (arriving task by task, see tasks/todo.md).

Like the CustomTkinter window it only reads the App's state and handles the App's queue messages on
Qt's main thread (a QTimer polls the queue); worker threads never touch a widget.
"""
from __future__ import annotations

import queue
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QTabWidget, QVBoxLayout, QWidget

import ui_model
from qtui import style
from qtui.activity_tab import ActivityTab
from qtui.folders_tab import FoldersTab
from qtui.header import Header
from qtui.settings_tab import SettingsTab
from qtui.space_tab import SpaceTab
from qtui.widgets import safe

HIDDEN_NOTICE = "Drive Sync is still running in the system tray."


class MainWindow(QMainWindow):
    POLL_VISIBLE_MS = 150    # react quickly while you're looking at the window
    POLL_HIDDEN_MS = 1000    # in the tray: only "show window" / "quit" / errors need handling

    def __init__(self, app, icon_path: Path | None = None):
        super().__init__()
        self.app = app
        self.hidden_notice_shown = False
        self.setWindowTitle("Drive Sync")
        self.resize(820, 560)
        self.setMinimumSize(680, 460)
        if icon_path and Path(icon_path).exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.apply_theme()
        QApplication.styleHints().colorSchemeChanged.connect(lambda _scheme: self.apply_theme())

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(22, 16, 22, 16)
        lay.setSpacing(10)
        self.header = Header(app)
        lay.addWidget(self.header)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)   # no line under the tabs
        self.folders = FoldersTab(app)
        self.tabs.addTab(self.folders, "Folders")
        self.settings = SettingsTab(app)
        self.tabs.addTab(self.settings, "Settings")
        self.activity = ActivityTab(app)
        self.tabs.addTab(self.activity, "Activity")
        self.space = SpaceTab(app)
        self.tabs.addTab(self.space, "Space")
        lay.addWidget(self.tabs, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.refresh_state()
        self.refresh_accounts()

    # ------------------------------------------------------------- look
    def apply_theme(self) -> None:
        """Light or dark, from Settings → Appearance ("System" follows Windows, also when it changes)."""
        style.apply(QApplication.instance(), self.app.cfg.get("appearance", "System"))

    # ------------------------------------------------------------- showing
    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Closing the window hides it: Drive Sync keeps syncing from the tray."""
        event.ignore()
        self.hide()
        if not self.hidden_notice_shown:
            self.hidden_notice_shown = True
            self.app.notify(HIDDEN_NOTICE)

    # ------------------------------------------------------------- updating
    def refresh_state(self) -> None:
        self.header.refresh()

    def refresh_accounts(self) -> None:
        """Accounts changed: Settings lists them and the folder cards show them."""
        self.settings.refresh_accounts()
        self.folders.refresh()

    def on_account_added(self, email: str, browser_profile: str | None = None) -> None:
        """Remember the browser profile it signed in with; folders without an account get this one."""
        if browser_profile:
            self.app.set_account_profile(email, browser_profile)
        cfg = ui_model.assign_account(self.app.cfg, email)
        if cfg is not None:
            self.app.save_config(cfg)
        self.refresh_accounts()
        self.app.notify(f"Google account added: {email}")

    def start_polling(self) -> None:
        self.timer.start(self.POLL_VISIBLE_MS if self.isVisible() else self.POLL_HIDDEN_MS)

    def poll(self) -> None:
        """Handle messages from the worker / tray threads."""
        try:
            while True:
                msg = self.app.ui.get_nowait()
                kind = msg[0]
                if kind == "state":
                    self.refresh_state()
                elif kind == "activity":
                    self.activity.add(msg[1])
                elif kind == "last_check":
                    self.activity.refresh_header()
                elif kind == "account":
                    self.refresh_accounts()
                elif kind == "space":
                    self.space.render()
                elif kind == "space_progress":
                    self.space.show_progress()
                elif kind == "update":
                    self.settings.refresh_update()
                elif kind == "account_added":
                    self.on_account_added(*msg[1:])
                elif kind == "error":
                    self.show_window()
                    QMessageBox.critical(self, "Drive Sync", safe(msg[1]))   # may name files: never HTML
                elif kind == "show":
                    self.show_window()
                elif kind == "quit":
                    self.app.quit()
                    return
                # activity, account, space, update...: their screens come in later tasks
        except queue.Empty:
            pass
        flag = self.app.show_flag
        if flag.exists():   # another copy of Drive Sync was started: show this one instead
            try:
                flag.unlink()
            except OSError:
                pass
            self.show_window()
        self.timer.setInterval(self.POLL_VISIBLE_MS if self.isVisible() else self.POLL_HIDDEN_MS)
