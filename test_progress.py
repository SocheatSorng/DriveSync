"""Tests for sync progress: percentage, current file, file counter and engine hooks.
Run:  python -m unittest test_progress -v
"""
import tempfile
import unittest
from pathlib import Path

from drive_sync import DEFAULT_EXCLUDES, Syncer
from progress import Progress, count_files, human_size
from test_drive_sync import FakeDrive
from test_twoway import FakeDrive as FakeDrive2
from twoway import TwoWaySyncer


class ProgressTests(unittest.TestCase):
    def test_percent_counter_and_current_file(self):
        p = Progress()
        p.add_total(10)
        p.begin_folder("D:/Lessons", 10)
        self.assertEqual((p.percent, p.counter(), p.current()), (0, "file 1 of 10", "Checking files"))
        for _ in range(4):
            p.file_done()
        p.transfer("upload", "Term 1/video.mp4", 40 * 1024 * 1024)
        p.transfer_fraction(0.5)
        self.assertEqual(p.percent, 45)                       # (4 + 0.5) / 10
        self.assertEqual(p.current(), "Uploading Term 1/video.mp4 (50% of 40.0 MB)")
        self.assertEqual(p.counter(), "file 5 of 10")
        p.transfer_end()
        p.file_done()
        self.assertEqual(p.percent, 50)

    def test_percentage_is_by_size_so_a_big_file_counts_properly(self):
        mb = 1024 * 1024
        p = Progress()
        p.add_total(31, 48 * mb + 30 * 1024)                 # one 48 MB video + 30 small documents
        p.transfer("upload", "Sports day video.mp4", 48 * mb)
        p.transfer_fraction(0.83)
        self.assertEqual(p.percent, 82)                       # by file count this would say 2%
        p.transfer_end()
        p.file_done(48 * mb)
        for _ in range(30):
            p.file_done(1024)
        self.assertEqual(p.percent, 99)

    def test_never_goes_backwards_or_shows_100_before_the_end(self):
        p = Progress()
        p.add_total(2)
        p.file_done()
        self.assertEqual(p.percent, 50)
        p.add_total(2)                                        # two-way found more files on Drive
        self.assertEqual(p.percent, 50)                       # not 25
        p.file_done()
        p.file_done()
        p.file_done()
        self.assertEqual(p.percent, 99)                       # 100% is shown as "Up to date"

    def test_speed_and_time_left_on_a_slow_connection(self):
        t = [0.0]
        p = Progress(clock=lambda: t[0])
        p.transfer("download", "Brave Browser/User Data.zip", 120 * 1024 * 1024)
        self.assertEqual(p.current(), "Downloading Brave Browser/User Data.zip (0% of 120.0 MB)")
        t[0] = 70.0
        p.transfer_fraction(1 / 120)                          # 1 MB in 70 s (~14.6 KB/s)
        self.assertEqual(p.current(), "Downloading Brave Browser/User Data.zip "
                                      "(0% of 120.0 MB · 14.6 KB/s · about 2.3 h left)")

    def test_each_file_gets_its_own_line_when_several_download_at_once(self):
        t = [0.0]
        mb = 1024 * 1024
        p = Progress(clock=lambda: t[0])
        p.transfer("download", "Application/OllamaSetup.exe", 1500 * mb)
        self.assertEqual(p.file_lines(), [])                  # one file: the main line already names it
        p.transfer("download", "Application/td-setup-win-x64-7.2.9.exe", 80 * mb)
        p.transfer("download", "Docs/notes.txt", 2048)
        t[0] = 10.0
        p.transfer_bytes("Application/OllamaSetup.exe", 675 * mb)
        p.transfer_bytes("Application/td-setup-win-x64-7.2.9.exe", 20 * mb)
        self.assertEqual(p.current(), "Downloading 3 files at once (69.5 MB/s)")
        self.assertEqual(p.file_lines(), [
            "↓ Application/OllamaSetup.exe - 45% of 1.5 GB · 67.5 MB/s",
            "↓ Application/td-setup-win-x64-7.2.9.exe - 25% of 80.0 MB · 2.0 MB/s",
            "↓ Docs/notes.txt - 2.0 KB",
        ])
        self.assertEqual(p.file_lines(full_path=False)[0], "↓ OllamaSetup.exe - 45% of 1.5 GB · 67.5 MB/s")
        p.transfer_end("Docs/notes.txt")
        p.transfer_end("Application/td-setup-win-x64-7.2.9.exe")
        self.assertEqual(p.file_lines(), [])                  # back to one file

    def test_small_files_show_size_without_percent(self):
        p = Progress()
        p.transfer("download", "notes.txt", 2048)
        self.assertEqual(p.current(), "Downloading notes.txt (2.0 KB)")
        self.assertEqual(human_size(5 * 1024 ** 3), "5.0 GB")

    def test_long_paths_are_shortened_from_the_left(self):
        p = Progress()
        p.transfer("upload", "a/" * 50 + "final.docx", 0)
        self.assertTrue(p.current(40).endswith("final.docx"))
        self.assertLessEqual(len(p.current(40)), len("Uploading ") + 40)

    def test_updates_are_throttled(self):
        t = [0.0]
        seen = []
        p = Progress(on_change=lambda _: seen.append(1), min_interval=0.3, clock=lambda: t[0])
        p.add_total(1000)
        for _ in range(1000):                                 # 1000 unchanged files in 0.1 s
            t[0] += 0.0001
            p.file_done()
        self.assertLessEqual(len(seen), 2)                    # not 1000 UI updates
        before = len(seen)
        p.transfer("upload", "x", 1)                          # a new transfer always shows at once
        self.assertEqual(len(seen), before + 1)

    def test_end_folder_counts_skipped_files(self):
        p = Progress()
        p.add_total(10)
        p.begin_folder("A", 10)
        p.file_done()
        p.end_folder()                                        # e.g. a whole folder went to the Recycle Bin
        self.assertEqual(p.done, 10)


class EngineHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "pc"
        (self.root / "sub").mkdir(parents=True)
        for rel in ("a.txt", "b.txt", "sub/c.txt", "~$lock.docx", "Thumbs.db"):
            (self.root / rel).write_text(rel)

    def tearDown(self):
        self.tmp.cleanup()

    def test_count_files_uses_the_same_exclusions(self):
        n, size = count_files(str(self.root), DEFAULT_EXCLUDES)
        self.assertEqual((n, size), (3, len("a.txt") + len("b.txt") + len("sub/c.txt")))

    def test_upload_engine_reports_every_file_and_transfer(self):
        p = Progress()
        p.add_total(3)
        transfers = []
        orig = p.transfer
        p.transfer = lambda a, path, size: (transfers.append((a, path)), orig(a, path, size))
        Syncer(FakeDrive(), self.root, "root", excludes=DEFAULT_EXCLUDES, progress=p,
               state_path=Path(self.tmp.name) / "s.json").run()
        self.assertEqual(p.done, 3)
        self.assertEqual(sorted(transfers), [("upload", "a.txt"), ("upload", "b.txt"), ("upload", "sub/c.txt")])
        self.assertIsNone(p.action)                           # nothing left "in progress"

    def test_two_way_counts_files_only_on_drive(self):
        drive = FakeDrive2()
        drive.web_add("", "from-drive.txt", "d")
        p = Progress()
        p.add_total(3)
        TwoWaySyncer(drive, self.root, "root", excludes=DEFAULT_EXCLUDES, progress=p,
                     state_path=Path(self.tmp.name) / "s2.json", recycle=lambda x: None).run()
        self.assertEqual((p.total, p.done), (4, 4))


if __name__ == "__main__":
    unittest.main()
