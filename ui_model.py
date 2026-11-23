"""What the window shows, without the window: texts, states and choices for the folder cards,
skip chips, Google accounts, the status header and the update line.

Shared by the CustomTkinter window (gui.py) and the Qt one (qtui/), so both say exactly the same
thing, and tested without either (test_ui_model.py). Colours are given as tones - "error",
"waiting", "muted", "link", "ok" - and each window picks its own colours for them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


# --------------------------------------------------------------------------- folder cards
@dataclass(frozen=True)
class FolderCard:
    title: str          # the folder's name ("Image"; a whole drive like "D:\" is shown whole)
    location: str       # the folder it's in ("D:\Storage"), or ""
    line: str           # where it syncs to, or what's wrong
    tone: str           # "muted", "waiting" or "error"
    waiting: bool       # a new folder waiting for "Start syncing"
    can_start: bool     # "Start syncing" can be pressed


def strip_my_drive(path: str) -> str:
    """'My Drive/School/2026' -> 'School/2026' (typed paths are always inside My Drive)."""
    path = path.strip().replace("\\", "/").strip("/")
    return path[len("My Drive"):].lstrip("/") if path.lower().startswith("my drive") else path


def drive_target(f: dict) -> str:
    """Where on Google Drive a folder syncs to."""
    if f.get("drive_folder_id"):   # chosen in the Drive browser: already a full path
        return f.get("drive_folder") or f"folder id {f['drive_folder_id']}"
    return f"My Drive/{strip_my_drive(f.get('drive_folder', ''))}"   # typed path: always inside My Drive


def folder_card(f: dict, email: str | None, known: list[str], signin: set[str], waiting: bool,
                exists: bool) -> FolderCard:
    """The card for one synced folder. email: its account (App.folder_account); known: signed-in
    accounts; signin: those that must sign in again; exists: the PC folder is there."""
    local = f.get("local", "")
    title = os.path.basename(local.rstrip("\\/")) or local
    location = os.path.dirname(local.rstrip("\\/")) if title != local else ""
    two_way = f.get("mode", "two_way") == "two_way"
    extra = " · two-way" if two_way else " · upload only" + (", Drive matches PC" if f.get("delete") else "")
    arrow = "↔" if two_way else "→"   # (⇄ isn't in Segoe UI)
    syncs_to = f"{arrow}  {email} · {drive_target(f)}{extra}"
    if not exists:
        line, tone = "⚠  This folder no longer exists on this PC", "error"
    elif not email:
        line, tone = "⚠  No Google account - add one in Settings", "error"
    elif email not in known:
        line, tone = f"⚠  Account {email} was removed - click Edit to choose another", "error"
    elif email in signin:
        line, tone = f"⚠  {email} needs to sign in again (Settings → Google accounts)", "error"
    elif waiting:
        line, tone = f"Waiting - press Start syncing   ·   {syncs_to}", "waiting"
    else:
        line, tone = syncs_to, "muted"
    can_start = waiting and exists and email in known and email not in signin
    return FolderCard(title, location, line, tone, waiting, can_start)


EMPTY_FOLDERS_TITLE = "Back up your first folder"
EMPTY_FOLDERS_TEXT = "Choose a folder on this PC and Drive Sync will keep a copy in Google Drive."
FOLDERS_HEADING = "Folders backed up to Google Drive"
DUPLICATE_FOLDER = "That folder is already being synced to this account."


def drive_url_email(email: str | None, known: list[str]) -> str | None:
    """Which account to open Google Drive in: the folder's, or the only one there is."""
    if email:
        return email
    return known[0] if len(known) == 1 else None


def drive_url(f: dict | None, email: str | None, known: list[str], remote_ids: dict) -> str:
    """The Google Drive page for a synced folder (f None: My Drive), opened in the right account.
    remote_ids: (normcased PC folder, account) -> Drive folder id found by the last sync."""
    from urllib.parse import urlencode
    rid = None
    if f is not None:
        rid = f.get("drive_folder_id") or remote_ids.get((os.path.normcase(f["local"]), email))
    email = drive_url_email(email, known)
    url = f"https://drive.google.com/drive/folders/{rid}" if rid else "https://drive.google.com/drive/my-drive"
    return url + "?" + urlencode({"authuser": email}) if email else url


def without_folder(cfg: dict, i: int) -> dict:
    """A copy of the settings with folder number i removed."""
    return {**cfg, "folders": [x for j, x in enumerate(cfg.get("folders", [])) if j != i]}


