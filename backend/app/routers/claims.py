"""Claims ledger + fact impact (Round 3, improvement 1). Logic lives in app/ledger.py.

GET  /claims                         every role: ledger grouped by fact (filters: status, channel, state, fact, all_versions)
GET  /claims/expiring?days=14        every role: facts expiring soon / expired that content relies on
GET  /facts/{id}/impact              every role: items whose latest version relies on the fact
POST /facts/{id}/impact/send-back    Brand Admin: send affected items back for review (+ re-check)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import Session, select

from app import ledger as L
from app.deps import AdminUser, CurrentUser, SessionDep
from app.models import ClaimRecord, ContentItem, Fact, Version
from app.pipeline import run as pipeline_run
from app.schemas import (
    ClaimsLedgerOut,
    ExpiringOut,
    FactImpactOut,
    LedgerGroup,
    SendBackIn,
    SendBackOut,
)
from app.services import NoBrandError, current_brand

router = APIRouter(tags=["claims"])

STATUSES = ("supported", "unsupported", "contradicted", "outdated")


def _brand(session: Session):  # noqa: ANN202
    try:
        return current_brand(session)
    except NoBrandError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _fact_or_404(session: Session, fact_id: int) -> Fact:
    f = session.get(Fact, fact_id)
    if f is None:
        raise HTTPException(status_code=404, detail="That fact doesn't exist.")
    return f


@router.get("/claims", response_model=ClaimsLedgerOut)
def claims_ledger(session: SessionDep, user: CurrentUser, status: Optional[str] = None, channel: Optional[str] = None,
                  state: Optional[str] = None, fact: Optional[str] = None, all_versions: bool = False) -> ClaimsLedgerOut:
    if status and status not in STATUSES:
        raise HTTPException(status_code=422, detail=f"Unknown claim status “{status[:30]}”. Use one of: {', '.join(STATUSES)}.")
    brand = _brand(session)
    items = {i.id: i for i in session.exec(select(ContentItem).where(ContentItem.brand_id == brand.id)).all()}
    lv = L.latest_versions(session, brand.id)
    versions = {v.id: v for v in session.exec(select(Version)).all() if v.item_id in items}
    vids = list(versions) if all_versions else [v.id for v in lv.values()]
    recs = session.exec(select(ClaimRecord).where(ClaimRecord.version_id.in_(vids))  # type: ignore[attr-defined]
                        .order_by(ClaimRecord.item_id, ClaimRecord.version_id, ClaimRecord.span_start)).all() if vids else []
    facts = {f.code: f for f in session.exec(select(Fact).where(Fact.brand_id == brand.id)).all()}
    changed = L.fact_changed_at(session)

    groups: dict[Optional[str], list] = {}
    totals = {s: 0 for s in STATUSES}
    seen_items: set[int] = set()
    for r in recs:
        it, v = items.get(r.item_id), versions.get(r.version_id)
        if it is None or v is None:
            continue
        if (status and r.status != status) or (channel and it.channel != channel) or (state and it.state != state):
            continue
        if fact and not L.relies_on(r, fact):
            continue
        row = L.to_ledger_claim(r, it, v, lv.get(it.id) is not None and lv[it.id].id == v.id, facts, changed)
        totals[r.status] = totals.get(r.status, 0) + 1
        seen_items.add(it.id)
        codes = [c for c in (r.fact_codes or ([r.fact_code] if r.fact_code else [])) if c in facts] or [None]
        for c in codes:  # a claim backed by two facts is listed under both
            groups.setdefault(c, []).append(row)

    out: list[LedgerGroup] = []
    for code, rows in groups.items():
        latest_items = {x.item_id: x.item_state for x in rows if x.latest}
        approved = sum(1 for s in latest_items.values() if s == "approved")
        in_progress = sum(1 for s in latest_items.values() if s in L.IN_PROGRESS)
        out.append(LedgerGroup(fact=L.ledger_fact(facts[code]) if code else None, items_approved=approved,
                               items_in_progress=in_progress, claims=rows))

    def key(g: LedgerGroup) -> tuple:
        if g.fact is None:
            return (1, 0, "")
        d = g.fact.days_left
        return (0, d if d is not None else 10**6, g.fact.code)  # expired first, then soonest to expire

    out.sort(key=key)
    checked = sum(1 for v in lv.values() if v.check_status == "done")
    return ClaimsLedgerOut(groups=out, totals=totals, items=len(seen_items), checked_items=checked, expiring_days=L.EXPIRING_DAYS)


@router.get("/claims/expiring", response_model=ExpiringOut)
def claims_expiring(session: SessionDep, user: CurrentUser, days: int = Query(default=L.EXPIRING_DAYS, ge=0, le=365)) -> ExpiringOut:
    brand = _brand(session)
    soon, gone = L.expiring(session, brand.id, days)
    return ExpiringOut(days=days, expiring=soon, expired=gone)


@router.get("/facts/{fact_id}/impact", response_model=FactImpactOut)
def fact_impact(fact_id: int, session: SessionDep, user: CurrentUser) -> FactImpactOut:
    return L.impact(session, _fact_or_404(session, fact_id))


@router.post("/facts/{fact_id}/impact/send-back", response_model=SendBackOut)
def fact_send_back(fact_id: int, body: SendBackIn, session: SessionDep, user: AdminUser) -> SendBackOut:
    f = _fact_or_404(session, fact_id)
    reason, results, to_check = L.send_back(session, f, user, recheck=body.recheck, note=body.note,
                                            is_running=pipeline_run.is_running)
    for vid in to_check:
        pipeline_run.start_check(vid)
    moved = sum(1 for r in results if r.to_state != r.from_state and r.to_state == "changes_requested")
    return SendBackOut(fact_code=f.code, reason=reason, results=results, moved=moved,  # type: ignore[arg-type]
                       rechecked=sum(1 for r in results if r.rechecked))
