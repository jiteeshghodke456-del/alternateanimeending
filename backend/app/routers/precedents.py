"""Precedent memory API (Round 3).

    GET  /precedents               every precedent of this workspace's brand, newest first, with uses
    POST /precedents/{id}/revoke   (admin) set inactive: future checks ignore it; findings already
                                   checked keep their "Seen before" note until they are re-checked
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlmodel import select

from app import precedents as PR
from app.deps import AdminUser, CurrentUser, SessionDep
from app.models import Precedent, Rule, utcnow
from app.schemas import PrecedentListOut, PrecedentOut
from app.services import NoBrandError, audit, current_brand

router = APIRouter(tags=["precedents"])


def _out(p: Precedent, rule: Rule | None, uses: int) -> PrecedentOut:
    cur = rule.version if rule else None
    why = PR.excluded_why(p, cur)
    return PrecedentOut(
        id=p.id, rule_code=p.rule_code, rule_version=p.rule_version, rule_current_version=cur,
        rule_statement=rule.statement if rule else "", quote=p.quote, context=p.context,
        decision=p.decision, reason=p.reason, note=p.note, by=p.user_name or (p.user_id or ""), at=p.created_at,  # type: ignore[arg-type]
        item_id=p.item_id, item_title=p.item_title, channel=p.channel, severity=p.severity, active=p.active,
        applies=why is None, excluded_why=why, uses=uses, seeded=p.seeded, revoked_by=p.revoked_by, revoked_at=p.revoked_at,
    )


@router.get("/precedents", response_model=PrecedentListOut)
def list_precedents(session: SessionDep, user: CurrentUser) -> PrecedentListOut:
    try:
        brand = current_brand(session)
    except NoBrandError:
        return PrecedentListOut(precedents=[], max_age_days=PR.max_age_days())
    rules = {r.code: r for r in session.exec(select(Rule).where(Rule.brand_id == brand.id)).all()}
    used = PR.uses(session)
    rows = session.exec(select(Precedent).where(Precedent.brand_id == brand.id)
                        .order_by(Precedent.created_at.desc(), Precedent.id.desc())).all()  # type: ignore[attr-defined]
    return PrecedentListOut(precedents=[_out(p, rules.get(p.rule_code), used.get(p.id, 0)) for p in rows],
                            max_age_days=PR.max_age_days())


@router.post("/precedents/{precedent_id}/revoke", response_model=PrecedentOut)
def revoke_precedent(precedent_id: int, session: SessionDep, user: AdminUser) -> PrecedentOut:
    p = session.get(Precedent, precedent_id)
    if p is None:
        raise HTTPException(status_code=404, detail="That precedent doesn't exist (the demo may have been reset).")
    if p.active:
        p.active, p.revoked_by, p.revoked_at = False, user.id, utcnow()
        session.add(p)
        audit(session, "precedent_revoked", user_id=user.id,
              detail={"precedent_id": p.id, "rule_code": p.rule_code, "item_id": p.item_id})
        session.commit()
        session.refresh(p)
    rule = session.exec(select(Rule).where(Rule.brand_id == p.brand_id, Rule.code == p.rule_code)).first()
    return _out(p, rule, PR.uses(session).get(p.id, 0))
