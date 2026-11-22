"""Tests for fast downloads: streaming, resuming, checksum checks, parallel two-way downloads.
Run:  python -m unittest test_downloader -v
"""
import hashlib
import tempfile
import threading
import time
import unittest
from pathlib import Path

import requests

import downloader
from drive_sync import DEFAULT_EXCLUDES
from progress import Progress
from test_twoway import FakeDrive
from twoway import TwoWaySyncer

DATA = bytes(range(256)) * 4096          # 1 MB of test content
MD5 = hashlib.md5(DATA).hexdigest()


class FakeResponse:
    def __init__(self, status, body=b"", fail_after=None):
        self.status_code, self.body, self.fail_after, self.text = status, body, fail_after, ""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, n):
        sent = 0
        for i in range(0, len(self.body), n):
            if self.fail_after is not None and sent >= self.fail_after:
                raise requests.exceptions.ChunkedEncodingError("connection broken")
            yield self.body[i:i + n]
            sent += n


class FakeSession:
    """Serves DATA; `script` is a list of behaviours for successive requests."""

    def __init__(self, script):
        self.script, self.ranges = list(script), []

    def get(self, url, headers=None, stream=False, timeout=None):
        assert timeout, "every request must have a timeout"
        start, end = 0, None
        if headers:
            a, _, b = headers["Range"].split("=")[1].partition("-")
            start, end = int(a), (int(b) + 1 if b else None)
        self.ranges.append(start)
        kind = self.script.pop(0) if self.script else "ok"
        if kind == "drop":                            # connection breaks after 256 KB
            return FakeResponse(206 if start else 200, DATA[start:], fail_after=256 * 1024)
        if kind == "503":
            return FakeResponse(503)
        if kind == "ignore-range":
            return FakeResponse(200, DATA)
        if kind == "corrupt":
            return FakeResponse(206 if start else 200, b"x" * (len(DATA) - start))
        if kind == "timeout":
            raise requests.Timeout("no answer")
        if kind == "fail":
            raise requests.ConnectionError("down")
        return FakeResponse(206 if headers else 200, DATA[start:end])


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmp.name) / "video.mp4"

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, script, **kw):
        s = FakeSession(script)
        downloader.download(None, "fid", self.dest, prefix="~drivesync-", expected_md5=MD5, session=s,
                            sleep=lambda _: None, **kw)
        return s

    def test_streams_in_one_request_and_reports_progress(self):
        seen = []
        s = self.get([], on_bytes=seen.append)
        self.assertEqual(self.dest.read_bytes(), DATA)
        self.assertEqual(s.ranges, [0])                       # one request for the whole file
        self.assertEqual(len(seen), 4)                        # progress every 256 KB
        self.assertFalse(downloader.part_path(self.dest, "~drivesync-").exists())

    def test_broken_connection_continues_where_it_stopped(self):
        s = self.get(["drop"])
        self.assertEqual(self.dest.read_bytes(), DATA)
        self.assertEqual(s.ranges, [0, 256 * 1024])           # second request starts at 256 KB

    def test_resumes_a_download_left_over_from_a_restart(self):
        downloader.part_path(self.dest, "~drivesync-").write_bytes(DATA[:600_000])
        s = self.get([])
        self.assertEqual(s.ranges, [600_000])
        self.assertEqual(self.dest.read_bytes(), DATA)

    def test_server_errors_and_timeouts_are_retried(self):
        s = self.get(["503", "timeout"])
        self.assertEqual(len(s.ranges), 3)                    # 503, timeout, then success
        self.assertEqual(self.dest.read_bytes(), DATA)

    def test_server_ignoring_resume_starts_over_cleanly(self):
        downloader.part_path(self.dest, "~drivesync-").write_bytes(DATA[:500_000])
        self.get(["ignore-range"])
        self.assertEqual(self.dest.read_bytes(), DATA)

    def test_wrong_checksum_is_never_used(self):
        self.dest.write_bytes(b"my original file")
        with self.assertRaises(downloader.DownloadError):
            self.get(["corrupt", "corrupt"])
        self.assertEqual(self.dest.read_bytes(), b"my original file")   # untouched
        self.get(["corrupt"])                                            # one bad try, then fine
        self.assertEqual(self.dest.read_bytes(), DATA)

    def test_gives_up_after_repeated_failures(self):
        with self.assertRaises(downloader.DownloadError):
            self.get(["timeout"] * 10, retries=3)


