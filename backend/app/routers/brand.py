"""Brand Kit: brand, rules, terms, facts, assets. OWNER: backend (scaffold agent).

Reads: any signed-in user. Writes: admin only. Editing a rule bumps its `version`.
PDF import endpoints (P1) are stubs. OWNER: pipeline-agent.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, func, select

from app.config import get_settings
from app.deps import AdminUser, CurrentUser, SessionDep
from app.models import Asset, Fact, Rule, Term, utcnow
from app.schemas import (
    BrandOut,
    FactCreate,
    FactOut,
    FactUpdate,
    ImportPdfConfirmIn,
    ImportPdfOut,
    LogoOut,
    PaletteColor,
    RuleCreate,
    RuleOut,
    RuleUpdate,
    TermCreate,
    TermOut,
    TermUpdate,
)
from app.services import NoBrandError, audit, current_brand, to_fact_out

router = APIRouter(tags=["brand"])


def _brand(session: Session):  # noqa: ANN202
    try:
        return current_brand(session)
    except NoBrandError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _count(session: Session, model, brand_id: int) -> int:  # noqa: ANN001
    return session.exec(select(func.count()).select_from(model).where(model.brand_id == brand_id)).one()


@router.get("/brand", response_model=BrandOut)
def get_brand(session: SessionDep, user: CurrentUser) -> BrandOut:
    brand = _brand(session)
    assets = session.exec(select(Asset).where(Asset.brand_id == brand.id).order_by(Asset.id)).all()
    guideline = next((a for a in assets if a.kind == "guideline"), None)
    return BrandOut(
        id=brand.id, name=brand.name, tone_labels=list(brand.tone_labels or []),
        channel_targets=dict(brand.channel_targets or {}),
        palette=[PaletteColor(name=a.meta.get("name", ""), hex=a.meta.get("hex", "")) for a in assets if a.kind == "palette"],
        fonts=[a.meta.get("name", "") for a in assets if a.kind == "font"],
        logos=[LogoOut(id=a.id, variant=a.meta.get("variant", "approved"), url=f"/api/assets/{a.id}") for a in assets if a.kind == "logo"],
        guideline_url=f"/api/assets/{guideline.id}" if guideline else None,
        rule_count=_count(session, Rule, brand.id), term_count=_count(session, Term, brand.id),
        fact_count=_count(session, Fact, brand.id), demo=get_settings().demo_mode,
    )


@router.get("/assets/{asset_id}")
def get_asset(asset_id: int, session: SessionDep, user: CurrentUser) -> FileResponse:
    a = session.get(Asset, asset_id)
    if a is None or not a.path or not Path(a.path).is_file():
        raise HTTPException(status_code=404, detail="That brand asset isn't available.")
    return FileResponse(a.path)


# ------------------------------------------------------------------ rules
@router.get("/rules", response_model=list[RuleOut])
def list_rules(
    session: SessionDep, user: CurrentUser,
    category: Optional[str] = None, severity: Optional[str] = None, enabled: Optional[bool] = None,
) -> list[Rule]:
    brand = _brand(session)
    q = select(Rule).where(Rule.brand_id == brand.id)
    if category:
        q = q.where(Rule.category == category)
    if severity:
        q = q.where(Rule.severity == severity)
    if enabled is not None:
        q = q.where(Rule.enabled == enabled)
    return list(session.exec(q.order_by(Rule.code)).all())


@router.post("/rules", response_model=RuleOut, status_code=201)
def create_rule(body: RuleCreate, session: SessionDep, user: AdminUser) -> Rule:
    brand = _brand(session)
    if session.exec(select(Rule).where(Rule.brand_id == brand.id, Rule.code == body.code)).first():
        raise HTTPException(status_code=409, detail=f"A rule with code {body.code} already exists. Pick another code.")
    rule = Rule(brand_id=brand.id, **body.model_dump())
    session.add(rule)
    audit(session, "rule_created", user_id=user.id, detail={"code": rule.code})
    session.commit()
    session.refresh(rule)
    return rule


@router.patch("/rules/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: int, body: RuleUpdate, session: SessionDep, user: AdminUser) -> Rule:
    rule = session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="That rule doesn't exist.")
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if getattr(rule, k) != v}
    if changes:
        for k, v in changes.items():
            setattr(rule, k, v)
        rule.version += 1
        rule.updated_at = utcnow()
        session.add(rule)
        audit(session, "rule_updated", user_id=user.id, detail={"code": rule.code, "version": rule.version, "fields": sorted(changes)})
        session.commit()
        session.refresh(rule)
    return rule


# ------------------------------------------------------------------ terms
@router.get("/terms", response_model=list[TermOut])
def list_terms(session: SessionDep, user: CurrentUser) -> list[Term]:
    brand = _brand(session)
    return list(session.exec(select(Term).where(Term.brand_id == brand.id).order_by(Term.kind, Term.value)).all())


@router.post("/terms", response_model=TermOut, status_code=201)
def create_term(body: TermCreate, session: SessionDep, user: AdminUser) -> Term:
    brand = _brand(session)
    term = Term(brand_id=brand.id, **body.model_dump())
    session.add(term)
    audit(session, "term_created", user_id=user.id, detail={"kind": term.kind, "value": term.value})
    session.commit()
    session.refresh(term)
    return term


@router.patch("/terms/{term_id}", response_model=TermOut)
def update_term(term_id: int, body: TermUpdate, session: SessionDep, user: AdminUser) -> Term:
    term = session.get(Term, term_id)
    if term is None:
        raise HTTPException(status_code=404, detail="That word entry doesn't exist.")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(term, k, v)
    session.add(term)
    audit(session, "term_updated", user_id=user.id, detail={"id": term.id})
    session.commit()
    session.refresh(term)
    return term


# ------------------------------------------------------------------ facts
@router.get("/facts", response_model=list[FactOut])
def list_facts(session: SessionDep, user: CurrentUser) -> list[FactOut]:
    brand = _brand(session)
    return [to_fact_out(f) for f in session.exec(select(Fact).where(Fact.brand_id == brand.id).order_by(Fact.code)).all()]


@router.post("/facts", response_model=FactOut, status_code=201)
def create_fact(body: FactCreate, session: SessionDep, user: AdminUser) -> FactOut:
    brand = _brand(session)
    if session.exec(select(Fact).where(Fact.brand_id == brand.id, Fact.code == body.code)).first():
        raise HTTPException(status_code=409, detail=f"A fact with code {body.code} already exists.")
    fact = Fact(brand_id=brand.id, **body.model_dump())
    session.add(fact)
    audit(session, "fact_created", user_id=user.id, detail={"code": fact.code})
    session.commit()
    session.refresh(fact)
    return to_fact_out(fact)


@router.patch("/facts/{fact_id}", response_model=FactOut)
def update_fact(fact_id: int, body: FactUpdate, session: SessionDep, user: AdminUser) -> FactOut:
    fact = session.get(Fact, fact_id)
    if fact is None:
        raise HTTPException(status_code=404, detail="That fact doesn't exist.")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(fact, k, v)
    session.add(fact)
    audit(session, "fact_updated", user_id=user.id, detail={"code": fact.code})
    session.commit()
    session.refresh(fact)
    return to_fact_out(fact)


# ------------------------------------------------------------------ P1: guideline PDF import
# OWNER: pipeline-agent
@router.post("/brand/import-pdf", response_model=ImportPdfOut)
async def import_pdf(user: AdminUser, file: UploadFile = File(...)) -> ImportPdfOut:
    raise HTTPException(status_code=501, detail="Guideline PDF import isn't available yet.")


# OWNER: pipeline-agent
@router.post("/brand/import-pdf/confirm", response_model=list[RuleOut])
def import_pdf_confirm(body: ImportPdfConfirmIn, session: SessionDep, user: AdminUser) -> list[Rule]:
    raise HTTPException(status_code=501, detail="Guideline PDF import isn't available yet.")
