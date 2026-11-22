# Drive Sync: keep folders on this PC and Google Drive in sync

Each folder syncs in one of two modes, which you choose in **Edit folder → Sync direction**:

- **Two-way** (default): changes on either side are copied to the other side. That covers new, edited, renamed and deleted files and folders.
  - Deleted on Drive → the PC copy goes to the **Windows Recycle Bin**. Deleted on the PC → the Drive copy goes to the **Drive trash**.
  - Edited on both sides → both are kept. Drive's version keeps the name, and the PC version is saved as `name (conflict <date> PC)`.
  - Deleted on one side but edited on the other → the edited file is kept.
  - Drive is checked for changes **every 10 seconds** by default (one tiny request per account; adjust in **Settings → Check Google Drive for changes**, from 5 seconds to 1 minute). So Drive changes reach the PC in about 5–15 seconds, and PC changes reach Drive within a few seconds. Google itself takes a few seconds to report a change made on drive.google.com.
  - If the chosen Drive folder itself is deleted or trashed, Drive Sync stops for that folder and changes nothing on the PC.
  - Google Docs, Sheets and Slides stay on Drive only; they have no file to download.
- **Upload only (backup)**: the PC is the master copy. Files are only uploaded, and Drive-side changes never touch the PC.

In both modes:

- New files are uploaded. Changed files are re-uploaded as a new revision of the same Drive file, and Drive keeps the old revisions. Unchanged files are skipped. Files are compared by MD5 checksum.
- Subfolders are mirrored.
- If you turn on **"Make Drive match this PC exactly"** for a folder (`--delete` on the command line), files in the Drive folder that aren't in the PC folder go to the **Drive trash**, where you can recover them for 30 days. That includes files that were already in a Drive folder you chose. Nothing is ever deleted permanently.
- Large files use resumable uploads, and network errors are retried. A big upload stopped part-way (pause, quit, restart) carries on where it stopped, for up to about a week.
- A file or folder you rename or move on this PC is renamed or moved on Drive too, not uploaded again (two-way folders, and upload-only folders with **Make Drive match this PC exactly**).
- Pause from the tray for 30 minutes, 1 or 3 hours, until tomorrow morning, or until you resume. Pausing stops at the next file, or part-way through a big one.
- Settings shows how much of each account's Drive storage is used, and warns at 90%. If Drive or this PC's disk is full, you get one clear message, the other direction keeps syncing, and the app tries again every 30 minutes (or on **Sync now**).
- Optional: **Don't sync on metered connections** (Settings → Internet use) waits while you're on a phone hotspot or metered Wi-Fi.
- It comes with a desktop window and a system-tray icon, and it can start automatically with Windows.

## 1. Google setup (once, about 5 minutes)

Google requires each app that uses the Drive API to have its own client ID:

1. Go to <https://console.cloud.google.com/> and create a project (any name, e.g. "Drive Sync").
2. **APIs & Services → Library** → search **Google Drive API** → **Enable**.
3. **APIs & Services → OAuth consent screen** (also called "Google Auth Platform") → User type **External** → fill in the app name and your email → save.
   Under **Audience / Test users**, add **every Google account you'll sync to**. While the app is in Testing mode, Google blocks any account that isn't on this list. Alternatively, publish the app (see the tip below).
4. **APIs & Services → Credentials → Create credentials → OAuth client ID** → Application type **Desktop app** → Create.
5. Click **Download JSON**. You'll import this file in the app (Settings → **Import credentials.json…**).

When you sign in, you may see an "unverified app" warning. Click **Advanced → Go to …**; this is safe because it's your own app.

> Tip: if you leave the consent screen in "Testing" mode, Google expires the login after 7 days. Click **Publish app** on the consent screen to avoid that. You don't need verification for personal use.

## 2. Install

### Option A: `DriveSync.exe` (no Python needed)
Download `DriveSync.exe` from the repository's **Releases** page, or build it yourself with `build.ps1`. Put it somewhere permanent, such as `C:\Program Files\DriveSync` or `%LOCALAPPDATA%\Programs`, and run it.

### Option B: from source (needs Python 3.10+)
```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```
This copies the app to `%LOCALAPPDATA%\DriveSync`, installs the Python packages, adds it to Windows startup, and opens the window. You can pre-fill folders with `-Folder "D:\A","D:\B"`; other options are `-IntervalMinutes 10`, `-Delete` and `-DriveParent "Backup"`.

