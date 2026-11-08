"""P1 innovations: Audience Preview (P1-1) and guideline PDF import (P1-3). OWNER: AI/ML (Jiteesh).
P1-2 logo fidelity has no endpoint: it runs inside every image check (pipeline/engine.py).

Audience Preview
    POST /versions/{id}/audience          start a run (202), or return the one already running
    GET  /versions/{id}/audience          latest run; ?compare_to=<version_id> compares with any version
                                          (default: the item's previous version that has a finished run)
    GET  /versions/{id}/audience/events   SSE: `progress` events (AudienceOut JSON), then `done` | `error`
    Refused for confidential items (409) and without a Jev key (503).

Guideline PDF import (admin; FEATURE_PDF_IMPORT=0 turns it off: 501 with a friendly message)
    POST /brand/import-pdf                    multipart `file` (PDF, <= 20 pages, <= 10 MB) -> 202, status "running"
    GET  /brand/import-pdf/{import_id}        poll until "done" | "error"
    POST /brand/import-pdf/{import_id}/confirm {selected, edits, enable} -> creates rules R-IMP-NN + AuditEvent
    Proposals are kept in memory (lost on restart; the UI then gets 404 and asks to upload again).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse

from app import workspaces as W
from app.config import get_settings
from app.db import get_engine
from app.deps import AdminUser, CurrentUser, SessionDep
from app.models import AICall, AudienceRun, ContentItem, Rule, Unit, Version, utcnow
from app.pipeline import audience as A
from app.pipeline import jev
from app.pipeline import pdf_import as PI
from app.pipeline import run as pipeline_run
from app.pipeline.privacy import mask_pii_map
from app.pipeline.units import split_text
from app.schemas import (
    AudienceCompare,
    AudienceOut,
    ImportPdfConfirmIn,
    ImportPdfConfirmOut,
    ImportPdfOut,
    ProposedRule,
    RuleOut,
)
from app.services import NoBrandError, audit, current_brand

log = logging.getLogger("brandgate.innovations")
router = APIRouter(tags=["innovations"])

CONFIDENTIAL_MESSAGE = ("Audience Preview sends the content to an external AI service, so it isn't available for "
                        "confidential items.")
NO_JEV_MESSAGE = "Audience Preview needs the AI check, and it isn't configured on this server."
STALE_MESSAGE = "The server restarted while this audience run was in progress. Start it again."
OLD_RUN_MESSAGE = "The audience model was updated since this preview was made. Run it again to see the new results."
FAILED_MESSAGE = "The audience run failed. Try again in a moment."

_active: set = set()  # W.key(version id) of audience runs in progress (this process)


def _busy(ws_id: Optional[str]) -> bool:
    """An audience run or a guideline PDF import of this workspace is still running."""
    if ws_id is None:
        audience = any(not isinstance(k, tuple) for k in list(_active))
    else:
        audience = any(isinstance(k, tuple) and k[0] == ws_id for k in list(_active))
    return audience or any(j.get("status") == "running" and j.get("ws") == ws_id for j in list(_imports.values()))


W.busy_checks.append(_busy)


# ------------------------------------------------------------------ helpers
def _version_item(session: Session, version_id: int) -> tuple[Version, ContentItem]:
    v = session.get(Version, version_id)
    item = session.get(ContentItem, v.item_id) if v else None
    if v is None or item is None:
        raise HTTPException(status_code=404, detail="That version doesn't exist (the demo may have been reset).")
    return v, item


def _latest_run(session: Session, version_id: int, *, done_only: bool = False) -> Optional[AudienceRun]:
    q = select(AudienceRun).where(AudienceRun.version_id == version_id)
    if done_only:
        q = q.where(AudienceRun.status == "done")
    return session.exec(q.order_by(AudienceRun.id.desc())).first()  # type: ignore[union-attr]


def _mark_stale(session: Session, run: AudienceRun) -> None:
    if run.status == "running" and W.key(run.version_id) not in _active:
        run.status, run.error, run.updated_at = "error", STALE_MESSAGE, utcnow()
        session.add(run)
        session.commit()
        session.refresh(run)


def _compare_target(session: Session, v: Version, compare_to: Optional[int]) -> Optional[tuple[Version, AudienceRun]]:
    if compare_to is not None:
        other = session.get(Version, compare_to)
        run = _latest_run(session, compare_to, done_only=True) if other else None
        return (other, run) if other and run else None
    prev = session.exec(select(Version).where(Version.item_id == v.item_id, Version.n < v.n)
                        .order_by(Version.n.desc())).all()  # type: ignore[union-attr]
    for pv in prev:
        run = _latest_run(session, pv.id, done_only=True)
        if run:
            return pv, run
    return None


def audience_out(session: Session, v: Version, item: ContentItem, compare_to: Optional[int] = None) -> AudienceOut:
    if item.confidential:
        return AudienceOut(version_id=v.id, status="unavailable", message=CONFIDENTIAL_MESSAGE)
    run = _latest_run(session, v.id)
    if run is None:
        if not get_settings().jev_configured:
            return AudienceOut(version_id=v.id, status="unavailable", message=NO_JEV_MESSAGE)
        return AudienceOut(version_id=v.id, status="none")
    _mark_stale(session, run)
    res = dict(run.results or {})
    if run.status in ("done", "running") and res.get("schema") != A.SCHEMA:
        # saved by the old distrust/unsure model (before 7 Nov): its shape no longer matches; ask for a re-run
        return AudienceOut(version_id=v.id, status="none", message=OLD_RUN_MESSAGE)
    out = AudienceOut(version_id=v.id, run_id=run.id, status=run.status, message=run.error,  # type: ignore[arg-type]
                      created_at=run.created_at, updated_at=run.updated_at, **res)
    if run.status == "done":
        tgt = _compare_target(session, v, compare_to)
        if tgt and tgt[1].id != run.id:
            pv, prun = tgt
            cmp = A.compare(res, dict(prun.results or {}), version_id=pv.id, version_n=pv.n, run_id=prun.id,
                            created_at=prun.created_at.isoformat() + "Z" if prun.created_at else None)
            if cmp:
                out = out.model_copy(update={"compare_to_previous_version": AudienceCompare.model_validate(cmp)})
    return out


def _content(session: Session, v: Version) -> tuple[str, list[dict], Optional[str], int]:
    """Masked content, paragraphs [{text (masked), char_start, char_end}] with offsets into the
    original Version.text, the image path for image versions, and how many PII items were masked."""
    image = session.exec(select(Unit).where(Unit.version_id == v.id, Unit.kind == "image")).first()
    text = v.text or ""
    masked, mapping = mask_pii_map(text)
    if image is not None and image.image_path:
        return masked, [], image.image_path, len(mapping)
    originals = split_text(text)
    masked_paras = split_text(masked)
    if len(masked_paras) != len(originals):  # masking changed the paragraph structure: mask one by one
        masked_paras = [type(o)(kind=o.kind, idx=o.idx, text=mask_pii_map(o.text)[0], char_start=o.char_start,
                                char_end=o.char_end) for o in originals]
    paras = [{"text": m.text, "char_start": o.char_start, "char_end": o.char_end} for o, m in zip(originals, masked_paras)]
    return masked, paras, None, len(mapping)


def _save_calls(calls: jev.CallLog, saved: int) -> int:
    with Session(get_engine()) as s:
        for r in calls.records[saved:]:
            s.add(AICall(version_id=r.version_id, unit_id=r.unit_id, provider=r.provider, model=r.model,
                         purpose=r.purpose, input_tokens=r.input_tokens, latency_ms=r.latency_ms, cost_usd=r.cost_usd,
                         ok=r.ok))
        s.commit()
    return len(calls.records)


async def run_audience(run_id: int) -> None:
    """Background task: own DB sessions; never raises. Takes a check slot (MAX_CONCURRENT_CHECKS)."""
    from app.pipeline import limits

    async with limits.check_slot():
        await _run_audience(run_id)


async def _run_audience(run_id: int) -> None:
    calls = jev.CallLog()
    version_id = None
    try:
        with Session(get_engine()) as s:
            run = s.get(AudienceRun, run_id)
            if run is None:
                return
            version_id = run.version_id
            calls.version_id = version_id
            v = s.get(Version, version_id)
            content, paragraphs, image_path, _pii = _content(s, v)
            item = s.get(ContentItem, v.item_id) if v is not None else None
            channel = item.channel if item is not None else None

        def on_progress(partial: dict) -> None:
            with Session(get_engine()) as s2:
                r = s2.get(AudienceRun, run_id)
                if r is not None and r.status == "running":
                    r.results = {**(r.results or {}), **partial}
                    r.updated_at = utcnow()
                    s2.add(r)
                    s2.commit()

        results = await A.simulate(content, paragraphs, image_path=image_path, calls=calls, on_progress=on_progress,
                                   channel=channel)
        _save_calls(calls, 0)
        with Session(get_engine()) as s:
            run = s.get(AudienceRun, run_id)
            run.results, run.status, run.error, run.updated_at = results, "done", None, utcnow()
            s.add(run)
            s.commit()
    except Exception as exc:  # noqa: BLE001
        log.exception("audience run %s failed", run_id)
        msg = str(exc) if isinstance(exc, RuntimeError) and str(exc) else FAILED_MESSAGE
        try:
            _save_calls(calls, 0)
            with Session(get_engine()) as s:
                run = s.get(AudienceRun, run_id)
                if run is not None:
                    run.status, run.error, run.updated_at = "error", msg, utcnow()
                    s.add(run)
                    s.commit()
        except Exception:  # noqa: BLE001
            log.exception("could not record the failed audience run %s", run_id)
    finally:
        if version_id is not None:
            _active.discard(W.key(version_id))


# ------------------------------------------------------------------ audience endpoints
@router.post("/versions/{version_id}/audience", response_model=AudienceOut, status_code=202)
async def start_audience(version_id: int, session: SessionDep, user: CurrentUser) -> AudienceOut:
    v, item = _version_item(session, version_id)
    if item.confidential:
        raise HTTPException(status_code=409, detail=CONFIDENTIAL_MESSAGE)
    if not jev.available():
        raise HTTPException(status_code=503, detail=NO_JEV_MESSAGE)
    if W.key(version_id) in _active:
        return audience_out(session, v, item)
    if not (v.text or "").strip() and not session.exec(select(Unit).where(Unit.version_id == v.id, Unit.kind == "image")).first():
        raise HTTPException(status_code=422, detail="This version has no text or image to show the audience.")
    run = AudienceRun(version_id=v.id, status="running",
                      results={"schema": A.SCHEMA, "progress": {"done": 0, "total": len(A.load_readers())}})
    session.add(run)
    session.commit()
    session.refresh(run)
    _active.add(W.key(version_id))
    try:
        pipeline_run.schedule(run_audience(run.id))
    except Exception:  # noqa: BLE001
        _active.discard(W.key(version_id))
        run.status, run.error = "error", FAILED_MESSAGE
        session.add(run)
        session.commit()
    return audience_out(session, v, item)


@router.get("/versions/{version_id}/audience", response_model=AudienceOut)
def get_audience(version_id: int, session: SessionDep, user: CurrentUser,
                 compare_to: Optional[int] = Query(default=None, description="Compare with this version's latest run")) -> AudienceOut:
    v, item = _version_item(session, version_id)
    return audience_out(session, v, item, compare_to)


@router.get("/versions/{version_id}/audience/events")
async def audience_events(version_id: int, session: SessionDep, user: CurrentUser) -> EventSourceResponse:
    """SSE: `progress` (AudienceOut JSON) whenever more personas have answered, then `done` or `error`
    (also AudienceOut). Pass `?token=` if cookies are blocked."""
    _version_item(session, version_id)

    async def gen():  # noqa: ANN202
        last = None
        for _ in range(600):  # ~2 minutes at most
            with Session(get_engine()) as s:
                v, item = _version_item(s, version_id)
                out = audience_out(s, v, item)
            key = (out.status, out.progress.done if out.progress else None, out.updated_at)
            if out.status != "running":
                yield {"event": "done" if out.status == "done" else "error", "data": out.model_dump_json()}
                return
            if key != last:
                last = key
                yield {"event": "progress", "data": out.model_dump_json()}
            await asyncio.sleep(0.2)
        yield {"event": "error", "data": AudienceOut(version_id=version_id, status="error",
                                                     message="Timed out waiting for the audience run.").model_dump_json()}

    return EventSourceResponse(gen(), ping=15)


# ------------------------------------------------------------------ guideline PDF import (P1-3)
PDF_DISABLED_MESSAGE = "Guideline PDF import is switched off on this server. Add rules one by one in the Brand Kit."
NO_LLM_MESSAGE = "Guideline PDF import needs the writing assistant, and it isn't configured on this server."
IMPORT_GONE_MESSAGE = "That import has expired (the server may have restarted). Upload the PDF again."
MAX_IMPORTS = 20

_imports: dict[str, dict] = {}  # import_id -> job (in memory, newest last)


def _job(import_id: str) -> dict:
    """The import job, only from the workspace that started it."""
    job = _imports.get(import_id)
    if job is None or job.get("ws") != W.current_id():
        raise HTTPException(status_code=404, detail=IMPORT_GONE_MESSAGE)
    return job


def _pdf_enabled() -> None:
    if not get_settings().feature_pdf_import:
        raise HTTPException(status_code=501, detail=PDF_DISABLED_MESSAGE)


def _import_out(job: dict) -> ImportPdfOut:
    props = [ProposedRule.model_validate(p) for p in job.get("proposals", [])]
    return ImportPdfOut(import_id=job["id"], status=job["status"], filename=job["filename"], pages=job["pages"],
                        proposals=props, verified=sum(1 for p in props if p.quote_verified),
                        warnings=job.get("warnings", []), message=job.get("message"), elapsed_ms=job.get("elapsed_ms"),
                        cost_usd=job.get("cost_usd"), confirmed=job.get("confirmed", False),
                        created_codes=job.get("created_codes", []))


async def run_import(import_id: str, pages: list[str]) -> None:
    """Background task; never raises. Logs every LLM call to ai_calls (no content)."""
    job = _imports.get(import_id)
    if job is None:
        return
    from app.pipeline import limits

    calls = jev.CallLog()
    try:
        async with limits.check_slot():
            res = await PI.propose_rules(pages, calls)
        job.update(status="done", proposals=res["proposals"], warnings=res["warnings"],
                   elapsed_ms=res["elapsed_ms"], cost_usd=res["cost_usd"])
    except PI.PdfImportError as exc:
        job.update(status="error", message=exc.message, cost_usd=round(calls.cost_usd, 6))
    except Exception:  # noqa: BLE001
        log.exception("pdf import %s failed", import_id)
        job.update(status="error", message="The import failed. Try again in a moment.", cost_usd=round(calls.cost_usd, 6))
    finally:
        try:
            _save_calls(calls, 0)
        except Exception:  # noqa: BLE001
            log.exception("could not log pdf import calls")


@router.post("/brand/import-pdf", response_model=ImportPdfOut, status_code=202)
async def import_pdf(user: AdminUser, file: UploadFile = File(...)) -> ImportPdfOut:
    _pdf_enabled()
    from app.pipeline import explain as X

    if not X.available():
        raise HTTPException(status_code=503, detail=NO_LLM_MESSAGE)
    data = await file.read(get_settings().max_upload_bytes + 1)
    if len(data) > get_settings().max_upload_bytes:
        raise HTTPException(status_code=413, detail="That file is larger than 10 MB.")
    try:
        from app.pipeline import limits

        async with limits.check_slot():  # pdfplumber on 20 pages is one of the heavier jobs
            pages = await asyncio.to_thread(PI.extract_pages, data)
    except PI.PdfImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    import uuid

    import_id = uuid.uuid4().hex[:12]
    _imports[import_id] = {"id": import_id, "status": "running", "filename": file.filename or "guideline.pdf",
                           "pages": len(pages), "user_id": user.id, "page_text": pages, "ws": W.current_id()}
    while len(_imports) > MAX_IMPORTS:
        _imports.pop(next(iter(_imports)))
    pipeline_run.schedule(run_import(import_id, pages))
    return _import_out(_imports[import_id])


@router.get("/brand/import-pdf/{import_id}", response_model=ImportPdfOut)
def get_import(import_id: str, user: AdminUser) -> ImportPdfOut:
    _pdf_enabled()
    job = _job(import_id)
    return _import_out(job)


@router.post("/brand/import-pdf/{import_id}/confirm", response_model=ImportPdfConfirmOut)
def confirm_import(import_id: str, body: ImportPdfConfirmIn, session: SessionDep, user: AdminUser) -> ImportPdfConfirmOut:
    _pdf_enabled()
    job = _job(import_id)
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail="The proposals aren't ready yet." if job["status"] == "running"
                            else "This import failed, so there is nothing to confirm. Upload the PDF again.")
    if job.get("confirmed"):
        raise HTTPException(status_code=409, detail="These rules were already imported: "
                            + ", ".join(job.get("created_codes", [])) + ".")
    props = {p["id"]: p for p in job["proposals"]}
    unknown = [i for i in list(body.selected) + list(body.edits) if i not in props]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown proposal id(s): {', '.join(sorted(set(unknown)))}.")
    try:
        brand = current_brand(session)
    except NoBrandError as exc:
        raise HTTPException(status_code=409, detail="Set up the brand before importing rules.") from exc
    selected = list(dict.fromkeys(body.selected))
    existing = [r.code for r in session.exec(select(Rule).where(Rule.brand_id == brand.id)).all()]
    codes = PI.next_codes(existing, len(selected))
    created: list[Rule] = []
    for code, pid in zip(codes, selected):
        draft = dict(props[pid]["rule"])
        edit = body.edits.get(pid)
        if edit is not None:
            draft.update({k: v for k, v in edit.model_dump(exclude_none=True).items()})
        rule = Rule(brand_id=brand.id, code=code, category=draft["category"], severity=draft["severity"],
                    statement=draft["statement"], source_quote=draft["source_quote"], page=draft["page"],
                    check_method=draft["check_method"], params={"imported_from": import_id},
                    examples_violating=draft.get("examples_violating", []), examples_ok=draft.get("examples_ok", []),
                    enabled=body.enable)
        session.add(rule)
        created.append(rule)
    audit(session, "rules_imported", user_id=user.id,
          detail={"import_id": import_id, "filename": job["filename"], "codes": codes,
                  "unverified_quotes": [c for c, pid in zip(codes, selected) if not props[pid]["quote_verified"]],
                  "edited": sorted(body.edits), "enabled": body.enable})
    session.commit()
    for r in created:
        session.refresh(r)
    job.update(confirmed=True, created_codes=codes)
    return ImportPdfConfirmOut(import_id=import_id, created=[RuleOut.model_validate(r) for r in created])

