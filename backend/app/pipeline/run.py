"""Orchestrates one check against the DB and streams progress over SSE. OWNER: pipeline-agent.

Contract with the routers:

    start_check(version_id) -> bool
        Returns immediately (False if that version is already being checked). Works from an async
        request handler, from sync code running in a worker thread (e.g. the demo reset), and at
        startup. Never raises into the caller.

    run_check(version_id)
        Own DB session. version.check_status running -> done | error. If the caller set
        item.state = "checking", it becomes "findings_ready" at the end (also on error, with
        stats.error = true and a human stats.error_message). Items in other states (e.g. a seeded
        item that is in review) keep their state. Findings are written as soon as each is final
        (span offsets into Version.text, rule or fact code, probability, band, explanation, fix,
        fix_verified). AuditEvents: check_started, check_done (or check_failed). AICall rows for
        every external call (none for confidential items).
        version.stats = {units_total, units_done, judgments, elapsed_ms, cost_usd, pii_masked,
        findings_by_band{flag,human,pass}, rule_scan[], tones[], claims{}, warnings[], jev, llm,
        error, error_message}.

    subscribe(version_id) -> AsyncIterator[CheckEvent]
        Live: a progress snapshot, the findings stored so far, then live events until done/error.
        Finished: a summary event (stage "units"), every stored finding, then "done"/"error".

Event order for a live check: queued, units, tier0, (jev|claims|explain|selfcheck)* with findings
(stage "selfcheck"), done | error. Every event carries the running counters.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator
from typing import Any, Optional

from sqlmodel import Session, delete, select

from app.config import get_settings
from app.db import get_engine
from app.models import AICall, Asset, Brand, ContentItem, Fact, Finding, Override, Rule, Term, Unit, Version, utcnow
from app.pipeline import engine as E
from app.pipeline import explain as X
from app.pipeline import jev
from app.pipeline.bands import band as to_band
from app.pipeline.context import CheckContext, FactSpec, RuleSpec, TermSpec, UnitIn
from app.schemas import CheckEvent

log = logging.getLogger("brandgate.run")

_tasks: set[Any] = set()
_running: set[int] = set()
_subs: dict[int, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}
_progress: dict[int, CheckEvent] = {}
_lock = threading.Lock()
_main_loop: Optional[asyncio.AbstractEventLoop] = None
_prerun_started = False

FINDING_STAGE = "selfcheck"
STALE_MESSAGE = "The server restarted while this check was running. Run the check again."
ERROR_MESSAGE = "The check stopped because of an internal error. Code-check findings found so far are shown; try running the check again."


# ------------------------------------------------------------------ scheduling
def bind_loop(loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
    """Remember the server's event loop so sync callers (worker threads) can schedule checks."""
    global _main_loop
    try:
        _main_loop = loop or asyncio.get_running_loop()
    except RuntimeError:
        pass


def is_running(version_id: int) -> bool:
    return version_id in _running


def start_check(version_id: int) -> bool:
    """Schedule run_check(version_id) in the background. Returns False if it is already running."""
    with _lock:
        if version_id in _running:
            return False
        _running.add(version_id)
    try:
        _schedule(run_check(version_id))
        return True
    except Exception:  # noqa: BLE001
        log.exception("could not schedule check for version %s", version_id)
        _running.discard(version_id)
        return False


def _schedule(coro: Any) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        bind_loop(loop)
        task = loop.create_task(coro)
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    elif _main_loop is not None and _main_loop.is_running():
        fut = asyncio.run_coroutine_threadsafe(coro, _main_loop)
        _tasks.add(fut)
        fut.add_done_callback(_tasks.discard)
    else:  # no server loop (CLI, scripts): run on a private loop in a thread
        t = threading.Thread(target=asyncio.run, args=(coro,), daemon=True, name="brandgate-check")
        t.start()


# ------------------------------------------------------------------ pub/sub (thread-safe fan-out)
def _publish(version_id: int, ev: CheckEvent) -> None:
    if ev.finding is None:
        _progress[version_id] = ev
    for loop, q in list(_subs.get(version_id, [])):
        try:
            loop.call_soon_threadsafe(q.put_nowait, ev)
        except RuntimeError:  # subscriber's loop closed
            pass


