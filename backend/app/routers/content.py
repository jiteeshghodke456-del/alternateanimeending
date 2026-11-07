"""Content items, versions, findings, SSE progress.

Implemented by scaffold: create/list/get items, get version (+ file/unit image), list findings,
simple accept/edit/dismiss. Stubs are tagged with their OWNER.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse

from app import workflow as W
from app.deps import CurrentUser, SessionDep, ensure_role
from app.models import CHANNELS, ContentItem, Finding, Unit, Version
from app.pipeline import run as pipeline_run
from app.pipeline.units import UploadError
from app.schemas import (
    FindingDismissIn,
    FindingEditIn,
    FindingOut,
    ItemCreated,
    ItemDetail,
    ItemState,
    ItemSummary,
    OkOut,
    ResubmitOut,
    VersionOut,
)
from app.services import (
    NoBrandError,
    audit,
    create_version,
    current_brand,
    to_finding_out,
    to_item_summary,
    to_version_out,
    to_version_summary,
    touch,
)

router = APIRouter(tags=["content"])


def get_item_or_404(session: Session, item_id: int) -> ContentItem:
    item = session.get(ContentItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="That content item doesn't exist (the demo may have been reset).")
    return item


def get_version_or_404(session: Session, version_id: int) -> Version:
    v = session.get(Version, version_id)
    if v is None:
        raise HTTPException(status_code=404, detail="That version doesn't exist (the demo may have been reset).")
    return v


def get_finding_or_404(session: Session, finding_id: int) -> Finding:
    f = session.get(Finding, finding_id)
    if f is None:
        raise HTTPException(status_code=404, detail="That finding doesn't exist.")
    return f


# ------------------------------------------------------------------ items
@router.post("/items", response_model=ItemCreated, status_code=201)
async def create_item(
    session: SessionDep,
    user: CurrentUser,
    title: str = Form(..., min_length=1, max_length=200),
    channel: str = Form(...),
    confidential: bool = Form(False),
    text: Optional[str] = Form(None),
    caption: Optional[str] = Form(None),
    alt_text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
) -> ItemCreated:
    """multipart/form-data. Send `text` OR `file` (PNG/JPG/WEBP ≤10 MB, or PDF ≤20 pages).
    Creates the item (state=checking), version 1 and its units, then hands off to
    pipeline.run.start_check(version_id)."""
    ensure_role(user, "creator", "admin")
    channel = channel.strip().lower()
    if channel not in CHANNELS:
        raise HTTPException(status_code=422, detail=f"Pick a channel: {', '.join(c.title() for c in CHANNELS)}.")
    data = await file.read() if file is not None else None
    if not data and not (text and text.strip()):
        raise HTTPException(status_code=422, detail="Paste some text or upload a file to check.")
    try:
        brand = current_brand(session)
    except NoBrandError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    item = ContentItem(brand_id=brand.id, title=title.strip(), channel=channel, creator_id=user.id,
                       state="checking", confidential=confidential)
    session.add(item)
    session.flush()
    try:
        version = create_version(
            session, item, n=1, text=text, file_bytes=data or None,
            filename=(file.filename or "") if file else "", caption=caption if data else None,
            alt_text=alt_text if data else None,
        )
    except UploadError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    audit(session, "created", item_id=item.id, version_id=version.id, user_id=user.id,
          detail={"kind": version.kind, "confidential": confidential})
    session.commit()
    pipeline_run.start_check(version.id)
    return ItemCreated(item_id=item.id, version_id=version.id)


@router.get("/items", response_model=list[ItemSummary])
def list_items(
    session: SessionDep, user: CurrentUser, state: Optional[ItemState] = None, mine: bool = False, key: Optional[str] = None,
) -> list[ItemSummary]:
    """Filters combine with AND. `key=demo_flawed_post` returns the seeded "Try the demo" post."""
    q = select(ContentItem)
    if key:
        q = q.where(ContentItem.key == key)
    if state:
        q = q.where(ContentItem.state == state)
    if mine:
        q = q.where(ContentItem.creator_id == user.id)
    items = session.exec(q.order_by(ContentItem.updated_at.desc())).all()
    return [to_item_summary(session, it) for it in items]


@router.get("/items/{item_id}", response_model=ItemDetail)
def get_item(item_id: int, session: SessionDep, user: CurrentUser) -> ItemDetail:
    from app.routers.review import item_actions

    item = get_item_or_404(session, item_id)
    versions = session.exec(select(Version).where(Version.item_id == item.id).order_by(Version.n)).all()
    return ItemDetail(
        item=to_item_summary(session, item),
        versions=[to_version_summary(v, session) for v in versions],
        actions=item_actions(session, item, user),
    )


@router.post("/items/{item_id}/resubmit", response_model=ResubmitOut)
async def resubmit(item_id: int, session: SessionDep, user: CurrentUser) -> ResubmitOut:
    """Apply accepted fixes and creator edits of the latest version to its text (spans applied
    end -> start; overlapping later spans are skipped and reported), create version n+1, set the
    item to "checking", audit "resubmitted", then start the check. Returns the word-level diff."""
    item = get_item_or_404(session, item_id)
    denial = W.can_resubmit(item, user)
    if denial:
        raise HTTPException(status_code=denial[0], detail=denial[1])
    prev = W.latest(session, item.id)
    if prev is None:
        raise HTTPException(status_code=409, detail="This item has no version to resubmit.")
    findings = session.exec(select(Finding).where(Finding.version_id == prev.id, Finding.status.in_(("accepted", "edited")))).all()
    units = {u.id: u for u in session.exec(select(Unit).where(Unit.version_id == prev.id)).all()}
    res = W.apply_findings(prev.text, list(findings), units)

    file_bytes, filename, caption, alt = None, "", None, None
    text: Optional[str] = res.text
    prov: Optional[dict] = None
    if prev.kind == "image" and prev.file_path and Path(prev.file_path).is_file():
        # keep the image; the (possibly fixed) caption is the version text
        file_bytes, filename, caption, alt, text = Path(prev.file_path).read_bytes(), Path(prev.file_path).name, res.text, prev.alt_text, None
        # the stored file has no metadata (privacy), so the provenance read from the original upload is carried over
        img = next((u for u in units.values() if u.kind == "image"), None)
        prov = (img.meta or {}).get("provenance") if img is not None else None
    # PDFs: the new version is a text version of the corrected paragraphs (the original file stays on version n)
    try:
        new = create_version(session, item, n=prev.n + 1, text=text, file_bytes=file_bytes, filename=filename,
                             caption=caption, alt_text=alt, provenance=prov)
    except UploadError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    frm = item.state
    item.state = "checking"
    touch(item)
    session.add(item)
    audit(session, "resubmitted", item_id=item.id, version_id=new.id, user_id=user.id,
          detail={"from": frm, "to": "checking", "version": new.n, "applied": res.applied, "skipped": res.skipped})
    session.commit()
    pipeline_run.start_check(new.id)
    return ResubmitOut(item_id=item.id, version_id=new.id, n=new.n, diff=W.word_diff(prev.text, new.text),
                       applied=res.applied, skipped=res.skipped)


# ------------------------------------------------------------------ versions
@router.get("/versions/{version_id}", response_model=VersionOut)
def get_version(version_id: int, session: SessionDep, user: CurrentUser) -> VersionOut:
    return to_version_out(session, get_version_or_404(session, version_id))


@router.get("/versions/{version_id}/file")
def get_version_file(version_id: int, session: SessionDep, user: CurrentUser) -> FileResponse:
    v = get_version_or_404(session, version_id)
    if not v.file_path or not Path(v.file_path).is_file():
        raise HTTPException(status_code=404, detail="This version has no file.")
    return FileResponse(v.file_path, media_type=v.mime or None)


@router.get("/units/{unit_id}/image")
def get_unit_image(unit_id: int, session: SessionDep, user: CurrentUser) -> FileResponse:
    u = session.get(Unit, unit_id)
    if u is None or not u.image_path or not Path(u.image_path).is_file():
        raise HTTPException(status_code=404, detail="This image isn't available.")
    return FileResponse(u.image_path)


# OWNER: pipeline-agent
@router.post("/versions/{version_id}/check", response_model=OkOut)
async def check_version(version_id: int, session: SessionDep, user: CurrentUser) -> OkOut:
    """(Re-)run the check on a version, e.g. a seeded item that shows "not checked yet".
    Admin, or the item's creator. Items in draft/findings_ready go to "checking" and back to
    "findings_ready"; items further along the workflow keep their state. A re-run replaces the
    version's findings. Progress streams on GET /versions/{id}/events."""
    v = get_version_or_404(session, version_id)
    item = get_item_or_404(session, v.item_id)
    if user.role != "admin" and item.creator_id != user.id:
        raise HTTPException(status_code=403, detail="Only a Brand Admin or the item's creator can run the check.")
    if pipeline_run.is_running(v.id) or v.check_status == "running":
        return OkOut(ok=True, message="This version is already being checked.")
    if item.state in ("draft", "findings_ready", "checking"):
        item.state = "checking"
        touch(item)
        session.add(item)
    v.check_status = "queued"
    session.add(v)
    audit(session, "check_requested", item_id=item.id, version_id=v.id, user_id=user.id, detail={})
    session.commit()
    pipeline_run.start_check(v.id)
    return OkOut(ok=True, message="Check started.")


