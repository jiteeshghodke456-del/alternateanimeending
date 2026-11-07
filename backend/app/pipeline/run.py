"""Orchestrates one check and streams progress. OWNER: pipeline-agent.

Contract with the routers (do not change the signatures without updating routers/content.py):

    start_check(version_id: int) -> None
        Called from `POST /api/items` (and resubmit) inside an async request handler, AFTER the
        Version and its Units are committed (Version.check_status == "queued", item.state ==
        "checking"). Must return immediately: schedule the work with
        `asyncio.get_running_loop().create_task(run_check(version_id))` (keep a reference in a
        module-level set so it isn't garbage-collected). Must never raise into the request.

    async run_check(version_id: int) -> None
        Opens its own `Session(get_engine())`. Steps (BUILD.md P0-5): units already exist ->
        tier0 -> jev (skip if item.confidential or Jev not configured) -> claims -> bands ->
        explain (flag/human only) -> selfcheck. Persist each Finding as soon as it is final and
        publish a CheckEvent(stage=..., finding=FindingOut) for it. Log every external call to
        AICall (never content text). At the end set Version.check_status="done",
        Version.stats={units_total, units_done, judgments, elapsed_ms, cost_usd, pii_masked},
        item.state="findings_ready", add AuditEvent(action="check_done"), publish stage="done".
        On exception: check_status="error", item.state="findings_ready" or "draft", publish
        stage="error" with a human `message`.

    subscribe(version_id: int) -> AsyncIterator[CheckEvent]
        Used by GET /api/versions/{id}/events. Should first replay already-stored findings
        (so a late subscriber or a page refresh sees everything), then live events, and stop
        after "done"/"error". If the version is already done, yield findings + one "done".
        An in-process asyncio.Queue fan-out per version_id is enough (single container).
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from app.schemas import CheckEvent


def start_check(version_id: int) -> None:
    """Schedule `run_check(version_id)` in the background. Currently a no-op stub."""
    return None


async def run_check(version_id: int) -> None:
    """Run the full pipeline for one version. Not implemented yet."""
    raise NotImplementedError("pipeline-agent: implement run_check")


async def subscribe(version_id: int) -> AsyncIterator[CheckEvent]:
    """Stub: yields one event reflecting the stored status so the frontend can wire up SSE."""
    from sqlmodel import Session

    from app.db import get_engine
    from app.models import Version

    with Session(get_engine()) as s:
        v = s.get(Version, version_id)
        stats = dict(v.stats or {}) if v else {}
        status = v.check_status if v else "error"
    stage = "done" if status in ("done", "not_run") else ("error" if status == "error" else "queued")
    yield CheckEvent(
        stage=stage,  # type: ignore[arg-type]
        units_done=int(stats.get("units_done", 0)),
        units_total=int(stats.get("units_total", 0)),
        judgments=int(stats.get("judgments", 0)),
        elapsed_ms=int(stats.get("elapsed_ms", 0)),
        cost_usd=float(stats.get("cost_usd", 0.0)),
        message="Pipeline not implemented yet (stub)." if stage == "queued" else None,
    )
