"""Tests for automatic updates (no network: GitHub is faked).
Run:  python -m unittest test_updater -v
"""
import hashlib
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import updater
from version import __version__


class FakeResponse:
    def __init__(self, status=200, data=None, body=b""):
        self.status_code, self.data, self.body = status, data, body
        self.headers = {"content-length": str(len(body))}
        self.text = body.decode() if isinstance(body, bytes) else body

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSession:
    def __init__(self, responses):
        self.responses = responses

    def get(self, url, **kw):
        return self.responses[url]


SETUP = b"pretend installer" * 1000
SHA = hashlib.sha256(SETUP).hexdigest()


def release(tag, digest=True):
    asset = {"name": "DriveSync-Setup.exe", "browser_download_url": "https://dl/setup.exe"}
    if digest:
        asset["digest"] = f"sha256:{SHA}"
    return {"tag_name": tag, "assets": [asset], "html_url": "https://page", "body": "notes"}


class VersionTests(unittest.TestCase):
    def test_compare(self):
        self.assertTrue(updater.newer("v1.2.0", "1.1.0"))
        self.assertTrue(updater.newer("v1.10", "1.9.9"))
        self.assertFalse(updater.newer("v1.1.0", "1.1"))
        self.assertFalse(updater.newer("v1.0.9", "1.1.0"))
        self.assertFalse(updater.newer("nightly", "1.1.0"))

    def test_pyproject_matches_version_py(self):
        text = (Path(__file__).parent / "pyproject.toml").read_text(encoding="utf-8")
        self.assertEqual(re.search(r'^version = "([^"]+)"', text, re.M).group(1), __version__)


class CheckTests(unittest.TestCase):
    def test_newer_release_found(self):
        s = FakeSession({updater.LATEST: FakeResponse(data=release("v99.0.0"))})
        u = updater.check(s)
        self.assertEqual((u.version, u.sha256, u.url), ("99.0.0", SHA, "https://dl/setup.exe"))

    def test_same_or_older_is_ignored(self):
        s = FakeSession({updater.LATEST: FakeResponse(data=release("v" + __version__))})
        self.assertIsNone(updater.check(s))

    def test_no_releases_yet(self):
        self.assertIsNone(updater.check(FakeSession({updater.LATEST: FakeResponse(404)})))

    def test_checksum_from_sha256_file(self):
        rel = release("v99.0.0", digest=False)
        rel["assets"].append({"name": "DriveSync-Setup.exe.sha256", "browser_download_url": "https://dl/sha"})
        s = FakeSession({updater.LATEST: FakeResponse(data=rel),
                         "https://dl/sha": FakeResponse(body=f"{SHA}  DriveSync-Setup.exe".encode())})
        self.assertEqual(updater.check(s).sha256, SHA)


class DownloadTests(unittest.TestCase):
    def test_download_checks_the_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            u = updater.Update("99.0.0", "https://dl/setup.exe", SHA)
            s = FakeSession({"https://dl/setup.exe": FakeResponse(body=SETUP)})
            path = updater.download(u, Path(tmp), s)
            self.assertEqual(path.read_bytes(), SETUP)
            bad = updater.Update("99.0.1", "https://dl/setup.exe", "0" * 64)
            with self.assertRaises(ValueError):
                updater.download(bad, Path(tmp), s)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["DriveSync-Setup-99.0.0.exe"])

    def test_no_checksum_no_install(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            updater.download(updater.Update("99.0.0", "https://dl/setup.exe", None), Path(tmp), FakeSession({}))

    def test_only_an_installed_copy_updates_itself(self):
        self.assertFalse(updater.installed_copy())          # running from source
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "DriveSync.exe"
            (Path(tmp) / "unins000.exe").write_bytes(b"")
            with mock.patch.object(updater.sys, "frozen", True, create=True), \
                    mock.patch.object(updater.sys, "executable", str(exe)):
                self.assertTrue(updater.installed_copy())


if __name__ == "__main__":
    unittest.main()
