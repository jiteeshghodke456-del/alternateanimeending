"""Regression (real-brand test, 8 Nov): with no rule asking for evidence, every number in a brand's own blog
post was flagged "Claim unsupported, p = 1.00, Major" and rewritten away. Not verified is not false: without
such a rule the finding is a minor "needs a human look" reminder with no rewrite; with the rule, it still flags."""
import asyncio

from app.pipeline import engine as E
from app.pipeline.context import CheckContext
from app.pipeline.jev import CallLog
from pipeline_helpers import seed_rules, text_units

TEXT = "Tier-II cities are powering the growth story too. Rajkot recorded a staggering 10x YoY growth."


def _claims(rules) -> list[E.FindingDraft]:  # noqa: ANN001
    ctx = CheckContext(channel="website", rules=rules, post_text=TEXT, confidential=True)
    res = asyncio.run(E.analyze(text_units(TEXT), ctx, calls=CallLog()))
    return [d for d in res.findings if d.tier == "claim"]


def test_number_without_any_evidence_rule_is_a_reminder_not_a_flag():
    ds = _claims([])
    assert ds, "the figure should still be pointed out"
    for d in ds:
        assert d.band == "human" and d.severity == "minor" and d.rule_code is None
        assert d.probability is None and d.fix is None
        assert "doesn't make it wrong" in (d.explanation or "")


def test_brand_rule_requiring_facts_still_flags():
    ds = _claims(seed_rules())  # the demo brand's R-CLAIM-02: every number must match an approved fact
    assert any(d.rule_code and d.band in ("flag", "human") and d.severity != "minor" for d in ds)
