"""Precedent memory: DB side (write on dismiss/override, load for a check, link seeded ones, serialise).
The matching and band rules are pure code in app/pipeline/precedent.py."""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func
from sqlmodel import Session, select

from app.config import get_settings
from app.models import ContentItem, Finding, Precedent, Rule, User, Version, utcnow
from app.pipeline import precedent as P

log = logging.getLogger("brandgate.precedents")


def max_age_days() -> int:
    return int(get_settings().precedent_max_age_days)


def _words_and_context(session: Session, f: Finding) -> tuple[str, str]:
    v = session.get(Version, f.version_id)
    text = (v.text or "") if v else ""
    words = P.flagged_words(text, f.quote, f.hit_start, f.hit_end)
    start = f.hit_start if f.hit_start is not None else f.span_start
    end = f.hit_end if f.hit_end is not None else f.span_end
    ctx = P.context_window(text, start, end) or words
    return words[:P.MAX_QUOTE_CHARS], ctx[:P.MAX_CONTEXT_CHARS]


def record(session: Session, f: Finding, item: ContentItem, user: User, decision: str, reason: str,
           note: Optional[str]) -> Optional[Precedent]:
    """Store (or refresh) the precedent for a human decision on finding `f`. Caller commits.
    No precedent for findings without a rule, nor from confidential items (their wording must not
    appear on other items' cards)."""
    if not f.rule_code or item.confidential:
        return None
    words, ctx = _words_and_context(session, f)
    if not P.normalise(words):
        return None
    qn = P.normalise(words)
    existing = session.exec(select(Precedent).where(Precedent.active == True, Precedent.rule_code == f.rule_code,  # noqa: E712
                                                    Precedent.brand_id == item.brand_id)).all()
    row = next((p for p in existing if p.finding_id == f.id
                or (p.version_id == f.version_id and p.quote_norm == qn)), None)
    if row is None:
        row = Precedent(brand_id=item.brand_id, rule_code=f.rule_code)
    row.rule_version = f.rule_version or 1
    row.quote, row.quote_norm, row.context = words, qn, ctx
    row.decision, row.reason = decision, reason
    row.note = (note or "").strip()[:1000] or None
    row.user_id, row.user_name = user.id, user.name
    row.item_id, row.item_title, row.version_id, row.finding_id = item.id, item.title, f.version_id, f.id
    row.channel, row.severity = item.channel, f.severity
    row.band = f.band_before or f.band
    row.active, row.revoked_by, row.revoked_at = True, None, None
    row.created_at = utcnow()
    session.add(row)
    return row


def deactivate_for_finding(session: Session, finding_id: int, user_id: Optional[str]) -> int:
    """The finding's decision changed (accepted/edited after a dismissal): its precedent no longer holds."""
    n = 0
    for p in session.exec(select(Precedent).where(Precedent.finding_id == finding_id, Precedent.active == True)).all():  # noqa: E712
        p.active, p.revoked_by, p.revoked_at = False, user_id, utcnow()
        session.add(p)
        n += 1
    return n


def to_spec(p: Precedent) -> P.PrecedentSpec:
    return P.PrecedentSpec(id=p.id, rule_code=p.rule_code, rule_version=p.rule_version, quote=p.quote, context=p.context,
                           decision=p.decision, reason=p.reason, note=p.note, by=p.user_name or (p.user_id or ""),
                           at=p.created_at, item_id=p.item_id, version_id=p.version_id, item_title=p.item_title,
                           active=p.active)


def load_specs(session: Session, brand_id: int) -> list[P.PrecedentSpec]:
    rows = session.exec(select(Precedent).where(Precedent.brand_id == brand_id, Precedent.active == True)).all()  # noqa: E712
    return [to_spec(p) for p in rows]


