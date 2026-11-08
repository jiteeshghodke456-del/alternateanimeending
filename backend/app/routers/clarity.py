"""Round 3 improvement 2: unclear-rule detection with rewrites. OWNER: AI/ML (Jiteesh).
Scoring lives in pipeline/clarity.py (pure, unit-tested); question wording in pipeline/questions.py.

    GET  /rules/clarity                 every rule of the brand, least clear first (any signed-in user)
    GET  /rules/clarity/run             progress of the run-all job of this workspace (poll)
    POST /rules/clarity/run             (admin) score every rule in the background -> 202 + progress
    POST /rules/{id}/clarity            (admin) score one rule now (sync, ~2-6 s)
    POST /rules/{id}/rewrite            (admin) suggest a clearer wording + its measured effect (sync, ~4-10 s)
    POST /rules/{id}/rewrite/apply      (admin) the rewrite becomes a new rule version (old one kept in history)
    POST /rules/{id}/rewrite/dismiss    (admin) drop the suggestion

The vague-wording and reviewer signals are recomputed on every GET (cheap, local); the Jev signal is stored
per rule version and marked stale when the rule changes. Without Jev, scores use the other two signals
(`partial: true`). Without an LLM, paraphrases are templates and probes are the rule's own examples; the
rewrite needs the LLM (503 otherwise). Every external call is logged to AICall with purpose "clarity".
Nothing from content items is sent anywhere: probes are synthetic, findings are only counted.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, select

from app import workspaces as W
from app.db import get_engine
from app.deps import AdminUser, CurrentUser, SessionDep
from app.models import AICall, Finding, Precedent, Rule, RuleClarity, utcnow
from app.pipeline import clarity as C
from app.pipeline import explain, jev
from app.pipeline import neutral as N
from app.pipeline import questions as Q
from app.pipeline import run as pipeline_run
from app.services import NoBrandError, audit, current_brand

log = logging.getLogger("brandgate.clarity")
router = APIRouter(tags=["clarity"])

RULE_CONCURRENCY = 6  # rules scored at once in a run-all (Jev and the LLM have their own semaphores)
NO_LLM_MESSAGE = "Suggesting a rewrite needs the writing assistant, and it isn't configured on this server."
NO_REWRITE_MESSAGE = "There's no suggested rewrite for this rule. Ask for one first."
OLD_REWRITE_MESSAGE = "The rule changed after this rewrite was suggested. Ask for a new one."
FAILED_REWRITE_MESSAGE = "The writing assistant couldn't produce a valid rewrite. Try again in a moment."


# ------------------------------------------------------------------ API shapes
class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ClarityReasons(_Out):
    vague_terms: list[str]
    disagreement: Optional[float]  # 0-1 unclarity from Jev self-disagreement (None = not measured)
    dismiss_rate: Optional[float]
    override_rate: Optional[float]
    human_share: Optional[float]
    findings: int
    notes: list[str]  # plain-language reasons, most important first


class RewriteOut(_Out):
    statement: str
    example_follows: str
    example_breaks: str
    what_changed: str
    for_version: int
    score_before: float
    score_after: float
    band_after: str
    disagreement_before: Optional[float]
    disagreement_after: Optional[float]
    spread_before: Optional[float]
    spread_after: Optional[float]
    mismatch_before: Optional[float]
    mismatch_after: Optional[float]
    vague_terms_after: list[str]
    intent_changed: Optional[float]  # Jev, high = the rewrite may ask for something else
    intent_warning: bool
    created_at: datetime


class RuleClarityOut(_Out):
    rule_id: int
    code: str
    statement: str
    category: str
    version: int
    enabled: bool
    score: float
    band: str  # clear|could_be_clearer|unclear
    band_label: str
    reasons: ClarityReasons
    signals: dict[str, Any]
    measured: bool  # the Jev signal is in the score
    partial: bool  # a signal is missing (no Jev answer yet, or no findings)
    stale: bool  # the Jev signal is missing or belongs to an older rule version
    computed_at: Optional[datetime]
    paraphrases: list[str]
    paraphrase_source: str
    probe_source: str
    rewrite: Optional[RewriteOut]
    history: list[dict[str, Any]]
    error: Optional[str]


class ClarityRunOut(_Out):
    status: str  # idle|running|done|error
    done: int = 0
    total: int = 0
    failed: int = 0
    cost_usd: float = 0.0
    elapsed_ms: int = 0
    error: Optional[str] = None


# ------------------------------------------------------------------ table + helpers
_tables_ok: set = set()


def _ensure_table() -> None:
    """Workspaces copied from a template built before this table existed get it on first use."""
    k = W.key("clarity-table")
    if k in _tables_ok:
        return
    RuleClarity.__table__.create(get_engine(), checkfirst=True)  # type: ignore[attr-defined]
    _tables_ok.add(k)


def _row(session: Session, rule_id: int) -> Optional[RuleClarity]:
    return session.exec(select(RuleClarity).where(RuleClarity.rule_id == rule_id)).first()


def _rule_or_404(session: Session, rule_id: int) -> Rule:
    rule = session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="That rule doesn't exist.")
    return rule


def _has_examples(rule: Rule) -> bool:
    return bool(rule.examples_ok) and bool(rule.examples_violating)


def _people(session: Session, rule: Rule) -> C.PeopleResult:
    q = select(Finding.id, Finding.status, Finding.dismiss_reason, Finding.band, Finding.band_before, Finding.rule_version
               ).where(Finding.rule_code == rule.code)
    rows = [(i, s, r, b, bb) for i, s, r, b, bb, v in session.exec(q).all() if (v or 1) == rule.version]
    # precedent memory: decisions whose finding a re-check replaced still count (once)
    pq = select(Precedent.finding_id, Precedent.decision, Precedent.reason, Precedent.band).where(
        Precedent.rule_code == rule.code, Precedent.brand_id == rule.brand_id, Precedent.rule_version == rule.version)
    return C.people_signal(C.people_rows(rows, [tuple(x) for x in session.exec(pq).all()]))


def _current_disagreement(rule: Rule, row: Optional[RuleClarity]) -> Optional[dict[str, Any]]:
    if row is None or row.rule_version != rule.version:
        return None
    return (row.signals or {}).get("disagreement")


def _d_from_dict(d: Optional[dict[str, Any]]) -> Optional[C.DisagreementResult]:
    if not d:
        return None
    fields = C.DisagreementResult.__dataclass_fields__
    return C.DisagreementResult(**{k: v for k, v in d.items() if k in fields})


def build_out(session: Session, rule: Rule, row: Optional[RuleClarity]) -> RuleClarityOut:
    v = C.vague_signal(rule.statement, _has_examples(rule))
    people = _people(session, rule)
    d_dict = _current_disagreement(rule, row)
    d = _d_from_dict(d_dict)
    score, band = C.combine(v.score, d.score if d else None, people.score)
    notes = C.reasons(v, d, people)
    rw = None
    if row is not None and row.rewrite and row.rewrite.get("for_version") == rule.version:
        rw = RewriteOut.model_validate(row.rewrite)
    return RuleClarityOut(
        rule_id=rule.id, code=rule.code, statement=rule.statement, category=rule.category, version=rule.version,
        enabled=rule.enabled, score=score, band=band, band_label=C.BAND_LABEL[band],
        reasons=ClarityReasons(vague_terms=[h["term"] for h in v.hits], disagreement=d.score if d else None,
                               dismiss_rate=people.dismiss_rate, override_rate=people.override_rate,
                               human_share=people.human_share, findings=people.findings, notes=notes),
        signals={"vague": C.as_dict(v), "disagreement": d_dict, "people": C.as_dict(people),
                 "weights": C.WEIGHTS, "bands": {"clear_at": C.CLEAR_AT, "unclear_below": C.UNCLEAR_BELOW}},
        measured=d is not None, partial=d is None or people.score is None, stale=d is None,
        computed_at=row.computed_at if row is not None else None,
        paraphrases=list(row.paraphrases or []) if row is not None and row.paraphrase_version == rule.version else [],
        paraphrase_source=(row.paraphrase_source if row is not None else "") or "",
        probe_source=(row.probe_source if row is not None else "") or "",
        rewrite=rw, history=list(row.history or []) if row is not None else [],
        error=row.error if row is not None else None,
    )


def _save_calls(calls: jev.CallLog, saved: int = 0) -> int:
    with Session(get_engine()) as s:
        for r in calls.records[saved:]:
            s.add(AICall(version_id=None, unit_id=None, provider=r.provider, model=r.model, purpose=r.purpose,
                         input_tokens=r.input_tokens, latency_ms=r.latency_ms, cost_usd=r.cost_usd, ok=r.ok))
        s.commit()
    return len(calls.records)


def _brand_name(session: Session) -> str:
    try:
        return current_brand(session).name
    except NoBrandError:
        return ""


# ------------------------------------------------------------------ scoring one rule (no session held across awaits)
async def score_rule(rule_id: int, calls: jev.CallLog) -> Optional[str]:
    """Measure the Jev signal for one rule and store it. Returns an error message or None."""
    t0 = time.perf_counter()
    with Session(get_engine()) as s:
        rule = s.get(Rule, rule_id)
        if rule is None:
            return "rule not found"
        row = _row(s, rule_id)
        brand = _brand_name(s)
        statement, category, version = rule.statement, rule.category, rule.version
        ex_ok, ex_bad = list(rule.examples_ok or []), list(rule.examples_violating or [])
        probes = [C.Probe(p["text"], p["expect"]) for p in (row.probes if row else []) or []]
        probe_source = row.probe_source if row else ""
        paraphrases = list(row.paraphrases or []) if row and row.paraphrase_version == version else []
        para_source = row.paraphrase_source if row and paraphrases else ""
    cost0 = calls.cost_usd
    err: Optional[str] = None
    # paraphrases (per rule version) + probes (once per rule): one LLM call gives both
    need_llm = (not paraphrases or para_source != "llm") or (not probes or probe_source != "llm")
    if need_llm and explain.available():
        gen = await C.generate_probe_set(statement, category, brand, calls)
        if gen is not None:
            if not paraphrases or para_source != "llm":
                paraphrases, para_source = gen.paraphrases, "llm"
            if not probes or probe_source != "llm":
                probes, probe_source = [C.Probe(p.text, p.expect) for p in gen.probes], "llm"
        else:
            err = "The writing assistant didn't return usable test texts; fell back to the rule's own examples."
    if not paraphrases:
        paraphrases, para_source = C.template_paraphrases(statement), "template"
    if not probes:
        probes, probe_source = C.fallback_probes(ex_ok, ex_bad), "examples"
    d: Optional[C.DisagreementResult] = None
    if jev.available() and len(probes) >= 2:
        answers = await C.measure([statement, *paraphrases], probes, field=C.probe_field(category), calls=calls)
        d = C.disagreement_signal(answers, probes)
        if d is None:
            err = "The AI check didn't answer (every call failed); the score uses the other signals."
    elif not jev.available():
        err = "The AI check isn't configured, so the score uses wording and reviewer signals only."
    elif len(probes) < 2:
        err = "No test texts for this rule (no writing assistant and no stored examples)."
    with Session(get_engine()) as s:
        rule = s.get(Rule, rule_id)
        if rule is None or rule.version != version:
            return "the rule changed while it was being scored"
        row = _row(s, rule_id) or RuleClarity(rule_id=rule_id)
        row.rule_code, row.rule_version = rule.code, version
        row.paraphrases, row.paraphrase_version, row.paraphrase_source = paraphrases, version, para_source
        row.probes, row.probe_source = [{"text": p.text, "expect": p.expect} for p in probes], probe_source
        row.signals = {**(row.signals or {}), "disagreement": C.as_dict(d)}
        row.error, row.computed_at = err, utcnow()
        row.cost_usd = round(calls.cost_usd - cost0, 6)
        row.latency_ms = int((time.perf_counter() - t0) * 1000)
        s.add(row)
        s.flush()
        out = build_out(s, rule, row)
        row.score, row.band, row.reasons = out.score, out.band, out.reasons.notes
        s.add(row)
        s.commit()
    return err if d is None else None


# ------------------------------------------------------------------ run-all (background, polled)
_runs: dict[Any, dict[str, Any]] = {}


def _busy(ws_id: Optional[str]) -> bool:
    for k, r in list(_runs.items()):
        mine = (not isinstance(k, tuple)) if ws_id is None else (isinstance(k, tuple) and k[0] == ws_id)
        if mine and r.get("status") == "running":
            return True
    return False


W.busy_checks.append(_busy)


async def run_all(rule_ids: list[int], state: dict[str, Any]) -> None:
    calls = jev.CallLog()
    sem = asyncio.Semaphore(RULE_CONCURRENCY)
    t0 = time.perf_counter()
    saved = 0

    async def one(rid: int) -> None:
        nonlocal saved
        async with sem:
            try:
                err = await score_rule(rid, calls)
            except Exception:  # noqa: BLE001
                log.exception("clarity: rule %s failed", rid)
                err = "internal error"
            if err and "isn't configured" not in err:
                state["failed"] += 1
            state["done"] += 1
            state["cost_usd"] = round(calls.cost_usd, 6)
            state["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
            saved = _save_calls(calls, saved)

    try:
        await asyncio.gather(*(one(r) for r in rule_ids))
        state["status"] = "done"
    except Exception:  # noqa: BLE001
        log.exception("clarity run failed")
        state["status"], state["error"] = "error", "The clarity run stopped because of an internal error. Try again."
    finally:
        state["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
        state["cost_usd"] = round(calls.cost_usd, 6)
        try:
            _save_calls(calls, saved)
        except Exception:  # noqa: BLE001
            log.exception("clarity: could not save AI calls")
        log.info("clarity run: %d rules, %d failed, $%.5f, %d ms", state["total"], state["failed"], state["cost_usd"],
                 state["elapsed_ms"])


# ------------------------------------------------------------------ endpoints
@router.get("/rules/clarity", response_model=list[RuleClarityOut])
def list_clarity(session: SessionDep, user: CurrentUser) -> list[RuleClarityOut]:
    _ensure_table()
    try:
        brand = current_brand(session)
    except NoBrandError:
        return []
    rules = session.exec(select(Rule).where(Rule.brand_id == brand.id).order_by(Rule.code)).all()
    rows = {r.rule_id: r for r in session.exec(select(RuleClarity)).all()}
    out = [build_out(session, r, rows.get(r.id)) for r in rules]
    return sorted(out, key=lambda o: (o.score, o.code))


@router.get("/rules/clarity/run", response_model=ClarityRunOut)
def clarity_run_status(user: CurrentUser) -> ClarityRunOut:
    st = _runs.get(W.key("clarity-run"))
    return ClarityRunOut(**st) if st else ClarityRunOut(status="idle")


@router.post("/rules/clarity/run", response_model=ClarityRunOut, status_code=202)
def start_clarity_run(session: SessionDep, user: AdminUser) -> ClarityRunOut:
    _ensure_table()
    k = W.key("clarity-run")
    cur = _runs.get(k)
    if cur and cur.get("status") == "running":
        return ClarityRunOut(**cur)
    try:
        brand = current_brand(session)
    except NoBrandError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    ids = [r.id for r in session.exec(select(Rule).where(Rule.brand_id == brand.id)).all()]
    state: dict[str, Any] = {"status": "running" if ids else "done", "done": 0, "total": len(ids), "failed": 0,
                             "cost_usd": 0.0, "elapsed_ms": 0, "error": None}
    _runs[k] = state
    if ids:
        audit(session, "clarity_run", user_id=user.id, detail={"rules": len(ids)})
        session.commit()
        pipeline_run.schedule(run_all(ids, state))
    return ClarityRunOut(**state)


@router.post("/rules/{rule_id}/clarity", response_model=RuleClarityOut)
async def score_one(rule_id: int, session: SessionDep, user: AdminUser) -> RuleClarityOut:
    _ensure_table()
    _rule_or_404(session, rule_id)
    calls = jev.CallLog()
    try:
        await score_rule(rule_id, calls)
    finally:
        _save_calls(calls)
    session.expire_all()
    rule = _rule_or_404(session, rule_id)
    return build_out(session, rule, _row(session, rule_id))


@router.post("/rules/{rule_id}/rewrite", response_model=RuleClarityOut)
async def suggest_rewrite(rule_id: int, session: SessionDep, user: AdminUser) -> RuleClarityOut:
    _ensure_table()
    rule = _rule_or_404(session, rule_id)
    if not explain.available():
        raise HTTPException(status_code=503, detail=NO_LLM_MESSAGE)
    calls = jev.CallLog()
    try:
        row = _row(session, rule_id)
        if jev.available() and _current_disagreement(rule, row) is None:
            await score_rule(rule_id, calls)  # the "before" measurement, on the same probes as "after"
            session.expire_all()
            rule = _rule_or_404(session, rule_id)
            row = _row(session, rule_id)
        before = build_out(session, rule, row)
        brand = _brand_name(session)
        why = before.reasons.notes[:4]
        best: Optional[C.RewriteOut] = None
        intent: Optional[float] = None
        feedback = None
        for _ in range(2):
            rw = await C.suggest_rewrite(rule.statement, rule.category, brand, why, list(rule.examples_ok or []),
                                         list(rule.examples_violating or []), calls, feedback)
            if rw is None:
                break
            p = await C.intent_changed(rule.statement, rw.statement, calls) if jev.available() else None
            if best is None or (p is not None and (intent is None or p < intent)):
                best, intent = rw, p
            if p is None or p < Q.CLARITY_INTENT_CHANGED_AT:
                break
            feedback = "it asks writers for something the original rule doesn't. Keep the original intent and scope."
        if best is None:
            raise HTTPException(status_code=502, detail=FAILED_REWRITE_MESSAGE)
        probes = [C.Probe(p["text"], p["expect"]) for p in (row.probes if row else []) or []]
        d_before = _d_from_dict(_current_disagreement(rule, row))
        d_after: Optional[C.DisagreementResult] = None
        if jev.available() and len(probes) >= 2:
            answers = await C.measure([best.statement, *best.paraphrases], probes, field=C.probe_field(rule.category),
                                      calls=calls)
            d_after = C.disagreement_signal(answers, probes)
        v_after = C.vague_signal(best.statement, True)
        score_after, band_after = C.combine(v_after.score, d_after.score if d_after else None, None)
        payload = {
            "statement": best.statement, "example_follows": best.example_follows, "example_breaks": best.example_breaks,
            "what_changed": N.scrub(best.what_changed, rule.statement) or best.what_changed, "paraphrases": best.paraphrases, "for_version": rule.version,
            "score_before": before.score, "score_after": score_after, "band_after": band_after,
            "disagreement_before": d_before.score if d_before else None,
            "disagreement_after": d_after.score if d_after else None,
            "spread_before": d_before.mean_spread if d_before else None,
            "spread_after": d_after.mean_spread if d_after else None,
            "mismatch_before": d_before.mismatch_rate if d_before else None,
            "mismatch_after": d_after.mismatch_rate if d_after else None,
            "vague_terms_after": [h["term"] for h in v_after.hits],
            "intent_changed": None if intent is None else round(intent, 3),
            "intent_warning": intent is not None and intent >= Q.CLARITY_INTENT_CHANGED_AT,
            "after_signals": {"vague": C.as_dict(v_after), "disagreement": C.as_dict(d_after)},
            "created_at": utcnow().isoformat(),
        }
        row = _row(session, rule_id) or RuleClarity(rule_id=rule_id, rule_code=rule.code, rule_version=rule.version)
        row.rewrite = payload
        session.add(row)
        audit(session, "rule_rewrite_suggested", user_id=user.id,
              detail={"code": rule.code, "version": rule.version, "score_before": before.score, "score_after": score_after})
        session.commit()
        session.refresh(row)
        return build_out(session, rule, row)
    finally:
        _save_calls(calls)


@router.post("/rules/{rule_id}/rewrite/apply", response_model=RuleClarityOut)
def apply_rewrite(rule_id: int, session: SessionDep, user: AdminUser) -> RuleClarityOut:
    _ensure_table()
    rule = _rule_or_404(session, rule_id)
    row = _row(session, rule_id)
    if row is None or not row.rewrite:
        raise HTTPException(status_code=409, detail=NO_REWRITE_MESSAGE)
    rw = row.rewrite
    if rw.get("for_version") != rule.version:
        raise HTTPException(status_code=409, detail=OLD_REWRITE_MESSAGE)
    before = build_out(session, rule, row)
    old = {"version": rule.version, "statement": rule.statement, "examples_ok": list(rule.examples_ok or []),
           "examples_violating": list(rule.examples_violating or []), "vague_flag": bool((rule.params or {}).get("vague")),
           "score": before.score, "band": before.band, "replaced_at": utcnow().isoformat(), "replaced_by": user.id,
           "what_changed": rw.get("what_changed", "")}
    rule.statement = rw["statement"]
    rule.examples_ok = [rw["example_follows"], *[x for x in (rule.examples_ok or []) if x != rw["example_follows"]]][:4]
    rule.examples_violating = [rw["example_breaks"], *[x for x in (rule.examples_violating or []) if x != rw["example_breaks"]]][:4]
    # a rewritten rule is no longer "vague": it creates findings like any other Jev rule (DECISIONS "vague rules")
    rule.params = {k: v for k, v in (rule.params or {}).items() if k != "vague"}
    rule.version += 1
    rule.updated_at = utcnow()
    session.add(rule)
    # the rewrite was measured on the same probes: it becomes the current Jev signal of the new version
    after = (rw.get("after_signals") or {}).get("disagreement")
    row.history = [*(row.history or []), old]
    row.rule_version = rule.version
    row.paraphrases, row.paraphrase_version, row.paraphrase_source = list(rw.get("paraphrases") or []), rule.version, "llm"
    row.signals = {**(row.signals or {}), "disagreement": after}
    row.rewrite = None
    row.computed_at, row.error = utcnow(), None
    session.add(row)
    audit(session, "rule_rewritten", user_id=user.id,
          detail={"code": rule.code, "from_version": old["version"], "to_version": rule.version,
                  "old_statement": old["statement"], "new_statement": rule.statement,
                  "score_before": before.score, "score_after": rw.get("score_after")})
    session.commit()
    session.refresh(rule)
    session.refresh(row)
    out = build_out(session, rule, row)
    row.score, row.band, row.reasons = out.score, out.band, out.reasons.notes
    session.add(row)
    session.commit()
    return out


@router.post("/rules/{rule_id}/rewrite/dismiss", response_model=RuleClarityOut)
def dismiss_rewrite(rule_id: int, session: SessionDep, user: AdminUser) -> RuleClarityOut:
    _ensure_table()
    rule = _rule_or_404(session, rule_id)
    row = _row(session, rule_id)
    if row is not None and row.rewrite:
        audit(session, "rule_rewrite_dismissed", user_id=user.id, detail={"code": rule.code, "version": rule.version})
        row.rewrite = None
        session.add(row)
        session.commit()
        session.refresh(row)
    return build_out(session, rule, row)
