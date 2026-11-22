#!/usr/bin/env python3
"""
drive_sync.py - one-way sync of a local folder to Google Drive.

Local is the source of truth: new files are uploaded, changed files are
re-uploaded (as a new revision of the same Drive file), unchanged files are
skipped. With --delete, items that no longer exist locally are moved to the
Drive trash (recoverable for 30 days).

Usage examples:
    python drive_sync.py "D:\\Photos"
    python drive_sync.py "D:\\Photos" --drive-folder "Backup/Photos" --delete
    python drive_sync.py "D:\\Work" --watch 300          # re-sync every 5 min
    python drive_sync.py "D:\\Work" --dry-run            # show what would happen
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import logging
import mimetypes
import os
import sys
import time
from pathlib import Path

# Full Drive access: needed to browse and sync into folders you created yourself (and shared
# folders / shared drives). Tokens granted with an older, narrower scope must sign in again.
SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_MIME = "application/vnd.google-apps.folder"
# Where settings, login token and state live. A packaged .exe (PyInstaller) can't
# write next to itself reliably, so it uses %LOCALAPPDATA%\DriveSync instead.
if getattr(sys, "frozen", False):
    APP_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "DriveSync"
    APP_DIR.mkdir(parents=True, exist_ok=True)
else:
    APP_DIR = Path(__file__).resolve().parent
# Bundled read-only files (README, icon): inside the .exe when frozen.
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
# Upload piece size (a multiple of 256 KB). Every piece is a separate request with ~0.5 s of
# waiting, so small pieces are slow: measured 1 MB pieces at 1.1 MB/s vs 8 MB at 2.1 MB/s.
# (Downloads don't use pieces at all - they stream; see downloader.py.)
CHUNK = 8 * 1024 * 1024
DOWNLOAD_PREFIX = "~drivesync-"   # in-progress downloads (two-way sync); never synced
DEFAULT_EXCLUDES = ["desktop.ini", "Thumbs.db", ".DS_Store", "~$*", "*.tmp", "*.crdownload", "*.part",
                    f"{DOWNLOAD_PREFIX}*"]

log = logging.getLogger("drive_sync")


# --------------------------------------------------------------------------- #
# Google Drive access
# --------------------------------------------------------------------------- #
def build_service(creds):
    """Drive v3 client for already-authorized credentials."""
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def get_service(credentials_path: Path, token_path: Path):
    """Return an authorized Drive v3 service, running the browser login once."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                raise FileNotFoundError(
                    f"Missing OAuth client file: {credentials_path}\n"
                    "Follow the setup steps in README.md to download credentials.json."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _q(value: str) -> str:
    """Escape a value for a Drive search query."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


class Drive:
    """Thin wrapper around the Drive API calls the syncer and the folder browser need.

    Works in My Drive, folders shared with you and shared drives (supportsAllDrives).
    """

    FIELDS = "id, name, mimeType, md5Checksum, size, modifiedTime"
    ALL = {"supportsAllDrives": True}

    def __init__(self, service, retries: int = 5):
        self.s = service
        self.retries = retries
        self.on_transfer = None  # on_transfer(fraction 0..1) while a big file uploads/downloads

    def _exec(self, request):
        return request.execute(num_retries=self.retries)

    def _list(self, q: str, fields: str, page_size: int = 1000, limit: int | None = None) -> list[dict]:
        items, token = [], None
        while True:
            resp = self._exec(self.s.files().list(
                q=q, fields=f"nextPageToken, files({fields})", pageSize=page_size, pageToken=token,
                corpora="allDrives", includeItemsFromAllDrives=True, **self.ALL))
            items.extend(resp.get("files", []))
            token = resp.get("nextPageToken")
            if not token or (limit and len(items) >= limit):
                return items

    # -- sync --------------------------------------------------------------
    def list_children(self, folder_id: str | None) -> list[dict]:
        if folder_id is None:  # folder doesn't exist yet (dry-run)
            return []
        return self._list(f"'{_q(folder_id)}' in parents and trashed = false", self.FIELDS)

    def find_folder(self, name: str, parent_id: str) -> str | None:
        found = self._list(f"name = '{_q(name)}' and mimeType = '{FOLDER_MIME}' "
                           f"and '{_q(parent_id)}' in parents and trashed = false", "id", page_size=1, limit=1)
        return found[0]["id"] if found else None

    def create_folder(self, name: str, parent_id: str) -> str:
        meta = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        return self._exec(self.s.files().create(body=meta, fields="id", **self.ALL))["id"]

    def _media(self, path: Path):
        from googleapiclient.http import MediaFileUpload
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        size = path.stat().st_size
        # Resumable upload for anything over 5 MB; simple upload otherwise.
        # (Empty files must use a simple upload.)
        from downloader import LIMIT
        # With a speed limit, smaller pieces keep uploads smooth (no 8 MB bursts at full speed).
        chunk = CHUNK if not LIMIT.rate or LIMIT.rate >= 4 * 1024 * 1024 else 1024 * 1024
        return MediaFileUpload(str(path), mimetype=mime, resumable=size > 5 * 1024 * 1024,
                               chunksize=chunk)

    def _send(self, request, media) -> dict:
        """Run an upload; big (resumable) uploads go in pieces and report progress after each.
        Every piece counts towards the speed limit (Settings)."""
        from downloader import LIMIT
        if not media.resumable():
            response = self._exec(request)
            LIMIT.consume(media.size() or 0)
            return response
        response, sent = None, 0
        while response is None:
            status, response = request.next_chunk(num_retries=self.retries)
            now = status.resumable_progress if status else (media.size() or sent)
            LIMIT.consume(max(0, now - sent))
            sent = now
            if status and self.on_transfer:
                self.on_transfer(status.progress())
        return response

    def upload(self, path: Path, parent_id: str) -> str:
        meta = {"name": path.name, "parents": [parent_id]}
        media = self._media(path)
        return self._send(self.s.files().create(body=meta, media_body=media, fields="id", **self.ALL), media)["id"]

    def update(self, file_id: str, path: Path) -> None:
        media = self._media(path)
        self._send(self.s.files().update(fileId=file_id, media_body=media, fields="id", **self.ALL), media)

    def trash(self, file_id: str) -> None:
        self._exec(self.s.files().update(fileId=file_id, body={"trashed": True}, fields="id", **self.ALL))

    # -- two-way sync -------------------------------------------------------
    def download(self, file_id: str, dest: Path, *, expected_md5: str | None = None, size: int | None = None,
                 on_bytes=None) -> None:
        """Download a file to dest - full speed, resumable, checksum-verified, split into parts in
        "High priority" mode, within the speed limit (see downloader.py).
        Safe to call from several threads at once (each thread uses its own connection)."""
        import downloader
        downloader.download(self.creds, file_id, dest, prefix=DOWNLOAD_PREFIX, expected_md5=expected_md5,
                            size=size, on_bytes=on_bytes)

    @property
    def creds(self):
        """The account's credentials (google-auth), taken from the API client."""
        return getattr(self.s._http, "credentials", None)

    def folder_status(self, folder_id: str) -> str:
        """'ok', 'trashed', 'missing' or 'not_folder'."""
        from googleapiclient.errors import HttpError
        try:
            f = self._exec(self.s.files().get(fileId=folder_id, fields="id, trashed, mimeType", **self.ALL))
        except HttpError as e:
            if getattr(e, "status_code", None) == 404 or getattr(e.resp, "status", None) == 404:
                return "missing"
            raise
        if f.get("trashed"):
            return "trashed"
        return "ok" if f.get("mimeType") == FOLDER_MIME else "not_folder"

    def changes_start_token(self) -> str:
        return self._exec(self.s.changes().getStartPageToken(**self.ALL))["startPageToken"]

    def changes_since(self, token: str) -> tuple[set[str], str]:
        """(ids of the changed files and of their folders, new token) - one cheap request usually.

        The folder ids tell the two-way engine which folders to look at; it ignores ids it doesn't know."""
        ids: set[str] = set()
        while True:
            resp = self._exec(self.s.changes().list(
                pageToken=token, pageSize=1000,
                fields="nextPageToken, newStartPageToken, changes(fileId, file(parents))",
                includeItemsFromAllDrives=True, **self.ALL))
            for c in resp.get("changes", []):
                if c.get("fileId"):
                    ids.add(c["fileId"])
                ids.update((c.get("file") or {}).get("parents") or [])
            if resp.get("newStartPageToken"):
                return ids, resp["newStartPageToken"]
            token = resp["nextPageToken"]

    # -- folder browser ------------------------------------------------------
    def list_subfolders(self, parent_id: str) -> list[dict]:
        """Folders directly inside parent_id ("root" = My Drive), sorted by name."""
        found = self._list(f"'{_q(parent_id)}' in parents and mimeType = '{FOLDER_MIME}' and trashed = false",
                           "id, name, capabilities(canAddChildren)")
        return sorted(found, key=lambda f: f["name"].lower())

    def list_shared_with_me(self) -> list[dict]:
        found = self._exec(self.s.files().list(
            q=f"sharedWithMe and mimeType = '{FOLDER_MIME}' and trashed = false",
            fields="files(id, name, capabilities(canAddChildren))", pageSize=200))
        return sorted(found.get("files", []), key=lambda f: f["name"].lower())

    def list_shared_drives(self) -> list[dict]:
        drives, token = [], None
        while True:
            resp = self._exec(self.s.drives().list(pageSize=100, pageToken=token,
                                                   fields="nextPageToken, drives(id, name)"))
            drives.extend(resp.get("drives", []))
            token = resp.get("nextPageToken")
            if not token:
                return sorted(drives, key=lambda d: d["name"].lower())


