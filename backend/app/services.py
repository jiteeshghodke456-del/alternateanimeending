"""Shared DB helpers used by routers and the seed loader (no FastAPI imports here).

- current_brand(): the single brand (Round 2 is single-tenant).
- create_version(): store file, split into units, write Version + Units.
- audit(): append an AuditEvent.
- to_* serializers: model rows -> schemas.* (keep shapes in sync with api.ts).
"""
from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Any, Optional

from sqlmodel import Session, func, select

from app.config import get_settings
from app.models import (
    AuditEvent,
    Brand,
    ContentItem,
    Fact,
    Finding,
    Rule,
    Unit,
    User,
    Version,
    utcnow,
)
from app.pipeline import units as U
from app.schemas import (
    CitationOut,
    FactOut,
    FactRef,
    FindingOut,
    ItemSummary,
    RuleRef,
    UnitOut,
    VersionOut,
    VersionSummary,
    WebSource,
)


class NoBrandError(RuntimeError):
    pass


def current_brand(session: Session) -> Brand:
    brand = session.exec(select(Brand).order_by(Brand.id)).first()
    if brand is None:
        raise NoBrandError("No brand loaded. Reset the demo to re-seed.")
    return brand


def audit(
    session: Session,
    action: str,
    *,
    item_id: Optional[int] = None,
    version_id: Optional[int] = None,
    user_id: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> AuditEvent:
    ev = AuditEvent(item_id=item_id, version_id=version_id, user_id=user_id, action=action, detail=detail or {})
    session.add(ev)
    return ev


def upload_dir() -> Path:
    from app import workspaces

    p = workspaces.current_upload_dir() or Path(get_settings().upload_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_upload(data: bytes, ext: str) -> str:
    path = upload_dir() / f"{uuid.uuid4().hex}.{ext}"
    path.write_bytes(data)
    return str(path)


def create_version(
    session: Session,
    item: ContentItem,
    *,
    n: int,
    text: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
    filename: str = "",
    caption: Optional[str] = None,
    alt_text: Optional[str] = None,
    check_status: str = "queued",
    provenance: Optional[dict] = None,
) -> Version:
    """Validate input, store the file, split into units and add Version + Units (flushes, no commit).

    Exactly one of `text` / `file_bytes` is used (file wins). Raises units.UploadError.
    Images: provenance (C2PA/XMP/EXIF) is read from the ORIGINAL bytes before normalise_image stores a
    metadata-free copy. `provenance` carries an earlier version's result (a resubmit re-reads the stored,
    stripped file, which no longer has it).
    """
    unit_meta: Optional[dict] = None
    if file_bytes:
        kind, mime, ext = U.validate_upload(filename, file_bytes)
        if kind == "pdf":
            split = U.split_pdf(file_bytes)  # validates page count / text before we store anything
            path = save_upload(file_bytes, ext)
            v_kind = "pdf"
        else:
            from app.pipeline.provenance import read_provenance

            prov = provenance if provenance is not None else read_provenance(file_bytes, filename).to_dict()
            unit_meta = {"provenance": prov}
            # store a small, upright PNG/JPEG once (metadata stripped); Jev, palette and logo fidelity read this file
            file_bytes, mime, ext = U.normalise_image(file_bytes)
            path = save_upload(file_bytes, ext)
            split = U.image_units(path, caption, alt_text)
            v_kind = "image"
        version = Version(
            item_id=item.id, n=n, kind=v_kind, text=split.text, caption=caption, alt_text=alt_text,
            file_path=path, mime=mime, check_status=check_status,
        )
    else:
        split = U.text_units(text or "")
        version = Version(item_id=item.id, n=n, kind="text", text=split.text, check_status=check_status)

    version.stats = {"units_total": len(split.units), "units_done": 0, "judgments": 0, "elapsed_ms": 0, "cost_usd": 0.0}
    session.add(version)
    session.flush()
    for d in split.units:
        session.add(Unit(
            version_id=version.id, kind=d.kind, page=d.page, idx=d.idx, text=d.text,
            image_path=d.image_path, char_start=d.char_start, char_end=d.char_end,
            meta=unit_meta if d.kind == "image" else None,
        ))
    session.flush()
    return version


# ------------------------------------------------------------------ serializers
def fact_expired(valid_until: Optional[date]) -> bool:
    return valid_until is not None and valid_until < date.today()


def to_fact_out(f: Fact) -> FactOut:
    return FactOut(id=f.id, code=f.code, statement=f.statement, value=f.value, source=f.source,
                   valid_until=f.valid_until, expired=fact_expired(f.valid_until))


def latest_version(session: Session, item_id: int) -> Optional[Version]:
    return session.exec(select(Version).where(Version.item_id == item_id).order_by(Version.n.desc())).first()


def to_item_summary(session: Session, item: ContentItem) -> ItemSummary:
    v = latest_version(session, item.id)
    creator = session.get(User, item.creator_id)
    open_n = blocking_n = 0
    if v is not None:
        open_q = select(func.count()).select_from(Finding).where(
            Finding.version_id == v.id, Finding.status == "open", Finding.band != "pass")
        open_n = session.exec(open_q).one()
        blocking_n = session.exec(open_q.where(Finding.severity == "blocking")).one()
    return ItemSummary(
        id=item.id, key=item.key, title=item.title, channel=item.channel, creator_id=item.creator_id,
        creator_name=creator.name if creator else item.creator_id, state=item.state,
        confidential=item.confidential, created_at=item.created_at, updated_at=item.updated_at,
        latest_version_id=v.id if v else None, latest_version_n=v.n if v else None,
        open_findings=open_n, blocking_open=blocking_n,
    )


def to_version_summary(v: Version) -> VersionSummary:
    return VersionSummary(id=v.id, n=v.n, kind=v.kind, check_status=v.check_status, created_at=v.created_at)


def to_unit_out(u: Unit) -> UnitOut:
    prov = (u.meta or {}).get("provenance") if u.kind == "image" else None
    return UnitOut(id=u.id, kind=u.kind, page=u.page, idx=u.idx, text=u.text,
                   image_url=f"/api/units/{u.id}/image" if u.image_path else None,
                   char_start=u.char_start, char_end=u.char_end,
                   provenance=prov if isinstance(prov, dict) and prov.get("status") else None)


def to_version_out(session: Session, v: Version) -> VersionOut:
    units = session.exec(select(Unit).where(Unit.version_id == v.id).order_by(Unit.idx)).all()
    return VersionOut(
        id=v.id, item_id=v.item_id, n=v.n, kind=v.kind, text=v.text, caption=v.caption, alt_text=v.alt_text,
        file_url=f"/api/versions/{v.id}/file" if v.file_path else None, check_status=v.check_status,
        stats=dict(v.stats or {}), created_at=v.created_at, units=[to_unit_out(u) for u in units],
        source=web_source(v),
    )


def rule_ref_of(r: Rule) -> RuleRef:
    cit = (r.params or {}).get("citation") if r.pack_id else None
    return RuleRef(code=r.code, version=r.version, statement=r.statement, source_quote=r.source_quote, page=r.page,
                   pack_id=r.pack_id, citation=CitationOut.model_validate(cit) if cit else None)


def web_source(v: Version) -> Optional[WebSource]:
    """The fetch record of a web-page version (kind == "web"), or None."""
    if v.kind != "web":
        return None
    from app.pipeline.webpage import load_record

    rec = load_record(v.file_path)
    try:
        return WebSource(**{k: rec[k] for k in WebSource.model_fields if k in rec})
    except (KeyError, ValueError):
        return None


def to_finding_out(session: Session, f: Finding, brand_id: Optional[int] = None) -> FindingOut:
    rule_ref = fact_ref = None
    regulations: list[RuleRef] = []
    if f.rule_code:
        q = select(Rule).where(Rule.code == f.rule_code)
        if brand_id is not None:
            q = q.where(Rule.brand_id == brand_id)
        r = session.exec(q).first()
        if r:
            rule_ref = rule_ref_of(r)
            if r.pack_id is None:  # rule packs: regulations linked to this brand rule
                from app.packs import regulations_for

                regulations = [rule_ref_of(x) for x in regulations_for(session, brand_id if brand_id is not None else r.brand_id, r.code, f.quote)]
    if f.fact_code:
        q2 = select(Fact).where(Fact.code == f.fact_code)
        if brand_id is not None:
            q2 = q2.where(Fact.brand_id == brand_id)
        fa = session.exec(q2).first()
        if fa:
            fact_ref = FactRef(code=fa.code, statement=fa.statement, source=fa.source,
                               valid_until=fa.valid_until, expired=fact_expired(fa.valid_until))
    data = f.model_dump()
    data.pop("created_at", None)
    data.pop("precedent_id", None)
    # Precedent memory: "Seen before" + what it did to the band
    precedent = band_note = None
    if f.precedent_id:
        from app import precedents
        from app.pipeline.precedent import band_note as _band_note

        precedent = precedents.ref_for(session, f, brand_id)
        if precedent is not None:
            band_note = _band_note(f.band_before or f.band, f.band, f.severity, precedent["decision"])
    return FindingOut(**data, rule=rule_ref, fact=fact_ref, regulations=regulations, precedent=precedent, band_note=band_note)


def touch(item: ContentItem) -> None:
    item.updated_at = utcnow()
