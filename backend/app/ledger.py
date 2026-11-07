"""Claims ledger and fact impact (Round 3, improvement 1). No FastAPI imports here.

The ledger is the ClaimRecord rows a check writes (pipeline/run.py). What an item "relies on" is
always read from its LATEST version: older versions stay in the ledger as history only.

Fact impact (a fact expired, is about to, or was edited after content was checked against it):
- `impact_items()` lists every item whose latest version relies on the fact.
- `send_back()` (Brand Admin) moves them back into the server-enforced workflow:
    approved / in_review                      -> changes_requested  (the creator must resubmit a new
                                                  version, which is checked again before it can be
                                                  submitted and approved again)
    draft / findings_ready / changes_requested -> state kept, the latest version is re-checked
    checking / rejected                       -> left alone (a check is running / not published)
  Every moved item gets an AuditEvent `fact_expired` | `fact_changed` | `fact_expiring` (detail:
  fact, from, to, version, note) and a comment the creator sees. With `recheck`, the latest version
  of every affected item is re-checked against the current facts (its state is kept), so the
  outdated claim shows up as a finding.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from app.models import AuditEvent, ClaimRecord, Comment, ContentItem, Fact, User, Version
from app.clock import local_today
from app.schemas import (
    ExpiringFact,
    FactImpactOut,
    ImpactItem,
    LedgerClaim,
    LedgerFact,
    SendBackResult,
)

EXPIRING_DAYS = 14
CHANGED_FIELDS = ("statement", "value")  # an expiry edit is reported as expired/expiring instead
IN_PROGRESS = ("draft", "checking", "findings_ready", "in_review", "changes_requested")


def today() -> date:
    return local_today()


def days_left(valid_until: Optional[date], on: Optional[date] = None) -> Optional[int]:
    """Days until the fact expires (0 = last valid day is today, negative = expired)."""
    return None if valid_until is None else (valid_until - (on or today())).days


def is_expired(valid_until: Optional[date], on: Optional[date] = None) -> bool:
    d = days_left(valid_until, on)
    return d is not None and d < 0


def ledger_fact(f: Fact) -> LedgerFact:
    return LedgerFact(id=f.id, code=f.code, statement=f.statement, value=f.value, source=f.source,
                      valid_until=f.valid_until, expired=is_expired(f.valid_until), days_left=days_left(f.valid_until))


def _utc(d: Optional[datetime]) -> Optional[datetime]:
    return None if d is None else (d if d.tzinfo else d.replace(tzinfo=timezone.utc))


def fact_changed_at(session: Session) -> dict[str, datetime]:
    """Last edit of each fact's statement or value (from the `fact_updated` audit events)."""
    out: dict[str, datetime] = {}
    for ev in session.exec(select(AuditEvent).where(AuditEvent.action == "fact_updated").order_by(AuditEvent.created_at)).all():
        d = ev.detail or {}
        changed = d.get("changed")
        if d.get("code") and (changed is None or any(c in CHANGED_FIELDS for c in changed)):
            out[str(d["code"])] = _utc(ev.created_at)  # type: ignore[assignment]
    return out


def latest_versions(session: Session, brand_id: int) -> dict[int, Version]:
    """item_id -> latest Version, for every item of the brand."""
    items = set(session.exec(select(ContentItem.id).where(ContentItem.brand_id == brand_id)).all())
    out: dict[int, Version] = {}
    for v in session.exec(select(Version).order_by(Version.item_id, Version.n)).all():
        if v.item_id in items:
            out[v.item_id] = v  # ascending n: the last one wins
    return out


def relies_on(r: ClaimRecord, code: str) -> bool:
    return r.fact_code == code or code in (r.fact_codes or [])


def is_stale(r: ClaimRecord, facts: dict[str, Fact], changed: dict[str, datetime]) -> bool:
    """The fact behind a claim expired or was edited after this check: the claim must be checked again."""
    codes = list(r.fact_codes or ([r.fact_code] if r.fact_code else []))
    for c in codes:
        f = facts.get(c)
        if f is None:
            return True  # the fact was deleted
        if r.status in ("supported", "contradicted") and is_expired(f.valid_until):
            return True
        if c in changed and _utc(r.created_at) is not None and changed[c] > _utc(r.created_at):  # type: ignore[operator]
            return True
    return False