def _register(version_id: int) -> tuple[asyncio.AbstractEventLoop, asyncio.Queue]:
    entry = (asyncio.get_running_loop(), asyncio.Queue())
    _subs.setdefault(version_id, []).append(entry)
    return entry


def _unregister(version_id: int, entry: tuple) -> None:
    lst = _subs.get(version_id, [])
    if entry in lst:
        lst.remove(entry)
    if not lst:
        _subs.pop(version_id, None)


# ------------------------------------------------------------------ DB -> pipeline inputs
def load_context(s: Session, item: ContentItem, v: Version) -> tuple[CheckContext, list[UnitIn]]:
    brand = s.get(Brand, item.brand_id)
    rules = [RuleSpec(code=r.code, category=r.category, severity=r.severity, statement=r.statement,
                      check_method=r.check_method, params=dict(r.params or {}), source_quote=r.source_quote,
                      page=r.page, version=r.version, examples_violating=list(r.examples_violating or []),
                      examples_ok=list(r.examples_ok or []), enabled=r.enabled)
             for r in s.exec(select(Rule).where(Rule.brand_id == item.brand_id).order_by(Rule.id)).all()]
    terms = [TermSpec(t.kind, t.value, t.replacement) for t in s.exec(select(Term).where(Term.brand_id == item.brand_id)).all()]
    facts = [FactSpec(f.code, f.statement, f.value, f.source, f.valid_until)
             for f in s.exec(select(Fact).where(Fact.brand_id == item.brand_id)).all()]
    palette = [(str(a.meta.get("name", "")), str(a.meta.get("hex", "")))
               for a in s.exec(select(Asset).where(Asset.brand_id == item.brand_id, Asset.kind == "palette")).all()
               if (a.meta or {}).get("hex")]
    units = [UnitIn(id=u.id, kind=u.kind, idx=u.idx, text=u.text or "", char_start=u.char_start,
                    image_path=u.image_path, page=u.page)
             for u in s.exec(select(Unit).where(Unit.version_id == v.id).order_by(Unit.idx)).all()]
    ctx = CheckContext(
        channel=item.channel, rules=rules, terms=terms, facts=facts, palette=palette,
        tone_labels=list(brand.tone_labels or []) if brand else [],
        channel_targets=dict(brand.channel_targets or {}) if brand else {},
        version_kind=v.kind, alt_text=v.alt_text, post_text=v.text or "", confidential=item.confidential,
        version_id=v.id,
    )
    return ctx, units


def _event(stage: str, started: float, res: Optional[E.EngineResult], calls: jev.CallLog, units_total: int,
           finding: Any = None, message: Optional[str] = None) -> CheckEvent:
    return CheckEvent(
        stage=stage,  # type: ignore[arg-type]
        units_done=res.units_done if res else 0,
        units_total=units_total,
        judgments=calls.judgments,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        cost_usd=round(calls.cost_usd, 6),
        finding=finding,
        message=message,
    )


def _rule_scan(res: E.EngineResult) -> list[dict]:
    best: dict[str, E.Judgment] = {}
    for j in res.judgments:
        if j.rule_code and j.kind in ("rule", "image", "cta") and (j.rule_code not in best or j.p > best[j.rule_code].p):
            best[j.rule_code] = j
    return sorted(({"rule_code": c, "category": j.category, "p": round(j.p, 4), "band": to_band(j.p, j.category)}
                   for c, j in best.items()), key=lambda r: -r["p"])


