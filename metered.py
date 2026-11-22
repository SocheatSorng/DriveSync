"""Is this PC on a metered connection (phone hotspot, mobile data, Wi-Fi set as metered in Windows)?

Windows knows: Settings → Network → "Metered connection". It's read through PowerShell (Windows
Runtime networking API), at most once a minute.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time

CHECK_EVERY = 60   # seconds an answer is reused
_SCRIPT = (
    "[void][Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,"
    "ContentType=WindowsRuntime];"
    "$p=[Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile();"
    "if ($p) { $c=$p.GetConnectionCost(); \"$($c.NetworkCostType) $($c.Roaming) $($c.OverDataLimit)\" }"
    " else { 'none' }"
)
log = logging.getLogger("drive_sync")
_lock = threading.Lock()
_cache: tuple[float, bool] | None = None


def parse(output: str) -> bool:
    """'Fixed False False' -> True. Metered: a data plan (Fixed/Variable), roaming or over the limit."""
    parts = output.split()
    if not parts or parts[0] == "none":
        return False
    cost = parts[0]
    flags = [p.lower() == "true" for p in parts[1:3]]
    return cost in ("Fixed", "Variable") or any(flags)


def _ask_windows() -> bool:
    if sys.platform != "win32":
        return False
    out = subprocess.run(  # noqa: S603 - fixed command, no user input
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
        capture_output=True, text=True, timeout=15, creationflags=0x08000000,  # CREATE_NO_WINDOW
    ).stdout
    return parse(out)


def is_metered(now: float | None = None) -> bool:
    """True on a metered connection. Unknown (check failed) counts as not metered."""
    global _cache
    now = time.monotonic() if now is None else now
    with _lock:
        if _cache and now - _cache[0] < CHECK_EVERY:
            return _cache[1]
    try:
        answer = _ask_windows()
    except Exception as e:  # noqa: BLE001 - never stop syncing because the check itself failed
        log.debug("Couldn't tell whether the connection is metered: %s", e)
        answer = False
    with _lock:
        _cache = (now, answer)
    return answer
