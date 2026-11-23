# Tasks: move the window to Qt (PySide6)

Plan and decisions: `tasks/plan.md`. Every task leaves the app working; Tk stays the default until Task 13.

Commands used below:
- Tests: `.\.venv\Scripts\python.exe -m unittest discover -p "test_*.py"`
- Build: `.\build.ps1`
- Qt mode: `$env:DRIVESYNC_UI = "qt"; .\.venv\Scripts\python.exe tray_app.py`

---

## Phase 1: Foundation

## Task 1: Qt window shell behind the `ui` switch
**Description:** Add PySide6, a UI host seam in `tray_app.py` (Tk and Qt versions of: create window,
appearance mode, callback errors to the log, window icon, timer, main loop, quit), and an empty Qt main
window that polls `App.ui` with a `QTimer`. With `DRIVESYNC_UI=qt` the app runs with the Qt window;
otherwise exactly as today.

**Acceptance criteria:**
- [x] `DRIVESYNC_UI=qt`: the tray icon (incl. sync animation and menu) works; "Open Drive Sync" shows the window, closing hides it to the tray, "Quit" exits cleanly
- [x] An exception in a Qt handler is logged ("Error in the window"), not shown as a console crash
- [x] Without the switch the Tk window behaves exactly as before

**Verification:**
- [x] Tests pass (new `test_ui_host.py`: switch picks the right host, Qt host polls the queue, `show`/`quit` messages)
- [x] Build succeeds (exe now 74.1 MB, was 44.0 MB: PyInstaller bundles PySide6 - Task 2 trims it)
- [x] Manual: start in Qt mode, open/hide/quit from the tray; start hidden (`--hidden`) - checked in a throwaway copy with no accounts

**Done 2026-11-23:** `ui_host.py` (TkHost/QtHost), `qtui/main_window.py` shell, `pyside6-essentials` added.

**Dependencies:** None
**Files likely touched:** `pyproject.toml`, `uv.lock`, `requirements.txt`, `tray_app.py`, `qtui/__init__.py`, `qtui/main_window.py`, `test_ui_host.py`
**Estimated scope:** Medium

