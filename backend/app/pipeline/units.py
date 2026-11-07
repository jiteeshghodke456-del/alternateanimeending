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
import threading
import unicodedata
from dataclasses import dataclass
from typing import Literal, Optional

from app.config import get_settings

UploadKind = Literal["image", "pdf"]

logging.getLogger("pdfminer").setLevel(logging.ERROR)  # noisy FontBBox warnings

_PARA_SPLIT = re.compile(r"\n[ \t]*\n+")

IMAGE_MIME = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}

# Image memory guard (Render free tier = 512 MiB). A 10 MB JPEG can hold 48+ MP, and decoding plus RGBA
# copies of that cost 1+ GiB. So uploads are capped by pixel count and stored downscaled, once.
MAX_IMAGE_PIXELS = 40_000_000  # 40 MP; larger uploads get a 413 with a human message
# PNG/WEBP can't be decoded at reduced scale (JPEG can, via draft), and Pillow keeps 4 bytes per pixel,
# so a 40 MP PNG alone costs ~160 MB. Formats that must be fully decoded get a lower cap (~100 MB).
MAX_FULL_DECODE_PIXELS = 24_000_000
STORE_MAX_SIDE = 2048  # every accepted image is stored with its long side <= this


def _pil():  # noqa: ANN202  (PIL.Image module)
    """Pillow with the decompression-bomb limit set to our cap. Pillow raises DecompressionBombError
    above 2x MAX_IMAGE_PIXELS while reading the header; we reject anything over 1x ourselves."""
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    return Image


def _open_checked(data: bytes):  # noqa: ANN202  (PIL.Image.Image, lazy: header only)
    """Image.open (reads the header only) + the pixel cap. Raises UploadError: 413 too big, 400 damaged."""
    import warnings

    Image = _pil()
    from PIL import UnidentifiedImageError

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)  # we enforce the cap ourselves
            im = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError as exc:
        is_jpeg = data.startswith(b"\xff\xd8\xff")
        raise UploadError(_too_big_message(None, MAX_IMAGE_PIXELS if is_jpeg else MAX_FULL_DECODE_PIXELS), 413) from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UploadError("This image looks damaged and can't be opened. Try exporting it again.") from exc
    try:
        check_pixels(im.size, im.format or "")
    except UploadError:
        im.close()
        raise
    return im


def _orientation(im) -> int:  # noqa: ANN001
    """EXIF orientation without decoding pixels. PngImageFile.getexif() loads the whole image to look for
    an eXIf chunk after the pixel data, so for PNG only the header-side chunk (im.info) is read."""
    try:
        if im.format == "PNG":
            raw = im.info.get("exif")
            if not raw:
                return 1
            ex = _pil().Exif()
            ex.load(raw)
            return int(ex.get(0x0112, 1) or 1)
        return int(im.getexif().get(0x0112, 1) or 1)
    except Exception:  # noqa: BLE001
        return 1


_DECODE_LOCK = threading.Lock()  # one full-size decode at a time per process (bounds peak memory)


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
        im = _open_checked(data)
        try:
            im.verify()
        except Exception as exc:  # noqa: BLE001
            raise UploadError("This image looks damaged and can't be opened. Try exporting it again.") from exc
        finally:
            im.close()
    return kind, mime, ext


def _too_big_message(size: tuple[int, int] | None, cap_px: int = MAX_IMAGE_PIXELS, fmt: str = "JPEG") -> str:
    cap = cap_px // 1_000_000
    if size is None:
        return f"This image has too many pixels. The limit is {cap} megapixels; resize it and upload again."
    w, h = size
    kind = "" if fmt == "JPEG" else f" for {fmt or 'this'} images ({MAX_IMAGE_PIXELS // 1_000_000} for JPEG)"
    return (f"This image is {w:,} × {h:,} px ({w * h / 1e6:.0f} megapixels). The limit is {cap} megapixels{kind}; "
            f"resize it (for example to {STORE_MAX_SIDE} px on the long side) and upload again.")


def check_pixels(size: tuple[int, int], fmt: str = "JPEG") -> None:
    """Raise a 413 UploadError if width x height is over the cap for this format (header size only):
    MAX_IMAGE_PIXELS for JPEG, MAX_FULL_DECODE_PIXELS for PNG/WEBP, which must be decoded at full size."""
    w, h = size
    cap = MAX_IMAGE_PIXELS if fmt == "JPEG" else MAX_FULL_DECODE_PIXELS
    if w * h > cap:
        raise UploadError(_too_big_message((w, h), cap, fmt), 413)