class SplitDownloadTests(unittest.TestCase):
    """'High priority': one big file in several parts at once."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmp.name) / "OllamaSetup.exe"
        self._min = downloader.SPLIT_MIN
        downloader.SPLIT_MIN = 1024          # treat the 1 MB test file as "big"

    def tearDown(self):
        downloader.SPLIT_MIN = self._min
        self.tmp.cleanup()

    def get(self, session, **kw):
        downloader.download(None, "fid", self.dest, prefix="~drivesync-", expected_md5=MD5, size=len(DATA),
                            session=session, sleep=lambda _: None, connections=4, **kw)

    def test_downloads_in_four_parts_and_reassembles_exactly(self):
        s = FakeSession([])
        seen = []
        self.get(s, on_bytes=seen.append)
        self.assertEqual(self.dest.read_bytes(), DATA)
        self.assertEqual(sorted(s.ranges), [0, 262144, 524288, 786432])   # 4 parts, one request each
        self.assertEqual(max(seen), len(DATA))
        self.assertEqual(list(Path(self.tmp.name).glob("~drivesync-*")), [])  # no leftovers

    def test_interrupted_split_download_continues_only_the_missing_parts(self):
        class FailingSession(FakeSession):
            def get(self, url, headers=None, **kw):
                if headers["Range"].startswith("bytes=786432"):
                    raise requests.ConnectionError("down")      # the last part keeps failing
                return super().get(url, headers=headers, **kw)
        with self.assertRaises(downloader.DownloadError):
            self.get(FailingSession([]), retries=1)
        record = Path(self.tmp.name) / "~drivesync-OllamaSetup.exe.part.json"
        self.assertTrue(record.exists())                       # progress of every part was saved
        self.assertFalse(self.dest.exists())                   # nothing half-done was used
        import json
        saved = json.loads(record.read_text())["segments"]
        missing_from = sorted(a + got for a, b, got in saved if a + got < b)
        self.assertIn(786432, missing_from)                    # the failing part is unfinished
        s = FakeSession([])
        self.get(s)                                            # e.g. after Drive Sync restarts
        self.assertEqual(sorted(s.ranges), missing_from)       # each unfinished part resumes where it stopped
        self.assertEqual(self.dest.read_bytes(), DATA)
        self.assertFalse(record.exists())

    def test_continues_an_earlier_single_stream_download_in_parts(self):
        downloader.part_path(self.dest, "~drivesync-").write_bytes(DATA[:500_000])
        s = FakeSession([])
        self.get(s)
        self.assertTrue(all(r >= 500_000 for r in s.ranges))   # the first 500 KB isn't fetched again
        self.assertEqual(self.dest.read_bytes(), DATA)

    def test_server_without_range_support_falls_back_to_one_stream(self):
        self.get(FakeSession(["ignore-range"] * 4))
        self.assertEqual(self.dest.read_bytes(), DATA)

    def test_free_connections_take_over_a_slow_part(self):
        """IDM-style: when 3 parts finish and 1 is slow, the free connections split what's left of it."""
        class SlowFirstPart(FakeSession):
            def get(self, url, headers=None, **kw):
                r = super().get(url, headers=headers, **kw)
                if headers and headers["Range"].startswith("bytes=0-"):   # the first part crawls
                    inner = r.iter_content

                    def slow(n):
                        for c in inner(n):
                            time.sleep(0.05)
                            yield c
                    r.iter_content = slow
                return r

        old = (downloader.PIECE, downloader.RESPLIT_MIN, downloader.RESPLIT_GAIN, downloader.SPEED_WINDOW)
        downloader.PIECE, downloader.RESPLIT_MIN = 16 * 1024, 32 * 1024
        downloader.RESPLIT_GAIN, downloader.SPEED_WINDOW = 0, 0.05
        try:
            s = SlowFirstPart([])
            t = time.monotonic()
            self.get(s)
            elapsed = time.monotonic() - t
        finally:
            downloader.PIECE, downloader.RESPLIT_MIN, downloader.RESPLIT_GAIN, downloader.SPEED_WINDOW = old
        self.assertEqual(self.dest.read_bytes(), DATA)                 # reassembled exactly
        self.assertGreater(len(s.ranges), 4)                           # free connections took over
        self.assertTrue(any(0 < r < 262144 for r in s.ranges))         # ...inside the slow first part
        self.assertLess(elapsed, 0.5)          # alone, the slow part would take 16 x 0.05 s = 0.8 s
        self.assertEqual(list(Path(self.tmp.name).glob("~drivesync-*")), [])

    def test_no_resplit_when_new_requests_wait_longer_than_it_saves(self):
        """Measured on Google Drive: each request waits 6-19 s for its first byte, so splitting a
        part that will finish soon only adds a wait. With slow-starting requests: no take-overs."""
        class SlowToStart(FakeSession):
            def get(self, url, headers=None, **kw):
                time.sleep(0.3)                                # the "first byte" wait
                r = super().get(url, headers=headers, **kw)
                if headers and headers["Range"].startswith("bytes=0-"):
                    inner = r.iter_content

                    def slow(n):
                        for c in inner(n):
                            time.sleep(0.02)                   # first part: ~0.3 s more than the others
                            yield c
                    r.iter_content = slow
                return r

        old = (downloader.PIECE, downloader.RESPLIT_MIN, downloader.RESPLIT_GAIN, downloader.SPEED_WINDOW)
        downloader.PIECE, downloader.RESPLIT_MIN, downloader.RESPLIT_GAIN, downloader.SPEED_WINDOW = \
            16 * 1024, 32 * 1024, 0, 0.05
        try:
            s = SlowToStart([])
            self.get(s)
        finally:
            downloader.PIECE, downloader.RESPLIT_MIN, downloader.RESPLIT_GAIN, downloader.SPEED_WINDOW = old
        self.assertEqual(self.dest.read_bytes(), DATA)
        self.assertEqual(len(s.ranges), 4)                     # splitting wouldn't pay: it didn't happen

    def test_resplitting_never_corrupts_the_file(self):
        """Stress: random slowness everywhere, many take-overs - the bytes must always be exact."""
        import random

        class RandomlySlow(FakeSession):
            def get(self, url, headers=None, **kw):
                r = super().get(url, headers=headers, **kw)
                inner, delay = r.iter_content, random.choice([0, 0, 0.002, 0.01])

                def slow(n):
                    for c in inner(n):
                        time.sleep(delay)
                        yield c
                r.iter_content = slow
                return r

        old = (downloader.PIECE, downloader.RESPLIT_MIN, downloader.RESPLIT_GAIN, downloader.SPEED_WINDOW)
        downloader.PIECE, downloader.RESPLIT_MIN = 8 * 1024, 16 * 1024
        downloader.RESPLIT_GAIN, downloader.SPEED_WINDOW = 0, 0.01
        try:
            for i in range(25):
                random.seed(i)
                self.dest.unlink(missing_ok=True)
                self.get(RandomlySlow([]))
                self.assertEqual(hashlib.md5(self.dest.read_bytes()).hexdigest(), MD5, f"round {i}")
        finally:
            downloader.PIECE, downloader.RESPLIT_MIN, downloader.RESPLIT_GAIN, downloader.SPEED_WINDOW = old

    def test_small_files_are_never_split(self):
        downloader.SPLIT_MIN = len(DATA) + 1
        s = FakeSession([])
        self.get(s)
        self.assertEqual(s.ranges, [0])


