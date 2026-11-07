"""Tier 0: deterministic checks driven by seed rule params."""
from __future__ import annotations

import pytest

from app.pipeline import tier0
from app.pipeline.context import RuleSpec
from pipeline_helpers import SEED, seed_ctx, seed_rules


def hits_for(text: str, channel: str = "instagram") -> list[tuple[str | None, str]]:
    ctx = seed_ctx(text, channel)
    return [(h.rule_code, h.quote) for h in tier0.run_tier0_text(text, ctx)]


@pytest.mark.parametrize("text,code,quote", [
    ("Try our Steam Classic this weekend.", "R-TERM-01", "Steam Classic"),
    ("try our steam classic this weekend.", "R-TERM-01", "steam classic"),
    ("A cheap and guaranteed great morning.", "R-TERM-02", "cheap"),
    ("A cheap and guaranteed great morning.", "R-TERM-02", "guaranteed"),
    ("Visit S&S this weekend.", "R-TERM-03", "S&S"),
    ("saffron and steam has a new roast.", "R-TERM-03", "saffron and steam"),
    ("Only today, hurry before it is gone.", "R-CLAIM-03", "Only today"),
    ("Our cold brew detoxes your body.", "R-LEGAL-01", "detoxes"),
    ("Tag two friends to enter our giveaway.", "R-LEGAL-02", "giveaway"),
    ("Flat 20% off with code MONSOON20.", "R-CLAIM-04", "Flat 20% off"),
    ("WOW AMAZING NEW ROAST, come and taste it.", "R-TONE-04", "WOW AMAZING NEW ROAST"),
    ("Saffron Signature Roast 250g for just Rs 399.", "R-LEGAL-03", "Rs 399"),
])
def test_violations_found_with_spans(text: str, code: str, quote: str) -> None:
    assert (code, quote) in hits_for(text)


@pytest.mark.parametrize("text", [
    "Try our Saffron Signature Roast this weekend.",
    "Fairly priced beans for a great morning.",
    "Visit Saffron & Steam this weekend.",
    "Tag two friends to enter our giveaway. T&C apply.",
    "Flat 15% off gift boxes with DIWALI15, valid till 15 November 2026.",
    "Saffron Signature Roast 250g, Rs 495 incl. taxes.",
    "Fresh roast is here. Come in for a cup!",
    "Thank you to all our customers, and see you soon.",  # "rs," inside a word is not a price
    "Our cheapskate jokes are bad.",  # whole words only
])
def test_ok_examples_pass(text: str) -> None:
    assert hits_for(text) == []


def test_word_boundaries_and_span_offsets() -> None:
    text = "Not cheapest. Just cheap."
    ctx = seed_ctx(text)
    hits = [h for h in tier0.run_tier0_text(text, ctx) if h.rule_code == "R-TERM-02"]
    assert [(h.span_start, h.span_end) for h in hits] == [(19, 24)]
    assert text[19:24] == "cheap"


def test_preferred_term_has_deterministic_fix() -> None:
    ctx = seed_ctx("Steam Classic 250g now in stores.")
    h = next(h for h in tier0.run_tier0_text(ctx.post_text, ctx) if h.rule_code == "R-TERM-01")
    assert h.fix == "Saffron Signature Roast" and h.probability == 1.0 and h.band == "flag"


def test_exclamation_budget_is_post_level() -> None:
    text_a, text_b = "Fresh roast is here!", "Come in today!"
    ctx = seed_ctx(text_a + "\n\n" + text_b)
    state = tier0.PostState()
    a = tier0.run_tier0_text(text_a, ctx, state)
    b = tier0.run_tier0_text(text_b, ctx, state)
    assert not [h for h in a if h.rule_code == "R-TONE-04"]
    assert [h.quote for h in b if h.rule_code == "R-TONE-04"] == ["!"]


def test_promo_end_date_anywhere_in_post_counts() -> None:
    post = "Flat 15% off gift boxes with DIWALI15.\n\nOffer valid till 15 November 2026."
    ctx = seed_ctx(post)
    assert not [h for h in tier0.run_tier0_text("Flat 15% off gift boxes with DIWALI15.", ctx) if h.rule_code == "R-CLAIM-04"]


def test_threshold_price_is_not_a_price_quote() -> None:
    assert not [c for c, _ in hits_for("Free shipping above Rs 799, ends 30 November 2026.") if c == "R-LEGAL-03"]