## Task 2: Packaging spike for Qt
**Description:** Build the exe with PySide6 included, excluding unused Qt modules. Measure size and
start time, check the installer and the in-app updater still work, and decide one-file vs one-folder
(LGPL lets users replace Qt's DLLs; a one-folder build inside the installer makes that easy).

**Acceptance criteria:**
- [x] The built exe starts in Qt mode on this PC; size and cold start time written in `tasks/plan.md`
- [x] Decision recorded: one-file or one-folder, with Qt licence notices shipped
- [ ] `DriveSync-Setup.exe` installs, starts, and uninstalls cleanly - **not done here**: installing would
      replace the real installed copy on this PC. Do it on a spare PC/VM or Windows Sandbox before release.

**Verification:**
- [x] Build succeeds (folder 97.1 MB / 1,043 files, portable exe 43.7 MB, Setup 33.1 MB)
- [ ] Manual: install with Setup, run in Qt mode, uninstall (see above)

**Done 2026-11-23:** `DriveSync.spec` (committed) + tested `build_filter.py`; Setup installs the one-folder
build; `licenses/` (LGPL-3.0, GPL-3.0, notices) bundled; flaky `test_skip_picker` race fixed.

**Dependencies:** Task 1
**Files likely touched:** `DriveSync.spec`, `build.ps1`, `installer.iss`, `tasks/plan.md`
**Estimated scope:** Small

## Task 3: Shared view logic in `ui_model.py`
**Description:** Move the text and decisions both windows need out of `gui.py` into pure functions:
folder card lines (arrow, account, target, "upload only", waiting/error states), `skip_side` and skip
chips, header status/detail lines, account lines (storage, nearly full, needs sign-in). The Tk window
uses them; no visible change.

**Acceptance criteria:**
- [x] `gui.py` calls `ui_model` for these texts; the Tk window looks and behaves the same
- [x] Each function has unit tests covering its states (no Tk/Qt needed)

**Verification:**
- [x] Tests pass (new `test_ui_model.py`, 15 tests; 309 in all)
- [x] Build succeeds
- [x] Manual: Tk window Folders/Settings tabs unchanged - old vs new `gui.py` rendered with the same fake
      data (every card state, skips, accounts, syncing header, update line): pixel-identical

**Done 2026-11-23:** `ui_model.py` (folder cards, skip chips, accounts, header, update line; colours as tones).

**Dependencies:** None (can run alongside Task 1)
**Files likely touched:** `ui_model.py`, `gui.py`, `test_ui_model.py`
**Estimated scope:** Small

## Checkpoint A: Foundation
- [x] All tests pass, build succeeds, Tk window unchanged
- [x] Qt shell runs from the built exe (size/start time: see `tasks/plan.md`) - acceptable to you?
- [ ] Review with you before Phase 2

---

## Phase 2: Main window screens (Qt mode)

## Task 4: Header and live status
**Description:** The Qt header: status dot and text, detail with one line per file while syncing,
progress bar, Pause/Resume and Sync now, driven by `state` messages. Theme (QSS light/dark, follows
Windows or the appearance setting) in `qtui/style.py`.

**Acceptance criteria:**
- [x] Shows the same status, detail and progress as the Tk header (same `ui_model.header`; compared with fake
      states - a real sync in Qt mode is still to watch, it needs a signed-in account)
- [x] Pause/Resume and Sync now work; Sync now is disabled while syncing
- [x] Light and dark both readable; follows the appearance setting (and Windows switching light/dark)

**Verification:**
- [x] Tests pass (`test_qtui.py`, 9 tests; 318 in all)
- [x] Build succeeds
- [ ] Manual: run a sync in Qt mode and watch the header (needs your account - do at Checkpoint B)

**Done 2026-11-23:** `qtui/style.py` (themes, shared stylesheet, tones), `qtui/header.py`; checked idle /
syncing / paused / error in light + dark at 680×460 and 1200×800.

**Dependencies:** Tasks 1, 3
**Files likely touched:** `qtui/style.py`, `qtui/main_window.py`, `qtui/header.py`, `test_qtui.py`
**Estimated scope:** Medium

## Task 5: Folders tab
**Description:** Folder cards from `ui_model`: name, path, status line, skip chips with tooltips,
Open / Drive / Remove (with the same confirmation), Start syncing for waiting folders, error states
(folder missing, account removed, needs sign-in). "Add folder" / "Edit" open the Tk-free dialogs later
(Tasks 10–11); until then they're hidden in Qt mode.

**Acceptance criteria:**
- [x] Every card state shown by the Tk window is shown the same way (same `ui_model` texts)
- [x] Remove asks for confirmation and removes only that folder; Start syncing starts it

**Verification:**
- [x] Tests pass (`test_qtui.py` FoldersTabTests, + shared actions in `test_ui_model.py`; 332 in all)
- [x] Build succeeds
- [ ] Manual: compare Folders tab Tk vs Qt with your real folders (Checkpoint B)

**Done 2026-11-23:** `qtui/folders_tab.py`; Drive link, remove and empty texts moved to `ui_model` (Tk uses them too).

**Dependencies:** Task 4
**Files likely touched:** `qtui/folders_tab.py`, `qtui/main_window.py`, `test_qtui.py`
**Estimated scope:** Medium

## Task 6: Activity tab
**Description:** Port the Activity view: All/Changes/Problems, search, day groups, run and event
cards, details with badges (problems first, up to 200 rows), auto-open problem runs, show older,
Clear, Log file; live `activity` and `last_check` messages.

**Acceptance criteria:**
- [x] Same entries, filters and search results as the Tk Activity tab (same `ui_model` rules; real history: Checkpoint B)
- [x] A new sync run appears live; Clear asks first
- [x] Scrolling a 200-file card stays smooth (prototype measured 2.5-4.1 ms per repaint)

**Verification:**
- [x] Tests pass (`test_qtui_activity.py` 8, `test_ui_model_activity.py` 9; 349 in all)
- [x] Build succeeds
- [ ] Manual: compare Tk vs Qt Activity on real data (Checkpoint B)

**Done 2026-11-23:** `qtui/activity_tab.py`, `qtui/widgets.py`; Activity texts/rules moved to `ui_model`
(Tk Activity tab renders pixel-identical before/after).

**Dependencies:** Task 4
**Files likely touched:** `qtui/activity_tab.py`, `qtui/main_window.py`, `test_qtui.py`
**Estimated scope:** Medium

## Task 7: Settings — Google accounts
**Description:** Accounts list (storage, nearly full, needs sign-in), Add Google account with
"Waiting for browser…" + Cancel, Sign in again, Remove (confirmation), browser profile per account,
Import credentials.json (QFileDialog), Setup guide; `account` / `account_added` messages.

**Acceptance criteria:**
- [x] Add, cancel, sign in again and remove behave exactly as in the Tk window (shared `ui_model` rules)
- [x] Importing credentials.json offers to sign in, as today

**Verification:**
- [x] Tests pass (`test_qtui_settings.py` 11, `test_ui_model_accounts.py` 5)
- [x] Build succeeds
- [ ] Manual: add and remove a test Google account in Qt mode (needs a real sign-in: Checkpoint B)

**Done 2026-11-23:** `qtui/settings_tab.py` (accounts section + Add account dialog); profile choices, remove
question and assigning a new account to folders moved to `ui_model` (Tk uses them too).

**Dependencies:** Task 4
**Files likely touched:** `qtui/settings_tab.py`, `qtui/main_window.py`, `test_qtui.py`
**Estimated scope:** Medium

## Task 8: Settings — options, updates, uninstall
**Description:** Sync interval, instant sync, notifications, start with Windows, network priority and
speed limit, metered connections, appearance, updates (check now, update button, download page),
uninstall; saved through `App.save_config` like today.

**Acceptance criteria:**
- [x] Every option saves and reloads the same `config.json` keys as the Tk window (shared `ui_model.options_cfg`)
- [x] Update and uninstall buttons behave as today

**Verification:**
- [x] Tests pass (`test_ui_model_options.py` 5, `OptionsSectionTests` 8)
- [x] Build succeeds
- [ ] Manual: change each option in Qt mode, restart, check it stuck (Checkpoint B)

**Done 2026-11-23:** options, updates, appearance, quit/uninstall in `qtui/settings_tab.py`; `Switch` and
`segments` in `qtui/widgets.py`. Also: the stylesheet is only re-applied when it changes, and Qt tests now
delete their widgets (the Qt tests went from 51.6 s to 3.8 s).

**Dependencies:** Task 7
**Files likely touched:** `qtui/settings_tab.py`, `test_qtui.py`
**Estimated scope:** Small

## Task 9: Space tab
**Description:** Port the Space view on top of the existing `SpaceState`/`space.py`: scan with
progress and Stop, Drive usage, biggest items, open item, delete selected and old versions with the
same confirmations.

**Acceptance criteria:**
- [x] Same numbers and items as the Tk Space tab after a scan (both draw `space_model`)
- [x] Delete actions ask first, delete only what was selected, and report what happened

**Verification:**
- [x] Tests pass (`test_space_model.py` 12 - the first tests of the delete code -, `test_qtui_space.py` 10)
- [x] Build succeeds
- [ ] Manual: scan an account in Qt mode; delete a test file (Checkpoint B)

**Done 2026-11-23:** `space_model.py` (scan / selection / Recycle Bin + Drive trash moves / old versions,
moved unchanged out of `space_view.py`, which now only draws: pixel-identical before/after), `qtui/space_tab.py`.
Also: a Qt colour palette per theme, so check boxes are readable in dark mode.

**Dependencies:** Task 4
**Files likely touched:** `qtui/space_tab.py`, `qtui/main_window.py`, `test_qtui.py`
**Estimated scope:** Medium

## Checkpoint B: Screens
- [ ] Each tab matches the Tk one, light + dark, 680×460 to 1200×800
- [ ] Drag/scroll on your real data: no lag (measure child controls and move cost)
- [ ] Review with you

---

## Phase 3: Dialogs (Qt mode)

## Task 10: Drive folder browser
**Description:** Browse My Drive and shared drives, go up, new folder, select, with loading in the
background (never freezing the window) — the Qt version of `DriveFolderBrowser`.

**Acceptance criteria:**
- [x] Lists the same folders as the Tk browser; creating a folder works
- [x] The window stays responsive while a folder loads (Drive calls on a thread, results via a Qt signal)

**Verification:**
- [x] Tests pass (`test_qtui_dialogs.py` 8, with a fake Drive; one test uses the real thread + signal)
- [x] Build succeeds
- [ ] Manual: pick a Drive folder with a real account (Checkpoint C)

**Done 2026-11-23:** `qtui/drive_browser.py`.

**Dependencies:** Checkpoint B
**Files likely touched:** `qtui/drive_browser.py`, `test_qtui_dialogs.py`
**Estimated scope:** Small

## Task 11: Add / edit folder dialog
**Description:** PC folder (QFileDialog), account, Drive folder (Task 10), mode, skips; validation and
"already synced" checks as today; new folders start waiting for "Start syncing". Shows "Add folder" and
"Edit" in the Qt Folders tab.

**Acceptance criteria:**
- [x] Adding and editing save the same folder entries as the Tk dialog (shared `ui_model.folder_result`)
- [x] Invalid or duplicate folders are refused with the same messages

**Verification:**
- [x] Tests pass (`test_ui_model_folder_dialog.py` 10, `test_qtui_folder_dialog.py` 10; 428 in all)
- [x] Build succeeds
- [ ] Manual: add a test folder in Qt mode, start it, edit it (Checkpoint C)

**Done 2026-11-23:** `qtui/folder_dialog.py`; Add folder / Edit / clickable skip chips in the Qt Folders tab;
the "still being built" note is gone. Save/add/duplicate rules moved to `ui_model` (Tk uses them too).

**Dependencies:** Task 10
**Files likely touched:** `qtui/folder_dialog.py`, `qtui/folders_tab.py`, `test_qtui_dialogs.py`
**Estimated scope:** Medium

## Task 12: Skip picker
**Description:** The Qt skip picker: tree of PC + Drive items with checkboxes, common names and rules,
which side each item is on; returns the same patterns as `skip_picker.py` (its pure functions stay and
are reused).

**Acceptance criteria:**
- [x] Picking items gives the same `exclude` patterns and sides as the Tk picker (same scenarios tested)
- [x] Large folders load without freezing the window (folders read when opened; Drive on a thread)

**Verification:**
- [x] Tests pass (`test_qtui_skip_picker.py` 13 - the Tk picker's scenarios on `SkipModel` + the Qt picker;
      `test_skip_picker.py` unchanged and passing; 442 in all)
- [x] Build succeeds
- [ ] Manual: choose skips for a real folder (Checkpoint C)

**Done 2026-11-23:** `skip_model.py` (picker state/rules + the helpers, now Tk-free), `qtui/skip_picker.py`,
skips "Choose…" in the Qt folder dialog. Also: themed check boxes (`assets/check.png`), readable in dark mode.

**Dependencies:** Task 11
**Files likely touched:** `qtui/skip_picker.py`, `skip_picker.py` (split logic from Tk), `test_skip_picker.py`
**Estimated scope:** Medium

## Checkpoint C: Feature parity
- [ ] Everything the Tk window does works in Qt mode, end to end, with your accounts
- [x] `/security-review` (file deletion in Space, folder removal, sign-in UI) - 2026-11-23: one finding,
      fixed: Qt showed Drive names as HTML (an `<img src="file://host/...">` folder name could leak the
      Windows login hash over SMB). Labels are now plain text, tooltips/message boxes go through
      `widgets.safe()`; `test_qtui_text_safety.py`. Deletion, update, OAuth, `.url` and path code: no issues.
- [ ] Review with you

---

## Phase 4: Switch over

## Task 13: Qt becomes the default
**Description:** The Qt window is used unless `DRIVESYNC_UI=tk`; release notes say how to fall back.

**Acceptance criteria:**
- [x] Fresh start and `--hidden` start use Qt; `DRIVESYNC_UI=tk` still gives the old window (checked end to end
      in a throwaway copy: Qt by default without even loading CustomTkinter; Tk with the switch)
- [ ] Installed update from the previous version keeps settings and starts in Qt (needs Setup on a spare PC/VM)

**Verification:**
- [x] Tests pass; build succeeds
- [ ] Manual: update an installed copy with the new Setup

**Done 2026-11-23:** `ui_host.chosen_ui()` defaults to Qt; README explains the fallback; the new modules
(`qtui/`, `ui_model`, `space_model`, `skip_model`, `ui_host`, `space`) are now scanned by bandit in CI.

**Dependencies:** Checkpoint C
**Files likely touched:** `tray_app.py`, `README.md`
**Estimated scope:** Small

## Task 14: Remove the Tk window
**Description:** Delete `gui.py`'s Tk window, `activity_view.py`, `space_view.py`'s view, the Tk skip
picker, `smooth_scroll.py`, CustomTkinter, Tk-only tests and build steps; update `README.md`, the
project `CLAUDE.md`, and turn `ctk-ui-designer` into a Qt UI designer agent.

**Acceptance criteria:**
- [ ] No `customtkinter`/`tkinter` imports left; dependency removed from `pyproject.toml`/`uv.lock`
- [ ] Docs and the UI designer agent describe the Qt window

**Verification:**
- [ ] Tests pass; build succeeds; exe size noted
- [ ] Manual: full click-through of every tab and dialog

**Dependencies:** Task 13 (after one release with the fallback)
**Files likely touched:** `gui.py`, `activity_view.py`, `space_view.py`, `smooth_scroll.py`, `pyproject.toml` (+ tests, `DriveSync.spec`, `build.ps1`, docs) — split into two commits if over 5 files of real change
**Estimated scope:** Medium

## Task 15 (optional): Tray icon on Qt
**Description:** Replace pystray with `QSystemTrayIcon` (menu, tooltip, sync animation, notifications).

**Acceptance criteria:**
- [ ] Tray menu, tooltip, animation and notifications behave as before
- [ ] pystray removed from dependencies

**Verification:**
- [ ] Tests pass (`test_app_extras.py` animation tests adapted); build succeeds
- [ ] Manual: every tray menu item, pause/resume, notifications

**Dependencies:** Task 14
**Files likely touched:** `tray_app.py`, `test_app_extras.py`, `pyproject.toml`
**Estimated scope:** Medium

## Checkpoint D: Complete
- [ ] All tests pass, exe and installer build, installed update works
- [ ] Ready to release
