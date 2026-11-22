"""The log file (%LOCALAPPDATA%\\DriveSync\\logs\\drive_sync.log) - set up so that nothing goes missing:

* Rotation never stops logging. When the file is full and can't be renamed (another program has it
  open), writing carries on in the same file and rotation is tried again later. (The standard handler
  fails on every line from then on, so the log would go quiet until the app restarts.)
* Errors that would otherwise vanish - in background threads and in window callbacks of the packaged
  app, which has no console - are written to the log with their traceback.
* A run that ended without "Drive Sync stopped" (killed, crashed, PC switched off) is noted at the
  next start.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"
MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 4
log = logging.getLogger("drive_sync")


class SafeRotatingFileHandler(RotatingFileHandler):
    """A RotatingFileHandler whose failed rotation doesn't silence the log (see the module docstring)."""

    retry_after = 60.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._next_try = 0.0

    def shouldRollover(self, record) -> bool:  # noqa: N802 - logging's API
        return time.monotonic() >= self._next_try and super().shouldRollover(record)

    def doRollover(self) -> None:  # noqa: N802
        try:
            super().doRollover()
        except OSError:
            self._next_try = time.monotonic() + self.retry_after
            if self.stream is None:            # the old file was closed before renaming failed: reopen it
                self.stream = self._open()


def setup(path: Path, level: int = logging.INFO) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = SafeRotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8", delay=False)
    handler.setFormatter(logging.Formatter(FORMAT, DATEFMT))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)
    # The sign-in library logs the browser redirect URL, which contains the one-time OAuth code.
    logging.getLogger("google_auth_oauthlib").setLevel(logging.WARNING)
    logging.captureWarnings(True)
    return handler


def install_crash_hooks() -> None:
    """Write uncaught errors (main thread and background threads) to the log."""
    def main_hook(kind, value, tb):
        if issubclass(kind, KeyboardInterrupt):
            return sys.__excepthook__(kind, value, tb)
        log.critical("Unexpected error", exc_info=(kind, value, tb))

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread else "?"
        log.error("Unexpected error in background task %s", name,
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = main_hook
    threading.excepthook = thread_hook


def tk_callback_error(kind, value, tb) -> None:
    """For Tk's report_callback_exception: a button / window handler failed."""
    log.error("Error in the window", exc_info=(kind, value, tb))


class RunMarker:
    """A file that exists while Drive Sync runs; still there at the next start = the last run didn't stop
    normally."""

    def __init__(self, path: Path):
        self.path = path

    def start(self) -> None:
        if self.path.exists():
            try:
                since = time.strftime(DATEFMT, time.localtime(self.path.stat().st_mtime))
            except OSError:
                since = "?"
            log.warning("The previous run (started %s) ended without closing normally - it was ended by "
                        "Task Manager or an update, crashed, or the PC was switched off", since)
        try:
            self.path.write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            pass

    def stop(self) -> None:
        self.path.unlink(missing_ok=True)