def to_ledger_claim(r: ClaimRecord, item: ContentItem, v: Version, latest: bool, facts: dict[str, Fact],
                    changed: dict[str, datetime]) -> LedgerClaim:
    return LedgerClaim(
        id=r.id, item_id=item.id, item_title=item.title, item_state=item.state, channel=item.channel,  # type: ignore[arg-type]
        version_id=v.id, version_n=v.n, latest=latest, text=r.text, span_start=r.span_start, span_end=r.span_end,
        status=r.status, via=r.via, fact_code=r.fact_code, fact_codes=list(r.fact_codes or []),  # type: ignore[arg-type]
        support_p=r.support_p, fact_valid_until=r.fact_valid_until, stale=is_stale(r, facts, changed))


def latest_records(session: Session, brand_id: int) -> tuple[dict[int, Version], list[ClaimRecord]]:
    lv = latest_versions(session, brand_id)
    ids = [v.id for v in lv.values()]
    recs = session.exec(select(ClaimRecord).where(ClaimRecord.version_id.in_(ids))).all() if ids else []  # type: ignore[attr-defined]
    return lv, list(recs)


def fact_usage(session: Session, brand_id: int) -> dict[str, tuple[set[int], set[int]]]:
    """fact code -> (items relying on it, of which approved), from latest versions only."""
    lv, recs = latest_records(session, brand_id)
    items = {i.id: i for i in session.exec(select(ContentItem).where(ContentItem.brand_id == brand_id)).all()}
    out: dict[str, tuple[set[int], set[int]]] = {}
    for r in recs:
        it = items.get(r.item_id)
        if it is None:
            continue
        for c in set(r.fact_codes or ([r.fact_code] if r.fact_code else [])):
            all_, appr = out.setdefault(c, (set(), set()))
            all_.add(it.id)
            if it.state == "approved":
                appr.add(it.id)
    return out


def expiring(session: Session, brand_id: int, days: int = EXPIRING_DAYS) -> tuple[list[ExpiringFact], list[ExpiringFact]]:
    """(facts expiring within `days`, expired facts) that content still relies on, soonest first."""
    usage = fact_usage(session, brand_id)
    soon: list[ExpiringFact] = []
    gone: list[ExpiringFact] = []
    for f in session.exec(select(Fact).where(Fact.brand_id == brand_id).order_by(Fact.valid_until, Fact.code)).all():
        d = days_left(f.valid_until)
        all_, appr = usage.get(f.code, (set(), set()))
        if d is None or not all_:
            continue
        row = ExpiringFact(fact=ledger_fact(f), items_approved=len(appr), items_in_progress=len(all_ - appr))
        if d < 0:
            gone.append(row)
        elif d <= days:
            soon.append(row)
    return soon, gone


# ------------------------------------------------------------------ impact
def impact_reason(f: Fact, records: list[ClaimRecord], changed: dict[str, datetime]) -> str:
    if is_expired(f.valid_until):
        return "expired"
    at = changed.get(f.code)
    if at is not None and any(_utc(r.created_at) is not None and at > _utc(r.created_at) for r in records):  # type: ignore[operator]
        return "changed"
    d = days_left(f.valid_until)
    if d is not None and d <= EXPIRING_DAYS:
        return "expiring"
    return "current"


def planned_action(item: ContentItem) -> tuple[str, str]:
    if item.state in ("approved", "in_review"):
        return "changes_requested", ("Approved content goes back to the creator for changes." if item.state == "approved"
                                     else "Taken out of review and sent back to the creator for changes.")
    if item.state in ("draft", "findings_ready", "changes_requested"):
        return "recheck", "Not approved yet: the latest version is checked again against the current facts."
    if item.state == "checking":
        return "none", "A check is already running; it uses the current facts."
    return "none", "Rejected content isn't published, so nothing changes."


