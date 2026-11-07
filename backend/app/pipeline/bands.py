"""Probability -> band. Defaults from questions.py; per-category thresholds tuned on the dev split
(`python -m app.eval.run --split dev --tune`) are loaded from app/eval/results/thresholds.json.
OWNER: pipeline-agent. The model answers; this code decides.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from app.config import APP_DIR
from app.pipeline.questions import FLAG_AT, HUMAN_AT

log = logging.getLogger("brandgate.bands")
THRESHOLDS_FILE = Path(os.environ.get("BRANDGATE_THRESHOLDS") or APP_DIR / "eval" / "results" / "thresholds.json")

_cache: dict[str, Any] = {"mtime": None, "data": {}}


def _load(path: Path = THRESHOLDS_FILE) -> dict[str, Any]:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _cache["mtime"] != mtime:
        try:
            _cache["data"] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            log.warning("bands: thresholds.json unreadable; using defaults")
            _cache["data"] = {}
        _cache["mtime"] = mtime
    return _cache["data"]


def thresholds(category: Optional[str] = None) -> tuple[float, float]:
    """(flag_at, human_at) for a category."""
    data = _load()
    default = data.get("default") or {}
    flag = float(default.get("flag", FLAG_AT))
    human = float(default.get("human", HUMAN_AT))
    cat = (data.get("categories") or {}).get(category or "") or {}
    flag = float(cat.get("flag", flag))
    human = float(cat.get("human", human))
    return flag, min(human, flag)


def band(p: Optional[float], category: Optional[str] = None) -> str:
    if p is None:
        return "human"
    flag, human = thresholds(category)
    if p >= flag:
        return "flag"
    if p >= human:
        return "human"
    return "pass"
