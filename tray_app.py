"""
Drive Sync desktop app: main window + system-tray icon + background syncing.

    pythonw tray_app.py            open the window
    pythonw tray_app.py --hidden   start in the tray only (used at Windows login)

Settings are stored in config.json in the app folder (see drive_sync.APP_DIR).
"""
from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import customtkinter as ctk
import pystray
from PIL import Image

import accounts
import activity
import browsers
import logo
import logs
import metered
import updater
from downloader import PARALLEL
from downloader import apply_settings as apply_network_settings
from drive_sync import APP_DIR, DEFAULT_EXCLUDES, RESOURCE_DIR, Drive, build_service, sync_folder
from progress import Paused, Progress, count_files, human_size
from version import __version__
from watcher import ChangeTracker, FolderWatcher

CONFIG_PATH = APP_DIR / "config.json"
CREDS_PATH = APP_DIR / "credentials.json"
TOKEN_PATH = APP_DIR / "token.json"
LOG_PATH = APP_DIR / "logs" / "drive_sync.log"
UPDATE_STATE = APP_DIR / "update.json"
RUN_MARKER = logs.RunMarker(APP_DIR / "logs" / "running")
SHOW_FLAG = APP_DIR / ".show"
ICO_PATH = RESOURCE_DIR / "assets" / "drivesync.ico"
STARTUP_LNK = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/Drive Sync.lnk"
WINDIR = Path(os.environ.get("WINDIR", r"C:\Windows"))
NOTEPAD = WINDIR / "notepad.exe"
POWERSHELL = WINDIR / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
DRIVE_SETTLE_SECONDS = 1.0  # Drive-side changes: short wait (a few files often change together)
SYNC_STATE_PATH = APP_DIR / "sync_state.json"  # where Drive's change list was read up to, etc.
FULL_CHECK_HOURS = 24       # two-way folders: check every file on both sides this often (safety net)
MAX_PENDING_IDS = 20_000    # more Drive changes than this waiting: just check everything

DEFAULT_CONFIG = {
    "interval_minutes": 15,
    "startup_delay_seconds": 30,
    "watch_changes": True,   # sync a folder seconds after something in it changes
    "drive_poll_seconds": 10,  # two-way: how often to ask Google "anything new?" (one small request)
    "high_priority": False,    # big downloads use 4 connections -> bigger share when other apps are busy
    "speed_limit_kbps": 0,     # cap on Drive Sync's combined speed in KB/s (0 = no limit)
    "notify_on_changes": False,
    "pause_on_metered": False,  # hold syncing on phone hotspots / metered Wi-Fi
    "auto_update": True,        # install new versions from GitHub Releases by itself (installed copies)
    "appearance": "System",
    "folders": [],
}
log = logging.getLogger("drive_sync")
METERED_STATUS = "Waiting - metered connection"
FULL_RETRY_SECONDS = 30 * 60   # a full Drive / disk is tried again this often (or on "Sync now")
QUOTA_MAX_AGE = 10 * 60        # how long an account's storage numbers are reused
NEARLY_FULL = 0.9              # warn once an account's Drive is this full


# --------------------------------------------------------------------------- #
def tray_icon_size() -> int:
    """The tray's icon size in pixels (16 at 100% display scaling), so the logo is drawn for it exactly."""
    try:
        return max(16, int(ctypes.windll.user32.GetSystemMetrics(49)))  # SM_CXSMICON
    except Exception:  # noqa: BLE001
        return 16


def make_icon(color: str | None = None) -> Image.Image:
    """The logo in full color, or all in one color that shows the state."""
    return logo.draw(tray_icon_size(), color)


SYNC_FRAMES = logo.draw_syncing_frames(tray_icon_size())  # a light runs round the logo - files moving
FRAME_SECONDS = 0.075                                     # 20 frames: one lap in 1.5 seconds
ICONS = {
    "idle": make_icon(),              # full color - up to date
    "syncing": SYNC_FRAMES[0],        # full color, light on its outline - working
    "error": make_icon("#ff4d4f"),    # red        - needs attention
    "paused": logo.draw_paused(tray_icon_size()),  # grey + pause badge - paused
}


def animations_enabled() -> bool:
    """Windows' "Show animations in Windows" (Accessibility / Visual effects) setting."""
    try:
        on = ctypes.c_bool(True)
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)  # SPI_GETCLIENTAREAANIMATION
        return bool(on.value)
    except Exception:  # noqa: BLE001
        return True


class TrayAnimation:
    """Spins the tray icon while files are uploading or downloading.

    Each frame's icon handle is made once and handed straight to Windows, so animating doesn't
    write a temporary .ico file per frame (what setting pystray's icon.icon does)."""

    def __init__(self, icon, frames: list[Image.Image]):
        self.icon, self.frames = icon, frames
        self.handles: list | None = None

    def _load(self) -> list:
        from pystray._util import serialized_image, win32
        handles = []
        for frame in self.frames:
            with serialized_image(frame, "ICO") as path:
                handles.append(win32.LoadImage(None, path, win32.IMAGE_ICON, 0, 0,
                                               win32.LR_DEFAULTSIZE | win32.LR_LOADFROMFILE))
        return handles

    def show(self, i: int) -> None:
        from pystray._util import win32
        if self.handles is None:
            self.handles = self._load()
        self.icon._message(win32.NIM_MODIFY, win32.NIF_ICON, hIcon=self.handles[i % len(self.handles)])


PAUSE_CHOICES = [("For 30 minutes", 30 * 60), ("For 1 hour", 3600), ("For 3 hours", 3 * 3600)]
MORNING_HOUR = 7   # "until tomorrow morning" ends at 7:00


def seconds_until(hour: int, now: float | None = None) -> float:
    """Seconds from now until the next hour:00 (tomorrow's if today's has passed)."""
    t = time.localtime(now if now is not None else time.time())
    target = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hour, 0, 0, 0, 0, -1))
    if target <= time.mktime(t):
        target = time.mktime((t.tm_year, t.tm_mon, t.tm_mday + 1, hour, 0, 0, 0, 0, -1))
    return target - time.mktime(t)


def quota_text(q: dict | None) -> str:
    """'12.3 GB of 15.0 GB used (82%)' - or '' when unknown."""
    if not q:
        return ""
    if not q.get("limit"):
        return f"{human_size(q['usage'])} used"
    return f"{human_size(q['usage'])} of {human_size(q['limit'])} used ({100 * q['usage'] // q['limit']}%)"