def test_readability_against_channel_target() -> None:
    dense = ("Our extraction methodology emphasises meticulous parameterisation of volumetric dissolution, "
             "thereby optimising the organoleptic characteristics of differentiated single-estate provenances.")
    assert ("R-ACC-02", dense) in hits_for(dense, "instagram")
    simple = "Fresh beans, roasted this week in Pune. Come in for a cup and say hello to the team."
    assert not [c for c, _ in hits_for(simple) if c == "R-ACC-02"]
    assert tier0.fk_grade(simple) < 8


def test_admin_rule_without_params_uses_quoted_words() -> None:
    rule = RuleSpec(code="R-TEST-99", category="words", severity="minor", statement="Never say 'cheap'", check_method="tier0")
    ctx = seed_ctx("A cheap brew.")
    ctx.rules = [rule]
    ctx.terms = []
    assert [(h.rule_code, h.quote) for h in tier0.run_tier0_text("A cheap brew.", ctx)] == [("R-TEST-99", "cheap")]


def test_claim_triggers_skip_dates_and_codes() -> None:
    rules = seed_rules()
    groups = [(t.group, t.text) for t in tier0.claim_triggers("Use code MONSOON20 for flat 20% off till 30 September 2026.", rules)]
    assert ("number", "20%") in groups
    assert not any(t in ("30", "2026") for _, t in groups)
    sup = [(t.group, t.text) for t in tier0.claim_triggers("India's #1 coffee, better than the rest.", rules)]
    assert ("superlative", "#1") in sup and ("superlative", "better than") in sup
    assert ("eco", "sustainable") in [(t.group, t.text) for t in tier0.claim_triggers("A sustainable roaster.", rules)]
    assert ("price", "Rs 399") in [(t.group, t.text) for t in tier0.claim_triggers("Just Rs 399.", rules)]


# ------------------------------------------------------------------ images
def test_alt_text() -> None:
    rule = next(r for r in seed_rules() if r.code == "R-ACC-01")
    assert tier0.check_alt_text(rule, None)[0].detail == "This image has no alt text."
    assert tier0.check_alt_text(rule, "image")
    assert tier0.check_alt_text(rule, "Barista pouring water over ground coffee.") == []


def test_palette_flags_neon_ad_and_passes_on_brand_ad() -> None:
    rule = next(r for r in seed_rules() if r.code == "R-VIS-02")
    pal = seed_ctx().palette
    bad = tier0.check_palette(rule, str(SEED / "content" / "ad_offpalette.png"), pal)
    assert bad and bad[0].rule_code == "R-VIS-02" and "#" in bad[0].quote
    assert tier0.check_palette(rule, str(SEED / "content" / "ad_good.png"), pal) == []


def test_dominant_colours_shares(tmp_path) -> None:  # noqa: ANN001
    from PIL import Image

    im = Image.new("RGB", (200, 100), "#3B2418")
    im.paste(Image.new("RGB", (50, 100), "#F28C28"), (0, 0))
    p = tmp_path / "x.png"
    im.save(p)
    cols = dict(tier0.dominant_colours(str(p), k=5))
    assert abs(cols["#3B2418"] - 0.75) < 0.02 and abs(cols["#F28C28"] - 0.25) < 0.02


# Sharma, Wu & Dalal (2005) CIEDE2000 test data
@pytest.mark.parametrize("lab1,lab2,expected", [
    ((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
    ((50.0, 3.1571, -77.2803), (50.0, 0.0, -82.7485), 2.8615),
    ((50.0, 0.0, 0.0), (50.0, -1.0, 2.0), 2.3669),
    ((50.0, 2.5, 0.0), (73.0, 25.0, -18.0), 27.1492),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
    ((50.0, 2.49, -0.001), (50.0, -2.49, 0.0011), 7.2195),
    ((50.0, -0.001, 2.49), (50.0, 0.0009, -2.49), 4.8045),
])
def test_ciede2000_known_values(lab1, lab2, expected) -> None:  # noqa: ANN001
    assert tier0.ciede2000(lab1, lab2) == pytest.approx(expected, abs=1e-4)
    assert tier0.ciede2000(lab2, lab1) == pytest.approx(expected, abs=1e-4)
