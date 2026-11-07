"""Split content into checkable units + enforce upload limits. OWNER: backend (implemented).

Units:
- text: paragraphs separated by blank lines; offsets point into the text.
- PDF: pdfplumber per page; paragraphs found by blank lines or vertical gaps between lines;
  each keeps its 1-based page. Version.text = paragraphs joined by "\\n\\n" (offsets point into it).
- image: one `image` unit, plus `caption` and `alt_text` text units when given.
  Version.text = caption (so caption findings can highlight spans).

Pure functions only (no DB). The router turns `UnitDraft`s into `Unit` rows.
"""
from __future__ import annotations

import io
import logging
import re
import statistics
from dataclasses import dataclass
from typing import Literal, Optional

from app.config import get_settings

UploadKind = Literal["image", "pdf"]

logging.getLogger("pdfminer").setLevel(logging.ERROR)  # noisy FontBBox warnings

_PARA_SPLIT = re.compile(r"\n[ \t]*\n+")

IMAGE_MIME = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}


class UploadError(ValueError):
    """Raised with a human message; routers map it to `status_code`."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class UnitDraft:
    kind: str  # text|image|caption|alt_text
    idx: int
    text: str = ""
    page: Optional[int] = None
    image_path: Optional[str] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None


@dataclass
class SplitResult:
    text: str  # what goes into Version.text
    units: list[UnitDraft]


# ------------------------------------------------------------------ uploads
def sniff_kind(data: bytes) -> tuple[UploadKind, str, str] | None:
    """Return (kind, mime, extension) from magic bytes, or None if unsupported."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png", "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg", "jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image", "image/webp", "webp"
    if data[:1024].lstrip().startswith(b"%PDF-"):
        return "pdf", "application/pdf", "pdf"
    return None


def validate_upload(filename: str, data: bytes) -> tuple[UploadKind, str, str]:
    """Check size and type. Returns (kind, mime, ext). Raises UploadError with a human message.

    Limits: 10 MB; PNG/JPG/WEBP images; PDFs up to 20 pages (page count checked in split_pdf).
    The extension in `filename` is ignored; we trust the bytes.
    """
    s = get_settings()
    if not data:
        raise UploadError("The file is empty.")
    if len(data) > s.max_upload_bytes:
        mb = len(data) / (1024 * 1024)
        raise UploadError(f"That file is {mb:.1f} MB. The limit is {s.max_upload_bytes // (1024 * 1024)} MB.", 413)
    sniffed = sniff_kind(data)
    if sniffed is None:
        raise UploadError(
            f"“{filename or 'This file'}” isn't a supported type. Upload a PNG, JPG or WEBP image, or a PDF.", 415
        )
    kind, mime, ext = sniffed
    if kind == "image":
        from PIL import Image

        try:
            with Image.open(io.BytesIO(data)) as im:
                im.verify()
        except Exception as exc:  # noqa: BLE001
            raise UploadError("This image looks damaged and can't be opened. Try exporting it again.") from exc
    return kind, mime, ext


# ------------------------------------------------------------------ text
def split_text(text: str, start_idx: int = 0, kind: str = "text") -> list[UnitDraft]:
    """Paragraphs separated by one or more blank lines. Offsets index into `text`."""
    units: list[UnitDraft] = []
    pos = 0
    for chunk in _PARA_SPLIT.split(text):
        found = text.find(chunk, pos)
        start = found if found >= 0 else pos
        pos = start + len(chunk)
        stripped = chunk.strip()
        if not stripped:
            continue
        lead = len(chunk) - len(chunk.lstrip())
        s0 = start + lead
        units.append(UnitDraft(kind=kind, idx=start_idx + len(units), text=stripped, char_start=s0, char_end=s0 + len(stripped)))
    return units


def validate_text(text: str) -> str:
    s = get_settings()
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > s.max_text_chars:
        raise UploadError(f"The text is {len(text):,} characters. The limit is {s.max_text_chars:,}.", 413)
    return text


# ------------------------------------------------------------------ pdf
def _page_paragraphs(page) -> list[str]:  # noqa: ANN001  (pdfplumber.page.Page)
    """Group a page's text lines into paragraphs by vertical gaps."""
    try:
        lines = page.extract_text_lines(return_chars=False)
    except Exception:  # noqa: BLE001
        lines = []
    if not lines:
        raw = page.extract_text() or ""
        return [p.strip() for p in _PARA_SPLIT.split(raw) if p.strip()]

    heights = [max(1.0, ln["bottom"] - ln["top"]) for ln in lines]
    med_h = statistics.median(heights)
    gaps = [lines[i]["top"] - lines[i - 1]["bottom"] for i in range(1, len(lines))]
    pos_gaps = [g for g in gaps if g > 0]
    med_gap = statistics.median(pos_gaps) if pos_gaps else 0.0
    threshold = max(med_gap * 1.8, med_h * 0.6)

    paras: list[list[str]] = [[lines[0]["text"]]]
    for i in range(1, len(lines)):
        if gaps[i - 1] > threshold:
            paras.append([])
        paras[-1].append(lines[i]["text"])
    return [" ".join(t.strip() for t in p if t.strip()) for p in paras if any(t.strip() for t in p)]


def split_pdf(data: bytes) -> SplitResult:
    """Extract paragraphs with 1-based page numbers. Raises UploadError if >20 pages or no text."""
    import pdfplumber

    s = get_settings()
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise UploadError("This PDF can't be opened. It may be damaged or password-protected.") from exc
    with pdf:
        n_pages = len(pdf.pages)
        if n_pages > s.max_pdf_pages:
            raise UploadError(f"This PDF has {n_pages} pages. The limit is {s.max_pdf_pages}.", 413)
        units: list[UnitDraft] = []
        parts: list[str] = []
        offset = 0
        for pno, page in enumerate(pdf.pages, start=1):
            for para in _page_paragraphs(page):
                if parts:
                    offset += 2  # "\n\n"
                units.append(UnitDraft(kind="text", idx=len(units), text=para, page=pno, char_start=offset, char_end=offset + len(para)))
                parts.append(para)
                offset += len(para)
    if not units:
        raise UploadError("No text found in this PDF. Scanned PDFs aren't supported yet; upload a text-based PDF.", 422)
    return SplitResult(text="\n\n".join(parts), units=units)


# ------------------------------------------------------------------ image
def image_units(image_path: str, caption: Optional[str], alt_text: Optional[str]) -> SplitResult:
    """One image unit, then caption paragraphs (kind=caption, offsets into caption), then alt text."""
    caption = (caption or "").strip()
    units = [UnitDraft(kind="image", idx=0, image_path=image_path)]
    if caption:
        units += split_text(caption, start_idx=1, kind="caption")
    if alt_text and alt_text.strip():
        units.append(UnitDraft(kind="alt_text", idx=len(units), text=alt_text.strip()))
    return SplitResult(text=caption, units=units)


def text_units(text: str) -> SplitResult:
    text = validate_text(text)
    units = split_text(text)
    if not units:
        raise UploadError("There's no text to check. Paste some content or upload a file.", 422)
    return SplitResult(text=text, units=units)
