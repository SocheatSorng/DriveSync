# Implementation Plan: Move Drive Sync's window from CustomTkinter to Qt (PySide6)

Source: the prototype `prototypes/qt_main_window.py` (fake data) and its measurements.

## Overview

Rebuild the Drive Sync window in PySide6, screen by screen, behind a switch, so the app keeps working
after every task. CustomTkinter makes every label and button a separate Windows control (the real
window had ~3,000), which made dragging and scrolling lag. Qt draws everything inside one Windows
window: the prototype had 0 child controls and ~0.5 ms per move step (vs 2.2–8.9 ms today), and
full repaints of the busiest list in 2.5–4.1 ms with no smearing. The sync engine (`drive_sync.py`,
`twoway.py`, `accounts.py`, `activity.py`, `space.py`, …) does not change.

## Architecture Decisions

- **Strangler, not big-bang.** A `ui` switch (`DRIVESYNC_UI=qt`, later a config value) picks the Tk or
  the Qt window at start-up. Tk stays the default until the Qt window does everything (Task 13), so
  every task ships a working app. Tk and Qt never run in the same process at the same time.
- **Keep the App ↔ window contract.** The window still reads `App` attributes and receives the same
  queue messages (`state`, `activity`, `last_check`, `account`, `account_added`, `space`,
  `space_progress`, `update`, `error`, `show`, `quit`), polled by a `QTimer` instead of `root.after`.
  No changes to how the worker threads talk to the UI.
- **A small UI host seam in `tray_app.py`.** What is Tk-specific there today (creating `ctk.CTk()`,
  appearance mode, `report_callback_exception`, `apply_window_icon`, `after`/`mainloop`/`destroy`)
  moves behind a tiny host object with a Tk and a Qt version.
- **Shared, testable view logic.** Text and decisions that both windows need (folder card lines, skip
  sides, status lines, account lines) move out of `gui.py` into `ui_model.py` with unit tests, so
  both windows show the same thing and the logic is tested once.
- **Keep pystray for the tray icon** for now: it doesn't depend on Tk, and its animation already
  works. Moving to `QSystemTrayIcon` is optional, last (Task 15).
- **New code lives in `qtui/`** (`style.py`, `main_window.py`, one module per tab/dialog), styled by
  one QSS stylesheet with light/dark palettes, as in the prototype.
- **Dependencies:** add `pyside6` to `pyproject.toml` (`uv lock`, `requirements.txt` re-exported);
  remove `customtkinter` only in Task 14. PyInstaller excludes Qt modules the app doesn't use
  (WebEngine, QML, Multimedia, …) to keep the exe small.

## Task List

Full tasks with acceptance criteria are in `tasks/todo.md`.

### Phase 1: Foundation (riskiest first)
- [x] Task 1: Qt window shell behind the `ui` switch (tray, show/hide, quit, errors to the log)
- [x] Task 2: Packaging spike: build the exe with PySide6; size, start time, installer, LGPL
- [x] Task 3: Shared view logic in `ui_model.py` (used by the Tk window first)

### Checkpoint A: Foundation
- [x] All tests pass, `build.ps1` succeeds, Tk window unchanged
- [x] Qt shell starts from the built exe; size/start time acceptable
- [ ] Review with you before Phase 2

### Phase 2: Main window screens (Qt mode)
- [x] Task 4: Header + live status (dot, status, detail, file lines, progress, Pause/Sync now)
- [x] Task 5: Folders tab (cards, Open/Drive/Remove, Start syncing, waiting state)
- [x] Task 6: Activity tab (filters, search, details, show older, clear, log file)
- [x] Task 7: Settings: Google accounts section (add/cancel/remove/sign in again, browser profile, import credentials)
- [x] Task 8: Settings: sync, network, appearance, startup, updates, uninstall
- [x] Task 9: Space tab (scan, progress, biggest items, delete with confirmation)

### Checkpoint B: Screens
- [ ] Each tab matches the Tk one (same information and actions), light + dark, 680×460 to 1200×800
- [ ] Drag/scroll measured on your real data: no lag
- [ ] Review with you

### Phase 3: Dialogs (Qt mode)
- [x] Task 10: Drive folder browser dialog
- [x] Task 11: Add / edit folder dialog (uses Task 10)
- [x] Task 12: Skip picker dialog

### Checkpoint C: Feature parity
- [ ] Everything the Tk window can do works in Qt mode, end to end, with your accounts
- [ ] `/security-review` (the Space tab and folder removal delete files; sign-in UI changed)
- [ ] Review with you

### Phase 4: Switch over
- [x] Task 13: Qt becomes the default; Tk kept as a fallback for one release
- [ ] Task 14: Remove the Tk window, CustomTkinter and Tk-only code/tests; update docs and the UI designer agent
- [ ] Task 15 (optional): Tray icon on `QSystemTrayIcon` instead of pystray

### Checkpoint D: Complete
- [ ] All tests pass, exe and installer build, installed update works
- [ ] Ready to release

## Packaging results (Task 2, 2026-11-23)

| Build | Size | Start (sandbox, until the app is loaded) | Window shown (Qt mode) |
|-------|------|-----------------------------------------|------------------------|
| Before Qt (one file) | 44.0 MB | - | - |
| Task 1 (one file, PySide6 untrimmed) | 74.1 MB | - | - |
| One file, trimmed (portable `DriveSync.exe`) | 43.7 MB | 3.5–5.2 s | 3.19 s |
| One folder, trimmed (installed by Setup) | 97.1 MB on disk, 1,043 files | 0.7–1.0 s | 0.85 s |
| `DriveSync-Setup.exe` (one folder inside) | 33.1 MB | | |

Trimmed: unused Qt modules and plugins, Qt translations, `opengl32sw.dll` (software OpenGL), and every
Google API discovery document except Drive v3. Tk window from the one-folder build: shown after 2.92 s.

**Decision (you):** Setup installs the one-folder build (fast start; Qt's DLLs stay replaceable, as the
LGPL asks); releases keep the portable single `DriveSync.exe`. Licence texts ship in `licenses/`.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| pystray and Qt's message loop clash (tray menu, animation) | High | Task 1 proves the tray works under Qt first; fall back to `QSystemTrayIcon` (Task 15) if not |
| Exe grows a lot (Qt DLLs) / starts slower | Med | Task 2 measures before any screen is built; exclude unused Qt modules |
| LGPL: a one-file exe makes Qt DLLs hard to replace | Med | Task 2 decides: ship a one-folder build inside the installer, plus licence notices |
| Behaviour drift between the Tk and Qt windows | Med | Shared `ui_model.py` + tests; tab-by-tab comparison at Checkpoint B |
| Threading: UI updated from worker threads | High | Keep the queue + timer contract; no Qt widget touched off the main thread |
| Deleting files from the Space tab / removing folders | High | Same confirmation texts and code paths as today; `/security-review` at Checkpoint C |
| Two UIs to maintain during the move | Low | Keep the window short-lived: new features wait or go into `ui_model.py` |

## Open Questions

- ~~One-file or one-folder?~~ Decided in Task 2: one folder in Setup, plus the portable single exe.
- Keep the Windows 11 "Fluent" look as in the prototype, or follow Windows' native style more closely?
- After the switch, keep pystray or move the tray to Qt (Task 15)?
