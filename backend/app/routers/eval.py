"""Evaluation page (BUILD.md P0-10). OWNER: pipeline-agent.

GET /eval/latest reads backend/app/eval/results/latest.json (written by `python -m app.eval.run`).
Returns `{available: false, status: "not_run"}` when no results exist. Never shows numbers that
don't come from that file.
POST /eval/run (admin) re-runs the test split in the background (detection: tier0 + Jev + claims;
a few cents with a Jev key) and rewrites latest.json.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import APIRouter

from app.config import APP_DIR
from app.deps import AdminUser, CurrentUser
from app.schemas import EvalLatestOut, EvalRunOut

router = APIRouter(tags=["eval"])
log = logging.getLogger("brandgate.eval")

LATEST = APP_DIR / "eval" / "results" / "latest.json"
_state: dict[str, Any] = {"task": None, "error": None}


def _running() -> bool:
    t: Optional[asyncio.Task] = _state["task"]
    return t is not None and not t.done()


BLANK_NOTE = "Evaluation uses the built-in demo brand's labelled test set."


def _brand_note() -> Optional[str]:
    from app import workspaces as W

    return BLANK_NOTE if W.current_kind() == "blank" else None


def _ai_checks(data: dict) -> Optional[bool]:
    """Whether the run included the AI check. The results file names the models; the UI never does (neutral.py)."""
    models = data.get("models")
    if not isinstance(models, dict) or "jev" not in models:
        return None
    m = str(models.get("jev") or "")
    return bool(m) and not m.startswith("not configured")


@router.get("/eval/latest", response_model=EvalLatestOut)
def eval_latest(user: CurrentUser) -> EvalLatestOut:
    status = "running" if _running() else "ok"
    if not LATEST.is_file():
        return EvalLatestOut(available=False, status="running" if status == "running" else "not_run", brand_note=_brand_note())
    try:
        data = json.loads(LATEST.read_text(encoding="utf-8"))
        return EvalLatestOut(available=True, status=status, brand_note=_brand_note(), ai_checks=_ai_checks(data),
                             **{k: data[k] for k in ("run_at", "split", "metrics", "misses") if k in data})
    except Exception:  # noqa: BLE001
        log.exception("eval: latest.json unreadable")
        return EvalLatestOut(available=False, status="not_run")


@router.post("/eval/run", response_model=EvalRunOut)
async def eval_run(user: AdminUser) -> EvalRunOut:
    if _running():
        return EvalRunOut(started=False, message="An evaluation run is already in progress.")

    async def _go() -> None:
        from app import workspaces as W
        from app.eval.run import evaluate

        # The labelled test set belongs to the demo brand: in workspace mode it runs on the demo template.
        tpl = W.TEMPLATE_IDS["demo"] if W.current_ref() is not None else None
        try:
            with W.use(tpl):
                await evaluate("test")
            _state["error"] = None
        except Exception:  # noqa: BLE001
            log.exception("eval: background run failed")
            _state["error"] = "The evaluation run failed. Check the server logs."

    _state["task"] = asyncio.get_running_loop().create_task(_go())
    from app.pipeline import jev

    note = "" if jev.available() else " The AI check isn't configured, so only the code checks are measured."
    return EvalRunOut(started=True, message=f"Evaluation started on the test split. Results replace the page in about a minute.{note}")
