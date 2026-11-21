"""
Open links in a specific browser profile.

Chromium browsers (Brave, Chrome, Edge) open links in whichever profile was used
last, so a Drive link can land in a profile signed in to a different Google account.
Here we list each browser's profiles and launch the browser with
--profile-directory=<profile> so the right one is used every time.

A profile is identified by an id string "<browser key>|<profile directory>",
e.g. "brave|Profile 7". None / "" means "the default browser, as Windows decides".
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import webbrowser
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("drive_sync")

LOCAL = Path(os.environ.get("LOCALAPPDATA", ""))
PF = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
PF86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))

# key: (display name, exe name, User Data folder, fallback install locations)
BROWSERS = {
    "brave": ("Brave", "brave.exe", LOCAL / "BraveSoftware/Brave-Browser/User Data",
              [PF / "BraveSoftware/Brave-Browser/Application", PF86 / "BraveSoftware/Brave-Browser/Application",
               LOCAL / "BraveSoftware/Brave-Browser/Application"]),
    "chrome": ("Chrome", "chrome.exe", LOCAL / "Google/Chrome/User Data",
               [PF / "Google/Chrome/Application", PF86 / "Google/Chrome/Application",
                LOCAL / "Google/Chrome/Application"]),
    "edge": ("Edge", "msedge.exe", LOCAL / "Microsoft/Edge/User Data",
             [PF86 / "Microsoft/Edge/Application", PF / "Microsoft/Edge/Application"]),
}


@dataclass(frozen=True)
class Profile:
    id: str           # "brave|Profile 7"
    browser: str      # "Brave"
    directory: str    # "Profile 7"
    name: str         # "Work" (the name shown in the browser)
    exe: str

    @property
    def label(self) -> str:
        return f"{self.browser} - {self.name}"


def _find_exe(exe_name: str, fallbacks: list[Path]) -> str | None:
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"
                with winreg.OpenKey(hive, key) as k:
                    path = winreg.QueryValue(k, None)
                if path and Path(path).is_file():
                    return path
            except OSError:
                continue
    except ImportError:  # not Windows
        pass
    for folder in fallbacks:
        if (folder / exe_name).is_file():
            return str(folder / exe_name)
    return None


def list_profiles(user_data_dirs: dict[str, Path] | None = None) -> list[Profile]:
    """All profiles of installed Chromium browsers, sorted by browser then name."""
    found: list[Profile] = []
    for key, (display, exe_name, user_data, fallbacks) in BROWSERS.items():
        user_data = (user_data_dirs or {}).get(key, user_data)
        state = user_data / "Local State"
        if not state.is_file():
            continue
        exe = _find_exe(exe_name, fallbacks)
        if not exe:
            continue
        try:
            cache = json.loads(state.read_text(encoding="utf-8"))["profile"]["info_cache"]
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.debug("Can't read %s profiles: %s", display, e)
            continue
        for directory, info in cache.items():
            if not (user_data / directory).is_dir():
                continue  # deleted profile still listed
            name = (info or {}).get("name") or directory
            found.append(Profile(f"{key}|{directory}", display, directory, name, exe))
    return sorted(found, key=lambda p: (p.browser, p.name.lower()))


def find_profile(profile_id: str | None, profiles: list[Profile] | None = None) -> Profile | None:
    if not profile_id:
        return None
    return next((p for p in (profiles if profiles is not None else list_profiles()) if p.id == profile_id), None)


def open_url(url: str, profile_id: str | None = None) -> None:
    """Open url in the given browser profile, or in the default browser."""
    profile = find_profile(profile_id)
    if profile_id and not profile:
        log.warning("Browser profile %s no longer exists - using the default browser", profile_id)
    if profile:
        try:
            # List arguments (no shell); the profile comes from the browser's own profile list.
            subprocess.Popen([profile.exe, f"--profile-directory={profile.directory}", url])
            return
        except OSError as e:
            log.warning("Couldn't start %s: %s - using the default browser", profile.label, e)
    webbrowser.open(url)


class _ProfileController(webbrowser.BaseBrowser):
    """Lets libraries that call webbrowser.get(name).open(url) use a chosen profile."""

    def __init__(self, profile_id: str | None):
        super().__init__("drivesync-profile")
        self.profile_id = profile_id

    def open(self, url, new=0, autoraise=True):
        open_url(url, self.profile_id)
        return True


def register_controller(profile_id: str | None) -> str:
    """Register a webbrowser controller for profile_id and return its name."""
    name = "drivesync-profile"
    webbrowser.register(name, None, _ProfileController(profile_id))
    return name