# --------------------------------------------------------------------------- #
# Sync logic
# --------------------------------------------------------------------------- #
def md5_of(path: Path) -> str:
    h = hashlib.md5(usedforsecurity=False)  # matches Drive's md5Checksum; not used for security
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class Syncer:
    def __init__(self, drive, local_root: Path, remote_root_id: str | None, *,
                 excludes: list[str], delete: bool = False, dry_run: bool = False,
                 state_path: Path | None = None, on_event=None, progress=None):
        self.on_event = on_event  # on_event(kind, rel_path, message) - feeds the Activity tab
        self.progress = progress  # progress.Progress - percentage / current file for the UI
        self.drive = drive
        self.local_root = local_root
        self.remote_root_id = remote_root_id
        self.excludes = excludes
        self.delete = delete
        self.dry_run = dry_run
        self.state_path = state_path
        # md5 cache: rel_path -> {"size", "mtime_ns", "md5"} so unchanged files
        # aren't re-hashed every run.
        self.cache: dict[str, dict] = {}
        if state_path and state_path.exists():
            try:
                self.cache = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("Ignoring unreadable state file %s", state_path)
        self.new_cache: dict[str, dict] = {}
        self.stats = dict(uploaded=0, updated=0, unchanged=0, folders=0, trashed=0, skipped=0, errors=0)

    # -- helpers ---------------------------------------------------------- #
    def _excluded(self, name: str, rel: str) -> bool:
        rel_posix = rel.replace(os.sep, "/")
        return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel_posix, p) for p in self.excludes)

    def _local_md5(self, path: Path, rel: str, st: os.stat_result) -> str:
        cached = self.cache.get(rel)
        if cached and cached["size"] == st.st_size and cached["mtime_ns"] == st.st_mtime_ns:
            md5 = cached["md5"]
        else:
            md5 = md5_of(path)
        self.new_cache[rel] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "md5": md5}
        return md5

    def _act(self, msg: str, fn, *args):
        log.info("%s%s", "[dry-run] " if self.dry_run else "", msg)
        return None if self.dry_run else fn(*args)

    def _xfer(self, action: str, rel: str, size: int, fn, *args):
        """Run an upload/update and show it as the current transfer."""
        if self.progress:
            self.progress.transfer(action, rel.replace(os.sep, "/"), size)
        try:
            return fn(*args)
        finally:
            if self.progress:
                self.progress.transfer_end()

    def _emit(self, kind: str, rel: str, message: str | None = None) -> None:
        """kind: uploaded | updated | trashed | folder | skipped | error"""
        if self.on_event:
            try:
                self.on_event(kind, rel.replace(os.sep, "/"), message)
            except Exception as e:  # noqa: BLE001 - reporting must never break syncing
                log.debug("Activity hook failed: %s", e)

    def _fail(self, rel: str, what: str, e: Exception) -> None:
        log.error("%s %s: %s", what, rel or str(self.local_root), e)
        self.stats["errors"] += 1
        self._emit("error", rel or ".", f"{what}: {e}")

    # -- main ------------------------------------------------------------- #
    def run(self) -> dict:
        start = time.monotonic()
        try:
            self._sync_dir(self.local_root, self.remote_root_id, "")
        finally:
            if self.state_path and not self.dry_run:
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                self.state_path.write_text(json.dumps(self.new_cache), encoding="utf-8")
        s = self.stats
        log.info("Done in %.1fs: %d uploaded, %d updated, %d unchanged, %d folders created, "
                 "%d trashed, %d skipped, %d errors", time.monotonic() - start,
                 s["uploaded"], s["updated"], s["unchanged"], s["folders"], s["trashed"],
                 s["skipped"], s["errors"])
        return s

    def _sync_dir(self, local_dir: Path, remote_id: str | None, rel_dir: str) -> None:
        try:
            remote_items = self.drive.list_children(remote_id)
            entries = sorted(os.scandir(local_dir), key=lambda e: e.name.lower())
        except Exception as e:  # noqa: BLE001
            self._fail(rel_dir, "Couldn't read folder", e)
            return

        by_name: dict[str, list[dict]] = {}
        for item in remote_items:
            by_name.setdefault(item["name"], []).append(item)

        kept_ids: set[str] = set()
        for entry in entries:
            rel = os.path.join(rel_dir, entry.name) if rel_dir else entry.name
            if self._excluded(entry.name, rel):
                continue
            if entry.is_symlink():
                log.debug("Skipping symlink %s", rel)
                continue
            existing = by_name.get(entry.name, [])
            try:
                if entry.is_dir():
                    folder = next((i for i in existing if i["mimeType"] == FOLDER_MIME), None)
                    if folder:
                        folder_id = folder["id"]
                    else:
                        folder_id = self._act(f"Create folder  {rel}", self.drive.create_folder,
                                              entry.name, remote_id)
                        self.stats["folders"] += 1
                        self._emit("folder", rel)
                    if folder_id:
                        kept_ids.add(folder_id)
                    self._sync_dir(Path(entry.path), folder_id, rel)
                elif entry.is_file():
                    st = entry.stat()
                    try:
                        self._sync_file(Path(entry.path), rel, st, existing, remote_id, kept_ids)
                    finally:
                        if self.progress:
                            self.progress.file_done(st.st_size)
            except Exception as e:  # noqa: BLE001 - keep going on per-item failures
                self._fail(rel, "Couldn't sync", e)
                # Don't trash the remote copy just because the local read/upload failed.
                kept_ids.update(i["id"] for i in existing)

        if self.delete:
            for item in remote_items:
                if item["id"] in kept_ids:
                    continue
                rel = os.path.join(rel_dir, item["name"]) if rel_dir else item["name"]
                if self._excluded(item["name"], rel):
                    continue
                if item["mimeType"] != FOLDER_MIME and "md5Checksum" not in item:
                    continue  # native Google Doc/Sheet etc. - never created by us, leave it
                try:
                    self._act(f"Trash          {rel}", self.drive.trash, item["id"])
                    self.stats["trashed"] += 1
                    self._emit("trashed", rel)
                except Exception as e:  # noqa: BLE001
                    self._fail(rel, "Couldn't move to Drive trash", e)

    def _sync_file(self, path: Path, rel: str, st: os.stat_result, existing: list[dict],
                   parent_id: str | None, kept_ids: set[str]) -> None:
        files = [i for i in existing if i["mimeType"] != FOLDER_MIME]
        if not files:
            self._act(f"Upload         {rel}", self._xfer, "upload", rel, st.st_size, self.drive.upload, path,
                      parent_id)
            self._local_md5(path, rel, st)  # prime cache
            self.stats["uploaded"] += 1
            self._emit("uploaded", rel)
            return
        remote = files[0]
        kept_ids.add(remote["id"])  # extra duplicates with the same name are left to --delete
        if "md5Checksum" not in remote:
            log.warning("Skipping %s: a Google-native file with that name exists on Drive", rel)
            self.stats["skipped"] += 1
            self._emit("skipped", rel, "A Google Docs/Sheets/Slides file with this name is already there")
            return
        if self._local_md5(path, rel, st) == remote["md5Checksum"]:
            self.stats["unchanged"] += 1
            return
        self._act(f"Update         {rel}", self._xfer, "update", rel, st.st_size, self.drive.update, remote["id"],
                  path)
        self.stats["updated"] += 1
        self._emit("updated", rel)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def resolve_remote_root(drive: Drive, path: str, create: bool) -> str | None:
    """Resolve 'A/B/C' under My Drive, creating missing folders (unless dry-run)."""
    parent = "root"
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if parts and parts[0].lower() == "my drive":  # "My Drive/X" means X inside My Drive
        parts = parts[1:]
    for part in parts:
        found = drive.find_folder(part, parent)
        if not found:
            if not create:
                log.info("[dry-run] Drive folder '%s' would be created", path)
                return None
            log.info("Creating Drive folder '%s'", part)
            found = drive.create_folder(part, parent)
        parent = found
    return parent