def remove_folder_question(f: dict) -> str:
    return f"Stop syncing this folder?\n\n{f['local']}\n\nFiles already on Google Drive are not deleted."


# --------------------------------------------------------------------------- skip chips
SKIP_CHARS = 70          # chip text shown on a folder card before "+N more"
SKIP_SHORT = 28          # longest name on a chip
SKIP_SIDES = {           # where a skipped item is: (badge, tone, what skipping it means)
    "pc": ("This PC", None, "On this PC only - it isn't uploaded to Google Drive."),
    "drive": ("Google Drive", "link", "On Google Drive only - it isn't downloaded to this PC."),
    "both": ("PC + Drive", None, "On this PC and on Google Drive - both copies are left as they are "
                                 "and no longer kept the same."),
    "rule": ("Rule", "muted", "A rule: anything matching it, anywhere in the folder, on this PC or "
                              "Google Drive, isn't synced."),
}
EDIT_HINT = "Click to change what's skipped."


@dataclass(frozen=True)
class Chip:
    text: str
    tooltip: str
    tone: str | None


@dataclass(frozen=True)
class SkipChips:
    shown: list[Chip]
    more: Chip | None    # "+3 more", listing the rest on hover


def skip_side(pattern: str, local: str, where: dict) -> str | None:
    """Where a skipped item is: "pc", "drive", "both", "rule", or None (not known)."""
    from skip_model import COMMON, describe, is_rule
    if is_rule(pattern) or pattern in {p for p, _ in COMMON}:
        return "rule"                          # *.log, or a common name like node_modules: anywhere
    full = describe(pattern)[1]
    on_pc = os.path.lexists(os.path.join(local, *full.split("/")))
    saved = where.get(pattern)                 # seen when it was ticked in the picker
    if saved is None:   # saved before sides were recorded: a picked path not on this PC came from Drive
        return "pc" if on_pc else "drive"
    on_drive = saved in ("drive", "both")
    return "both" if on_pc and on_drive else "drive" if on_drive else "pc" if on_pc else saved


def skip_chips(skips: list[str], local: str, where: dict | None = None) -> SkipChips:
    """'Skips  [Big folder · Google Drive] [*.log · Rule] [+3 more]': short names and which side each
    is on, the full path and meaning on hover."""
    from skip_model import describe
    where = where or {}
    picked, used = [], 0
    for p in skips:
        short = describe(p)[0]
        if len(short) > SKIP_SHORT:
            short = short[:SKIP_SHORT - 1] + "…"
        side = skip_side(p, local, where)
        badge = SKIP_SIDES[side][0] if side else ""
        if picked and used + len(short) + len(badge) > SKIP_CHARS:
            break
        picked.append((p, short, side))
        used += len(short) + len(badge) + 6
    shown = []
    for p, short, side in picked:
        badge, tone, meaning = SKIP_SIDES[side] if side else ("", None, "Not synced either way.")
        shown.append(Chip(f"{short}  ·  {badge}" if badge else short,
                          f"{describe(p)[1]}\n{meaning}\n\n{EDIT_HINT}", tone))
    rest = skips[len(picked):]
    more = None
    if rest:
        lines = []
        for p in rest:
            side = skip_side(p, local, where)
            lines.append(describe(p)[1] + (f"   ({SKIP_SIDES[side][0]})" if side else ""))
        more = Chip(f"+{len(rest)} more", "\n".join(lines) + f"\n\n{EDIT_HINT}", "link")
    return SkipChips(shown, more)


# --------------------------------------------------------------------------- Google accounts
@dataclass(frozen=True)
class AccountRow:
    email: str
    dot: str            # "ok" or "error"
    status: str
    tone: str           # "muted" or "error"
    needs_signin: bool


def account_row(a: dict) -> AccountRow:
    """One account in Settings, from App.accounts_info()."""
    n = a["folders"]
    if a["needs_signin"]:
        status = "Click “Sign in again” to keep syncing (needs updated access)"
    else:
        status = f"{n} folder{'s' if n != 1 else ''}" if n else "No folders yet"
        if a.get("storage"):
            status += f"  ·  Drive: {a['storage']}" + ("  -  nearly full" if a.get("nearly_full") else "")
    bad = a["needs_signin"] or bool(a.get("nearly_full"))
    return AccountRow(a["email"], "error" if a["needs_signin"] else "ok", status, "error" if bad else "muted",
                      a["needs_signin"])


