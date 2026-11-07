"""Rule packs (app/packs.py): list, apply (idempotent), remove. Reads: any signed-in user. Writes: admin."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlmodel import Session

from app import packs as P
from app.deps import AdminUser, CurrentUser, SessionDep
from app.schemas import PackApplyOut, PackOut, PackRemoveOut, PackRuleOut, PackSourceOut
from app.services import NoBrandError, audit, current_brand

router = APIRouter(tags=["packs"])


def _brand_id(session: Session) -> int:
    try:
        return current_brand(session).id
    except NoBrandError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _pack(pack_id: str) -> P.Pack:
    try:
        return P.get_pack(pack_id)
    except P.PackNotFound as exc:
        raise HTTPException(status_code=404, detail="That rule pack doesn't exist.") from exc


def pack_out(session: Session, brand_id: int, pack: P.Pack) -> PackOut:
    rows = P.pack_rows(session, brand_id, pack.id)
    versions = [int((r.params or {}).get("pack_version") or 0) for r in rows]
    return PackOut(
        id=pack.id, name=pack.name, description=pack.description, version=pack.version,
        retrieved=pack.retrieved.isoformat(), rule_count=len(pack.rules),
        sources=[PackSourceOut(id=s.id, title=s.title, short=s.short, issuer=s.issuer, year=s.year, status=s.status,
                               url=s.url, retrieved=s.retrieved.isoformat()) for s in pack.sources],
        rules=[PackRuleOut(code=r.code, category=r.category, severity=r.severity, check_method=r.check_method,
                           statement=r.statement, clause=r.citation.clause,
                           source_short=pack.source(r.citation.source).short,
                           url=pack.source(r.citation.source).url) for r in pack.rules],
        applied=bool(rows), applied_count=len(rows), applied_version=max(versions) if versions else None,
        linked={r.code: list((r.params or {}).get("linked_rules") or []) for r in rows
                if (r.params or {}).get("linked_rules")},
    )


@router.get("/rules/packs", response_model=list[PackOut])
def list_packs(session: SessionDep, user: CurrentUser) -> list[PackOut]:
    bid = _brand_id(session)
    return [pack_out(session, bid, p) for p in P.all_packs()]


@router.post("/rules/packs/{pack_id}/apply", response_model=PackApplyOut)
def apply_pack(pack_id: str, session: SessionDep, user: AdminUser) -> PackApplyOut:
    pack = _pack(pack_id)
    res = P.apply_pack(session, _brand_id(session), pack)
    audit(session, "pack_applied", user_id=user.id,
          detail={k: res[k] for k in ("pack_id", "added", "updated", "unchanged", "skipped")})
    session.commit()
    return PackApplyOut(**res)


@router.delete("/rules/packs/{pack_id}", response_model=PackRemoveOut)
def remove_pack(pack_id: str, session: SessionDep, user: AdminUser) -> PackRemoveOut:
    pack = _pack(pack_id)
    n = P.remove_pack(session, _brand_id(session), pack.id)
    audit(session, "pack_removed", user_id=user.id, detail={"pack_id": pack.id, "removed": n})
    session.commit()
    return PackRemoveOut(pack_id=pack.id, removed=n)