def sync_folder(drive: Drive, local_folder: str, *, drive_folder: str | None = None,
                drive_folder_id: str | None = None, delete: bool = False,
                excludes: list[str] | None = None, dry_run: bool = False, on_event=None,
                mode: str = "upload", progress=None, quick: dict | None = None) -> dict:
    """Run one sync of local_folder. Returns stats (plus 'remote_id' and 'scope').

    mode: "upload" (PC -> Drive backup) or "two_way" (changes go both ways).
    quick (two-way only): check only what changed - {"paths": changed paths on this PC,
        "ids": changed Drive ids, "scan": also compare every file's size/time with the last sync}.
        Falls back to checking everything when the folder has no usable sync state yet.
    scope in the result: "quick" or "full" (what actually ran).
    """
    local_root = Path(local_folder).expanduser().resolve()
    if not local_root.is_dir():
        raise NotADirectoryError(f"Not a folder: {local_root}")
    if excludes is None:
        excludes = list(DEFAULT_EXCLUDES)
    target = drive_folder or local_root.name
    remote_root = drive_folder_id or resolve_remote_root(drive, target, create=not dry_run)
    key = hashlib.sha1(f"{local_root}|{remote_root}".lower().encode(), usedforsecurity=False).hexdigest()[:16]
    log.info("Syncing %s  %s  Google Drive:%s", local_root, "<->" if mode == "two_way" else "->",
             drive_folder_id or target)
    state = APP_DIR / "state" / f"{key}.json"
    if hasattr(drive, "on_transfer") and progress:
        drive.on_transfer = progress.transfer_fraction
    try:
        if mode == "two_way" and not dry_run:
            from twoway import TwoWaySyncer
            # The upload-only state (if any) tells the two-way engine what was already on Drive.
            syncer = TwoWaySyncer(drive, local_root, remote_root, excludes=excludes,
                                  state_path=state.with_suffix(".2way.json"), migrate_from=state,
                                  on_event=on_event, progress=progress)
            targets = syncer.targets_for(quick.get("paths", ()), quick.get("ids", ()),
                                         scan=bool(quick.get("scan"))) if quick else None
            stats = syncer.run(targets)
            stats["scope"] = "full" if targets is None else "quick"
        else:
            stats = Syncer(drive, local_root, remote_root, excludes=excludes, delete=delete,
                           dry_run=dry_run, state_path=state, on_event=on_event, progress=progress).run()
            stats["scope"] = "full"
    finally:
        if hasattr(drive, "on_transfer"):
            drive.on_transfer = None
    stats["remote_id"] = remote_root
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="One-way sync of a local folder to Google Drive.")
    ap.add_argument("local_folder", help="Folder on this PC to sync")
    ap.add_argument("--drive-folder", help="Target path under My Drive, e.g. 'Backup/Photos' "
                    "(default: the local folder's name)")
    ap.add_argument("--drive-folder-id", help="Target Drive folder ID instead of a path")
    ap.add_argument("--delete", action="store_true",
                    help="Move Drive items that no longer exist locally to the Drive trash")
    ap.add_argument("--exclude", action="append", default=[], metavar="PATTERN",
                    help="Glob to skip (name or relative path), repeatable, e.g. --exclude '*.log' "
                         "--exclude 'node_modules'")
    ap.add_argument("--no-default-excludes", action="store_true",
                    help=f"Don't skip the default junk files ({', '.join(DEFAULT_EXCLUDES)})")
    ap.add_argument("--dry-run", action="store_true", help="Show what would change without changing anything")
    ap.add_argument("--watch", type=int, metavar="SECONDS", help="Keep running and re-sync every N seconds")
    ap.add_argument("--credentials", default=str(APP_DIR / "credentials.json"), help="OAuth client file")
    ap.add_argument("--token", default=str(APP_DIR / "token.json"), help="Where to cache the login token")
    ap.add_argument("--log-file", help="Also write the log to this file")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S",
                        handlers=handlers)
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)

    if not Path(args.local_folder).expanduser().is_dir():
        log.error("Not a folder: %s", args.local_folder)
        return 2

    excludes = ([] if args.no_default_excludes else list(DEFAULT_EXCLUDES)) + args.exclude
    try:
        drive = Drive(get_service(Path(args.credentials), Path(args.token)))
    except FileNotFoundError as e:
        log.error("%s", e)
        return 2

    while True:
        try:
            stats = sync_folder(drive, args.local_folder, drive_folder=args.drive_folder,
                                drive_folder_id=args.drive_folder_id, delete=args.delete,
                                excludes=excludes, dry_run=args.dry_run)
        except KeyboardInterrupt:
            return 130
        except Exception as e:  # noqa: BLE001
            log.exception("Sync run failed: %s", e)
            stats = {"errors": 1}
            if not args.watch:
                return 1
        if not args.watch:
            return 1 if stats["errors"] else 0
        try:
            time.sleep(args.watch)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())