def single_instance() -> bool:
    """False if another copy is already running."""
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\DriveSyncTrayApp")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS



# --------------------------------------------------------------------------- #
class App:
    def __init__(self, start_hidden: bool):
        self.ui: queue.Queue = queue.Queue()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.paused = False
        self.pause_until: float | None = None       # time.time() when a timed pause ends (None: until resumed)
        self.metered_held = False                   # waiting: on a metered connection (setting on)
        self.animating = False                      # the tray icon is spinning (TrayAnimation)
        self.update: updater.Update | None = None   # a newer version on GitHub
        self.update_status = ""                     # shown in Settings → Updates
        self.update_wake = threading.Event()        # "Check now"
        self._update_notified: str | None = None
        self.state, self.status, self.detail = "idle", "Starting…", ""
        self.drives: dict[str, Drive] = {}          # email -> connected Drive (worker thread only)
        self.needs_signin: set[str] = set()         # accounts whose login expired / was revoked
        self.signing_in: str | None = None          # "new" or an email while the browser flow runs
        self.force_full = False                     # "Sync now" pressed
        self.resume_full = False                    # a full check was paused part-way: finish it on resume
        self.storage_full: dict[str, set[str]] = {}  # last sync: "drive" -> accounts, "pc" -> folders
        self._full_notified: frozenset = frozenset()  # what the last "storage full" notification said
        self.quota: dict[str, dict] = {}            # account -> {"limit", "usage", "at"} (Drive storage)
        self._quota_warned: set[str] = set()        # accounts already warned about being nearly full
        self.activity = activity.ActivityStore(APP_DIR / "activity.jsonl")
        self.last_check: dict | None = None         # most recent sync, even if nothing changed
        self.progress: Progress | None = None       # set while a sync runs
        self.file_lines: list[str] = []             # files transferring at once (window: with folders)
        self.menu_file_lines: list[str] = []        # the same, names only (tray menu)
        # Saved across restarts, so a restart only looks at what changed while the app was off:
        #   tokens:    account -> position in Drive's change list
        #   pending:   folder -> Drive ids changed since its last sync
        #   last_full: folder -> when every file was last checked
        self.sync_state = self._load_sync_state()
        self.change_tokens: dict[str, str] = self.sync_state["tokens"]
        self.drive_marked: set = set()              # folders queued because Drive changed
        self._last_event_key = None
        self.changes = ChangeTracker()              # folders changed on disk, waiting to settle
        self.watcher = FolderWatcher(self._on_folder_changed, ignore_under=(str(APP_DIR),))
        self.remote_ids: dict[tuple[str, str], str] = {}  # (local folder, account) -> Drive folder id
        self.show_flag = SHOW_FLAG
        self.readme_path = RESOURCE_DIR / "README.md"
        self.start_hidden = start_hidden
        self.cfg = self.load_config()
        self.apply_network(self.cfg)

        ctk.set_appearance_mode(self.cfg.get("appearance", "System").lower())
        ctk.set_default_color_theme("blue")
        self.root = ctk.CTk()
        self.root.report_callback_exception = logs.tk_callback_error   # no console: errors go to the log
        if start_hidden:
            self.root.withdraw()
        from gui import MainWindow  # after CTk() exists
        self.window = MainWindow(self.root, self, build=not start_hidden)  # built when first opened

        self.icon = pystray.Icon("DriveSync", ICONS["idle"], "Drive Sync", menu=pystray.Menu(
            pystray.MenuItem(lambda _: self.status, None, enabled=False),
            pystray.MenuItem(lambda _: self.menu_detail(), None, enabled=False,
                             visible=lambda _: bool(self.menu_detail())),
            # One line per file while several transfer at once (up to the number of parallel downloads).
            *[pystray.MenuItem(lambda _, i=i: self.menu_file(i), None, enabled=False,
                               visible=lambda _, i=i: bool(self.menu_file(i))) for i in range(PARALLEL)],
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open Drive Sync", lambda *_: self.ui.put(("show",)), default=True),
            pystray.MenuItem("Sync now", lambda *_: self.sync_now()),
            pystray.MenuItem(lambda _: f"Update to {self.update.version}" if self.update else "",
                             lambda *_: self.install_update(), visible=lambda _: self.update is not None),
            pystray.MenuItem("Resume syncing", lambda *_: self.resume(), visible=lambda _: self.paused),
            pystray.MenuItem("Pause syncing", pystray.Menu(
                *[pystray.MenuItem(label, lambda *_, s=secs: self.pause(s)) for label, secs in PAUSE_CHOICES],
                pystray.MenuItem("Until tomorrow morning", lambda *_: self.pause(seconds_until(MORNING_HOUR))),
                pystray.MenuItem("Until I resume", lambda *_: self.pause(None)),
            ), visible=lambda _: not self.paused),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda *_: self.ui.put(("quit",))),
        ))

    # ---------------------------------------------------------------- config
    def load_config(self) -> dict:
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            data = {}
        except ValueError as e:
            log.error("config.json is not valid JSON (%s) - using defaults", e)
            data = {}
        cfg = {**DEFAULT_CONFIG, **data}
        cfg["folders"] = [f for f in cfg.get("folders", []) if isinstance(f, dict) and f.get("local")]
        return cfg

    def save_config(self, cfg: dict) -> None:
        self.cfg = cfg
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        tmp.replace(CONFIG_PATH)
        log.info("Settings saved")
        self.refresh_watchers()  # folders / exclusions / instant-sync switch may have changed
        self.apply_network(cfg)
        self.wake.set()          # e.g. "don't sync on metered connections" applies right away

    @staticmethod
    def apply_network(cfg: dict) -> None:
        """Internet priority and speed limit - take effect immediately, even mid-transfer."""
        try:
            limit = max(0, int(cfg.get("speed_limit_kbps") or 0)) * 1024
        except (TypeError, ValueError):
            limit = 0
        apply_network_settings(bool(cfg.get("high_priority")), limit)

    # ----------------------------------------------------------------- state
    def set_state(self, state: str, status: str, detail: str = "") -> None:
        self.state, self.status, self.detail = state, status, detail
        if state != "syncing" and (state, status, detail) != getattr(self, "_logged_state", None):
            self._logged_state = (state, status, detail)   # each new status once, not every refresh
            (log.warning if state == "error" else log.info)("Status: %s%s", status, f" - {detail}" if detail else "")
        try:
            if not self.animating:   # while spinning, the animation thread owns the icon
                self.icon.icon = self.static_icon()
            tip = f"Drive Sync - {status}" + (f"\n{detail}" if state == "syncing" and detail else "")
            self.icon.title = tip[:127]  # Windows tooltip limit
            self.icon.update_menu()
        except Exception as e:  # noqa: BLE001 - icon may not be running yet
            log.debug("Tray icon not ready: %s", e)
        self.ui.put(("state",))

    def static_icon(self) -> Image.Image:
        return ICONS["paused" if self.paused or self.metered_held else self.state]

    def should_spin(self) -> bool:
        """Spin only while files are actually moving (not while comparing), and not when paused."""
        p = self.progress
        return (self.state == "syncing" and not self.paused and not self.metered_held
                and p is not None and bool(p.active))

    def animate(self) -> None:
        """Runs on its own thread for the life of the app."""
        anim, frame, allowed, checked = TrayAnimation(self.icon, SYNC_FRAMES), 0, True, 0.0
        while not self.stopping.is_set():
            if time.monotonic() - checked > 10:      # the Windows setting can change while we run
                allowed, checked = animations_enabled(), time.monotonic()
            spin = allowed and self.should_spin()
            try:
                if spin:
                    self.animating = True
                    anim.show(frame)
                    frame += 1
                elif self.animating:
                    self.animating = False
                    self.icon.icon = self.static_icon()   # back to the still icon for the state
            except Exception as e:  # noqa: BLE001 - icon not ready / gone: try again shortly
                log.debug("Tray animation: %s", e)
            self.stopping.wait(FRAME_SECONDS if spin else 0.25)

    def notify(self, msg: str) -> None:
        try:
            self.icon.notify(msg, "Drive Sync")
        except Exception as e:  # noqa: BLE001 - notifications are best-effort
            log.debug("Notification failed: %s", e)

    def icon_image(self, state: str) -> Image.Image:
        return ICONS[state]

    def apply_window_icon(self, win) -> None:
        if ICO_PATH.exists():
            try:
                win.iconbitmap(str(ICO_PATH))
            except Exception as e:  # noqa: BLE001
                log.debug("Couldn't set window icon: %s", e)

    # ---------------------------------------------------------------- worker
    def worker(self) -> None:
        """A check at start and on the timer (safety net); changed folders sync within seconds.

        Two-way folders only look at what changed: Drive's change list (saved across restarts) plus a
        quick size/time comparison on this PC. Every file is checked once a day and on "Sync now"."""
        delay = self.cfg.get("startup_delay_seconds", 30) if self.start_hidden else 1
        self.set_state("idle", "Starting…", "Waiting for the network" if self.start_hidden else "")
        self.wake.wait(delay)
        self.wake.clear()
        next_full = time.monotonic()  # check right away
        next_poll = time.monotonic()  # first poll: what changed on Drive while the app was off
        first = True
        while not self.stopping.is_set():
            if self.paused and self.pause_until and time.time() >= self.pause_until:
                self.resume()       # a timed pause is over
                self.wake.clear()
            self._check_metered()
            held = self.paused or self.metered_held
            if not held and time.monotonic() >= next_poll:
                try:
                    self._poll_drive()  # marks two-way folders whose Drive account has new changes
                except Exception:  # noqa: BLE001 - keep the worker alive; it's tried again next time
                    log.exception("Checking Google Drive for changes failed")
                next_poll = time.monotonic() + self._poll_interval()
            if not held:
                if self.force_full or self.resume_full or time.monotonic() >= next_full:
                    manual = self.force_full
                    trigger = "manual" if manual else "startup" if first else "scheduled"
                    self.force_full, self.resume_full, first = False, False, False
                    ok = self.sync_all(trigger=trigger, full=manual, scan=True)
                    next_full = time.monotonic() + (self._interval() if ok else
                                                    min(self._interval(), self._retry_delay()))
                else:
                    changed = self.changes.take_settled()
                    trigger = "drive" if changed and changed <= self.drive_marked else "changes"
                    self.drive_marked -= changed
                    if changed and not self.sync_all(only=changed, trigger=trigger):
                        next_full = min(next_full, time.monotonic() + self._retry_delay())
            # Sleep until the timer, the next settled change, or a wake-up (change / Sync now / quit).
            due = [next_full, next_poll] if not held else []
            if self.paused and self.pause_until:
                due.append(time.monotonic() + (self.pause_until - time.time()))
            if self.metered_held:
                due.append(time.monotonic() + metered.CHECK_EVERY)   # look again for a free connection
            if not held and (settle := self.changes.next_due()) is not None:
                due.append(settle)
            timeout = max(0.5, min(due) - time.monotonic()) if due else None
            self.wake.wait(timeout)
            self.wake.clear()

    def _update_quota(self, email: str, drive) -> None:
        """Refresh the account's Drive storage numbers (every few minutes), warn once when it's nearly
        full, and tell the upload pre-check how much space is left."""
        q = self.quota.get(email)
        if not q or time.time() - q["at"] > QUOTA_MAX_AGE:
            try:
                q = {**drive.storage_quota(), "at": time.time()}
            except Exception as e:  # noqa: BLE001 - only informative
                log.debug("Couldn't read Drive storage for %s: %s", email, e)
                drive.quota_free = None
                return
            self.quota[email] = q
            self.ui.put(("account",))
        limit, usage = q["limit"], q["usage"]
        drive.quota_free = None if not limit else limit - usage
        if limit and usage >= NEARLY_FULL * limit:
            if email not in self._quota_warned:
                self._quota_warned.add(email)
                self.notify(f"Google Drive for {email} is {quota_text(q)}. Free up space soon so "
                            "uploads keep working.")
        else:
            self._quota_warned.discard(email)

    def _metered_hold(self) -> bool:
        """Hold syncing now? (setting on, and Windows says the connection is metered)"""
        return bool(self.cfg.get("pause_on_metered")) and metered.is_metered()

    def _check_metered(self) -> None:
        held = self._metered_hold()
        if held == self.metered_held:
            return
        self.metered_held = held
        if self.paused:
            return
        if held:
            self.set_state("idle", METERED_STATUS, "Syncing carries on when you're on another network "
                                                   "(Settings → Internet use)")
        else:
            self.set_state("idle", "Resuming…", "")

    # ------------------------------------------------------------------ updates
    def _set_update_status(self, text: str) -> None:
        self.update_status = text
        self.ui.put(("update",))

    def update_loop(self) -> None:
        """Check GitHub for a newer version a minute after start, then once a day (or on "Check now")."""
        state = updater.load_state(UPDATE_STATE)
        self.update_wake.wait(60)
        while not self.stopping.is_set():
            manual = self.update_wake.is_set()
            self.update_wake.clear()
            if manual or time.time() - state.get("last_check", 0) >= updater.CHECK_EVERY:
                self._check_for_update(manual)
                state["last_check"] = time.time()
                updater.save_state(UPDATE_STATE, state)
            if self.update and self.cfg.get("auto_update", True) and updater.installed_copy():
                self._auto_install()
            self.update_wake.wait(3600)

    def _check_for_update(self, manual: bool) -> None:
        self._set_update_status("Checking for updates…")
        try:
            u = updater.check()
        except Exception as e:  # noqa: BLE001 - offline etc.: try again later
            log.info("Couldn't check for updates: %s", e)
            self._set_update_status("Couldn't check for updates (no internet?) - will try again later")
            return
        self.update = u
        if not u:
            self._set_update_status(f"Up to date (checked {time.strftime('%H:%M')})")
            return
        log.info("Drive Sync %s is available", u.version)
        auto = self.cfg.get("auto_update", True) and updater.installed_copy()
        self._set_update_status(f"Version {u.version} is available" + (" - installs when nothing is syncing"
                                                                        if auto else ""))
        self.icon.update_menu()
        if self._update_notified != u.version and not auto:
            self._update_notified = u.version
            self.notify(f"Drive Sync {u.version} is available. Right-click the tray icon → Update.")

    def _auto_install(self) -> None:
        if self.state == "syncing" or self.progress is not None:
            return          # not in the middle of a sync; the hourly loop tries again
        self.install_update(auto=True)

    def install_update(self, auto: bool = False) -> None:
        """Download the new version's installer and run it (it restarts Drive Sync)."""
        u = self.update
        if not u:
            return
        if not updater.installed_copy():
            self.open_url(u.page)   # a loose .exe / source copy: show where to get it
            return
        if not auto and threading.current_thread() is threading.main_thread():
            threading.Thread(target=self.install_update, daemon=True).start()
            return
        try:
            self._set_update_status(f"Downloading version {u.version}…")
            setup = updater.download(u, APP_DIR / "updates",
                                     on_progress=lambda f: self._set_update_status(
                                         f"Downloading version {u.version}… {int(f * 100)}%"))
        except Exception as e:  # noqa: BLE001
            log.error("Update download failed: %s", e)
            self._set_update_status(f"Couldn't download the update: {e}")
            return
        log.info("Installing Drive Sync %s", u.version)
        self.notify(f"Updating Drive Sync to {u.version} - it restarts by itself in a moment.")
        updater.run_installer(setup)
        self.ui.put(("quit",))

    def uninstall(self) -> None:
        """Start the uninstaller (installed copies). It closes Drive Sync and asks about your settings."""
        exe = Path(sys.executable).parent / "unins000.exe"
        if exe.exists():
            subprocess.Popen([str(exe)], creationflags=0x00000008, close_fds=True)  # noqa: S603 - DETACHED_PROCESS

    def check_updates_now(self) -> None:
        self.update_wake.set()

    def _retry_delay(self) -> int:
        """Seconds before a failed sync is tried again: soon, unless a side is full (that takes
        someone freeing space - "Sync now" tries right away)."""
        return FULL_RETRY_SECONDS if self.storage_full else 120

    def _poll_drive(self) -> None:
        """Ask Google (cheaply) what changed in each two-way account since last time. The changed
        ids are saved per folder, so changes made while the app was off are found at the next start."""
        cfg = self.load_config()
        known = accounts.list_accounts()
        by_account: dict[str, list] = {}
        for f in cfg.get("folders", []):
            if f.get("mode", "two_way") == "two_way":
                email = self.folder_account(f, known)
                if email in known and email not in self.needs_signin:
                    by_account.setdefault(email, []).append(self.folder_key(f, known))
        for email, keys in by_account.items():
            try:
                drive = self.drives.get(email) or self._drive_for(email, known)
                if drive is None:
                    continue
                token = self.change_tokens.get(email)
                ids: set[str] = set()
                if token is not None:
                    try:
                        ids, new_token = drive.changes_since(token)
                    except Exception as e:  # noqa: BLE001
                        if not self._is_bad_token(e):
                            raise
                        log.info("Google's change list for %s restarted - checking everything once", email)
                        token = None
                if token is None:
                    # Starting the change list now: whatever happened before is unknown, so these
                    # folders get one full check (which also covers anything from before this point).
                    new_token = drive.changes_start_token()
                    for key in keys:
                        self.sync_state["last_full"].pop(self._state_key(key), None)
                for key in keys:
                    if ids:
                        k = self._state_key(key)
                        pending = set(self.sync_state["pending"].get(k, ())) | ids
                        if len(pending) > MAX_PENDING_IDS:
                            pending = set()
                            self.sync_state["last_full"].pop(k, None)   # too much changed: check it all
                        self.sync_state["pending"][k] = sorted(pending)
                self.change_tokens[email] = new_token
                self._save_sync_state()     # token and pending ids together, so nothing is lost
                if ids:
                    log.info("Changes on Google Drive (%s) - checking two-way folders", email)
                    for key in keys:
                        self.drive_marked.add(key)
                        self.changes.mark(key, settle=DRIVE_SETTLE_SECONDS)
            except Exception as e:  # noqa: BLE001 - offline etc.; the timer still covers it
                log.debug("Couldn't check Drive for changes (%s): %s", email, e)

    @staticmethod
    def _is_bad_token(e: Exception) -> bool:
        status = getattr(e, "status_code", None) or getattr(getattr(e, "resp", None), "status", None)
        return status in (400, 404, 410)

    # Saved sync state ------------------------------------------------------------
    @staticmethod
    def _state_key(key) -> str:
        return f"{key[0]}|{key[1]}"

    def _load_sync_state(self) -> dict:
        state = {"tokens": {}, "pending": {}, "last_full": {}}
        try:
            data = json.loads(SYNC_STATE_PATH.read_text(encoding="utf-8"))
            for k in state:
                if isinstance(data.get(k), dict):
                    state[k] = data[k]
        except (OSError, ValueError):
            pass
        return state

    def _save_sync_state(self) -> None:
        try:
            SYNC_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = SYNC_STATE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.sync_state), encoding="utf-8")
            tmp.replace(SYNC_STATE_PATH)
        except OSError as e:
            log.warning("Couldn't save sync state: %s", e)

    def _quick_plan(self, f: dict, key, email: str | None, full: bool, scan: bool) -> dict | None:
        """What a quick sync of this folder should look at, or None to check every file."""
        k = self._state_key(key)
        last_full = self.sync_state["last_full"].get(k)
        if (full or f.get("mode", "two_way") != "two_way" or not last_full or email not in self.change_tokens
                or time.time() - last_full > FULL_CHECK_HOURS * 3600):
            return None
        paths = self.changes.take_paths(key)
        if paths is None:              # too many changes on this PC to list
            return None
        return {"paths": paths, "ids": set(self.sync_state["pending"].get(k, ())), "scan": scan}

    def _poll_interval(self) -> float:
        try:
            return min(300.0, max(5.0, float(self.cfg.get("drive_poll_seconds", 10))))
        except (TypeError, ValueError):
            return 10.0

    def _interval(self) -> float:
        try:
            return max(1, float(self.load_config()["interval_minutes"])) * 60
        except (TypeError, ValueError, KeyError):
            return 15 * 60

    # ---------------------------------------------------------------- watching
    @staticmethod
    def folder_key(folder: dict, known: list[str]) -> tuple[str, str | None]:
        return os.path.normcase(os.path.normpath(folder["local"])), App.folder_account(folder, known)

    def _on_folder_changed(self, key, paths) -> None:  # called from the watcher thread
        self.changes.mark(key, paths=paths)
        self.wake.set()

    def refresh_watchers(self) -> None:
        """Watch exactly the configured folders (or none, if instant sync is off)."""
        if self.stopping.is_set():
            return
        cfg = self.cfg
        wanted = {}
        if cfg.get("watch_changes", True):
            known = accounts.list_accounts()
            for f in cfg.get("folders", []):
                excludes = list(DEFAULT_EXCLUDES) + list(f.get("exclude", []))
                wanted[self.folder_key(f, known)] = (os.path.normpath(f["local"]), excludes)
        try:
            self.watcher.update(wanted)
        except Exception as e:  # noqa: BLE001 - never let watching break the app; the timer still works
            log.warning("Couldn't watch folders for changes: %s", e)
        self.ui.put(("state",))

    # -------------------------------------------------------------- accounts
    @staticmethod
    def folder_account(folder: dict, known: list[str]) -> str | None:
        """The account a folder syncs to (folders saved before accounts existed use the first one)."""
        acc = (folder.get("account") or "").strip().lower()
        return acc or (known[0] if known else None)

    def _migrate_legacy_token(self) -> None:
        """Older versions kept a single login in token.json - turn it into a named account."""
        if not TOKEN_PATH.exists():
            return
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        from drive_sync import SCOPES
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
            if not creds.valid and creds.refresh_token:
                creds.refresh(Request())
            email = accounts.account_email(build_service(creds))
            accounts.save_account(email, creds.to_json())
            TOKEN_PATH.unlink()
            log.info("Moved the existing login to account %s", email)
            self.ui.put(("account_added", email))
        except RefreshError:
            TOKEN_PATH.unlink(missing_ok=True)
            log.warning("The old login had expired - add your Google account again in Settings")
        except Exception as e:  # noqa: BLE001 - probably offline; try again next sync
            log.warning("Couldn't convert the old login yet: %s", e)

    def _drive_for(self, email: str | None, known: list[str]) -> Drive | None:
        if not email or email not in known:
            log.error("This folder uses the Google account %s, which isn't added on this PC", email or "(none)")
            return None
        if email in self.drives:
            return self.drives[email]
        try:
            drive = Drive(build_service(accounts.load_credentials(email)))
        except accounts.NeedsSignIn as e:
            log.error("%s - sign in again in Settings", e)
            self.record_event("warning", f"{email} needs to sign in again",
                              "Settings → Google accounts → Sign in again")
            self.needs_signin.add(email)
            self.ui.put(("account",))
            return None
        except Exception as e:  # noqa: BLE001 - offline etc.
            log.error("Can't connect to Google Drive for %s: %s", email, e)
            return None
        self.drives[email] = drive
        if email in self.needs_signin:
            self.needs_signin.discard(email)
            self.ui.put(("account",))
        return drive

    # -------------------------------------------------------------- progress
    def _on_progress(self, p: Progress) -> None:
        """Called (throttled) from the sync thread: update the tray menu, tooltip and window."""
        folder = Path(p.folder).name if p.folder else ""
        # Snapshot the per-file lines first: set_state() redraws the menu and window from them.
        self.file_lines = p.file_lines(full_path=True)
        self.menu_file_lines = p.file_lines(full_path=False, max_len=45)
        self.set_state("syncing", f"Syncing… {p.percent}%",
                       f"{p.current()} · {p.counter()}" + (f" · {folder}" if folder else ""))

    def menu_detail(self) -> str:
        text = self.detail if self.state == "syncing" else ""
        return text if len(text) <= 70 else text[:69] + "…"

    def menu_file(self, i: int) -> str:
        """Tray menu line for the i-th file transferring right now ('' hides the line)."""
        lines = self.menu_file_lines if self.state == "syncing" else []
        return f"    {lines[i]}" if i < len(lines) else ""

    # -------------------------------------------------------------- activity
    def _record(self, entry: dict) -> None:
        """Save an Activity entry (runs that changed nothing are skipped) and show it."""
        if not activity.is_worth_keeping(entry):
            return
        self.activity.append(entry)
        self.ui.put(("activity", entry))

    def record_event(self, level: str, message: str, detail: str = "") -> None:
        # Don't repeat the same warning every retry (e.g. offline for an hour).
        key = (level, message, detail)
        if level != "info" and key == self._last_event_key:
            return
        self._last_event_key = key
        self._record(activity.new_event(level, message, detail))

    @staticmethod
    def _is_auth_error(e: Exception) -> bool:
        from google.auth.exceptions import RefreshError
        return isinstance(e, RefreshError) or "invalid_grant" in str(e) or "401" in str(e)

    # ------------------------------------------------------------------ sync
    def sync_all(self, *args, **kwargs) -> bool:
        """One sync round. An unexpected error is logged and shown, and syncing carries on later
        (it used to end the background thread, so nothing synced until a restart)."""
        try:
            return self._sync_all(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            log.exception("Sync stopped by an unexpected error")
            self.progress = None
            self.set_state("error", "Sync stopped by an unexpected error", f"{e} - see the log file")
            return False

    def _sync_all(self, only: set | None = None, trigger: str = "scheduled", full: bool = False,
                 scan: bool = False) -> bool:
        """Sync every folder, or only the folders whose keys are in `only` (changed folders).

        Two-way folders look only at what changed (see _quick_plan) unless `full`, or their daily
        full check is due. scan: also compare every file's size/time on this PC with the last sync."""
        self.set_state("syncing", "Syncing changes…" if only else "Syncing…")
        if not CREDS_PATH.exists():
            self.set_state("error", "Set up Google access", "Settings → Import credentials.json")
            return False
        self._migrate_legacy_token()
        known = accounts.list_accounts()
        if not known:
            self.set_state("error", "No Google account yet", "Settings → Add Google account")
            return False
        cfg = self.load_config()
        folders = cfg["folders"]
        if not folders:
            self.set_state("idle", "No folders yet", "Add a folder to start backing up")
            return True

        if only is not None:
            folders = [f for f in folders if self.folder_key(f, known) in only]

        full: dict[str, set[str]] = {}
        totals = dict(uploaded=0, updated=0, moved=0, trashed=0, downloaded=0, deleted_local=0, conflicts=0, errors=0)
        # Decide per folder: only what changed, or every file.
        plans = []
        for f in folders:
            key = self.folder_key(f, known)
            email = self.folder_account(f, known)
            plan = self._quick_plan(f, key, email, full, scan)
            if plan is None:
                self.changes.take_paths(key)   # checking everything covers these
            elif not (plan["paths"] or plan["ids"] or plan["scan"]):
                continue                       # nothing changed for this folder
            plans.append((f, key, email, plan))
        if not plans:
            self.set_state("idle", "Up to date", self.detail)
            return True
        # Count files first so the UI can show a percentage and "file X of Y". A quick sync counts
        # the files of each folder it looks at as it goes.
        prog = Progress(on_change=self._on_progress,
                        should_stop=lambda: self.paused or self.stopping.is_set() or self._metered_hold())
        counts = []
        for f, _key, _email, plan in plans:
            try:
                counts.append((0, 0) if plan else
                              count_files(f["local"], list(DEFAULT_EXCLUDES) + list(f.get("exclude", []))))
            except OSError:
                counts.append((0, 0))
        prog.add_total(sum(n for n, _ in counts), sum(b for _, b in counts))
        self.progress = prog
        for (f, key, email, plan), (n_files, n_bytes) in zip(plans, counts, strict=True):
            if self.stopping.is_set() or self.paused or self._metered_hold():   # not done: keep for later
                if plan:
                    self.changes.mark(key, paths=plan["paths"])
                else:
                    self.resume_full = True
                continue
            started_at = time.time()
            self.set_state("syncing", "Checking changes…" if plan else "Checking all files…",
                           f"{f['local']}  →  {email}")
            run = activity.new_run(f["local"], email, trigger, f.get("drive_folder", ""))
            started = time.monotonic()
            prog.begin_folder(f["local"], n_files, n_bytes)
            drive = self._drive_for(email, known)
            if drive is None:
                totals["errors"] += 1
                why = ("needs you to sign in again (Settings → Google accounts)" if email in self.needs_signin
                       else "couldn't connect to Google Drive - will retry")
                activity.add_item(run, "error", ".", f"Account {email or '(none)'} {why}")
                if plan:
                    self.changes.mark(key, settle=120, paths=plan["paths"])  # try these again later
            else:
                self._update_quota(email, drive)
                try:
                    excludes = list(DEFAULT_EXCLUDES) + list(f.get("exclude", []))
                    s = sync_folder(drive, f["local"], drive_folder=f.get("drive_folder"),
                                    drive_folder_id=f.get("drive_folder_id"),
                                    delete=bool(f.get("delete")), excludes=excludes,
                                    mode=f.get("mode", "two_way"), progress=prog, quick=plan,
                                    on_event=lambda kind, rel, msg, r=run: activity.add_item(r, kind, rel, msg))
                    if s.get("remote_id"):
                        self.remote_ids[(os.path.normcase(f["local"]), email)] = s["remote_id"]
                    for t in totals:
                        totals[t] += s.get(t, 0)
                    for where in s.get("storage_full", ()):
                        full.setdefault(where, set()).add(email if where == "drive" else f["local"])
                    self._synced(key, plan, s, started_at)
                except Paused:
                    # Stopped part-way: what finished is saved; the rest is done when syncing resumes.
                    if plan:
                        self.changes.mark(key, paths=plan["paths"])
                    else:
                        self.resume_full = True
                    activity.add_item(run, "skipped", ".", "Paused - the rest syncs when you resume")
                except Exception as e:  # noqa: BLE001
                    log.error("Sync of %s (%s) failed: %s", f["local"], email, e)
                    totals["errors"] += 1
                    activity.add_item(run, "error", ".", f"Sync failed: {e}")
                    if plan:
                        self.changes.mark(key, settle=120, paths=plan["paths"])  # try these again later
                    if self._is_auth_error(e):
                        self.drives.pop(email, None)
                        self.needs_signin.add(email)
                        self.ui.put(("account",))
            prog.end_folder()
            run["duration"] = round(time.monotonic() - started, 1)
            self._record(run)
        self.progress = None
        self.file_lines, self.menu_file_lines = [], []

        when = time.strftime("%H:%M")
        self.storage_full = full
        changes = sum(totals[k] for k in ("uploaded", "updated", "moved", "trashed", "downloaded", "deleted_local",
                                          "conflicts"))
        self.last_check = {"ts": time.time(), "trigger": trigger, "folders": len(folders),
                           "changes": changes, "errors": totals["errors"]}
        self.ui.put(("last_check",))
        if self.paused and not totals["errors"] and not self.needs_signin:
            self.set_state("idle", self.pause_label(), f"Stopped at {when} - the rest syncs when you resume")
            return True
        if not self.paused and self._metered_hold():
            self.metered_held = True
            self.set_state("idle", METERED_STATUS, f"Stopped at {when} - the rest syncs on another network")
            return True
        parts = [(totals["uploaded"], "uploaded"), (totals["updated"], "updated"), (totals["moved"], "moved"),
                 (totals["downloaded"], "downloaded"), (totals["deleted_local"], "to Recycle Bin"),
                 (totals["trashed"], "to Drive trash"), (totals["conflicts"], "conflicts kept")]
        summary = ", ".join(f"{n} {label}" for n, label in parts if n)
        if self.needs_signin:
            who = ", ".join(sorted(self.needs_signin))
            self.set_state("error", "Sign in again needed", f"{who} - open Settings → Google accounts")
            self.notify(f"Sign in again to keep syncing: {who}")
            return False
        if full:
            self._report_full(full, when)
            return False
        self._full_notified = frozenset()
        if totals["errors"]:
            self.set_state("error", f"{totals['errors']} problem(s) during sync",
                           f"Last sync {when} - see the Activity tab")
            self.notify(f"Sync finished with {totals['errors']} problem(s). Open Drive Sync for details.")
            return False
        watching = self.watcher.watched
        live = f" · watching {watching} folder{'s' if watching != 1 else ''} for changes" if watching else ""
        self.set_state("idle", "Up to date", f"Last sync {when}" + (f" - {summary}" if changes else "") + live)
        if changes and cfg.get("notify_on_changes"):
            self.notify(f"Synced: {summary}")
        return True

    def _report_full(self, full: dict[str, set[str]], when: str) -> None:
        """One clear status for a full Drive / disk; notify only when that's news (not every retry)."""
        bits, notes = [], []
        if "drive" in full:
            who = ", ".join(sorted(full["drive"]))
            bits.append(f"Google Drive storage is full ({who})")
            notes.append(f"Google Drive storage is full for {who}. New files won't upload until you free "
                         "up space on Drive or get more storage.")
        if "pc" in full:
            bits.append("This PC's disk is full")
            notes.append("This PC's disk is full. Files from Drive won't download until you free up space.")
        status = " · ".join(bits)
        self.set_state("error", status, f"Last sync {when} - free up space, then press Sync now "
                                        f"(tried again every {FULL_RETRY_SECONDS // 60} min)")
        key = frozenset((w, x) for w, xs in full.items() for x in xs)
        if key != self._full_notified:
            self._full_notified = key
            self.notify(" ".join(notes))

    def _synced(self, key, plan: dict | None, stats: dict, started_at: float) -> None:
        """Update the saved state after a folder synced: its Drive changes are handled now (unless
        something failed - then they're looked at again), and when it was last fully checked."""
        k = self._state_key(key)
        if stats.get("scope") == "full" and key[1] in self.change_tokens:
            self.sync_state["last_full"][k] = started_at
        if stats.get("errors"):
            # Keep the Drive changes; the PC side is found again by the size/time comparison
            # (a file that failed isn't recorded as synced).
            if plan:
                self.changes.mark(key, settle=FULL_RETRY_SECONDS if stats.get("storage_full") else 120,
                                  paths=plan["paths"])
        else:
            handled = set(plan["ids"]) if plan else set(self.sync_state["pending"].get(k, ()))
            left = set(self.sync_state["pending"].get(k, ())) - handled
            if left:
                self.sync_state["pending"][k] = sorted(left)
            else:
                self.sync_state["pending"].pop(k, None)
        self._save_sync_state()

    # --------------------------------------------------------------- actions
    def sync_now(self):
        self.paused = False
        self.force_full = True
        self.wake.set()

    def toggle_pause(self):
        """The window's Pause / Resume button."""
        if self.paused:
            self.resume()
        else:
            self.pause(None)

    def pause(self, seconds: float | None) -> None:
        """Pause syncing - for `seconds`, or until resumed (None)."""
        self.paused = True
        self.pause_until = time.time() + seconds if seconds else None
        self._show_pause_change()
        self.wake.set()   # the worker sleeps until the pause ends

    def resume(self) -> None:
        self.paused, self.pause_until = False, None
        self._show_pause_change()
        self.wake.set()

    def pause_label(self) -> str:
        """'Paused', 'Paused until 15:30' or 'Paused until tomorrow 07:00'."""
        if not self.pause_until:
            return "Paused"
        end = time.localtime(self.pause_until)
        day = "" if end.tm_yday == time.localtime().tm_yday else "tomorrow "
        return f"Paused until {day}{time.strftime('%H:%M', end)}"

    def _show_pause_change(self) -> None:
        if self.state != "syncing":
            self.set_state("idle", self.pause_label() if self.paused else "Resuming…", "")
        else:  # stops at the next file (or part-way through a big one); switch the icon now
            self.set_state(self.state, self.status, self.detail)

    def credentials_ready(self) -> bool:
        return CREDS_PATH.exists()

    def accounts_info(self) -> list[dict]:
        known = accounts.list_accounts()
        counts = {e: 0 for e in known}
        for f in self.cfg.get("folders", []):
            acc = self.folder_account(f, known)
            if acc in counts:
                counts[acc] += 1
        for e in known:  # logins from before full Drive access was needed
            if e not in self.needs_signin and not accounts.has_required_scopes(accounts.token_path(e)):
                self.needs_signin.add(e)
        def nearly_full(q):
            return bool(q and q["limit"] and q["usage"] >= NEARLY_FULL * q["limit"])
        return [{"email": e, "folders": counts[e], "needs_signin": e in self.needs_signin,
                 "storage": quota_text(self.quota.get(e)), "nearly_full": nearly_full(self.quota.get(e))}
                for e in known]

    def drive_for_browsing(self, email: str) -> Drive:
        """A separate Drive connection for the folder browser (called from its own thread)."""
        return Drive(build_service(accounts.load_credentials(email)))

    # ------------------------------------------------------- browser profiles
    def account_profile(self, email: str | None) -> str | None:
        """Browser profile chosen for this account ("brave|Profile 7"), or None = default browser."""
        return (self.cfg.get("account_browsers") or {}).get(email or "") or None

    def set_account_profile(self, email: str, profile_id: str | None) -> None:
        mapping = dict(self.cfg.get("account_browsers") or {})
        if profile_id:
            mapping[email] = profile_id
        else:
            mapping.pop(email, None)
        self.save_config({**self.cfg, "account_browsers": mapping})

    def open_url(self, url: str, email: str | None = None) -> None:
        """Open a link in the browser profile that belongs to this Google account."""
        browsers.open_url(url, self.account_profile(email))

    def add_account(self, login_hint: str | None = None, browser_profile: str | None = None):
        """Open the browser so the user can choose a Google account (runs in the background)."""
        if self.signing_in or not CREDS_PATH.exists():
            return
        self.signing_in = login_hint or "new"
        self.ui.put(("account",))
        if browser_profile is None and login_hint:
            browser_profile = self.account_profile(login_hint)
        threading.Thread(target=self._sign_in_thread, args=(login_hint, browser_profile), daemon=True).start()

    def _sign_in_thread(self, login_hint: str | None, browser_profile: str | None = None):
        try:
            creds = accounts.sign_in(CREDS_PATH, login_hint=login_hint, browser_profile=browser_profile)
            email = accounts.account_email(build_service(creds))
            accounts.save_account(email, creds.to_json())
            self.drives.pop(email, None)
            self.needs_signin.discard(email)
            log.info("Google account ready: %s", email)
            self.record_event("info", f"Signed in: {email}", "Google account is ready to sync")
            self.ui.put(("account_added", email, browser_profile))
            self.wake.set()
        except Exception as e:  # noqa: BLE001 - closed the tab, timed out, denied access, ...
            log.error("Sign-in didn't finish: %s", e)
            self.record_event("warning", "Sign-in didn't finish", str(e)[:300])
            self.ui.put(("error", "Sign-in didn't finish. Try again, and approve access in the browser window "
                                  f"that opens.\n\nDetails: {e}"))
        finally:
            self.signing_in = None
            self.ui.put(("account",))

    def remove_account(self, email: str):
        def work():
            accounts.remove_account(email)
            self.drives.pop(email, None)
            self.needs_signin.discard(email)
            log.info("Removed Google account %s", email)
            self.record_event("info", f"Removed account: {email}", "Drive Sync's access was revoked")
            self.ui.put(("account",))
        threading.Thread(target=work, daemon=True).start()

    def import_credentials(self, path: Path):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            raise ValueError(f"Couldn't read that file: {e}") from e
        if "installed" not in data:
            raise ValueError("That doesn't look like a Desktop-app OAuth client file.\n"
                             "In Google Cloud Console create an OAuth client of type “Desktop app” "
                             "and download its JSON.")
        if path.resolve() != CREDS_PATH.resolve():
            shutil.copyfile(path, CREDS_PATH)
        log.info("Imported OAuth client file")
        self.record_event("info", "Imported credentials.json")

    def startup_enabled(self) -> bool:
        return STARTUP_LNK.exists()

    def set_startup(self, enabled: bool):
        if not enabled:
            STARTUP_LNK.unlink(missing_ok=True)
            log.info("Removed from Windows startup")
            return
        if getattr(sys, "frozen", False):
            target, args = sys.executable, "--hidden"
        else:
            exe = Path(sys.executable)
            pyw = exe.with_name("pythonw.exe")
            target, args = str(pyw if pyw.exists() else exe), f'"{Path(__file__).resolve()}" --hidden'
        # Values are passed as environment variables, never pasted into the script text.
        script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:DS_LNK);"
                  "$s.TargetPath=$env:DS_TARGET;$s.Arguments=$env:DS_ARGS;$s.WorkingDirectory=$env:DS_DIR;"
                  "$s.Description='Sync folders to Google Drive';$s.Save()")
        env = dict(os.environ, DS_LNK=str(STARTUP_LNK), DS_TARGET=target, DS_ARGS=args, DS_DIR=str(APP_DIR))
        subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", script],
                       env=env, check=True, creationflags=NO_WINDOW, capture_output=True)
        log.info("Added to Windows startup")

    def open_log(self):
        LOG_PATH.touch(exist_ok=True)
        subprocess.Popen([str(NOTEPAD), str(LOG_PATH)])

    def open_readme(self):
        subprocess.Popen([str(NOTEPAD), str(self.readme_path)])

    def quit(self):
        log.info("Drive Sync stopped")
        RUN_MARKER.stop()
        self.stopping.set()
        self.wake.set()
        try:
            self.watcher.stop()
            self.icon.stop()
        finally:
            self.root.destroy()

    def run(self):
        self.refresh_watchers()
        threading.Thread(target=self.worker, daemon=True).start()
        self.icon.run_detached()
        threading.Thread(target=self.animate, daemon=True, name="tray-animation").start()
        threading.Thread(target=self.update_loop, daemon=True, name="updates").start()
        if getattr(sys, "frozen", False) and self.startup_enabled():
            # Point the Windows-startup shortcut at this copy (e.g. after moving to the installed one).
            threading.Thread(target=self.set_startup, args=(True,), daemon=True).start()
        self.root.after(150, self.window.poll)
        self.root.mainloop()