def accounts_help(ready: bool, busy: str | None, has_accounts: bool) -> str:
    """The text above the accounts list. ready: credentials.json imported; busy: signing in."""
    if not ready:
        return ("First, import the OAuth client file (credentials.json) from Google Cloud Console. "
                "The setup guide explains each step.")
    if busy:
        return ("Waiting for you in the browser… choose the Google account and allow access. "
                "(This window updates by itself when you're done. Closed the browser? Press Cancel.)")
    if not has_accounts:
        return ("Add the Google account to back up to. Your browser opens so you can choose which "
                "account - you can add more than one and pick one per folder.")
    return ("Each folder syncs to the account chosen for it (Folders → Edit). "
            "Drive Sync can only see files it created.")


def add_account_button(ready: bool, busy: str | None) -> tuple[bool, str]:
    """(enabled, text) of the Add Google account button."""
    return bool(ready and not busy), "Waiting for browser…" if busy else "+  Add Google account"


# --------------------------------------------------------------------------- header, updates
@dataclass(frozen=True)
class Header:
    dot: str                  # "idle", "syncing", "error" or "paused"
    status: str
    detail: str               # one line per file while several transfer at once
    pause_text: str
    sync_enabled: bool
    progress: float | None    # 0..1 while syncing with a known total, else None (no bar)


def header(app) -> Header:
    """The status at the top of the window, from the App's state."""
    syncing = app.state == "syncing"
    files = app.file_lines if syncing else []
    prog = app.progress
    fraction = prog.percent / 100 if syncing and prog is not None and prog.total else None
    return Header("paused" if app.paused else app.state, app.status, "\n".join([app.detail, *files]),
                  "Resume" if app.paused else "Pause", not syncing, fraction)


def update_line(version: str, installed: bool, update_status: str, update) -> tuple[str, str | None]:
    """(text, button) for Settings → Updates; button None = no button."""
    where = "installed" if installed else "not installed with Setup: updates are only shown"
    text = f"Version {version} ({where})" + (f"\n{update_status}" if update_status else "")
    if update is None:
        return text, None
    return text, f"Update to {update.version}" if installed else "Download page"


# --------------------------------------------------------------------------- Activity tab
PAGE = 40              # cards shown at a time ("Show older activity" shows more)
DETAILS = 200          # file rows shown per card
_RED, _AMBER, _GREEN, _BLUE, _GRAY = "#d93025", "#e37400", "#1e8e3e", "#1a73e8", "#80868b"
BADGES = {  # kind: (label, colour) - problems first, the order of a card's file list
    "error": ("Problem", _RED), "conflict": ("Conflict", _AMBER), "skipped": ("Skipped", _AMBER),
    "uploaded": ("Uploaded", _GREEN), "updated": ("Updated", _BLUE), "moved": ("Moved", _BLUE),
    "downloaded": ("Downloaded", "#00838f"),
    "deleted_local": ("Recycle Bin", _GRAY), "trashed": ("Drive trash", _GRAY), "folder": ("New folder", "#9334e6"),
}
ORDER = list(BADGES)
DOT_COLORS = {"error": _RED, "warning": _AMBER, "ok": _GREEN, "info": _BLUE}   # an Activity card's dot
CLEAR_QUESTION = ("Clear the activity history?\n\nThis only clears the list. "
                  "Your files and the log file are not affected.")
SEARCH_HINT = "Search files, folders, accounts…"
VIEWS = ("All", "Changes", "Problems")


@dataclass(frozen=True)
class ActivityCard:
    title: str
    subtitle: str
    when: str
    dot: str        # "error", "warning", "ok" or "info"
    items: int      # files listed (0: no "Show items")


def entry_id(e: dict) -> str:
    return f"{e['ts']}|{e.get('folder', e.get('message', ''))}"


def badge(kind: str) -> tuple[str, str]:
    """(label, colour) of a file row's badge; unknown kinds show their own name in grey."""
    return BADGES.get(kind, (kind, _GRAY))


def activity_header(last_check: dict | None, entries: list[dict], view: str) -> str:
    """The line above the list: the last check, and recent problems (unless already showing them)."""
    import time

    import activity
    problems = sum(1 for e in entries[-200:] if activity.has_problems(e))
    if not last_check:
        text = "Not checked yet."
    else:
        lc = last_check
        when = time.strftime("%H:%M", time.localtime(lc["ts"]))
        n = lc["folders"]
        result = (f"{lc['errors']} problem{'s' if lc['errors'] != 1 else ''}" if lc["errors"]
                  else "no changes" if not lc["changes"]
                  else f"{lc['changes']} change{'s' if lc['changes'] != 1 else ''}")
        text = (f"Last checked {when} ({activity.TRIGGERS.get(lc['trigger'], lc['trigger'])}) · "
                f"{n} folder{'s' if n != 1 else ''} · {result}")
    text += "  ·  Checks that changed nothing aren't listed."
    if problems and view != "Problems":
        text += f"  ·  {problems} recent problem(s) - see Problems"
    return text


