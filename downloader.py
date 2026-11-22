"""
Fast, safe downloads from Google Drive - plus the network settings (priority, speed limit).

Measured on a real ~2 MB/s connection:
    1 MB pieces, one request each   1,149 KB/s   <- every request waits ~0.5 s before data flows
    one continuous stream           2,173 KB/s   <- full line speed
    small files, one at a time      1.10 s/file  <- almost all waiting
    small files, 4 at a time        0.28 s/file

So each file is downloaded as ONE streamed request (progress is still reported every
256 KB), and the two-way engine runs up to PARALLEL downloads at once.

"High priority" (settings["connections"] > 1): a big file is split into that many parts
downloaded at the same time. An internet connection is shared per connection, not per
app, so when other apps are busy this gives Drive Sync a bigger share. (With nothing else
running it changes nothing: one stream already fills the line.)

Speed limit: LIMIT caps Drive Sync's combined speed (downloads and uploads), so other
apps keep some of the connection.

Safety:
  * files are written to "~drivesync-<name>.part" next to the destination and only
    swapped in when complete - a half-downloaded file never replaces yours;
  * a broken connection continues where it stopped, also after Drive Sync restarts
    (single stream: from the .part size; split download: from the .part.json record);
  * the finished file is checked against Google's MD5 checksum before it's used;
  * every request has a timeout, so a dead connection can't hang the sync.
"""
from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PARALLEL = 4                       # files downloaded at the same time
PIECE = 256 * 1024                 # how often progress is reported while streaming
TIMEOUT = (15, 60)                 # seconds: connect, and silence between bytes
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
URL = "https://www.googleapis.com/drive/v3/files/{}?alt=media&supportsAllDrives=true"
SPLIT_MIN = 16 * 1024 * 1024       # only files at least this big are split ("High priority")
HIGH_PRIORITY_CONNECTIONS = 4
RESPLIT_MIN = 2 * 1024 * 1024  # only re-split a part with at least this much left (each split costs a request)
RESPLIT_GAIN = 5.0             # ...and only if it saves at least this many seconds beyond the request's wait
SPEED_WINDOW = 1.0             # seconds of data before a connection's speed is trusted

# Set by the app from Settings; read at the start of every download.
settings = {"connections": 1}

log = logging.getLogger("drive_sync")
_local = threading.local()


class DownloadError(Exception):
    pass


class _Retry(Exception):
    pass


class _NoRanges(Exception):
    """The server ignored a byte range, so the file can't be fetched in parts."""


