from __future__ import annotations

from app.pipeline.privacy import mask_pii, mask_pii_map, unmask


def test_masks_emails_phones_and_long_digits() -> None:
    text = ("Write to asha.k+promo@example.co.in or call +91 98765 43210, 9876543210, 020-2345 6789 "
            "or +44 20 7946 0958. Order id 123456789012.")
    masked, n = mask_pii(text)
    assert n == 6
    for leaked in ("asha.k", "98765", "9876543210", "2345 6789", "7946", "123456789012"):
        assert leaked not in masked
    assert "[email_1]" in masked and "[phone_1]" in masked and "[number_1]" in masked


def test_does_not_mask_prices_dates_or_short_numbers() -> None:
    text = "Rs 1,299 incl. taxes, valid till 15 November 2026. 12 cafes, 250g, open 8 am to 10 pm."
    assert mask_pii(text) == (text, 0)


def test_unmask_restores_originals_in_a_fix() -> None:
    masked, mapping = mask_pii_map("Call 9876543210 now!!!")
    fix = masked.replace("now!!!", "any time.")
    assert unmask(fix, mapping) == "Call 9876543210 any time."
