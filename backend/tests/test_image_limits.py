"""Regression for the image OOM (audit #4): one high-resolution upload could push the 512 MiB Render
instance over its limit, because full-size images were decoded and converted to RGBA before downscaling.

Now every accepted image is checked against a 40 MP cap from its header (413 above it) and stored ONCE
downscaled to <= 2048 px, upright, RGB/RGBA, PNG or JPEG. Logo fidelity and the palette check still work
on the stored copy. No external calls.
"""
from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.pipeline import logo as L
from app.pipeline import tier0
from app.pipeline import units as U
from conftest import login
from pipeline_helpers import SEED, seed_ctx, seed_rules

LOGO = str(SEED / "logo.png")


def _png_header_only(w: int, h: int) -> bytes:
    """A PNG whose header claims w x h. Pillow reads the size without decoding, so a test can stand in
    for a huge upload (a 12000 x 12000 PNG compresses to ~400 KB but decodes to 432 MB)."""
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00" * 16)) + chunk(b"IEND", b"")


def _encode(im: Image.Image, fmt: str, **kw) -> bytes:  # noqa: ANN003
    buf = io.BytesIO()
    im.save(buf, format=fmt, **kw)
    return buf.getvalue()


def _open(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


# ------------------------------------------------------------------ pixel cap
@pytest.mark.parametrize("w,h", [(7000, 6000), (8000, 6000), (12000, 12000)])
def test_over_40_megapixels_is_a_413_with_a_human_message(w: int, h: int) -> None:
    data = _png_header_only(w, h)
    for fn in (lambda: U.validate_upload("big.png", data), lambda: U.normalise_image(data)):
        with pytest.raises(U.UploadError) as e:
            fn()
        assert e.value.status_code == 413
        assert "megapixels" in e.value.message and "Traceback" not in e.value.message


def test_decompression_bomb_limit_matches_the_cap() -> None:
    U._pil()
    assert Image.MAX_IMAGE_PIXELS == U.MAX_IMAGE_PIXELS == 40_000_000


def test_just_under_the_cap_is_accepted() -> None:
    U.check_pixels((8000, 5000), "JPEG")  # exactly 40 MP
    with pytest.raises(U.UploadError):
        U.check_pixels((8000, 5001), "JPEG")
    # PNG/WEBP are decoded at full size (no reduced-scale decode), so their cap is lower
    U.check_pixels((6000, 4000), "PNG")
    with pytest.raises(U.UploadError) as e:
        U.check_pixels((7000, 4000), "PNG")
    assert "24 megapixels for PNG images (40 for JPEG)" in e.value.message


def test_big_rgba_png_is_reduced_with_alpha_kept() -> None:
    im = Image.new("RGBA", (4400, 2200), (10, 120, 200, 255))
    im.paste((0, 0, 0, 0), (0, 0, 2200, 2200))
    out = _open(U.normalise_image(_encode(im, "PNG", compress_level=1))[0])
    assert out.size == (2048, 1024) and out.mode == "RGBA"
    assert out.getpixel((10, 10))[3] == 0 and out.getpixel((2000, 500)) == (10, 120, 200, 255)


# ------------------------------------------------------------------ normalisation
def test_large_jpeg_is_stored_at_2048_px_as_jpeg() -> None:
    data = _encode(Image.new("RGB", (4000, 3000), "#3B2418"), "JPEG", quality=85)
    out, mime, ext = U.normalise_image(data)
    im = _open(out)
    assert (mime, ext, im.format) == ("image/jpeg", "jpg", "JPEG")
    assert im.size == (2048, 1536) and im.mode == "RGB"


def test_small_rgb_png_and_jpeg_are_stored_unchanged() -> None:
    for fmt in ("PNG", "JPEG"):
        data = _encode(Image.new("RGB", (1080, 1080), "#F28C28"), fmt)
        assert U.normalise_image(data)[0] is data


def test_exif_orientation_is_applied_once() -> None:
    im = Image.new("RGB", (300, 100), "white")
    im.paste(Image.new("RGB", (30, 100), "red"), (0, 0))  # red stripe on the left before rotation
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotate 90 CW to display"
    out, _, _ = U.normalise_image(_encode(im, "JPEG", exif=exif.tobytes()))
    up = _open(out)
    assert up.size == (100, 300)
    assert up.getexif().get(0x0112, 1) == 1  # nothing downstream rotates it again
    r, g, b = up.convert("RGB").getpixel((50, 5))  # the stripe is now along the top
    assert r > 200 and g < 60


@pytest.mark.parametrize("mode,fmt,expect_mode,expect_fmt", [
    ("CMYK", "JPEG", "RGB", "JPEG"),
    ("L", "PNG", "RGB", "PNG"),
    ("LA", "PNG", "RGBA", "PNG"),
    ("I;16", "PNG", "RGB", "PNG"),
])
def test_modes_become_rgb_or_rgba(mode: str, fmt: str, expect_mode: str, expect_fmt: str) -> None:
    out, mime, _ = U.normalise_image(_encode(Image.new(mode, (300, 200)), fmt))
    im = _open(out)
    assert (im.mode, im.format) == (expect_mode, expect_fmt)
    assert mime == ("image/jpeg" if expect_fmt == "JPEG" else "image/png")


def test_palette_png_with_transparency_keeps_alpha() -> None:
    p = Image.new("P", (300, 200), 0)
    p.putpalette([255, 0, 0, 0, 0, 255] + [0] * 762)
    p.paste(1, (0, 0, 150, 200))
    p.info["transparency"] = 0
    im = _open(U.normalise_image(_encode(p, "PNG", transparency=0))[0])
    assert im.mode == "RGBA" and im.getpixel((250, 100))[3] == 0 and im.getpixel((10, 100))[3] == 255


def test_webp_becomes_png_or_jpeg() -> None:
    opaque = _open(U.normalise_image(_encode(Image.new("RGB", (300, 200), "blue"), "WEBP"))[0])
    alpha = _open(U.normalise_image(_encode(Image.new("RGBA", (300, 200), (0, 0, 255, 128)), "WEBP"))[0])
    assert opaque.format == "JPEG" and alpha.format == "PNG" and alpha.mode == "RGBA"


# ------------------------------------------------------------------ downstream checks still work
def _big_copy(tmp_path: Path, name: str, side: int) -> str:
    """The seed ad upscaled to side x side, uploaded as a JPEG, then normalised the way the API stores it."""
    with Image.open(SEED / "content" / f"{name}.png") as im:
        big = im.convert("RGB").resize((side, side), Image.LANCZOS)
    stored, _, ext = U.normalise_image(_encode(big, "JPEG", quality=92))
    p = tmp_path / f"{name}_{side}.{ext}"
    p.write_bytes(stored)
    return str(p)


def test_logo_fidelity_survives_normalisation_of_a_big_upload(tmp_path: Path) -> None:
    path = _big_copy(tmp_path, "ad_good", 4320)  # 18.7 MP upload -> stored at 2048 px
    assert _open(Path(path).read_bytes()).size == (2048, 2048)
    m = L.match_logo(path, LOGO)
    assert m.error is None and m.valid_outline and m.inliers >= 40, m.inliers
    assert L.decide(m).verdict == "genuine"
    wrong = L.match_logo(_big_copy(tmp_path, "ad_wronglogo", 4320), LOGO)
    assert L.decide(wrong).verdict != "genuine"


def test_palette_check_survives_normalisation_of_a_big_upload(tmp_path: Path) -> None:
    rule = next(r for r in seed_rules() if r.code == "R-VIS-02")
    pal = seed_ctx().palette
    assert tier0.check_palette(rule, _big_copy(tmp_path, "ad_offpalette", 4320), pal)
    assert tier0.check_palette(rule, _big_copy(tmp_path, "ad_good", 4320), pal) == []


# ------------------------------------------------------------------ API
def test_api_rejects_a_huge_image_with_413(client: TestClient) -> None:
    login(client, "creator")
    r = client.post("/api/items", data={"title": "huge", "channel": "instagram", "caption": "Hi."},
                    files={"file": ("huge.png", _png_header_only(12000, 12000), "image/png")})
    assert r.status_code == 413 and "megapixels" in r.json()["detail"]


def test_api_stores_a_big_upload_downscaled(client: TestClient) -> None:
    from sqlmodel import Session

    from app.db import get_engine
    from app.models import Version

    login(client, "creator")
    data = _encode(Image.new("RGB", (5000, 2500), "#3B2418"), "JPEG", quality=80)
    r = client.post("/api/items", data={"title": "wide", "channel": "instagram", "caption": "Hi.", "confidential": "true"},
                    files={"file": ("wide.jpg", data, "image/jpeg")})
    assert r.status_code == 201, r.text
    with Session(get_engine()) as s:
        v = s.get(Version, r.json()["version_id"])
        assert v is not None and v.mime == "image/jpeg"
        with Image.open(v.file_path) as im:
            assert im.size == (2048, 1024)


def test_logo_upload_over_the_cap_is_a_413(client: TestClient) -> None:
    login(client, "admin")
    r = client.post("/api/brand/logo", files={"file": ("logo.png", _png_header_only(9000, 9000), "image/png")})
    assert r.status_code == 413 and "megapixels" in r.json()["detail"]
