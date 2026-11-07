"""Brand Kit: brand, rules, terms, facts, assets. OWNER: backend (scaffold agent).

Reads: any signed-in user. Writes: admin only. Editing a rule bumps its `version`.
PDF import endpoints (P1-3) live in routers/innovations.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, func, select

from app import workspaces
from app.config import get_settings
from app.deps import AdminUser, CurrentUser, SessionDep
from app.models import Asset, ContentItem, Fact, Rule, Term, utcnow
from app.schemas import (
    BrandCounts,
    BrandOut,
    BrandPatch,
    FactImportOut,
    FactImportSkip,
    PaletteIn,
    FactCreate,
    FactOut,
    FactUpdate,
    LogoOut,
    PaletteColor,
    RuleCreate,
    RuleOut,
    RuleUpdate,
    TermCreate,
    TermOut,
    TermUpdate,
)
from app.services import NoBrandError, audit, current_brand, save_upload, to_fact_out

router = APIRouter(tags=["brand"])


def _brand(session: Session):  # noqa: ANN202
    try:
        return current_brand(session)
    except NoBrandError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _count(session: Session, model, brand_id: int) -> int:  # noqa: ANN001
    return session.exec(select(func.count()).select_from(model).where(model.brand_id == brand_id)).one()


def brand_out(session: Session) -> BrandOut:
    brand = _brand(session)
    assets = session.exec(select(Asset).where(Asset.brand_id == brand.id).order_by(Asset.id)).all()
    guideline = next((a for a in assets if a.kind == "guideline"), None)
    approved = next((a for a in assets if a.kind == "logo" and a.path and (a.meta or {}).get("variant", "approved") == "approved"), None)
    counts = BrandCounts(rules=_count(session, Rule, brand.id), facts=_count(session, Fact, brand.id),
                         terms=_count(session, Term, brand.id), items=_count(session, ContentItem, brand.id))
    blank = workspaces.current_kind() == "blank"
    return BrandOut(
        id=brand.id, name=brand.name, tone_labels=list(brand.tone_labels or []),
        channel_targets=dict(brand.channel_targets or {}),
        palette=[PaletteColor(name=a.meta.get("name", ""), hex=a.meta.get("hex", "")) for a in assets if a.kind == "palette"],
        fonts=[a.meta.get("name", "") for a in assets if a.kind == "font"],
        logos=[LogoOut(id=a.id, variant=a.meta.get("variant", "approved"), url=f"/api/assets/{a.id}") for a in assets if a.kind == "logo"],
        guideline_url=f"/api/assets/{guideline.id}" if guideline else None,
        rule_count=counts.rules, term_count=counts.terms, fact_count=counts.facts,
        demo=get_settings().demo_mode and not blank,
        tagline=brand.tagline or "", description=brand.description or "",
        logo_url=f"/api/assets/{approved.id}" if approved else None, counts=counts,
        is_blank_setup=blank and counts.rules == 0 and counts.items == 0,
    )


@router.get("/brand", response_model=BrandOut)
def get_brand(session: SessionDep, user: CurrentUser) -> BrandOut:
    return brand_out(session)


@router.patch("/brand", response_model=BrandOut)
def patch_brand(body: BrandPatch, session: SessionDep, user: AdminUser) -> BrandOut:
    brand = _brand(session)
    changes = {k: (v.strip() if isinstance(v, str) else v) for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "name" in changes and not changes["name"]:
        raise HTTPException(status_code=422, detail="Please give the brand a name.")
    if changes:
        for k, v in changes.items():
            setattr(brand, k, v)
        session.add(brand)
        audit(session, "brand_updated", user_id=user.id, detail={"fields": sorted(changes)})
        session.commit()
    return brand_out(session)


@router.put("/brand/palette", response_model=BrandOut)
def put_palette(body: PaletteIn, session: SessionDep, user: AdminUser) -> BrandOut:
    """Replaces the whole palette (1-12 colours, #RRGGBB)."""
    brand = _brand(session)
    for a in session.exec(select(Asset).where(Asset.brand_id == brand.id, Asset.kind == "palette")).all():
        session.delete(a)
    for c in body.colours:
        session.add(Asset(brand_id=brand.id, kind="palette", meta={"name": c.name.strip(), "hex": c.hex.upper()}))
    audit(session, "palette_updated", user_id=user.id, detail={"colours": [c.hex.upper() for c in body.colours]})
    session.commit()
    return brand_out(session)


MAX_LOGO_BYTES = 5 * 1024 * 1024
LOGO_MAX_SIDE = 800  # same size as the demo logo.png; pipeline/logo.py downsizes to SIFT_MAX_SIDE anyway
LOGO_MIN_SIDE = 48


def normalise_logo(data: bytes) -> bytes:
    """PNG/JPEG -> opaque RGB PNG, long side <= LOGO_MAX_SIDE. Transparency is flattened onto white,
    because OpenCV reads the approved logo with IMREAD_COLOR (alpha dropped: transparent pixels would
    turn black and SIFT would match the wrong shapes). Over 40 MP -> 413; JPEGs decode at reduced scale."""
    import io

    from PIL import Image

    from app.pipeline import units as U

    try:
        with U._open_checked(data) as probe:  # header only: pixel cap (413) before any decode
            fmt, size = probe.format, probe.size
    except U.UploadError as exc:
        if exc.status_code == 413:
            raise HTTPException(status_code=413, detail=exc.message) from exc
        raise HTTPException(status_code=422, detail="That file isn't a readable PNG or JPEG image.") from exc
    if fmt not in ("PNG", "JPEG"):
        raise HTTPException(status_code=422, detail="Upload the logo as a PNG or JPEG.")
    if min(size) < LOGO_MIN_SIDE:
        raise HTTPException(status_code=422, detail=f"The logo is too small; use at least {LOGO_MIN_SIDE} px on each side.")
    try:
        img, _ = U.open_image_small(data, LOGO_MAX_SIDE)  # pixel cap, reduced JPEG decode, EXIF upright
    except U.UploadError as exc:
        status = exc.status_code if exc.status_code == 413 else 422
        msg = exc.message if status == 413 else "That file isn't a readable PNG or JPEG image."
        raise HTTPException(status_code=status, detail=msg) from exc
    img = U._sensible_mode(img)  # CMYK/P/LA/16-bit -> RGB or RGBA
    if img.mode == "RGBA":
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


@router.post("/brand/logo", response_model=BrandOut)
async def upload_logo(session: SessionDep, user: AdminUser, file: UploadFile = File(...)) -> BrandOut:
    """multipart `file`: PNG or JPEG, <= 5 MB. Becomes the approved logo used by logo fidelity."""
    data = await file.read(MAX_LOGO_BYTES + 1)
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(status_code=413, detail="The logo is larger than 5 MB.")
    if not data:
        raise HTTPException(status_code=422, detail="Choose a logo file to upload.")
    png = normalise_logo(data)
    brand = _brand(session)
    path = save_upload(png, "png")
    for a in session.exec(select(Asset).where(Asset.brand_id == brand.id, Asset.kind == "logo")).all():
        if (a.meta or {}).get("variant", "approved") == "approved":
            session.delete(a)
    session.add(Asset(brand_id=brand.id, kind="logo", path=path, meta={"variant": "approved", "filename": (file.filename or "logo")[:120]}))
    audit(session, "logo_uploaded", user_id=user.id, detail={"bytes": len(png)})
    session.commit()
    return brand_out(session)


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


MAX_FACT_ROWS = 500
MAX_FACT_CSV_BYTES = 1024 * 1024
FACT_COLUMNS = ("statement", "value", "source", "valid_until")


def _next_fact_code(taken: set[str]) -> str:
    n = 1
    while f"F-{n:02d}" in taken:
        n += 1
    code = f"F-{n:02d}"
    taken.add(code)
    return code


@router.post("/facts/import", response_model=FactImportOut)
async def import_facts(session: SessionDep, user: AdminUser, file: UploadFile = File(...)) -> FactImportOut:
    """multipart `file`: CSV with header statement,value,source,valid_until (only statement is required;
    valid_until is YYYY-MM-DD; an optional `code` column is used when free). At most 500 rows.
    Bad rows are skipped and reported; good rows are added in one go."""
    import csv
    import io
    from datetime import date

    data = await file.read(MAX_FACT_CSV_BYTES + 1)
    if len(data) > MAX_FACT_CSV_BYTES:
        raise HTTPException(status_code=413, detail="The CSV is larger than 1 MB.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="Save the CSV as UTF-8 and upload it again.") from exc
    reader = csv.DictReader(io.StringIO(text))
    header = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
    if "statement" not in header:
        raise HTTPException(status_code=422, detail="The CSV needs a header row: statement,value,source,valid_until.")
    rows = list(reader)
    if len(rows) > MAX_FACT_ROWS:
        raise HTTPException(status_code=422, detail=f"The CSV has {len(rows)} rows; import at most {MAX_FACT_ROWS} at a time.")
    brand = _brand(session)
    existing = session.exec(select(Fact).where(Fact.brand_id == brand.id)).all()
    taken = {f.code for f in existing}
    seen = {f.statement.strip().lower() for f in existing}
    skipped: list[FactImportSkip] = []
    added: list[str] = []
    for i, raw in enumerate(rows, start=1):
        row = {(k or "").strip().lower(): (v or "").strip() if isinstance(v, str) else "" for k, v in raw.items() if k is not None}
        statement = row.get("statement", "")
        if not statement:
            skipped.append(FactImportSkip(row=i, reason="The statement is empty."))
            continue
        if len(statement) > 1000:
            skipped.append(FactImportSkip(row=i, reason="The statement is longer than 1000 characters."))
            continue
        if statement.lower() in seen:
            skipped.append(FactImportSkip(row=i, reason="This fact is already in the list."))
            continue
        valid_until = None
        if row.get("valid_until"):
            try:
                valid_until = date.fromisoformat(row["valid_until"])
            except ValueError:
                skipped.append(FactImportSkip(row=i, reason=f"valid_until \"{row['valid_until'][:20]}\" isn't a date like 2027-03-31."))
                continue
        code = row.get("code", "")
        if not code or code in taken or len(code) > 40:
            code = _next_fact_code(taken)
        else:
            taken.add(code)
        seen.add(statement.lower())
        session.add(Fact(brand_id=brand.id, code=code, statement=statement, value=row.get("value", "")[:200],
                         source=row.get("source", "")[:300], valid_until=valid_until))
        added.append(code)
    if added:
        audit(session, "facts_imported", user_id=user.id,
              detail={"filename": (file.filename or "facts.csv")[:120], "codes": added, "skipped": len(skipped)})
        session.commit()
    return FactImportOut(imported=len(added), skipped=skipped)
