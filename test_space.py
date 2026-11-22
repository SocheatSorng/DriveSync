"""Tests for the "what's using my space" scans.
Run:  python -m unittest test_space -v
"""
import os
import tempfile
import time
import unittest
from pathlib import Path

import space

KB = 1024


class PcScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "EBC"
        self.put("Term 1/lesson.wav", b"a" * (300 * KB))
        self.put("Backup/lesson copy.wav", b"a" * (300 * KB))      # duplicate
        self.put("Term 1/other.wav", b"b" * (300 * KB))            # same size, different content
        self.put("Term 2/big.mp4", b"c" * (900 * KB))
        self.put("__MACOSX/Term 1/._lesson.wav", b"x" * 4096)      # junk folder
        self.put("Term 2/._notes.txt", b"x" * 100)                 # junk file
        self.put("Term 2/small1.txt", b"same")                     # too small to count as duplicates
        self.put("Term 2/small2.txt", b"same")
        old = time.time() - 3600
        os.utime(self.root / "Term 1/lesson.wav", (old, old))      # the original

    def put(self, rel, data):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def test_finds_duplicates_and_keeps_the_original(self):
        r = space.scan_pc([str(self.root)])
        self.assertEqual(len(r.duplicates), 1)
        g = r.duplicates[0]
        self.assertTrue(g.items[0].path.endswith(os.path.join("Term 1", "lesson.wav")))
        self.assertEqual(g.wasted, 300 * KB)

    def test_junk_and_safe_list(self):
        r = space.scan_pc([str(self.root)])
        junk = sorted(os.path.basename(j.path) for j in r.junk)
        self.assertEqual(junk, ["._notes.txt", "__MACOSX"])
        safe = {os.path.basename(i.path) for i in r.safe()}
        self.assertEqual(safe, {"._notes.txt", "__MACOSX", "lesson copy.wav"})
        self.assertEqual(r.safe_bytes(), 100 + 4096 + 300 * KB)

    def test_biggest(self):
        r = space.scan_pc([str(self.root)])
        self.assertTrue(r.biggest_files[0].path.endswith("big.mp4"))
        self.assertEqual(r.biggest_folders[0].path, str(self.root))     # the whole folder first
        self.assertEqual(r.biggest_folders[0].size, r.total)
        self.assertEqual(r.biggest_folders[1].size, 900 * KB + 100 + 8)  # Term 2

    def test_stop_returns_early(self):
        r = space.scan_pc([str(self.root)], should_stop=lambda: True)
        self.assertEqual(r.duplicates, [])


class DriveReportTests(unittest.TestCase):
    def test_drive_report(self):
        def f(i, name, parent, size, md5=None, used=None, mime="audio/wav"):
            return {"id": i, "name": name, "parents": [parent], "mimeType": mime, "size": str(size),
                    "quotaBytesUsed": str(used if used is not None else size), "md5Checksum": md5,
                    "modifiedTime": f"2026-11-0{int(i[-1]) % 9 + 1}T10:00:00.000Z"}
        items = [
            {"id": "d1", "name": "EBC", "parents": ["root"], "mimeType": space.FOLDER_MIME},
            {"id": "d2", "name": "__MACOSX", "parents": ["d1"], "mimeType": space.FOLDER_MIME},
            f("f1", "a.wav", "d1", 200 * KB, "m1"),
            f("f2", "a copy.wav", "d1", 200 * KB, "m1"),
            f("f3", "._a.wav", "d2", 4 * KB, "m3"),
            f("f4", "edited.docx", "d1", 100 * KB, "m4", used=5 * 1024 * KB),  # 4.9 MB in old versions
        ]
        r = space.build_drive_report("me@example.com", items, [{"id": "t", "quotaBytesUsed": "777"}],
                                     {"limit": 10, "usage": 5})
        self.assertEqual([g.items[0].key for g in r.duplicates], ["f1"])          # older copy kept
        self.assertEqual([j.path for j in r.junk], ["My Drive/EBC/__MACOSX"])     # one row for the folder
        self.assertEqual([o.key for o in r.old_versions], ["f4"])
        self.assertEqual(r.old_versions[0].size, 5 * 1024 * KB - 100 * KB)
        self.assertEqual(r.trash, 777)
        self.assertEqual(r.biggest_folders[0].path, "My Drive/EBC")
        self.assertEqual({i.key for i in r.safe()}, {"d2", "f2"})


if __name__ == "__main__":
    unittest.main()
