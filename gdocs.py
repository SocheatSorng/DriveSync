"""Google Docs, Sheets, Slides etc. on this PC.

They have no file to download, so two-way sync puts a small Internet Shortcut in their place,
e.g. "Enrollment.gsheet.url". Explorer always hides ".url", so it shows as "Enrollment.gsheet",
and double-clicking it opens the file in the browser. Deleting, renaming or moving the shortcut
does the same to the file on Google Drive.
"""
from __future__ import annotations

import re
from pathlib import Path

FOLDER_MIME = "application/vnd.google-apps.folder"
NATIVE = "application/vnd.google-apps."

# kind -> (extension shown in Explorer, address that opens it)
_KINDS = {
    "document": ("gdoc", "https://docs.google.com/document/d/{id}/edit"),
    "spreadsheet": ("gsheet", "https://docs.google.com/spreadsheets/d/{id}/edit"),
    "presentation": ("gslides", "https://docs.google.com/presentation/d/{id}/edit"),
    "form": ("gform", "https://docs.google.com/forms/d/{id}/edit"),
    "drawing": ("gdraw", "https://docs.google.com/drawings/d/{id}/edit"),
    "script": ("gscript", "https://script.google.com/d/{id}/edit"),
    "site": ("gsite", "https://drive.google.com/open?id={id}"),
    "map": ("gmap", "https://drive.google.com/open?id={id}"),
    "jam": ("gjam", "https://drive.google.com/open?id={id}"),
    "vid": ("gvid", "https://drive.google.com/open?id={id}"),
}
_OTHER = ("glink", "https://drive.google.com/open?id={id}")   # shortcuts and anything newer
NAMES = {
    "gdoc": "Google Docs", "gsheet": "Google Sheets", "gslides": "Google Slides", "gform": "Google Forms",
    "gdraw": "Google Drawings", "gscript": "Apps Script", "gsite": "Google Sites", "gmap": "Google My Maps",
    "gjam": "Jamboard", "gvid": "Google Vids", "glink": "Google Drive",
}
_LINK = re.compile(r"\.(" + "|".join(NAMES) + r")\.url$", re.IGNORECASE)
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_ID = re.compile(r"^id=([\w-]+)\s*$", re.MULTILINE)
_URL_ID = re.compile(r"/d/([\w-]+)|[?&]id=([\w-]+)")


def is_native(item: dict) -> bool:
    """A Google Doc/Sheet/Slides/shortcut etc. (no file content), not a folder."""
    mime = item.get("mimeType", "")
    return mime.startswith(NATIVE) and mime != FOLDER_MIME


def _kind(item: dict) -> tuple[str, str]:
    return _KINDS.get(item["mimeType"][len(NATIVE):], _OTHER)


def link_name(item: dict) -> str:
    """The shortcut's file name on this PC, e.g. "Enrollment.gsheet.url"."""
    name = _BAD.sub("_", item["name"]).rstrip(" .") or "Untitled"
    return f"{name}.{_kind(item)[0]}.url"


def is_link_name(name: str) -> bool:
    return bool(_LINK.search(name))


def drive_name(link: str) -> str:
    """The Google Drive name for a shortcut file name ("Enrollment.gsheet.url" -> "Enrollment")."""
    return _LINK.sub("", link)


def kind_name(link: str) -> str:
    m = _LINK.search(link)
    return NAMES.get(m.group(1).lower(), "Google Drive") if m else "Google Drive"


def write_link(path: Path, item: dict) -> None:
    url = _kind(item)[1].format(id=item["id"])
    path.write_bytes(f"[InternetShortcut]\r\nURL={url}\r\n[DriveSync]\r\nid={item['id']}\r\n".encode("ascii"))


def read_link(path: Path) -> str | None:
    """The Google Drive id a shortcut points to (None if it isn't one of ours or can't be read)."""
    try:
        text = path.read_bytes()[:4096].decode("utf-8", "ignore")
    except OSError:
        return None
    m = _ID.search(text)
    if m:
        return m.group(1)
    m = next((m for line in text.splitlines() if line.upper().startswith("URL=")
              for m in [_URL_ID.search(line)] if m), None)
    return (m.group(1) or m.group(2)) if m else None