# --------------------------------------------------------------------------- #
# Everything Drive Sync keeps about you (APP_DIR): removed by --remove-data (the uninstaller's option).
DATA_ITEMS = ["accounts", "config.json", "config.tmp", "credentials.json", "token.json", "state", "logs",
              "activity.jsonl", "sync_state.json", "update.json", "uploads.json", "updates", ".show"]


def remove_data(base: Path = APP_DIR, revoke: bool = True) -> list[str]:
    """Sign out every Google account (revoking Drive Sync's access) and delete settings, logins, logs
    and sync history. Synced files on this PC and on Google Drive aren't touched. Returns what was removed."""
    removed = []
    for email in accounts.list_accounts(base / "accounts"):
        try:
            accounts.remove_account(email, base / "accounts", revoke=revoke)
        except Exception as e:  # noqa: BLE001 - deleting the files below still signs it out on this PC
            log.warning("Couldn't sign out %s: %s", email, e)
    for name in DATA_ITEMS:
        p = base / name
        try:
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
            else:
                continue
            removed.append(name)
        except OSError as e:
            log.warning("Couldn't delete %s: %s", p, e)
    try:
        base.rmdir()      # only if nothing else is left (e.g. a copy run from source keeps its code)
    except OSError:
        pass
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(description="Drive Sync desktop app")
    ap.add_argument("--hidden", action="store_true", help="Start in the system tray without opening the window")
    ap.add_argument("--remove-data", action="store_true",
                    help="Sign out of Google and delete settings, logins and sync history, then exit (uninstall)")
    args = ap.parse_args()
    if args.remove_data:
        remove_data()
        return 0

    if not single_instance():
        if not args.hidden:
            SHOW_FLAG.touch()  # ask the running copy to show its window
        return 0              # (no logging here: only the running copy writes the log file)
    logs.setup(LOG_PATH)
    logs.install_crash_hooks()
    SHOW_FLAG.unlink(missing_ok=True)
    log.info("Drive Sync %s started%s (process %d, %s)", __version__, " in the tray" if args.hidden else "",
             os.getpid(), sys.executable if getattr(sys, "frozen", False) else "from source")
    RUN_MARKER.start()
    try:
        App(start_hidden=args.hidden).run()
    except Exception:  # noqa: BLE001
        log.exception("Drive Sync crashed")
        return 1
    finally:
        logging.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