def activity_empty(has_entries: bool, query: str, view: str) -> tuple[str, str]:
    """(title, text) when nothing is listed."""
    if not has_entries:
        return "No activity yet", "Uploads, updates and problems will appear here, newest first, grouped by day."
    if query:
        return "Nothing found", f"No activity matches “{query}”."
    if view == "Problems":
        return "No problems", "Everything synced without errors."
    return "Nothing to show", "No activity matches this filter."


def run_card(e: dict) -> ActivityCard:
    """A sync run: the folder, what changed, why it ran, and the account."""
    from pathlib import Path

    import activity
    c = e["counts"]
    dot = "error" if c.get("error") else "warning" if c.get("skipped") or c.get("conflict") else "ok"
    bits = [activity.summary(e), activity.TRIGGERS.get(e.get("trigger"), "")]
    if e.get("account"):
        bits.append(e["account"])
    return ActivityCard(Path(e["folder"]).name or e["folder"], " · ".join(b for b in bits if b),
                        activity.time_range(e), dot, len(e["items"]) + e.get("more", 0))


def event_card(e: dict) -> ActivityCard:
    """Something that isn't a sync run (signed in, update ready, a problem...)."""
    import time
    dot = {"error": "error", "warning": "warning"}.get(e.get("level"), "info")
    return ActivityCard(e.get("message", ""), e.get("detail", ""),
                        time.strftime("%H:%M", time.localtime(e["ts"])), dot, 0)


def details_toggle(is_open: bool, n: int) -> str:
    return "Hide details ▴" if is_open else f"Show {n} item{'s' if n != 1 else ''} ▾"


def details_open(e: dict, expanded: set[str], collapsed: set[str]) -> bool:
    """Is a run's file list showing? Runs with problems open by themselves until closed."""
    eid = entry_id(e)
    return eid in expanded or (bool(e["counts"].get("error")) and eid not in collapsed)


def detail_rows(e: dict, query: str) -> tuple[list[dict], list[str], int]:
    """(files to list, their times, how many more aren't listed): problems first and, while
    searching, matching files first; at most DETAILS rows."""
    import activity
    items = sorted(e["items"], key=lambda i: ORDER.index(i["kind"]) if i["kind"] in ORDER else 99)
    q = query.strip().lower()
    if q:
        items.sort(key=lambda i: q not in i["path"].lower())
    shown = items[:DETAILS]
    hidden = max(0, len(items) - DETAILS) + e.get("more", 0)
    return shown, activity.item_times(shown, e["ts"]), hidden


# --------------------------------------------------------------------------- accounts: actions
DEFAULT_BROWSER = "Default browser"
IMPORTED_ASK = "credentials.json imported.\n\nSign in with a Google account now?"
ADD_ACCOUNT_TITLE = "Add Google account"
ADD_ACCOUNT_TEXT = ("Pick the browser profile that's signed in to (or should hold) this Google account. "
                    "Drive Sync remembers it and opens this account's Drive links there too.")
ADD_ACCOUNT_HINT = "On Google's page, choose the account - or “Use another account”."
PROFILE_LABEL = "Open Drive links and sign-in in:"


def profile_choices(profiles) -> dict[str, str | None]:
    """{label shown in menus: profile id} for browsers.list_profiles(); the default browser first."""
    choices: dict[str, str | None] = {DEFAULT_BROWSER: None}
    for p in profiles:
        label = p.label if p.label not in choices else f"{p.label} ({p.directory})"
        choices[label] = p.id
    return choices


def profile_label(profile_id: str | None, choices: dict[str, str | None]) -> str:
    if not profile_id:
        return DEFAULT_BROWSER
    return next((label for label, pid in choices.items() if pid == profile_id),
                f"Missing profile ({profile_id.split('|')[-1]})")


def remove_account_question(email: str, n_folders: int) -> str:
    warn = (f"\n\n{n_folders} folder{'s' if n_folders != 1 else ''} use this account and will stop "
            "syncing until you choose another account for them.") if n_folders else ""
    return (f"Remove {email} from Drive Sync?\n\nDrive Sync's access to this account is revoked. "
            f"Files already on Google Drive are not deleted.{warn}")


