"""Tests for carrying on a big upload that was stopped part-way (pause, quit, restart).
Run:  python -m unittest test_resume_upload -v
"""
import itertools
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httplib2
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaUploadProgress

import drive_sync
from drive_sync import Drive, ResumableUploads
from progress import Paused

MB = 1024 * 1024


class FakeGoogle:
    """Google's resumable upload endpoint: remembers how much of each upload it has."""

    def __init__(self):
        self.sessions: dict[str, int] = {}   # upload link -> bytes received
        self.links = itertools.count(1)
        self.sent = 0                        # bytes actually sent to it
        self.probes = 0
        self.forget_all = False

    def files(self):
        return self

    def create(self, media_body, **_):
        return FakeRequest(self, media_body)

    update = create


class FakeRequest:
    def __init__(self, google: FakeGoogle, media):
        self.g, self.media = google, media
        self.resumable_uri = None
        self._in_error_state = False
        self.resumable_progress = 0

    def next_chunk(self, num_retries=0):
        size, chunk = self.media.size(), self.media.chunksize()
        if self.resumable_uri is None:
            self.resumable_uri = f"link{next(self.g.links)}"
            self.g.sessions[self.resumable_uri] = 0
        elif self._in_error_state:
            self.g.probes += 1
            if self.g.forget_all or self.resumable_uri not in self.g.sessions:
                raise HttpError(httplib2.Response({"status": 404}), b"gone")
            self.resumable_progress = self.g.sessions[self.resumable_uri]
            self._in_error_state = False
        n = min(chunk, size - self.resumable_progress)
        self.resumable_progress += n
        self.g.sent += n
        self.g.sessions[self.resumable_uri] = self.resumable_progress
        if self.resumable_progress >= size:
            return None, {"id": "new-file"}
        return MediaUploadProgress(self.resumable_progress, size), None


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "lesson.wav"
        self.file.write_bytes(b"x" * (10 * MB))
        store = ResumableUploads(Path(self.tmp.name) / "uploads.json")
        for p in (mock.patch.object(drive_sync, "RESUMABLE", store), mock.patch.object(drive_sync, "CHUNK", MB)):
            p.start()
            self.addCleanup(p.stop)
        self.store = store
        self.google = FakeGoogle()

    def drive(self, stop_after_pieces=None):
        d = Drive(self.google)
        if stop_after_pieces is not None:
            pieces = []

            def on_transfer(_fraction):
                pieces.append(1)
                if len(pieces) >= stop_after_pieces:
                    raise Paused()
            d.on_transfer = on_transfer
        return d

    def test_stopped_upload_carries_on_where_it_stopped(self):
        with self.assertRaises(Paused):
            self.drive(stop_after_pieces=4).upload(self.file, "folder")
        self.assertEqual(self.google.sent, 4 * MB)
        # Fresh Drive object, as after a restart: the link comes from the saved file.
        fresh = ResumableUploads(self.store.path)
        with mock.patch.object(drive_sync, "RESUMABLE", fresh):
            self.assertEqual(self.drive().upload(self.file, "folder"), "new-file")
        self.assertEqual(self.google.sent, 10 * MB)           # nothing sent twice
        self.assertEqual(self.google.probes, 1)
        self.assertIsNone(fresh.get(ResumableUploads.key("upload", "folder", self.file)))  # done: forgotten

    def test_changed_file_starts_again(self):
        with self.assertRaises(Paused):
            self.drive(stop_after_pieces=4).upload(self.file, "folder")
        self.file.write_bytes(b"y" * (10 * MB))
        self.drive().upload(self.file, "folder")
        self.assertEqual((self.google.sent, self.google.probes), (14 * MB, 0))

    def test_link_google_no_longer_has_starts_again(self):
        with self.assertRaises(Paused):
            self.drive(stop_after_pieces=4).upload(self.file, "folder")
        self.google.forget_all = True
        self.assertEqual(self.drive().upload(self.file, "folder"), "new-file")
        self.assertEqual((self.google.sent, self.google.probes), (14 * MB, 1))

    def test_old_links_are_forgotten(self):
        self.store.put("k", "link")
        self.store.links["k"]["at"] -= ResumableUploads.MAX_AGE + 1
        self.store._save()
        self.assertIsNone(ResumableUploads(self.store.path).get("k"))


if __name__ == "__main__":
    unittest.main()
