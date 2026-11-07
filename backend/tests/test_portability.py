"""Windows (native, Anaconda Python 3.12) portability guards. No external calls."""
from __future__ import annotations

import ctypes
import os
import tempfile
from pathlib import Path

from app import config
from app.pipeline import limits


def test_trim_never_probes_the_c_runtime_off_linux(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(limits.sys, "platform", "win32")
    monkeypatch.setattr(limits, "_libc", None)
    monkeypatch.setattr(limits, "_libc_loaded", False)

    def boom(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("ctypes.CDLL must not be called on Windows")

    monkeypatch.setattr(ctypes, "CDLL", boom)
    limits.trim()
    limits.trim()
    assert limits._libc is None


def test_trim_is_safe_on_this_platform(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(limits, "_libc", None)
    monkeypatch.setattr(limits, "_libc_loaded", False)
    limits.trim()  # never raises


def test_default_storage_is_the_os_temp_dir(monkeypatch) -> None:  # noqa: ANN001
    assert config.default_tmp_dir() == (Path("/tmp") if os.name == "posix" else Path(tempfile.gettempdir()))
    monkeypatch.setattr(config.os, "name", "nt")
    assert config.default_tmp_dir() == Path(tempfile.gettempdir())


def test_sqlite_url_accepts_a_windows_style_path(tmp_path: Path) -> None:
    """db.get_engine builds sqlite:///<path>; a path with spaces and backslash-free parts must open."""
    from sqlmodel import create_engine, text

    p = tmp_path / "dir with spaces" / "brand gate.db"
    p.parent.mkdir()
    eng = create_engine(f"sqlite:///{p}")
    with eng.connect() as c:
        assert c.execute(text("select 1")).scalar() == 1
    eng.dispose()