def impact(session: Session, f: Fact) -> FactImpactOut:
    lv, recs = latest_records(session, f.brand_id)
    recs = [r for r in recs if relies_on(r, f.code)]
    facts = {x.code: x for x in session.exec(select(Fact).where(Fact.brand_id == f.brand_id)).all()}
    changed = fact_changed_at(session)
    by_item: dict[int, list[ClaimRecord]] = {}
    for r in recs:
        by_item.setdefault(r.item_id, []).append(r)
    users = {u.id: u for u in session.exec(select(User)).all()}
    out: list[ImpactItem] = []
    for item_id, rs in by_item.items():
        item = session.get(ContentItem, item_id)
        v = lv.get(item_id)
        if item is None or v is None:
            continue
        action, why = planned_action(item)
        out.append(ImpactItem(
            item_id=item.id, title=item.title, channel=item.channel, state=item.state,  # type: ignore[arg-type]
            creator_name=users[item.creator_id].name if item.creator_id in users else item.creator_id,
            version_id=v.id, version_n=v.n, claims=[to_ledger_claim(r, item, v, True, facts, changed) for r in rs],
            action=action, action_reason=why))  # type: ignore[arg-type]
    order = {"approved": 0, "in_review": 1, "changes_requested": 2, "findings_ready": 3, "draft": 4, "checking": 5, "rejected": 6}
    out.sort(key=lambda x: (order.get(x.state, 9), x.title.lower()))
    approved = sum(1 for x in out if x.state == "approved")
    return FactImpactOut(fact=ledger_fact(f), reason=impact_reason(f, recs, changed), changed_at=changed.get(f.code),  # type: ignore[arg-type]
                         items=out, approved=approved, in_progress=sum(1 for x in out if x.state in IN_PROGRESS))


def _reason_text(f: Fact, reason: str) -> str:
    if reason == "expired":
        return f"Fact {f.code} (“{f.statement}”) expired on {f.valid_until:%d %b %Y}."
    if reason == "changed":
        return f"Fact {f.code} changed: it now reads “{f.statement}” ({f.value or 'no value'})."
    if reason == "expiring":
        return f"Fact {f.code} (“{f.statement}”) expires on {f.valid_until:%d %b %Y}."
    return f"Fact {f.code} (“{f.statement}”) was flagged for re-review."


def send_back(session: Session, f: Fact, user: User, *, recheck: bool, note: Optional[str] = None,
              is_running=lambda _vid: False) -> tuple[str, list[SendBackResult], list[int]]:  # noqa: ANN001
    """Apply the impact (see module docstring). Commits. Returns (reason, results, version ids to re-check);
    the caller starts the checks after the commit."""
    from app.services import audit, touch

    imp = impact(session, f)
    reason = imp.reason
    why = _reason_text(f, reason)
    extra = (note or "").strip()
    results: list[SendBackResult] = []
    to_check: list[int] = []
    for it in imp.items:
        item = session.get(ContentItem, it.item_id)
        v = session.get(Version, it.version_id)
        if item is None or v is None:
            continue
        frm = item.state
        if it.action == "none":
            results.append(SendBackResult(item_id=item.id, title=item.title, from_state=frm, to_state=frm,  # type: ignore[arg-type]
                                          rechecked=False, skipped_reason=it.action_reason))
            continue
        running = is_running(v.id) or v.check_status in ("queued", "running")
        to = "changes_requested" if it.action == "changes_requested" else frm
        will_check = recheck and not running
        if will_check and to in ("draft", "findings_ready"):
            to = "checking"
        item.state = to
        touch(item)
        session.add(item)
        msg = f"{why} Sent back for review" + (" and re-checked against the current facts." if will_check else ".")
        audit(session, f"fact_{reason}" if reason != "current" else "fact_review", item_id=item.id, version_id=v.id,
              user_id=user.id, detail={"fact": f.code, "from": frm, "to": to, "version": v.n,
                                       "note": f"{msg}{(' ' + extra) if extra else ''}"})
        session.add(Comment(item_id=item.id, user_id=user.id, body=f"Sent back: {msg}{(' ' + extra) if extra else ''}"))
        if will_check:
            v.check_status = "queued"
            session.add(v)
            audit(session, "check_requested", item_id=item.id, version_id=v.id, user_id=user.id, detail={"fact": f.code})
            to_check.append(v.id)
        results.append(SendBackResult(item_id=item.id, title=item.title, from_state=frm, to_state=to,  # type: ignore[arg-type]
                                      rechecked=will_check,
                                      skipped_reason=None if will_check or not recheck else "A check is already running."))
    session.commit()
    return reason, results, to_check
