"""PII masking for Indian formats before any external call."""
from __future__ import annotations

import pytest

from app.pipeline.privacy import mask_pii_map, unmask


@pytest.mark.parametrize("text,kind,original", [
    ("Call +91 98765 43210 now", "phone", "+91 98765 43210"),
    ("फोन करें +91-9876543210", "phone", "+91-9876543210"),
    ("फोन करें 98765 43210 पर", "phone", "98765 43210"),
    ("मो. ९८७६५ ४३२१०", "phone", "९८७६५ ४३२१०"),
    ("संपर्क +९१ ९८७६५४३२१०", "phone", "+९१ ९८७६५४३२१०"),
    ("Aadhaar 2345 6789 0123 attached", "id", "2345 6789 0123"),
    ("आधार: 2345-6789-0123", "id", "2345-6789-0123"),
    ("PAN ABCPE1234F on file", "id", "ABCPE1234F"),
    ("UPI: saffron.steam@okicici", "upi", "saffron.steam@okicici"),
    ("pay to 9876543210@ybl today", "upi", "9876543210@ybl"),
])
def test_indian_pii_masked(text: str, kind: str, original: str) -> None:
    masked, mapping = mask_pii_map(text)
    assert original not in masked
    assert mapping.get(f"[{kind}_1]") == original
    assert unmask(masked, mapping) == text


@pytest.mark.parametrize("text", [
    "Rs 1,299 incl. taxes", "12 cafes, 50,000 followers, 1,00,000 cups", "Use code MONSOON20 or DIWALI15.",
    "आमचे १२ कॅफे, १ लाख फॉलोअर्स", "Saffron Signature Roast 250g", "Write to hello@saffronsteam.in",
])
def test_brand_numbers_and_codes_are_not_masked(text: str) -> None:
    masked, mapping = mask_pii_map(text)
    assert all(k.startswith("[email") for k in mapping), mapping