Both options keep settings, logs and the Google logins in `%LOCALAPPDATA%\DriveSync`, with one file per account in `accounts\`. Removing an account in Settings deletes its login file and revokes Drive Sync's access at Google.

## 3. Using the app

1. **Settings → Import credentials.json…**, pick the file from step 1, then click **+ Add Google account**. Your browser opens on Google's account chooser: pick the account (or **Use another account**) and allow access. Repeat this to add more accounts, such as personal and work.
2. **Folders → + Add folder**, choose a folder and **which Google account** it syncs to. Then click **Choose…** next to *Google Drive folder* to browse that account's Drive (**My Drive**, **Shared with me** and **Shared drives**). Open folders by clicking them, use **+ New folder** if needed, then click **Use this folder**. Alternatively, type a path such as `My Drive/PC Backup/Photos` and it's created on the first sync. Files already in the chosen folder are matched by name and content, so identical files aren't uploaded twice. The same PC folder can sync to two accounts if you add it twice.
3. In **Settings**, turn on **"Start Drive Sync when I sign in to Windows"**.

| Tab | What's there |
|---|---|
| **Folders** | Your synced folders, each with **Open**, **Drive** (opens it in Google Drive), **Edit** and **Remove** buttons |
| **Settings** | Google accounts (add, remove, sign in again, and which browser profile each one opens in), instant sync on/off, how often to do a full check, notifications, start with Windows, light/dark mode |
| **Activity** | What Drive Sync did, newest first and grouped by day. One card per folder sync that changed something, showing counts, the reason it ran, and the account. Click **Show items** for the file list. Filter by **All / Changes / Problems**, or search for a file, folder or account. Problems open automatically. Checks that changed nothing aren't listed; the line at the top shows when the last check ran. The newest 1,000 entries are kept across restarts. **Log file** opens the full technical log. |

Closing the window keeps Drive Sync running in the **system tray**. Look in the `^` overflow area of the taskbar, or drag the icon onto the taskbar to keep it visible. Double-click the tray icon to open the window. Right-click it for **Sync now**, **Pause** and **Quit**.

| Icon | Meaning |
|---|---|
| Green DS | Up to date |
| Amber DS | Syncing |
| Red DS | Needs attention; open the window to see why |
| Gray DS | Paused |

**Instant sync:** when you create, save, rename or delete something in a synced folder, that folder syncs a few seconds later. Drive Sync waits until the activity stops, so a file that's still being copied or saved isn't uploaded half-finished. Temporary files such as Word's `~$` lock files and `.tmp` files are ignored. As a safety net, Drive Sync also looks for missed changes on the **"Look for missed changes"** schedule. You can turn instant sync off in **Settings → Syncing**.

**Only what changed is checked (two-way folders):** Drive Sync remembers where it stopped reading Google's list of changes, even when it's closed. At startup it asks Google what changed in the meantime, and compares every file's size and modified time on this PC with the last sync. That takes seconds even for tens of thousands of files, and nothing is read or sent. Then it syncs only the folders where something changed. Every file on both sides is still checked **once a day**, when a folder is synced for the first time, and when you press **Sync now**. Upload-only folders check every file each time.

At Windows login it starts in the tray only. It waits 30 seconds for the network, then checks what changed while it was off. If the internet is down, it retries every 2 minutes.

**Remove auto-start:** switch it off in Settings. If you installed from source, you can also run `powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\DriveSync\uninstall.ps1"`, adding `-RemoveFiles` to also delete the app folder. Neither touches your files on Drive.

## 4. Command line (optional)

The sync engine can also run on its own:
```powershell
pip install -r requirements.txt
python drive_sync.py "D:\Photos" --drive-folder "Backup/Photos" --delete --dry-run
```