def _stats(res: E.EngineResult, calls: jev.CallLog, ctx: CheckContext, findings: list[Finding], elapsed_ms: int) -> dict:
    by_band = {"flag": 0, "human": 0, "pass": 0}
    for f in findings:
        by_band[f.band] = by_band.get(f.band, 0) + 1
    by_band["pass"] += sum(1 for j in res.judgments if to_band(j.p, j.category) == "pass" and j.kind != "claim")
    by_band["pass"] += sum(1 for c in res.claims if c["status"] == "supported")
    claim_counts: dict[str, int] = {}
    for c in res.claims:
        claim_counts[c["status"]] = claim_counts.get(c["status"], 0) + 1
    if ctx.confidential:
        jev_state = llm_state = "skipped_confidential"
    else:
        jev_state = "used" if res.jev_used else "not_configured"
        llm_state = "used" if res.llm_used else "not_configured"
    return {
        "units_total": res.units_total, "units_done": res.units_done, "judgments": calls.judgments,
        "elapsed_ms": elapsed_ms, "cost_usd": round(calls.cost_usd, 6), "pii_masked": res.pii_masked,
        "findings_by_band": by_band, "rule_scan": _rule_scan(res), "tones": res.tones, "claims": claim_counts,
        "warnings": res.warnings, "jev": jev_state, "llm": llm_state,
        "models": {"jev": jev.model_name() if res.jev_used else None, "llm": X.model_name() if res.llm_used else None},
        "external_calls": len(calls.records), "error": False, "error_message": None,
    }


def _finding_row(version_id: int, d: E.FindingDraft) -> Finding:
    return Finding(
        version_id=version_id, unit_id=d.unit_id, rule_code=d.rule_code, rule_version=d.rule_version,
        fact_code=d.fact_code, tier=d.tier, category=d.category or "", severity=d.severity, band=d.band,
        probability=d.probability, claim_status=d.claim_status, span_start=d.span_start, span_end=d.span_end,
        quote=d.quote or "", explanation=d.explanation, fix=d.fix, fix_verified=d.fix_verified,
    )


def _save_calls(s: Session, calls: jev.CallLog, saved: int) -> int:
    for r in calls.records[saved:]:
        s.add(AICall(version_id=r.version_id, unit_id=r.unit_id, provider=r.provider, model=r.model, purpose=r.purpose,
                     input_tokens=r.input_tokens, latency_ms=r.latency_ms, cost_usd=r.cost_usd, ok=r.ok))
    return len(calls.records)


# ------------------------------------------------------------------ the check
async def run_check(version_id: int, *, use_jev: Optional[bool] = None, use_llm: Optional[bool] = None) -> None:
    from app.services import audit, to_finding_out

    started = time.perf_counter()
    calls = jev.CallLog(version_id=version_id)
    res: Optional[E.EngineResult] = None
    units_total = 0
    saved_calls = 0
    rows: list[Finding] = []
    try:
        with Session(get_engine()) as s:
            v = s.get(Version, version_id)
            item = s.get(ContentItem, v.item_id) if v else None
            if v is None or item is None:
                log.warning("run_check: version %s not found", version_id)
                return
            ctx, units = load_context(s, item, v)
            units_total = len(units)
            old = select(Finding.id).where(Finding.version_id == v.id)
            s.execute(delete(Override).where(Override.finding_id.in_(old)))  # type: ignore[attr-defined]
            s.execute(delete(Finding).where(Finding.version_id == v.id))  # a re-run replaces earlier findings
            v.check_status = "running"
            v.stats = {"units_total": units_total, "units_done": 0, "judgments": 0, "elapsed_ms": 0, "cost_usd": 0.0}
            audit(s, "check_started", item_id=item.id, version_id=v.id,
                  detail={"units": units_total, "confidential": item.confidential})
            s.add(v)
            s.commit()
            brand_id = item.brand_id
            _publish(version_id, _event("queued", started, None, calls, units_total))
            _publish(version_id, _event("units", started, None, calls, units_total))

            async def on_progress(stage: str, r: E.EngineResult) -> None:
                _publish(version_id, _event(stage, started, r, calls, units_total))

            async def on_finding(d: E.FindingDraft) -> None:
                row = _finding_row(version_id, d)
                s.add(row)
                s.commit()
                s.refresh(row)
                rows.append(row)
                _publish(version_id, _event(FINDING_STAGE, started, res_holder[0], calls, units_total,
                                            finding=to_finding_out(s, row, brand_id)))

            res_holder: list[Optional[E.EngineResult]] = [None]

            async def on_progress_wrap(stage: str, r: E.EngineResult) -> None:
                res_holder[0] = r
                await on_progress(stage, r)

            res = await E.analyze(units, ctx, calls=calls, use_jev=use_jev, use_llm=use_llm,
                                  on_finding=on_finding, on_progress=on_progress_wrap)
            elapsed = int((time.perf_counter() - started) * 1000)
            saved_calls = _save_calls(s, calls, saved_calls)
            v = s.get(Version, version_id)
            item = s.get(ContentItem, v.item_id)
            v.stats = _stats(res, calls, ctx, rows, elapsed)
            v.check_status = "done"
            if item.state == "checking":
                item.state = "findings_ready"
                item.updated_at = utcnow()
            audit(s, "check_done", item_id=item.id, version_id=v.id,
                  detail={"findings": len(rows), "findings_by_band": v.stats["findings_by_band"],
                          "elapsed_ms": elapsed, "cost_usd": v.stats["cost_usd"], "warnings": len(res.warnings)})
            s.add(v)
            s.add(item)
            s.commit()
            msg = " ".join(res.warnings) if res.warnings else None
            _publish(version_id, _event("done", started, res, calls, units_total, message=msg))
    except Exception:  # noqa: BLE001
        log.exception("check failed for version %s", version_id)
        _fail(version_id, calls, saved_calls, started, units_total, res)
    finally:
        _running.discard(version_id)
        _progress.pop(version_id, None)