def assign_account(cfg: dict, email: str) -> dict | None:
    """Settings where folders saved without an account (older versions / before any account existed)
    get this newly added one; None if no folder needed it."""
    folders = cfg.get("folders", [])
    if all(f.get("account") for f in folders):
        return None
    return {**cfg, "folders": [f if f.get("account") else {**f, "account": email} for f in folders]}


# --------------------------------------------------------------------------- Settings options
LIMITS = {"No limit": 0, "256 KB/s": 256, "512 KB/s": 512, "1 MB/s": 1024, "2 MB/s": 2048,
          "5 MB/s": 5120, "10 MB/s": 10240}
POLLS = {"Every 5 seconds": 5, "Every 10 seconds": 10, "Every 30 seconds": 30, "Every minute": 60}
INTERVALS = {"Every 5 minutes": 5, "Every 15 minutes": 15, "Every 30 minutes": 30, "Every hour": 60,
             "Every 3 hours": 180, "Every 6 hours": 360, "Once a day": 1440}
APPEARANCES = ("System", "Light", "Dark")
PRIORITIES = ("Normal", "High")

# Settings texts (the same as the Tk window's)
WATCH_LABEL = "Sync as soon as files change"
WATCH_HELP = "New, changed, renamed and deleted files are synced a few seconds after you save them."
POLL_LABEL = "Check Google Drive for changes"
POLL_HELP = "Two-way folders: how quickly changes made on Drive reach this PC. Each check is one tiny request."
INTERVAL_LABEL = "Look for missed changes"
INTERVAL_HELP = ("Two-way folders compare file sizes and times on this PC (a few seconds, no internet) and check "
                 "every file on both sides once a day. Sync now checks every file right away.")
NOTIFY_LABEL = "Show a notification when files are uploaded"
STARTUP_LABEL = "Start Drive Sync when I sign in to Windows"
PRIORITY_LABEL = "Priority"
PRIORITY_HELP = ("High: big downloads (16 MB+) use 4 connections at once, so Drive Sync gets more of your internet "
                 "while other apps are using it. It can't make the line itself faster, and it doesn't take priority "
                 "over other devices on your network (your router decides that).")
LIMIT_LABEL = "Speed limit"
LIMIT_HELP = ("Caps Drive Sync's total upload + download speed, to keep the rest of your internet free (e.g. for "
              "video calls). Applies immediately.")
METERED_LABEL = "Don't sync on metered connections"
METERED_HELP = ("Waits while you're on a phone hotspot, mobile data or Wi-Fi set as “metered” in Windows Settings, "
                "and carries on by itself on another network. Stops at the next file if the connection changes "
                "mid-sync.")
AUTO_UPDATE_LABEL = "Install updates automatically"
UPDATE_HELP = ("New versions come from the project's GitHub page. They're installed when nothing is syncing, and "
               "Drive Sync restarts by itself. Your folders, accounts and settings are kept.")
CLOSE_HINT = "Closing this window keeps Drive Sync running in the system tray."
QUIT_QUESTION = "Quit Drive Sync? Folders won't sync until it starts again."


@dataclass(frozen=True)
class OptionLabels:
    interval: str
    intervals: list[str]     # the menu (a value set outside it is listed first)
    poll: str
    limit: str
    priority: str
    watch: bool
    notify: bool
    metered: bool
    auto_update: bool
    appearance: str


def option_labels(cfg: dict) -> OptionLabels:
    """What Settings shows for the saved options."""
    current = next((k for k, v in INTERVALS.items() if v == cfg.get("interval_minutes")), None)
    intervals = list(INTERVALS) if current else [f"Every {cfg.get('interval_minutes')} minutes", *INTERVALS]
    poll = next((k for k, v in POLLS.items() if v == cfg.get("drive_poll_seconds", 10)), "Every 10 seconds")
    limit = next((k for k, v in LIMITS.items() if v == cfg.get("speed_limit_kbps", 0)),
                 f"{cfg.get('speed_limit_kbps')} KB/s")
    return OptionLabels(current or intervals[0], intervals, poll, limit,
                        "High" if cfg.get("high_priority") else "Normal", bool(cfg.get("watch_changes", True)),
                        bool(cfg.get("notify_on_changes")), bool(cfg.get("pause_on_metered")),
                        bool(cfg.get("auto_update", True)), cfg.get("appearance", "System"))


