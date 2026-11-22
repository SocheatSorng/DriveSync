"""Tests for a full Google Drive or a full PC disk: one clear error, the rest skipped, nothing lost.
Run:  python -m unittest test_storage_full -v
"""
import errno
import tempfile
import unittest
from pathlib import Path

import httplib2
from googleapiclient.errors import HttpError

import tray_app
from downloader import is_disk_full
from drive_sync import DEFAULT_EXCLUDES, FULL_MESSAGES, Syncer, storage_full
from test_drive_sync import FakeDrive
from test_twoway import FakeDrive as FakeDrive2
from twoway import TwoWaySyncer


def http_403(reason: str) -> HttpError:
    body = f'{{"error": {{"errors": [{{"reason": "{reason}"}}], "code": 403}}}}'.encode()
    return HttpError(httplib2.Response({"status": 403}), body)


def disk_full() -> OSError:
    return OSError(errno.ENOSPC, "No space left on device")


class DetectTests(unittest.TestCase):
    def test_drive_full_is_recognised(self):
        self.assertEqual(storage_full(http_403("storageQuotaExceeded")), "drive")

    def test_other_403s_are_not_drive_full(self):
        self.assertIsNone(storage_full(http_403("insufficientFilePermissions")))

    def test_disk_full_is_recognised(self):
        self.assertTrue(is_disk_full(disk_full()))
        self.assertEqual(storage_full(disk_full()), "pc")
        self.assertIsNone(storage_full(OSError(errno.EACCES, "Access is denied")))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "pc"
        (self.root / "sub").mkdir(parents=True)
        for rel in ("a.txt", "b.txt", "sub/c.txt"):
            (self.root / rel).write_text(rel)
        self.events = []

    def on_event(self, kind, rel, msg):
        self.events.append((kind, rel, msg))

    def errors(self):
        return [e for e in self.events if e[0] == "error"]

    def test_upload_engine_reports_drive_full_once_and_stops_trying(self):
        drive, tries = FakeDrive(), []

        def full(*_):
            tries.append(1)
            raise http_403("storageQuotaExceeded")
        drive.upload = full
        stats = Syncer(drive, self.root, "root", excludes=DEFAULT_EXCLUDES, on_event=self.on_event,
                       state_path=Path(self.tmp.name) / "s.json").run()
        self.assertEqual(len(tries), 1)                       # the other two weren't even sent
        self.assertEqual((stats["errors"], stats["skipped"], stats["storage_full"]), (1, 2, ["drive"]))
        self.assertEqual([e[2] for e in self.errors()], [FULL_MESSAGES["drive"]])

    def test_two_way_drive_full_still_downloads(self):
        drive, tries = FakeDrive2(), []
        drive.web_add("", "from-drive.txt", "d")

        def full(*_):
            tries.append(1)
            raise http_403("storageQuotaExceeded")
        drive.upload = full
        stats = TwoWaySyncer(drive, self.root, "root", excludes=DEFAULT_EXCLUDES, on_event=self.on_event,
                             state_path=Path(self.tmp.name) / "s2.json", recycle=lambda x: None).run()
        self.assertEqual(len(tries), 1)
        self.assertEqual((stats["errors"], stats["downloaded"], stats["storage_full"]), (1, 1, ["drive"]))
        self.assertTrue((self.root / "from-drive.txt").exists())
        # Space freed: the next sync uploads everything that was skipped, and deletes nothing.
        del drive.upload
        stats = TwoWaySyncer(drive, self.root, "root", excludes=DEFAULT_EXCLUDES,
                             state_path=Path(self.tmp.name) / "s2.json", recycle=lambda x: None).run()
        self.assertEqual((stats["uploaded"], stats["errors"], stats["deleted_local"]), (3, 0, 0))

    def test_two_way_disk_full_reports_once_and_still_uploads(self):
        drive = FakeDrive2()
        for i in range(5):
            drive.web_add("", f"d{i}.txt", "d")

        def full(*_a, **_k):
            raise disk_full()
        drive.download = full
        stats = TwoWaySyncer(drive, self.root, "root", excludes=DEFAULT_EXCLUDES, on_event=self.on_event,
                             state_path=Path(self.tmp.name) / "s3.json", recycle=lambda x: None).run()
        self.assertEqual((stats["errors"], stats["uploaded"], stats["storage_full"]), (1, 3, ["pc"]))
        self.assertEqual([e[2] for e in self.errors()], [FULL_MESSAGES["pc"]])


class PreCheckTests(unittest.TestCase):
    def test_file_too_big_for_drive_space_left_is_not_sent(self):
        from unittest import mock

        from drive_sync import Drive, NotEnoughSpace
        with tempfile.TemporaryDirectory() as tmp:
            big, small = Path(tmp) / "big.wav", Path(tmp) / "small.txt"
            big.write_bytes(b"x" * 5000)
            small.write_bytes(b"x" * 10)
            d = Drive(mock.Mock())
            d.quota_free = 1000
            with self.assertRaises(NotEnoughSpace):
                d.upload(big, "folder")
            d.s.files().create.assert_not_called()
            d._check_space(small)            # smaller files still go
            self.assertEqual(d.quota_free, 990)

    def test_download_too_big_for_this_pc_is_not_started(self):
        from unittest import mock

        import twoway
        from drive_sync import NotEnoughSpace
        with tempfile.TemporaryDirectory() as tmp:
            s = TwoWaySyncer(FakeDrive2(), Path(tmp), "root", excludes=DEFAULT_EXCLUDES,
                             state_path=Path(tmp) / "s.json", recycle=lambda x: None)
            usage = mock.Mock(free=twoway.DISK_MARGIN + 50 * 1024 * 1024)
            with mock.patch.object(twoway.shutil, "disk_usage", return_value=usage):
                s._check_disk(10 * 1024 * 1024)
                with self.assertRaises(NotEnoughSpace):
                    s._check_disk(100 * 1024 * 1024)


class TrayTests(unittest.TestCase):
    def test_full_is_notified_once_not_on_every_retry(self):
        app = tray_app.App.__new__(tray_app.App)
        app._full_notified = frozenset()
        states, notes = [], []
        app.set_state = lambda *a: states.append(a)
        app.notify = notes.append
        full = {"drive": {"teacher@example.com"}}
        app._report_full(full, "10:00")
        app._report_full(full, "10:30")
        self.assertEqual(len(notes), 1)
        self.assertIn("Google Drive storage is full", states[-1][1])
        app.storage_full = full
        self.assertEqual(app._retry_delay(), tray_app.FULL_RETRY_SECONDS)
        app.storage_full = {}
        self.assertEqual(app._retry_delay(), 120)


if __name__ == "__main__":
    unittest.main()