class Applier:
    """Used by pipeline/run.py for one check: matches each finding draft and lowers its band."""

    def __init__(self, specs: list[P.PrecedentSpec], rule_versions: dict[str, int], text: str,
                 version_id: Optional[int], now: Optional[datetime] = None, max_age: Optional[int] = None) -> None:
        self.specs, self.rule_versions, self.text, self.version_id = specs, rule_versions, text or "", version_id
        self.now = now or datetime.now(timezone.utc)
        self.max_age = max_age_days() if max_age is None else max_age

    def apply(self, d: Any) -> Optional[P.PrecedentSpec]:
        """`d`: engine.FindingDraft (band, severity, rule_code, quote, span/hit offsets). Returns the matching
        precedent or None. Doesn't change `d`: the caller lowers the band (pipeline.precedent.lower_band)."""
        if not self.specs or not d.rule_code or d.band == "pass":
            return None
        words = P.flagged_words(self.text, d.quote, d.hit_start, d.hit_end)
        start = d.hit_start if d.hit_start is not None else d.span_start
        end = d.hit_end if d.hit_end is not None else d.span_end
        ctx = P.context_window(self.text, start, end) or words
        return P.find_match(d.rule_code, self.rule_versions.get(d.rule_code), words, ctx, self.specs,
                            now=self.now, max_age_days=self.max_age, version_id=self.version_id)


def link_seeded(session: Session, version_id: int, rows: list[Finding]) -> int:
    """Demo seed: a seeded precedent names the item, rule and words a person dismissed. The first check of
    that version finds the finding again; mark THAT finding dismissed (as the seed says) and link it.
    Only for seeded precedents not yet linked, so a later re-check leaves its findings open. Caller commits."""
    from app.services import audit

    n = 0
    seeded = session.exec(select(Precedent).where(Precedent.version_id == version_id, Precedent.seeded == True,  # noqa: E712
                                                  Precedent.seed_linked == False)).all()  # noqa: E712
    v = session.get(Version, version_id)
    text = (v.text or "") if v else ""
    for p in seeded:
        for f in rows:
            if f.rule_code != p.rule_code or f.status != "open":
                continue
            words = P.flagged_words(text, f.quote, f.hit_start, f.hit_end)
            if P.normalise(words) != p.quote_norm:
                continue
            f.status, f.dismiss_reason, f.dismiss_note = "dismissed", p.reason, p.note
            p.finding_id, p.band, p.severity, p.seed_linked = f.id, f.band, f.severity, True
            session.add(f)
            session.add(p)
            audit(session, "finding_dismissed", item_id=p.item_id, version_id=version_id, user_id=p.user_id,
                  detail={"finding_id": f.id, "rule_code": f.rule_code, "reason": p.reason, "seeded": True})
            n += 1
            break
    if seeded:
        log.info("seeded precedents for version %s: %d of %d linked (%d findings)", version_id, n, len(seeded), len(rows))
    return n


def uses(session: Session) -> dict[int, int]:
    rows = session.exec(select(Finding.precedent_id, func.count()).where(Finding.precedent_id != None)  # noqa: E711
                        .group_by(Finding.precedent_id)).all()
    return {int(pid): int(n) for pid, n in rows}


def rule_versions(session: Session, brand_id: Optional[int]) -> dict[str, int]:
    q = select(Rule.code, Rule.version)
    if brand_id is not None:
        q = q.where(Rule.brand_id == brand_id)
    return {c: int(v or 1) for c, v in session.exec(q).all()}


def ref_for(session: Session, f: Finding, brand_id: Optional[int]) -> Optional[dict[str, Any]]:
    """FindingOut.precedent as a dict (validated into schemas.PrecedentRef by the caller)."""
    if not f.precedent_id:
        return None
    p = session.get(Precedent, f.precedent_id)
    if p is None:
        return None
    current = rule_versions(session, brand_id).get(p.rule_code)
    return {"id": p.id, "decision": p.decision, "reason": p.reason, "note": p.note, "by": p.user_name or (p.user_id or ""),
            "at": p.created_at, "item_id": p.item_id, "item_title": p.item_title,
            "rule_changed": current is not None and current != p.rule_version}


def excluded_why(p: Precedent, current_version: Optional[int], now: Optional[datetime] = None, max_age: Optional[int] = None) -> Optional[str]:
    if not p.active:
        return "Revoked"
    spec = to_spec(p)
    ma = max_age_days() if max_age is None else max_age
    if P.eligible(spec, current_version, now=now, max_age_days=ma):
        return None
    if p.reason == "rule_outdated" and current_version is not None and current_version > p.rule_version:
        return "The rule was rewritten since it was called out of date"
    return f"Older than {ma} days"


def counts_by_rule(session: Session) -> Counter:
    return Counter(r for r in session.exec(select(Precedent.rule_code).where(Precedent.active == True)).all())  # noqa: E712