def options_cfg(cfg: dict, *, interval: str, poll: str, limit: str, priority: str, watch: bool, notify: bool,
                metered: bool, auto_update: bool) -> dict:
    """The settings with the options as chosen; a label not in the menus keeps the saved value."""
    return dict(cfg, interval_minutes=INTERVALS.get(interval, cfg.get("interval_minutes", 15)),
                notify_on_changes=notify, watch_changes=watch, drive_poll_seconds=POLLS.get(poll, 10),
                high_priority=priority == "High", pause_on_metered=metered, auto_update=auto_update,
                speed_limit_kbps=LIMITS.get(limit, cfg.get("speed_limit_kbps", 0)))


# --------------------------------------------------------------------------- Add / Edit folder
MODES = {"Two-way": "two_way", "Upload only (backup)": "upload"}
DRIVE_FOLDER_HINT = ("Click Choose… to pick a folder in your Drive (My Drive, shared folders or shared drives) - "
                     "or type a path like My Drive/PC Backup/Photos to create one.")
SKIPS_HINT = "Click Choose… to tick files and folders to skip - or type comma-separated patterns."
NO_ACCOUNT_YET = "No account added yet. Add one in Settings → Google accounts; this folder will use it."
MIRROR_LABEL = "Make Drive match this PC exactly"
MIRROR_HELP = ("Files in the Drive folder that aren't in the PC folder - including files that were already there - "
               "are moved to the Drive trash (recoverable for 30 days). Leave this off to only add and update files.")
NEED_PC_FOLDER = "Please choose a folder that exists on this PC."
NEED_ACCOUNT = "Add a Google account in Settings first."


def mode_help(mode: str) -> str:
    if mode == "two_way":
        return ("Changes on either side are copied to the other: new, edited and deleted files. Deleted files go "
                "to the Windows Recycle Bin or the Drive trash. If a file is edited on both sides, both versions "
                "are kept.")
    return "This PC is the master copy: files are only uploaded. Changes made on Drive never touch this PC."


def drive_text_for(data: dict) -> str:
    """The Drive folder box when editing: a chosen folder's path, or a typed one inside My Drive."""
    shown = data.get("drive_folder", "")
    return f"My Drive/{strip_my_drive(shown)}" if shown and not data.get("drive_folder_id") else shown


def default_drive_text(local: str) -> str:
    """Suggested Drive folder for a newly picked PC folder."""
    from pathlib import Path
    return f"My Drive/PC Backup/{Path(local).name}"


def folder_result(data: dict, *, local: str, account: str | None, mode: str, delete: bool, exclude_text: str,
                  exclude_where: dict, drive_text: str, drive_id: str | None, chosen_label: str | None) -> dict | None:
    """The folder's settings from the dialog, or None if the PC folder doesn't exist (NEED_PC_FOLDER).
    A Drive folder chosen in the browser is kept by id (unless the box was typed over)."""
    from pathlib import Path

    from skip_model import split_patterns
    local = local.strip()
    if not local or not os.path.isdir(local):
        return None
    local = os.path.normpath(local)
    text = drive_text.strip()
    result = {**data, "local": local, "account": account, "mode": mode,
              "delete": delete if mode == "upload" else False, "exclude": split_patterns(exclude_text)}
    where = {p: w for p, w in exclude_where.items() if p in result["exclude"]}
    if where:
        result["exclude_where"] = where
    else:
        result.pop("exclude_where", None)
    result.pop("drive_folder_id", None)
    if drive_id and text == chosen_label:
        result["drive_folder_id"] = drive_id
        result["drive_folder"] = chosen_label
    else:
        result["drive_folder"] = strip_my_drive(text) or f"PC Backup/{Path(local).name}"
    if account is None:
        result.pop("account")
    return result


def add_folder(cfg: dict, folder: dict, known: list[str], folder_account) -> dict | None:
    """Settings with a new folder that waits for "Start syncing"; None if that folder already syncs to
    that account (DUPLICATE_FOLDER)."""
    def key(f):
        return os.path.normcase(f["local"]), folder_account(f, known)
    if any(key(f) == key(folder) for f in cfg.get("folders", [])):
        return None
    return {**cfg, "folders": [*cfg.get("folders", []), {**folder, "waiting": True}]}


def replace_folder(cfg: dict, i: int, folder: dict) -> dict:
    folders = list(cfg.get("folders", []))
    folders[i] = folder
    return {**cfg, "folders": folders}
