"""Choose a Google Drive folder (Qt): My Drive, Shared with me, Shared drives, and "+ New folder".

Drive calls run on a background thread, one at a time (the Google client isn't thread-safe); the
result comes back to the window's thread through a Qt signal, so the window never freezes.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QInputDialog, QVBoxLayout, QWidget

from qtui.widgets import button, frame, label, scroll


class _Results(QObject):
    done = Signal(object, object)    # (on_done, result or exception) - delivered on the window's thread


class DriveBrowser(QDialog):
    """self.result = (folder_id, path) when a folder was chosen, else None."""

    ROOTS = "__roots__"
    SHARED = "__shared__"
    DRIVES = "__drives__"
    VIRTUAL = (ROOTS, SHARED, DRIVES)

    def __init__(self, parent: QWidget | None, app, email: str, run_now: bool = False):
        super().__init__(parent)
        self.app, self.email = app, email
        self.result = None
        self.drive = None                    # made on the background thread
        self.lock = threading.Lock()
        self.run_now = run_now               # (tests: run Drive calls straight away)
        self._results = _Results()
        self._results.done.connect(lambda on_done, out: on_done(out))
        # Where we are: (folder id, name, can add files here)
        self.stack: list[tuple[str, str, bool]] = [(self.ROOTS, "Google Drive", False), ("root", "My Drive", True)]

        self.setWindowTitle("Choose a Google Drive folder")
        self.resize(560, 520)
        self.setMinimumSize(460, 400)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        top = QHBoxLayout()
        self.up_btn = button("← Back", self.go_up)
        top.addWidget(self.up_btn)
        self.path_lbl = label("", "heading")
        top.addWidget(self.path_lbl, 1)
        lay.addLayout(top)
        lay.addWidget(label(email, "muted"))
        holder = frame("panel")
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(4, 4, 4, 4)
        self.area = scroll(QWidget())
        hl.addWidget(self.area)
        lay.addWidget(holder, 1)
        self.note = label("", "muted")
        lay.addWidget(self.note)
        bottom = QHBoxLayout()
        self.new_btn = button("+ New folder", self.new_folder)
        bottom.addWidget(self.new_btn)
        bottom.addStretch(1)
        self.select_btn = button("Use this folder", self.select, "primary")
        bottom.addWidget(self.select_btn)
        bottom.addWidget(button("Cancel", self.reject))
        lay.addLayout(bottom)
        self.load()

    @classmethod
    def ask(cls, parent: QWidget | None, app, email: str):
        """(folder_id, path) of the chosen folder, or None."""
        d = cls(parent, app, email)
        d.exec()
        return d.result

    # ------------------------------------------------------------- background work
    def _run(self, fn, on_done) -> None:
        """fn(drive) off the window's thread; on_done(result or exception) back on it."""
        def work():
            try:
                with self.lock:
                    if self.drive is None:
                        self.drive = self.app.drive_for_browsing(self.email)
                    out = fn(self.drive)
            except Exception as e:  # noqa: BLE001 - shown in the dialog
                out = e
            if self.run_now:
                on_done(out)
            else:
                self._results.done.emit(on_done, out)
        if self.run_now:
            work()
        else:
            threading.Thread(target=work, daemon=True, name="drive-browse").start()

    # ------------------------------------------------------------- navigation
    @property
    def here(self) -> tuple[str, str, bool]:
        return self.stack[-1]

    def path_text(self) -> str:
        return "/".join(name for _, name, _ in self.stack[1:]) or "Google Drive"

    def load(self) -> None:
        folder_id, _, can_add = self.here
        virtual = folder_id in self.VIRTUAL
        self.path_lbl.setText(self.path_text())
        self.up_btn.setEnabled(len(self.stack) > 1)
        self.select_btn.setEnabled(not virtual and can_add)
        self.new_btn.setEnabled(not virtual and can_add)
        self.note.setText("" if virtual or can_add else "View-only folder - you can't add files here.")
        self._message("Loading…")
        if folder_id == self.ROOTS:
            self.show_folders([("root", "My Drive", True), (self.SHARED, "Shared with me", False),
                               (self.DRIVES, "Shared drives", False)])
            return
        if folder_id == self.SHARED:
            def fn(d):
                return [(f["id"], f["name"], f.get("capabilities", {}).get("canAddChildren", False))
                        for f in d.list_shared_with_me()]
        elif folder_id == self.DRIVES:
            def fn(d):
                return [(x["id"], x["name"], True) for x in d.list_shared_drives()]
        else:
            def fn(d):
                return [(f["id"], f["name"], f.get("capabilities", {}).get("canAddChildren", True))
                        for f in d.list_subfolders(folder_id)]
        expected = len(self.stack)
        self._run(fn, lambda out: self.show_folders(out) if len(self.stack) == expected else None)

    def _fill(self, widgets) -> None:
        inner = QWidget()
        self.list_box = inner
        v = QVBoxLayout(inner)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(1)
        for w in widgets:
            v.addWidget(w)
        v.addStretch(1)
        self.area.setWidget(inner)

    def _message(self, text: str, error: bool = False) -> None:
        m = label(text, "error" if error else "muted", wrap=True)
        m.setAlignment(Qt.AlignmentFlag.AlignCenter)
        m.setContentsMargins(12, 24, 12, 24)
        self._fill([m])

    def show_folders(self, out) -> None:
        if isinstance(out, Exception):
            from accounts import NeedsSignIn
            if isinstance(out, NeedsSignIn):
                self.app.needs_signin.add(self.email)
                self.app.ui.put(("account",))
                text = (f"{out}.\n\nClose this window, go to Settings → Google accounts and click "
                        "“Sign in again”, then try again.")
            else:
                text = f"Couldn't load this folder:\n{out}"
            self._message(text, error=True)
            return
        if not out:
            self._message("No folders here." + ("" if self.here[0] in self.VIRTUAL
                                                else " Click “Use this folder”, or create one with “+ New folder”."))
            return
        rows = []
        for fid, name, can_add in out:
            b = button(f"📁  {name}", lambda f=(fid, name, can_add): self.open(f), "row")
            rows.append(b)
        self._fill(rows)

    def open(self, folder: tuple[str, str, bool]) -> None:
        self.stack.append(folder)
        self.load()

    def go_up(self) -> None:
        if len(self.stack) > 1:
            self.stack.pop()
            self.load()

    def new_folder(self) -> None:
        name, ok = QInputDialog.getText(self, "New folder", "Name of the new folder:")
        name = (name or "").strip()
        if not ok or not name:
            return
        parent_id = self.here[0]
        self._message("Creating folder…")

        def done(out):
            if isinstance(out, Exception):
                self._message(f"Couldn't create the folder:\n{out}", error=True)
            else:
                self.open((out, name, True))
        self._run(lambda d: d.create_folder(name, parent_id), done)

    def select(self) -> None:
        self.result = (self.here[0], self.path_text())
        self.accept()