| Option | Meaning |
|---|---|
| `--drive-folder PATH` | Destination under My Drive (default: the local folder's name) |
| `--drive-folder-id ID` | Destination folder by ID instead of by path |
| `--delete` | Trash Drive items that no longer exist locally |
| `--exclude PATTERN` | Glob to skip, matched on the name or relative path (repeatable) |
| `--no-default-excludes` | Also sync `desktop.ini`, `Thumbs.db`, `~$*`, `*.tmp`, Mac leftovers (`__MACOSX`, `._*`, `.DS_Store`) and similar junk files |
| `--dry-run` | Only print what would happen |
| `--watch SECONDS` | Loop forever, syncing every N seconds |
| `--log-file FILE` | Also write the log to a file |

When run from source, the command line uses `credentials.json` and `token.json` from the script's own folder.

## 5. Development: CI/CD and security

| File | What it does |
|---|---|
| `pyproject.toml` + `uv.lock` | Dependencies managed by **uv**. `uv.lock` pins the exact version and hash of every package, including indirect ones, so every build uses the same, verified code. `requirements.txt` is generated from it for `install.ps1`; pip checks the hashes. |
| `.github/workflows/ci.yml` | On every push or pull request: lints with **Ruff**, runs the unit tests on Windows (Python 3.11 and 3.13), and checks that `requirements.txt` matches `uv.lock`. Then it builds `DriveSync.exe` with **PyInstaller**. Pushing a tag such as `v1.0.0` publishes the exe as a **GitHub Release** using GitHub's built-in `gh` command. |
| `.github/workflows/security.yml` | On every push, every pull request and every Monday: **Gitleaks** scans the whole git history for leaked secrets, **Bandit** scans the Python code, **pip-audit** checks the pinned packages for known vulnerabilities, and **zizmor** checks the workflow files for unsafe permissions and injection risks. |
| `.github/workflows/codeql.yml` | GitHub **CodeQL** follows data through the Python code and the workflows. Results appear in the repo's **Security → Code scanning** tab. It's free for public repos; private repos need GitHub Code Security. |
| `.github/dependabot.yml` | Opens pull requests when GitHub Actions or packages in `uv.lock` have updates. It waits 7 days after a new release first, as protection against compromised releases. |
| `.pre-commit-config.yaml` | Runs Gitleaks, Ruff, Bandit and zizmor, and keeps `uv.lock` and `requirements.txt` in sync, on your PC **before each commit**. |
| `build.ps1` | Builds `dist\DriveSync.exe`. |

**How the workflows are hardened:**
- Every job starts with **Harden-Runner** (step-security), which records every network connection the build machine makes. On GitHub-hosted Windows runners this works in audit mode only, and it's free for public repos.
- Every action is pinned to a full commit SHA, and Dependabot keeps the SHAs up to date.
- Jobs get only the permissions they need; only the release job can write to the repo.
- Checkouts don't keep the GitHub token, and the build that produces releases doesn't use shared caches.

Run the checks locally (first install uv with `pip install uv` or from <https://docs.astral.sh/uv/>):
```powershell
uv sync                                   # creates .venv with exactly what uv.lock says
uv run ruff check .
uv run python -m unittest -v
uv run bandit -c pyproject.toml -r drive_sync.py tray_app.py gui.py accounts.py browsers.py watcher.py activity.py activity_view.py twoway.py progress.py downloader.py
uv run pip-audit -r requirements.txt --require-hashes --disable-pip
uv run zizmor .github/workflows
uv run pre-commit install                 # once per clone (needs a git repository)
```

Add or upgrade a package:
```powershell
uv add some-package                       # or: uv lock --upgrade-package some-package
uv export --no-dev --no-emit-project -o requirements.txt
```

Release a new version:
```powershell
git tag v1.0.0
git push origin v1.0.0
```

> **Never commit `credentials.json` or `token.json`.** `token.json` is a live login to your Google Drive. `.gitignore` excludes both files, and Gitleaks blocks them if they slip through. If the repository belongs to a GitHub *organization*, Gitleaks needs a free licence key saved as the `GITLEAKS_LICENSE` secret. Personal repositories don't need one.

## Notes and limits
- **Permissions:** the app asks for full Google Drive access (the `drive` scope) so you can browse and sync into folders you created yourself, shared folders and shared drives. Google shows this as "See, edit, create, and delete all of your Google Drive files". Drive Sync only *changes* files inside the folders you choose. It only *reads* other folders to list them in the folder browser. Accounts added by older versions, which had the narrower `drive.file` permission, show **Sign in again** once.
- **Upload only** folders: changes made on the Drive side are overwritten by the next sync if the local file differs. They are never downloaded. Use **Two-way** if you also work on Drive.
- **Two-way** folders: Drive Sync remembers what both sides looked like after each sync, in `state\*.2way.json`. That's how it tells "deleted on Drive" apart from "new on this PC". A folder switched from upload-only to two-way reuses its earlier upload state, so files that were already backed up are recognised.
- If you rename or move a file locally in an **upload-only** folder without **Make Drive match this PC exactly**, the next sync uploads it under the new name and the old copy stays on Drive. With that option on (and in two-way folders), Drive's copy is renamed or moved instead. A file that was renamed *and* edited counts as a new file.
- Files that are open or locked during a sync are logged as errors and retried next time.
