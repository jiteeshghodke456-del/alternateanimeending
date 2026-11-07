"""Evaluation page (BUILD.md P0-10). OWNER: pipeline-agent.

GET /eval/latest reads backend/app/eval/results/latest.json (written by `python -m app.eval.run`).
Expected file shape = EvalLatestOut fields: {run_at, split, models{}, metrics{}, misses[]}.
Never show numbers that don't come from that file.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException

from app.config import APP_DIR
from app.deps import AdminUser, CurrentUser
from app.schemas import EvalLatestOut, EvalRunOut

router = APIRouter(tags=["eval"])
log = logging.getLogger("brandgate.eval")

LATEST = APP_DIR / "eval" / "results" / "latest.json"


@router.get("/eval/latest", response_model=EvalLatestOut)
def eval_latest(user: CurrentUser) -> EvalLatestOut:
    if not LATEST.is_file():
        return EvalLatestOut(available=False)
    try:
        data = json.loads(LATEST.read_text(encoding="utf-8"))
        return EvalLatestOut(available=True, **{k: data[k] for k in ("run_at", "split", "models", "metrics", "misses") if k in data})
    except Exception:  # noqa: BLE001
        log.exception("eval: latest.json unreadable")
        return EvalLatestOut(available=False)


# OWNER: pipeline-agent
@router.post("/eval/run", response_model=EvalRunOut)
async def eval_run(user: AdminUser) -> EvalRunOut:
    raise HTTPException(status_code=501, detail="Re-running the evaluation isn't available yet.")
