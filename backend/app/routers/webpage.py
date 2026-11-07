"""Web page source: POST /api/items/from-url. OWNER: backend.

Fetches a public page (pipeline/webfetch.py: SSRF-guarded, robots.txt, size and time caps), extracts its main
text and up to 4 content images (pipeline/webpage.py), audits basic accessibility in code, and creates an item
whose version (kind "web") has one text unit per heading/paragraph and one image unit per kept image. Then the
normal check runs (findings, claims, palette, logo fidelity, fixes) plus the accessibility findings.

The version's file is the fetch record (JSON: url, final URL, fetch time, counts, accessibility issues), never
the page's HTML. The page fetch is a plain HTTP request to the page's own server: no third-party service, and
it also runs for confidential items (they skip only the external AI calls).
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.deps import CurrentUser, SessionDep, ensure_role
from app.models import CHANNELS, ContentItem, Unit, Version
from app.pipeline import run as pipeline_run
from app.pipeline import units as U
from app.pipeline import webfetch as F
from app.pipeline import webpage as P
from app.schemas import ItemCreated, WebSource
from app.services import NoBrandError, audit, current_brand, save_upload

router = APIRouter(tags=["content"])

_FETCHES = asyncio.Semaphore(2)  # page fetches in flight per process (memory: HTML + up to 4 images each)


class FromUrlIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    title: Optional[str] = Field(default=None, max_length=200)
    channel: str = "website"
    confidential: bool = False


class WebItemCreated(ItemCreated):
    """Same shape as POST /items (item_id, version_id), plus what was extracted from the page."""

    page: WebSource


async def _extract(url: str) -> tuple[F.FetchedPage, P.ParsedPage, list[F.FetchedImage], int]:
    page = await F.fetch_page(url)
    parsed = await asyncio.to_thread(P.parse_page, page.html, page.final_url)
    page.html = ""  # the HTML isn't kept: only the extracted text, images and the audit
    cands = P.image_candidates(parsed)
    images, skipped = await F.fetch_images([c.url for c in cands])
    return page, parsed, images, skipped + max(0, len(cands) - len(images) - skipped)


def _normalise(images: list[F.FetchedImage]) -> list[tuple[F.FetchedImage, str]]:
    """Store each kept image once, through the same normalisation and pixel caps as uploads."""
    out: list[tuple[F.FetchedImage, str]] = []
    for im in images:
        try:
            data, _mime, ext = U.normalise_image(im.data)
        except U.UploadError:
            continue
        out.append((im, save_upload(data, ext)))
        im.data = b""
    return out


@router.post("/items/from-url", response_model=WebItemCreated, status_code=201)
async def create_item_from_url(body: FromUrlIn, session: SessionDep, user: CurrentUser) -> WebItemCreated:
    """Fetch a public web page and check it. Errors are human messages: blocked address (400), robots.txt
    disallow (403), too large (413), not HTML (415), unreachable (502), timeout (504), nothing readable (422)."""
    ensure_role(user, "creator", "admin")
    channel = body.channel.strip().lower()
    if channel not in CHANNELS:
        raise HTTPException(status_code=422, detail=f"Pick a channel: {', '.join(c.title() for c in CHANNELS)}.")
    try:
        brand = current_brand(session)
    except NoBrandError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    started = time.perf_counter()
    try:
        async with _FETCHES:
            page, parsed, images, skipped = await _extract(body.url)
            stored = await asyncio.to_thread(_normalise, images)
    except F.FetchError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    skipped += len(images) - len(stored)

    units = P.to_units(parsed, get_settings().max_text_chars)
    if not units.blocks and not stored:
        raise HTTPException(status_code=422, detail=(
            "No readable text or images were found on that page. It may build its content with JavaScript, "
            "which Rulemint doesn't run. Paste the page's text on the Text tab instead."))
    chosen = [im.url for im, _ in stored]
    issues = P.audit(parsed, units, chosen)
    n_text = len(units.blocks)
    for it in issues:  # unit references -> unit idx (text blocks first, then images)
        ref = it.pop("unit_ref", None)
        it["unit_idx"] = (n_text + int(ref.split(":")[1])) if isinstance(ref, str) else ref
    fetched_at = datetime.now(timezone.utc)
    record = {
        "url": page.url, "final_url": page.final_url, "title": parsed.title, "fetched_at": fetched_at.isoformat(),
        "elapsed_ms": int((time.perf_counter() - started) * 1000), "bytes": page.bytes,
        "paragraphs": sum(1 for b in units.blocks if not b.is_heading), "headings": sum(1 for b in units.blocks if b.is_heading),
        "images": len(stored), "images_skipped": skipped, "truncated": units.truncated, "a11y_issues": len(issues),
        "lang": parsed.lang, "robots": page.robots, "status": page.status,
        "blocks": [{"idx": i, "tag": b.tag, "heading": b.heading} for i, b in enumerate(units.blocks)],
        "block_tags": [b.tag for b in units.blocks],
        "image_list": [{"idx": n_text + i, "url": im.url, "width": im.width, "height": im.height,
                        "alt": next((r.alt for r in parsed.images if r.url == im.url), None)}
                       for i, (im, _) in enumerate(stored)],
        "a11y": issues,
    }
    title = (body.title or "").strip() or parsed.title or page.final_url
    item = ContentItem(brand_id=brand.id, title=title[:200], channel=channel, creator_id=user.id, state="checking",
                       confidential=body.confidential)
    session.add(item)
    session.flush()
    path = save_upload(json.dumps(record, ensure_ascii=False).encode("utf-8"), "json")
    version = Version(item_id=item.id, n=1, kind="web", text=units.text, file_path=path, mime="application/json",
                      check_status="queued")
    version.stats = {"units_total": n_text + len(stored), "units_done": 0, "judgments": 0, "elapsed_ms": 0, "cost_usd": 0.0}
    session.add(version)
    session.flush()
    for i, (b, (s0, e0)) in enumerate(zip(units.blocks, units.offsets)):
        session.add(Unit(version_id=version.id, kind="text", idx=i, text=b.text, char_start=s0, char_end=e0))
    for j, (_im, img_path) in enumerate(stored):
        session.add(Unit(version_id=version.id, kind="image", idx=n_text + j, image_path=img_path))
    audit(session, "created", item_id=item.id, version_id=version.id, user_id=user.id,
          detail={"kind": "web", "confidential": body.confidential, "url": page.final_url})
    session.commit()
    pipeline_run.start_check(version.id)
    src = WebSource(**{k: record[k] for k in WebSource.model_fields if k != "fetched_at"}, fetched_at=fetched_at)
    return WebItemCreated(item_id=item.id, version_id=version.id, page=src)
