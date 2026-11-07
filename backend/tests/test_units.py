from __future__ import annotations

import io

import pytest
from PIL import Image

from app.pipeline import units as U


def test_split_text_offsets() -> None:
    text = "  First para.\n\n\nSecond\nline.\n \nThird."
    us = U.split_text(text)
    assert [u.text for u in us] == ["First para.", "Second\nline.", "Third."]
    for u in us:
        assert text[u.char_start:u.char_end] == u.text


def test_validate_upload_limits() -> None:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, "PNG")
    assert U.validate_upload("a.png", buf.getvalue())[0] == "image"
    with pytest.raises(U.UploadError) as e:
        U.validate_upload("a.txt", b"hello")
    assert e.value.status_code == 415
    with pytest.raises(U.UploadError) as e:
        U.validate_upload("big.png", b"\x89PNG\r\n\x1a\n" + b"0" * (10 * 1024 * 1024 + 1))
    assert e.value.status_code == 413


def test_image_units() -> None:
    r = U.image_units("/tmp/x.png", "Caption one.\n\nCaption two.", "A cup of coffee")
    assert [u.kind for u in r.units] == ["image", "caption", "caption", "alt_text"]
    assert r.text == "Caption one.\n\nCaption two."


def test_pdf_without_text_is_rejected() -> None:
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(buf, "PDF")
    with pytest.raises(U.UploadError) as e:
        U.split_pdf(buf.getvalue())
    assert "No text found" in e.value.message