# OWNER: pipeline-agent (event source lives in pipeline/run.py: subscribe())
@router.get("/versions/{version_id}/events")
async def version_events(version_id: int, session: SessionDep, user: CurrentUser) -> EventSourceResponse:
    """SSE. Each message: `event: <stage>` + `data: CheckEvent JSON`. Ends after done/error.
    EventSource can't send headers: pass `?token=` (LoginOut.token) if cookies are blocked."""
    get_version_or_404(session, version_id)

    async def gen():  # noqa: ANN202
        async for ev in pipeline_run.subscribe(version_id):
            yield {"event": ev.stage, "data": ev.model_dump_json()}

    return EventSourceResponse(gen(), ping=15)


@router.get("/versions/{version_id}/findings", response_model=list[FindingOut])
def list_findings(version_id: int, session: SessionDep, user: CurrentUser, include_pass: bool = False) -> list[FindingOut]:
    v = get_version_or_404(session, version_id)
    q = select(Finding).where(Finding.version_id == v.id)
    if not include_pass:
        q = q.where(Finding.band != "pass")
    item = session.get(ContentItem, v.item_id)
    brand_id = item.brand_id if item else None
    return [to_finding_out(session, f, brand_id) for f in session.exec(q.order_by(Finding.span_start, Finding.id)).all()]