# --------------------------------------------------------------------------- #
# Speed limit
# --------------------------------------------------------------------------- #
class RateLimiter:
    """Caps the combined speed of all transfers (any thread). rate 0 = no limit."""

    def __init__(self, rate: float = 0, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()
        self.rate = 0.0
        self.set_rate(rate)

    def set_rate(self, bytes_per_second: float) -> None:
        with self.lock:
            self.rate = max(0.0, float(bytes_per_second or 0))
            self.allowance = 0.0
            self.last = self.clock()

    def consume(self, n: int) -> None:
        """Account for n bytes just sent/received; sleep if that's ahead of the limit."""
        with self.lock:
            if self.rate <= 0:
                return
            now = self.clock()
            self.allowance = min(self.rate, self.allowance + (now - self.last) * self.rate)  # 1 s burst max
            self.last = now
            self.allowance -= n
            wait = -self.allowance / self.rate if self.allowance < 0 else 0.0
        if wait > 0:
            self.sleep(wait)


LIMIT = RateLimiter()


def apply_settings(high_priority: bool, limit_bytes_per_second: float) -> None:
    settings["connections"] = HIGH_PRIORITY_CONNECTIONS if high_priority else 1
    LIMIT.set_rate(limit_bytes_per_second)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _session(creds):
    """One HTTP session per thread (keeps the connection open between files; not shared across threads)."""
    sessions = getattr(_local, "sessions", None)
    if sessions is None:
        sessions = _local.sessions = {}
    s = sessions.get(id(creds))
    if s is None:
        from google.auth.transport.requests import AuthorizedSession
        s = sessions[id(creds)] = AuthorizedSession(creds)
    return s


def _network_errors() -> tuple[type[BaseException], ...]:
    """Connection drops, timeouts and broken streams - the errors worth retrying."""
    import requests
    return (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError)


def _md5(path: Path) -> str:
    h = hashlib.md5(usedforsecurity=False)  # Drive's checksum; integrity check, not security
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def part_path(dest: Path, prefix: str) -> Path:
    return dest.with_name(f"{prefix}{dest.name}.part")


def _record_path(tmp: Path) -> Path:
    return tmp.with_name(tmp.name + ".json")   # "~drivesync-<name>.part.json" (never synced)


def _split(start: int, end: int, n: int) -> list[list[int]]:
    """[[from, to (exclusive), received], ...] covering start..end in n parts."""
    step = max(1, -(-(end - start) // n))
    return [[a, min(a + step, end), 0] for a in range(start, end, step)]


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def download(creds, file_id: str, dest: Path, *, prefix: str, expected_md5: str | None = None,
             size: int | None = None, on_bytes: Callable[[int], None] | None = None, retries: int = 6,
             session=None, sleep: Callable[[float], None] = time.sleep, limiter: RateLimiter | None = None,
             connections: int | None = None, before_replace: Callable[[], None] | None = None) -> None:
    """Download a Drive file to dest (via a .part file), resuming and verifying as described above.
    before_replace runs just before dest is replaced; if it raises, dest is left alone."""
    tmp = part_path(dest, prefix)
    record = _record_path(tmp)
    limiter = limiter or LIMIT
    n = connections if connections is not None else settings["connections"]
    for attempt in (1, 2):                       # a second, clean try if the checksum doesn't match
        split = record.exists() or (n > 1 and size is not None and size >= SPLIT_MIN)
        try:
            if split:
                _download_split(creds, file_id, dest, tmp, record, size, max(n, 2), on_bytes, retries,
                                session, sleep, limiter)
            else:
                _download_stream(creds, file_id, dest, tmp, on_bytes, retries, session, sleep, limiter)
        except _NoRanges:
            log.info("Server won't send %s in parts - downloading it in one piece", dest.name)
            tmp.unlink(missing_ok=True)
            record.unlink(missing_ok=True)
            try:
                _download_stream(creds, file_id, dest, tmp, on_bytes, retries, session, sleep, limiter)
            except OSError as e:
                _drop_part_if_disk_full(e, tmp, record)
                raise
        except OSError as e:
            _drop_part_if_disk_full(e, tmp, record)
            raise
        if expected_md5 and _md5(tmp) != expected_md5:
            tmp.unlink(missing_ok=True)
            record.unlink(missing_ok=True)
            if attempt == 2:
                raise DownloadError("The downloaded file didn't match Google's checksum (twice) - not used")
            log.warning("Downloaded %s didn't match Google's checksum - downloading it again", dest.name)
            continue
        if before_replace:
            try:
                before_replace()
            except BaseException:
                tmp.unlink(missing_ok=True)
                record.unlink(missing_ok=True)
                raise
        os.replace(tmp, dest)
        record.unlink(missing_ok=True)
        return


def _drop_part_if_disk_full(e: OSError, tmp: Path, record: Path) -> None:
    """Disk full: delete the half-downloaded file so it doesn't keep taking the space that's short."""
    if is_disk_full(e):
        tmp.unlink(missing_ok=True)
        record.unlink(missing_ok=True)


def is_disk_full(e: BaseException) -> bool:
    """True for "the disk is full" (on Windows also "not enough space on the disk")."""
    return isinstance(e, OSError) and (e.errno == errno.ENOSPC or getattr(e, "winerror", None) in (39, 112))


def _retry_wait(e: Exception, attempt: int, retries: int, name: str, sleep) -> None:
    if attempt > retries:
        raise DownloadError(f"Download kept failing ({e})") from e
    wait = min(30, 2 ** attempt)
    log.info("Download of %s interrupted (%s) - continuing in %d s", name, type(e).__name__, wait)
    sleep(wait)


def _download_stream(creds, file_id, dest, tmp, on_bytes, retries, session, sleep, limiter) -> None:
    """One continuous stream; resumes from the .part file's size."""
    s = session or _session(creds)
    attempt = 0
    while True:
        got = tmp.stat().st_size if tmp.exists() else 0   # resume an earlier, interrupted download
        try:
            headers = {"Range": f"bytes={got}-"} if got else {}
            with s.get(URL.format(file_id), headers=headers, stream=True, timeout=TIMEOUT) as r:
                if r.status_code == 416:                  # .part already has everything
                    return
                if r.status_code in RETRYABLE_STATUS:
                    raise _Retry(f"HTTP {r.status_code}")
                if r.status_code >= 400:
                    raise DownloadError(f"HTTP {r.status_code}: {r.text[:200]}")
                if got and r.status_code != 206:          # server ignored the range: start over
                    got = 0
                with open(tmp, "ab" if got else "wb") as fh:
                    for part in r.iter_content(PIECE):
                        fh.write(part)
                        got += len(part)
                        limiter.consume(len(part))
                        if on_bytes:
                            on_bytes(got)
            return
        except (_Retry, *_network_errors()) as e:        # network trouble: wait, then continue
            attempt += 1
            _retry_wait(e, attempt, retries, dest.name, sleep)
        # Anything else (disk full, no permission to write, ...) is raised: retrying won't help.


def _download_split(creds, file_id, dest, tmp, record, size, n, on_bytes, retries, session, sleep,
                    limiter) -> None:
    """Several parts at once into one preallocated .part file; progress is kept in .part.json."""
    segs = None
    if record.exists():
        try:
            saved = json.loads(record.read_text(encoding="utf-8"))
            if size is None or saved.get("size") == size:
                size, segs = saved["size"], saved["segments"]
        except (OSError, ValueError, KeyError):
            segs = None
    if size is None:
        raise _NoRanges()                                 # nothing to split on - use one stream
    if segs is None:                                      # fresh, or continue an earlier single stream
        start = min(tmp.stat().st_size, size) if tmp.exists() else 0
        segs = _split(start, size, n)
    if not tmp.exists():
        tmp.touch()
    with open(tmp, "r+b") as fh:
        fh.truncate(size)                                 # room for every part

    # All bookkeeping (which bytes each part covers, how much arrived) changes under this lock,
    # together with the write to disk, so a part can be cut short safely at any moment.
    lock = threading.Lock()
    already = size - sum(b - a for a, b, _ in segs)       # bytes before the parts (earlier single stream)
    last_save = [0.0]

    def save(force=False):
        now = time.monotonic()
        if force or now - last_save[0] > 2:
            last_save[0] = now
            record.write_text(json.dumps({"size": size, "segments": segs}), encoding="utf-8")

    def remaining(seg) -> int:
        return seg[1] - (seg[0] + seg[2])

    failed = threading.Event()   # set when a part gives up, so the other connections stop
    waits: list[float] = []      # measured seconds from asking to first byte (Google: often 6-19 s!)
    flow: dict[int, tuple[float, int]] = {}   # id(part) -> (time of first byte, bytes it had then)

    def speed(seg) -> float | None:
        f = flow.get(id(seg))
        if not f:
            return None
        elapsed = time.monotonic() - f[0]
        return (seg[2] - f[1]) / elapsed if elapsed >= SPEED_WINDOW and seg[2] > f[1] else None

    def take_over():
        """IDM-style re-splitting, but only when it pays: a free connection takes the second half of
        the part that will take longest - IF the time that saves beats the wait for a new request.

        Measured on Google Drive: every new request waits 6-19 s before the first byte arrives, so a
        blind IDM-style split made a 32 MB download 50% SLOWER. So we measure that wait (and each
        connection's speed) during the download and split only when the remaining half would take
        clearly longer than the wait. Returns the new part, or None."""
        with lock:
            candidates = []
            for s in segs:
                left, v = remaining(s), speed(s)
                if left >= RESPLIT_MIN and v:
                    candidates.append((left / v, left, s))      # (seconds still to go, bytes, part)
            if not candidates:
                return None
            seconds_left, left, victim = max(candidates, key=lambda c: c[0])
            wait = max(waits) if waits else 0.0                 # cautious: the slowest start seen
            # Taking half saves ~half its time; it costs one request's wait (plus a safety margin).
            if seconds_left / 2 <= 1.5 * wait + RESPLIT_GAIN:
                return None
            pos = victim[0] + victim[2]
            cut = pos + left // 2
            new = [cut, victim[1], 0]
            victim[1] = cut                                # the original connection stops at `cut`
            segs.append(new)
            save(force=True)
            return new

    def fetch(seg, s, fh) -> None:
        """Download one part (it may be shortened by take_over() while it runs)."""
        attempt = 0
        while not failed.is_set():
            with lock:
                if remaining(seg) <= 0:
                    return
                pos, end = seg[0] + seg[2], seg[1]
            try:
                headers = {"Range": f"bytes={pos}-{end - 1}"}
                asked, first = time.monotonic(), True
                with s.get(URL.format(file_id), headers=headers, stream=True, timeout=TIMEOUT) as r:
                    if r.status_code in RETRYABLE_STATUS:
                        raise _Retry(f"HTTP {r.status_code}")
                    if r.status_code == 200:
                        raise _NoRanges()
                    if r.status_code != 206:
                        raise DownloadError(f"HTTP {r.status_code}: {r.text[:200]}")
                    for chunk in r.iter_content(PIECE):
                        with lock:
                            if first:                     # measure the wait and start timing the speed
                                first = False
                                now = time.monotonic()
                                waits.append(now - asked)
                                flow[id(seg)] = (now, seg[2])
                            allowed = remaining(seg)      # may have shrunk: another connection took over
                            chunk = chunk[:allowed]
                            if chunk:
                                fh.seek(seg[0] + seg[2])
                                fh.write(chunk)
                                seg[2] += len(chunk)
                                total = already + sum(x[2] for x in segs)
                                save()
                        if chunk:
                            limiter.consume(len(chunk))
                            if on_bytes:
                                on_bytes(total)
                        if remaining(seg) <= 0:
                            break                         # this part is done (or was cut short): hang up
            except (_Retry, *_network_errors()) as e:
                attempt += 1
                fh.flush()
                _retry_wait(e, attempt, retries, dest.name, sleep)

    def connection(seg):
        """One connection: its own part first; then, while other parts are still running, keep
        checking whether taking one over pays (speeds are only known after a moment)."""
        s = session or _session(creds)
        try:
            with open(tmp, "r+b") as fh:
                while not failed.is_set():
                    if seg is not None:
                        fetch(seg, s, fh)
                    seg = take_over()
                    if seg is None:
                        with lock:
                            busy = any(remaining(x) > 0 for x in segs)
                        if not busy:
                            return
                        time.sleep(min(0.5, SPEED_WINDOW))   # check again shortly
        except BaseException:
            failed.set()          # one part gave up: the others stop too (progress is saved; resumes later)
            raise

    try:
        with ThreadPoolExecutor(max_workers=n, thread_name_prefix="part") as pool:
            own = [seg for seg in segs if remaining(seg) > 0]
            starts = own + [None] * max(0, n - len(own))  # resumed downloads: free connections take over
            futures = [pool.submit(connection, seg) if seg is not None else
                       pool.submit(lambda: connection(take_over())) for seg in starts[:max(n, len(own))]]
            for f in futures:
                f.result()                                # first failure is raised (others finish)
    finally:
        with lock:
            save(force=True)                              # so a restart continues every part