def _fail(version_id: int, calls: jev.CallLog, saved_calls: int, started: float, units_total: int,
          res: Optional[E.EngineResult]) -> None:
    from app.services import audit

    try:
        with Session(get_engine()) as s:
            _save_calls(s, calls, saved_calls)
            v = s.get(Version, version_id)
            if v is None:
                return
            item = s.get(ContentItem, v.item_id)
            v.check_status = "error"
            v.stats = {**(v.stats or {}), "error": True, "error_message": ERROR_MESSAGE, "cost_usd": round(calls.cost_usd, 6),
                       "judgments": calls.judgments, "elapsed_ms": int((time.perf_counter() - started) * 1000)}
            if item is not None and item.state == "checking":
                item.state = "findings_ready"
                item.updated_at = utcnow()
                s.add(item)
            audit(s, "check_failed", item_id=v.item_id, version_id=v.id, detail={"message": ERROR_MESSAGE})
            s.add(v)
            s.commit()
    except Exception:  # noqa: BLE001
        log.exception("could not record the failed check for version %s", version_id)
    _publish(version_id, _event("error", started, res, calls, units_total, message=ERROR_MESSAGE))


# ------------------------------------------------------------------ SSE
def _stored(version_id: int) -> tuple[Optional[str], dict, list[Any]]:
    from app.services import to_finding_out

    with Session(get_engine()) as s:
        v = s.get(Version, version_id)
        if v is None:
            return None, {}, []
        item = s.get(ContentItem, v.item_id)
        rows = s.exec(select(Finding).where(Finding.version_id == version_id, Finding.band != "pass")
                      .order_by(Finding.span_start, Finding.id)).all()
        return v.check_status, dict(v.stats or {}), [to_finding_out(s, f, item.brand_id if item else None) for f in rows]


def _summary(stage: str, stats: dict, message: Optional[str] = None) -> CheckEvent:
    return CheckEvent(stage=stage,  # type: ignore[arg-type]
                      units_done=int(stats.get("units_done", 0) or 0), units_total=int(stats.get("units_total", 0) or 0),
                      judgments=int(stats.get("judgments", 0) or 0), elapsed_ms=int(stats.get("elapsed_ms", 0) or 0),
                      cost_usd=float(stats.get("cost_usd", 0.0) or 0.0), message=message)


def _final_message(status: str, stats: dict) -> Optional[str]:
    if status == "error":
        return stats.get("error_message") or ERROR_MESSAGE
    if status == "not_run":
        return "This version hasn't been checked yet."
    warnings = stats.get("warnings") or []
    return " ".join(warnings) if warnings else None


IDLE_TIMEOUT_S = 90.0