def open_image_small(data: bytes, max_side: int = STORE_MAX_SIDE):  # noqa: ANN201  (PIL.Image.Image)
    """Decode `data` with its long side <= max_side, upright (EXIF orientation applied), pixels loaded.

    Checks the pixel cap from the header before decoding. JPEGs are decoded at 1/2, 1/4 or 1/8 scale via
    `draft()` when that still leaves at least `max_side`, so a 48 MP photo never exists in memory at full
    size. Returns the image and its original format ("PNG", "JPEG", "WEBP"). Raises UploadError.
    """
    Image = _pil()

    im = _open_checked(data)
    fmt = im.format or ""
    exif_orientation = _orientation(im)
    w, h = im.size
    scale = min(1.0, max_side / max(w, h))
    target = (max(1, round(w * scale)), max(1, round(h * scale)))
    try:
        with _DECODE_LOCK:
            if fmt == "JPEG" and scale < 1.0:
                im.draft(im.mode, target)  # DCT scaling: decodes at the smallest 1/n size >= target
            im.load()
            w, h = im.size
            f = max(w, h) // max_side
            if f >= 2 and im.mode in ("RGBA", "LA"):
                # Image.reduce/thumbnail would first make a full-size premultiplied copy (4 bytes/pixel);
                # the core box reduce works in place on straight alpha. Fine at this downscale.
                im = im._new(im.im.reduce((f, f), (0, 0, w, h)))
            if max(im.size) > max_side:
                im.thumbnail((max_side, max_side), Image.LANCZOS)
    except UploadError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise UploadError("This image looks damaged and can't be opened. Try exporting it again.") from exc
    if exif_orientation != 1:
        method = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180, 4: Image.Transpose.FLIP_TOP_BOTTOM,
                  5: Image.Transpose.TRANSPOSE, 6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
                  8: Image.Transpose.ROTATE_90}.get(exif_orientation)
        if method is not None:
            im = im.transpose(method)  # the stored file carries no EXIF, so nothing rotates it twice
    return im, fmt


def _sensible_mode(im):  # noqa: ANN001, ANN202
    """RGB, or RGBA when the image has transparency. CMYK, palette, grey, 16-bit and 1-bit all map here."""
    has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info) \
        or (im.mode in ("RGB", "L") and "transparency" in im.info)
    if im.mode in ("I;16", "I;16B", "I;16L", "I"):
        im = im.convert("I").point(lambda v: v * (1 / 256)).convert("L")  # 16-bit grey -> 8-bit
    elif im.mode == "F":
        im = im.convert("L")
    if has_alpha:
        return im.convert("RGBA")
    return im.convert("RGB")


def normalise_image(data: bytes, max_side: int = STORE_MAX_SIDE) -> tuple[bytes, str, str]:
    """Return (bytes, mime, ext) to store for an accepted image: long side <= max_side, upright,
    RGB or RGBA, PNG or JPEG only. Every later step (Jev, palette, logo fidelity) then works on this small
    file. A file that is already small, upright, RGB(A) PNG/JPEG is returned unchanged (no re-encode).
    JPEG stays JPEG (quality 92); PNG and WEBP become PNG, except an opaque WEBP, which becomes JPEG.
    Raises UploadError (413 over the pixel cap, 400 when damaged)."""
    Image = _pil()

    with _open_checked(data) as probe:
        fmt0, size0, mode0, orient0 = probe.format, probe.size, probe.mode, _orientation(probe)
    del probe
    if fmt0 in ("PNG", "JPEG") and max(size0) <= max_side and orient0 in (None, 1) and mode0 in ("RGB", "RGBA"):
        return data, IMAGE_MIME["png" if fmt0 == "PNG" else "jpg"], "png" if fmt0 == "PNG" else "jpg"
    im, fmt = open_image_small(data, max_side)
    im = _sensible_mode(im)
    out = io.BytesIO()
    if im.mode == "RGB" and fmt in ("JPEG", "WEBP"):
        im.save(out, format="JPEG", quality=92, optimize=True)
        mime, ext = IMAGE_MIME["jpg"], "jpg"
    else:
        im.save(out, format="PNG", optimize=False, compress_level=6)
        mime, ext = IMAGE_MIME["png"], "png"
    im.close()
    return out.getvalue(), mime, ext


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
    # One Unicode form (NFC) for every stored text, so Indic words typed with a different sequence of
    # combining marks (e.g. a nukta) still match the brand word list. Offsets are computed after this.
    text = unicodedata.normalize("NFC", text)
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
            try:
                paras = _page_paragraphs(page)
            finally:
                page.close()  # drop the page's parsed objects (chars, lines) before the next page
            for para in paras:
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