class SpeedLimitTests(unittest.TestCase):
    def test_limit_is_respected_across_threads(self):
        clock = [0.0]
        lock = threading.Lock()

        def sleep(s):
            with lock:
                clock[0] += s
        lim = downloader.RateLimiter(256 * 1024, clock=lambda: clock[0], sleep=sleep)
        threads = [threading.Thread(target=lambda: [lim.consume(64 * 1024) for _ in range(16)]) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertAlmostEqual(clock[0], 4 * 1024 * 1024 / (256 * 1024), delta=1.5)   # 4 MB at 256 KB/s = 16 s

    def test_no_limit_never_waits(self):
        waited = []
        lim = downloader.RateLimiter(0, sleep=waited.append)
        for _ in range(100):
            lim.consume(10 * 1024 * 1024)
        self.assertEqual(waited, [])

    def test_download_uses_the_limit(self):
        clock = [0.0]
        lim = downloader.RateLimiter(512 * 1024, clock=lambda: clock[0],
                                     sleep=lambda s: clock.__setitem__(0, clock[0] + s))
        with tempfile.TemporaryDirectory() as d:
            downloader.download(None, "fid", Path(d) / "f.bin", prefix="~drivesync-", expected_md5=MD5,
                                session=FakeSession([]), limiter=lim, connections=1)
        self.assertAlmostEqual(clock[0], 2.0, delta=0.6)                # 1 MB at 512 KB/s

    def test_settings_switch_mode_and_limit(self):
        try:
            downloader.apply_settings(True, 1024 * 1024)
            self.assertEqual((downloader.settings["connections"], downloader.LIMIT.rate), (4, 1024 * 1024))
        finally:
            downloader.apply_settings(False, 0)
        self.assertEqual((downloader.settings["connections"], downloader.LIMIT.rate), (1, 0))


class ParallelTwoWayTests(unittest.TestCase):
    def test_many_drive_files_download_in_parallel(self):
        with tempfile.TemporaryDirectory() as d:
            pc = Path(d) / "pc"
            pc.mkdir()
            drive = FakeDrive()
            for i in range(12):
                drive.web_add("", f"file{i:02d}.txt", f"content {i}")
            orig = drive.download
            running, peak, lock = [0], [0], threading.Lock()

            def slow(fid, dest, **kw):
                with lock:
                    running[0] += 1
                    peak[0] = max(peak[0], running[0])
                time.sleep(0.2)                               # ~ the per-file wait measured on Drive
                orig(fid, dest)
                with lock:
                    running[0] -= 1
            drive.download = slow
            p = Progress()
            t = time.monotonic()
            TwoWaySyncer(drive, pc, "root", excludes=DEFAULT_EXCLUDES, state_path=Path(d) / "s.json",
                         recycle=lambda x: None, progress=p, parallel=4).run()
            elapsed = time.monotonic() - t
            self.assertEqual(sorted(x.name for x in pc.iterdir()), [f"file{i:02d}.txt" for i in range(12)])
            self.assertEqual(peak[0], 4)                      # 4 at a time
            self.assertLess(elapsed, 12 * 0.2 / 2)            # well under the 2.4 s one-by-one would take
            self.assertEqual((p.done, p.total), (12, 12))
            # state was recorded for every file: a second sync has nothing to do
            ev = []
            TwoWaySyncer(drive, pc, "root", excludes=DEFAULT_EXCLUDES, state_path=Path(d) / "s.json",
                         recycle=lambda x: None, on_event=lambda *a: ev.append(a)).run()
            self.assertEqual(ev, [])

    def test_one_failed_download_doesnt_stop_the_others(self):
        with tempfile.TemporaryDirectory() as d:
            pc = Path(d) / "pc"
            pc.mkdir()
            drive = FakeDrive()
            for i in range(5):
                drive.web_add("", f"f{i}.txt", str(i))
            orig = drive.download

            def flaky(fid, dest, **kw):
                if Path(dest).name == "f2.txt":
                    raise downloader.DownloadError("kept failing")
                orig(fid, dest)
            drive.download = flaky
            ev = []
            TwoWaySyncer(drive, pc, "root", excludes=DEFAULT_EXCLUDES, state_path=Path(d) / "s.json",
                         recycle=lambda x: None, on_event=lambda k, p, m: ev.append((k, p))).run()
            self.assertIn(("error", "f2.txt"), ev)
            self.assertEqual(sorted(x.name for x in pc.iterdir()), ["f0.txt", "f1.txt", "f3.txt", "f4.txt"])
            drive.download = orig                             # next sync retries just the failed one
            ev.clear()
            TwoWaySyncer(drive, pc, "root", excludes=DEFAULT_EXCLUDES, state_path=Path(d) / "s.json",
                         recycle=lambda x: None, on_event=lambda k, p, m: ev.append((k, p))).run()
            self.assertEqual(ev, [("downloaded", "f2.txt")])


if __name__ == "__main__":
    unittest.main()