# ------------------------------------------------------------------ finding actions
# Only the item's creator, a reviewer or an approver may act; only on the latest version; not once final.
def _finding_ctx(session: Session, finding_id: int, user) -> tuple[Finding, ContentItem]:  # noqa: ANN001
    f = get_finding_or_404(session, finding_id)
    v = get_version_or_404(session, f.version_id)
    item = get_item_or_404(session, v.item_id)
    denial = W.can_act_on_finding(user, item)
    if denial:
        raise HTTPException(status_code=denial[0], detail=denial[1])
    latest = W.latest(session, item.id)
    if latest and latest.id != v.id:
        raise HTTPException(status_code=409, detail="This finding belongs to an older version. Open the latest version to act on findings.")
    return f, item


def _save_finding(session: Session, f: Finding, item: ContentItem, user, action: str, detail: dict) -> FindingOut:  # noqa: ANN001
    session.add(f)
    touch(item)
    session.add(item)
    audit(session, action, item_id=item.id, version_id=f.version_id, user_id=user.id, detail={"finding_id": f.id, **detail})
    session.commit()
    session.refresh(f)
    return to_finding_out(session, f, item.brand_id)


@router.post("/findings/{finding_id}/accept", response_model=FindingOut)
def accept_finding(finding_id: int, session: SessionDep, user: CurrentUser) -> FindingOut:
    f, item = _finding_ctx(session, finding_id, user)
    if not f.fix:
        raise HTTPException(status_code=409, detail="This finding has no suggested fix. Use Edit to write your own.")
    if f.span_start is None or f.span_end is None:
        raise HTTPException(status_code=409, detail="This finding isn't tied to a piece of text, so a fix can't be applied automatically. Dismiss it or edit the content.")
    f.status = "accepted"
    f.edited_text = f.dismiss_reason = f.dismiss_note = None
    # Edit region: the other open findings that share this fix are handled by it too (applied once on resubmit).
    siblings = []
    if f.fix_group:
        for g in session.exec(select(Finding).where(Finding.version_id == f.version_id, Finding.fix_group == f.fix_group,
                                                    Finding.id != f.id, Finding.status == "open")).all():
            g.status = "accepted"
            session.add(g)
            siblings.append(g.id)
    return _save_finding(session, f, item, user, "finding_accepted",
                         {"rule_code": f.rule_code, **({"region_findings": siblings} if siblings else {})})


@router.post("/findings/{finding_id}/edit", response_model=FindingOut)
def edit_finding(finding_id: int, body: FindingEditIn, session: SessionDep, user: CurrentUser) -> FindingOut:
    f, item = _finding_ctx(session, finding_id, user)
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="Write the replacement text for this span.")
    if f.span_start is None or f.span_end is None:
        raise HTTPException(status_code=409, detail="This finding isn't tied to a piece of text, so an edit can't be applied automatically. Dismiss it or edit the content.")
    f.status = "edited"
    f.edited_text = body.text
    f.dismiss_reason = f.dismiss_note = None
    return _save_finding(session, f, item, user, "finding_edited", {"rule_code": f.rule_code})


@router.post("/findings/{finding_id}/dismiss", response_model=FindingOut)
def dismiss_finding(finding_id: int, body: FindingDismissIn, session: SessionDep, user: CurrentUser) -> FindingOut:
    f, item = _finding_ctx(session, finding_id, user)
    if body.reason == "other" and not (body.note and body.note.strip()):
        raise HTTPException(status_code=422, detail="Please add a short note when the reason is “Other”.")
    f.status = "dismissed"
    f.dismiss_reason = body.reason
    f.dismiss_note = (body.note or "").strip() or None
    f.edited_text = None
    return _save_finding(session, f, item, user, "finding_dismissed", {"rule_code": f.rule_code, "reason": body.reason})
