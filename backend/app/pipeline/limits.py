"""Memory guard rails for the 512 MiB Render free tier. OWNER: pipeline-agent.

Measured 7 Nov (DECISIONS.md "Memory budget on Render free tier"): the app idles at ~130 MiB, and
one OpenCV SIFT pass on a 1080 px ad needs ~265 MiB more for a moment. Two at once killed the
container. So:

- `check_slot()`: at most MAX_CONCURRENT_CHECKS heavy jobs (checks, audience runs, PDF imports) run
  at once per process; the rest wait their turn (the UI already shows "checking"/"running").
- `SIFT_LOCK`: one SIFT pass at a time, whatever the check limit is.
- `trim()`: hand freed heap memory back to the OS after a big job (glibc only; a no-op elsewhere,
  including Windows and macOS), so RSS falls back instead of ratcheting up.
"""
from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import logging
import sys
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Optional

from app.config import get_settings

log = logging.getLogger("brandgate.limits")

SIFT_LOCK = threading.Lock()
_sems: dict[int, asyncio.Semaphore] = {}
_libc: Any = None
_libc_loaded = False


def max_concurrent_checks() -> int:
    return max(1, int(get_settings().max_concurrent_checks))


def _semaphore() -> asyncio.Semaphore:
    k = id(asyncio.get_running_loop())
    if k not in _sems:
        _sems[k] = asyncio.Semaphore(max_concurrent_checks())
    return _sems[k]


@asynccontextmanager
async def check_slot() -> AsyncIterator[None]:
    """Hold one of the MAX_CONCURRENT_CHECKS slots for a heavy job; frees memory afterwards."""
    async with _semaphore():
        try:
            yield
        finally:
            trim()


def trim() -> None:
    """malloc_trim(0): return freed heap pages to the OS. Never raises."""
    global _libc, _libc_loaded
    if not _libc_loaded:
        _libc_loaded = True
        if not sys.platform.startswith("linux"):  # malloc_trim is glibc-only; never probe the C runtime elsewhere
            return
        try:
            name: Optional[str] = ctypes.util.find_library("c")
            lib = ctypes.CDLL(name or "libc.so.6")
            _libc = lib if hasattr(lib, "malloc_trim") else None
        except Exception:  # noqa: BLE001  (not glibc, e.g. macOS or musl)
            _libc = None
    if _libc is not None:
        try:
            _libc.malloc_trim(0)
        except Exception:  # noqa: BLE001
            pass


_cv2_ready = False


def cv2() -> Any:
    """Import OpenCV lazily with one worker thread: its thread pool sizes itself from the host's CPU
    count (not the container's share), and every pool thread keeps its own scratch buffers."""
    global _cv2_ready
    import cv2 as _cv2

    if not _cv2_ready:
        _cv2_ready = True
        try:
            _cv2.setNumThreads(1)
        except Exception:  # noqa: BLE001
            pass
    return _cv2