async def subscribe(version_id: int) -> AsyncIterator[CheckEvent]:
    bind_loop()
    entry = _register(version_id)  # before reading the DB, so nothing falls between the two
    try:
        status, stats, findings = _stored(version_id)
        if status is None:
            yield _summary("error", {}, "That version doesn't exist (the demo may have been reset).")
            return
        if status in ("queued", "running") and not is_running(version_id):
            if status == "queued":
                start_check(version_id)  # e.g. created just before a restart: start it now
            else:
                _mark_stale(version_id)
                status, stats, findings = _stored(version_id)
        if status in ("done", "error", "not_run"):
            yield _summary("units", stats, "Showing stored results.")
            for f in findings:
                yield _summary(FINDING_STAGE, stats).model_copy(update={"finding": f})
            yield _summary("done" if status != "error" else "error", stats, _final_message(status, stats))
            return

        # live
        snap = _progress.get(version_id)
        yield snap if snap is not None else _summary("queued", stats)
        sent: set[int] = set()
        for f in findings:
            sent.add(f.id)
            yield (snap or _summary(FINDING_STAGE, stats)).model_copy(update={"stage": FINDING_STAGE, "finding": f})
        q = entry[1]
        while True:
            try:
                ev: CheckEvent = await asyncio.wait_for(q.get(), IDLE_TIMEOUT_S)
            except asyncio.TimeoutError:
                status, stats, _ = _stored(version_id)
                if status in ("done", "error") or not is_running(version_id):
                    yield _summary("done" if status == "done" else "error", stats, _final_message(status or "error", stats))
                    return
                continue
            if ev.finding is not None:
                if ev.finding.id in sent:
                    continue
                sent.add(ev.finding.id)
            yield ev
            if ev.stage in ("done", "error"):
                return
    finally:
        _unregister(version_id, entry)


# ------------------------------------------------------------------ startup
def _mark_stale(version_id: Optional[int] = None) -> int:
    """Checks left 'running' by a previous process can never finish: mark them as errors."""
    n = 0
    with Session(get_engine()) as s:
        q = select(Version).where(Version.check_status == "running")
        if version_id is not None:
            q = q.where(Version.id == version_id)
        for v in s.exec(q).all():
            if v.id in _running:
                continue
            v.check_status = "error"
            v.stats = {**(v.stats or {}), "error": True, "error_message": STALE_MESSAGE}
            item = s.get(ContentItem, v.item_id)
            if item is not None and item.state == "checking":
                item.state = "findings_ready"
                s.add(item)
            s.add(v)
            n += 1
        s.commit()
    return n


def on_startup() -> None:
    """Called from the app lifespan: bind the loop, clean up stale checks, maybe pre-run the demo."""
    bind_loop()
    try:
        n = _mark_stale()
        if n:
            log.info("marked %d stale running checks as errors", n)
    except Exception:  # noqa: BLE001
        log.exception("stale-check cleanup failed")
    start_prerun()


def start_prerun() -> bool:
    """DEMO_MODE + a Jev key: check every seeded version that was never checked, once, in the
    background and one at a time, so the dashboard has real data. Idempotent: only versions with
    check_status "not_run" are picked up, and only once per process. Item states are untouched."""
    global _prerun_started
    s = get_settings()
    if _prerun_started or not s.demo_mode or not s.jev_configured:
        return False
    _prerun_started = True

    async def _go() -> None:
        with Session(get_engine()) as sess:
            ids = [v.id for v in sess.exec(select(Version).where(Version.check_status == "not_run").order_by(Version.id)).all()]
        log.info("demo pre-run: checking %d seeded versions", len(ids))
        for vid in ids:
            with _lock:
                if vid in _running:
                    continue
                _running.add(vid)
            await run_check(vid)

    try:
        _schedule(_go())
        return True
    except Exception:  # noqa: BLE001
        log.exception("demo pre-run could not start")
        return False


def reset_prerun_flag() -> None:
    """For the demo reset: allow one more pre-run of the freshly seeded items."""
    global _prerun_started
    _prerun_started = False
