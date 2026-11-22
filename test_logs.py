"""Tests for the log file: nothing goes missing.
Run:  python -m unittest test_logs -v
"""
import logging
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import logs
from drive_sync import DEFAULT_EXCLUDES, Syncer
from test_drive_sync import FakeDrive


class LogTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "logs" / "drive_sync.log"
        root = logging.getLogger()
        self.saved = (root.handlers[:], root.level, threading.excepthook)
        self.handler = logs.setup(self.path)

    def tearDown(self):
        root = logging.getLogger()
        self.handler.close()
        root.handlers[:], level, threading.excepthook = self.saved
        root.setLevel(level)
        self.tmp.cleanup()

    def text(self):
        self.handler.flush()
        return self.path.read_text(encoding="utf-8")


class RotationTests(LogTestCase):
    def test_failed_rotation_keeps_logging(self):
        self.handler.maxBytes = 200
        with mock.patch("os.rename", side_effect=PermissionError("in use")), \
                mock.patch.object(logs.os, "replace", side_effect=PermissionError("in use"), create=True):
            for i in range(20):
                logging.getLogger("drive_sync").info("line %d %s", i, "x" * 30)
        self.assertIn("line 19", self.text())        # nothing lost while the file couldn't be renamed

    def test_rotation_works_normally(self):
        self.handler.maxBytes = 200
        for i in range(20):
            logging.getLogger("drive_sync").info("line %d %s", i, "x" * 30)
        self.assertTrue(Path(str(self.path) + ".1").exists())


class CrashHookTests(LogTestCase):
    def test_background_thread_error_is_logged(self):
        logs.install_crash_hooks()

        def boom():
            raise RuntimeError("worker blew up")
        t = threading.Thread(target=boom, name="worker-test")
        t.start()
        t.join()
        self.assertIn("worker blew up", self.text())
        self.assertIn("worker-test", self.text())

    def test_unclean_stop_is_noted(self):
        marker = logs.RunMarker(self.path.parent / "running")
        marker.start()
        marker.stop()
        logs.RunMarker(marker.path).start()
        self.assertNotIn("without closing normally", self.text())
        logs.RunMarker(marker.path).start()          # the last run never called stop()
        self.assertIn("without closing normally", self.text())


class SyncLoggingTests(LogTestCase):
    def test_every_upload_is_logged(self):
        root = Path(self.tmp.name) / "pc"
        (root / "sub").mkdir(parents=True)
        (root / "a.txt").write_text("a")
        (root / "sub" / "b.txt").write_text("b")
        Syncer(FakeDrive(), root, "root", excludes=DEFAULT_EXCLUDES, state_path=Path(self.tmp.name) / "s.json").run()
        text = self.text()
        self.assertIn("Uploaded        a.txt", text)
        self.assertIn("Uploaded        sub/b.txt", text)
        self.assertIn("Folder          sub", text)


if __name__ == "__main__":
    unittest.main()
