"""The top of the Qt window: status dot and text, details (one line per file while several
transfer), progress bar, Pause/Resume and Sync now. Texts come from ui_model.header, as in Tk."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QProgressBar, QPushButton, QWidget

import ui_model
from qtui.style import set_prop


class Header(QWidget):
    def __init__(self, app, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(2)

        self.dot = QLabel()
        self.dot.setFixedSize(12, 12)
        self.dot.setProperty("role", "dot")    # round, coloured by its state (qtui/style.py)
        g.addWidget(self.dot, 0, 0, Qt.AlignmentFlag.AlignVCenter)

        self.status_lbl = QLabel()
        self.status_lbl.setTextFormat(Qt.TextFormat.PlainText)   # never HTML (see qtui/widgets.py)
        self.status_lbl.setProperty("role", "title")
        g.addWidget(self.status_lbl, 0, 1)

        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setFixedWidth(96)
        self.pause_btn.clicked.connect(lambda: app.toggle_pause())
        self.sync_btn = QPushButton("Sync now")
        self.sync_btn.setProperty("role", "primary")
        self.sync_btn.setFixedWidth(110)
        self.sync_btn.clicked.connect(lambda: app.sync_now())
        for b in (self.pause_btn, self.sync_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        g.addWidget(self.pause_btn, 0, 2)
        g.addWidget(self.sync_btn, 0, 3)

        # Full width under the buttons, wrapping (long file names, speed, time left).
        self.detail_lbl = QLabel()
        self.detail_lbl.setTextFormat(Qt.TextFormat.PlainText)   # file names while syncing: never HTML
        self.detail_lbl.setProperty("role", "muted")
        self.detail_lbl.setWordWrap(True)
        self.detail_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        g.addWidget(self.detail_lbl, 1, 1, 1, 3)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.hide()
        g.addWidget(self.bar, 2, 1, 1, 3)
        g.setColumnStretch(1, 1)

    def refresh(self) -> None:
        h = ui_model.header(self.app)
        set_prop(self.dot, "state", h.dot)
        self.dot.setAccessibleName(f"Status: {h.status}")
        self.status_lbl.setText(h.status)
        self.detail_lbl.setText(h.detail)
        self.pause_btn.setText(h.pause_text)
        self.sync_btn.setEnabled(h.sync_enabled)
        if h.progress is None:
            self.bar.hide()
        else:
            self.bar.setValue(round(h.progress * 1000))
            self.bar.show()
