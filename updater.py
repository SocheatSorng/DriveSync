"""Automatic updates from the project's GitHub Releases.

A release (made by CI when a tag like v1.2.0 is pushed) carries DriveSync-Setup.exe and its SHA-256
checksum. The app checks once a day, downloads a newer installer, checks the checksum, and - when
nothing is syncing - runs it silently. The installer closes Drive Sync, replaces it and starts it again.

Only a copy installed with DriveSync-Setup.exe updates itself; a loose DriveSync.exe (or running from
source) just shows that an update exists.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import requests

from version import __version__

REPO = "SocheatSorng/DriveSync"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
SETUP_NAME = "DriveSync-Setup.exe"
CHECK_EVERY = 24 * 3600
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "DriveSync-updater"}
log = logging.getLogger("drive_sync")


def parse_version(text: str) -> tuple[int, ...] | None:
    """'v1.2.0' / '1.2' -> (1, 2, 0) / (1, 2); anything else -> None."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+){0,3})", text.strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def newer(candidate: str, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


@dataclass
class Update:
    version: str          # "1.2.0"
    url: str              # installer download link
    sha256: str | None    # expected checksum (None: not published - won't be installed)
    notes: str = ""
    page: str = RELEASES_PAGE


def installed_copy() -> bool:
    """True when running the .exe that DriveSync-Setup.exe installed (it can replace itself)."""
    if not getattr(sys, "frozen", False):
        return False
    return (Path(sys.executable).parent / "unins000.exe").exists()


def check(session=requests, timeout: float = 15) -> Update | None:
    """The newest release if it's newer than this copy, else None. Raises on network errors."""
    r = session.get(LATEST, headers=HEADERS, timeout=timeout)
    if r.status_code == 404:          # no releases published yet
        return None
    r.raise_for_status()
    rel = r.json()
    if rel.get("draft") or rel.get("prerelease"):
        return None
    tag = rel.get("tag_name", "")
    if not newer(tag):
        return None
    assets = {a["name"]: a for a in rel.get("assets", [])}
    setup = assets.get(SETUP_NAME)
    if not setup:
        log.info("Drive Sync %s is out, but has no installer attached", tag)
        return None
    sha = None
    digest = setup.get("digest") or ""           # GitHub records "sha256:<hex>" for uploaded files
    if digest.startswith("sha256:"):
        sha = digest.split(":", 1)[1].lower()
    elif SETUP_NAME + ".sha256" in assets:
        s = session.get(assets[SETUP_NAME + ".sha256"]["browser_download_url"], headers=HEADERS, timeout=timeout)
        s.raise_for_status()
        sha = s.text.split()[0].lower()
    return Update(tag.lstrip("v"), setup["browser_download_url"], sha, rel.get("body") or "",
                  rel.get("html_url") or RELEASES_PAGE)


def download(update: Update, folder: Path, session=requests, on_progress=None) -> Path:
    """Download the installer and check its checksum. Returns its path."""
    if not update.sha256:
        raise ValueError("The release has no checksum for the installer - not installing it")
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"DriveSync-Setup-{update.version}.exe"
    if dest.exists() and _sha256(dest) == update.sha256:
        return dest
    tmp = dest.with_suffix(".part")
    h = hashlib.sha256()
    with session.get(update.url, headers={"User-Agent": HEADERS["User-Agent"]}, stream=True, timeout=60) as r:
        r.raise_for_status()
        total, got = int(r.headers.get("content-length") or 0), 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if on_progress and total:
                    on_progress(got / total)
    if h.hexdigest() != update.sha256:
        tmp.unlink(missing_ok=True)
        raise ValueError("The downloaded installer doesn't match its published checksum - not installing it")
    os.replace(tmp, dest)
    for old in folder.glob("DriveSync-Setup-*.exe"):   # earlier downloads aren't needed any more
        if old != dest:
            old.unlink(missing_ok=True)
    return dest


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run_installer(setup: Path) -> None:
    """Start the installer on its own (it closes this app, updates it and starts it again)."""
    flags = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",  # noqa: S603
                      "/LOG=" + str(setup.with_suffix(".log"))], creationflags=flags, close_fds=True)


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    try:
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError as e:
        log.debug("Couldn't save update state: %s", e)
