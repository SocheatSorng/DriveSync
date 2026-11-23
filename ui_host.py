"""Which window Drive Sync shows: the Qt one (default) or the classic CustomTkinter one.

The window moved to Qt (tasks/plan.md). For one release the classic window stays as a fallback:
set DRIVESYNC_UI=tk before starting Drive Sync to get it back.

A host owns the window and its event loop. The App talks to it through the same queue messages
either way (App.ui), so syncing doesn't depend on which window is used.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

import logs

UI_ENV = "DRIVESYNC_UI"


def chosen_ui(env: Mapping[str, str] = os.environ) -> str:
    """"tk" when DRIVESYNC_UI=tk (the classic window, as a fallback), else "qt"."""
    return "tk" if env.get(UI_ENV, "").strip().lower() == "tk" else "qt"


class TkHost:
    """The CustomTkinter window (gui.MainWindow) and Tk's event loop."""

    def __init__(self, app, start_hidden: bool):
        import customtkinter as ctk

        ctk.set_appearance_mode(app.cfg.get("appearance", "System").lower())
        ctk.set_default_color_theme("blue")
        self.root = ctk.CTk()
        self.root.report_callback_exception = logs.tk_callback_error   # no console: errors go to the log
        if start_hidden:
            self.root.withdraw()
        from gui import MainWindow  # after CTk() exists
        self.window = MainWindow(self.root, app, build=not start_hidden)   # built when first opened

    def run(self) -> None:
        self.root.after(150, self.window.poll)
        self.root.mainloop()

    def stop(self) -> None:
        self.root.destroy()


class QtHost:
    """The Qt window (qtui.main_window.MainWindow) and Qt's event loop."""

    def __init__(self, app, start_hidden: bool, icon_path: Path | None = None):
        import PySide6
        from PySide6.QtWidgets import QApplication

        logs.log.info("Qt for Python %s", PySide6.__version__)   # licences/THIRD-PARTY-NOTICES.txt points here
        self.qapp = QApplication.instance() or QApplication([sys.argv[0]])
        self.qapp.setQuitOnLastWindowClosed(False)   # closing the window hides it; the tray keeps running
        from qtui.main_window import MainWindow
        self.window = MainWindow(app, icon_path)
        if not start_hidden:
            self.window.show_window()

    @staticmethod
    def install_error_hook() -> None:
        """Qt reports an error in a button / window handler through sys.excepthook and carries on:
        write it to the log (there's no console)."""
        sys.excepthook = logs.tk_callback_error

    def run(self) -> None:
        self.install_error_hook()
        self.window.start_polling()
        self.qapp.exec()

    def stop(self) -> None:
        self.window.timer.stop()
        self.window.hide()
        self.qapp.quit()


def host_class(kind: str) -> type:
    return QtHost if kind == "qt" else TkHost


def make_host(app, start_hidden: bool, icon_path: Path | None = None):
    kind = chosen_ui()
    if kind == "qt":
        return QtHost(app, start_hidden, icon_path)
    return TkHost(app, start_hidden)
