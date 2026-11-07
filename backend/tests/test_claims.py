"""Claims: fact matching, Jev support, statuses (incl. an expired fact) decided in code."""
from __future__ import annotations

import asyncio
from datetime import date

from app.pipeline import claims as C
from fakes import FakeJev, install
from pipeline_helpers import seed_ctx


def check(text: str, monkeypatch=None, support=None, use_jev=True) -> list[C.ClaimResult]:  # noqa: ANN001
    if use_jev:
        install(monkeypatch, FakeJev(support=support or {}))
    ctx = seed_ctx(text)
    return [asyncio.run(C.check_claim(cl, ctx, use_jev=use_jev)) for cl in C.extract_claims(text, ctx)]


def test_is_expired() -> None:
    assert C.is_expired(date(2026, 6, 30), date(2026, 11, 7))
    assert not C.is_expired(date(2026, 11, 7), date(2026, 11, 7))
    assert not C.is_expired(None)


def test_top_facts_ranks_by_token_set_ratio() -> None:
    ctx = seed_ctx()
    top = C.top_facts("We run 12 cafes across Pune, Mumbai and Bengaluru", ctx.facts)
    assert top[0][0].code == "F-04" and len(top) <= 3 and all(s >= 40 for _, s in top)


def test_supported_by_current_fact(monkeypatch) -> None:  # noqa: ANN001
    [r] = check("Rated 4.6 on Google by 3,200 guests.", monkeypatch, {"F-06": 0.93})
    assert r.status == "supported" and not r.is_finding


def test_outdated_when_support_comes_from_expired_fact(monkeypatch) -> None:  # noqa: ANN001
    [r] = check("Use code MONSOON20 for flat 20% off online orders.", monkeypatch, {"F-12": 0.94})
    assert r.status == "outdated" and r.fact_code == "F-12" and r.fact_valid_until == date(2026, 9, 30)
    assert r.rule_code == "R-CLAIM-02" and r.band == "flag" and r.probability == 0.94


def test_unsupported_cites_claim_rule(monkeypatch) -> None:  # noqa: ANN001
    [r] = check("We now have 40 cafes across India.", monkeypatch, {"F-04": 0.1})
    assert r.status == "unsupported" and r.rule_code == "R-CLAIM-02" and r.band == "flag"
    assert r.probability == 0.9


def test_superlative_uses_claim_01_and_price_uses_legal_03(monkeypatch) -> None:  # noqa: ANN001
    rs = check("India's #1 coffee. Saffron Signature Roast 250g, just Rs 399.", monkeypatch)
    codes = {(r.claim.group, r.rule_code, r.status) for r in rs}
    assert ("superlative", "R-CLAIM-01", "unsupported") in codes
    assert ("factual", "R-LEGAL-03", "unsupported") in codes


def test_claim_split_across_two_facts_is_supported_by_numbers(monkeypatch) -> None:  # noqa: ANN001
    [r] = check("We have 12 cafes in Pune, Mumbai and Bengaluru, open from 8 am to 10 pm every day.", monkeypatch,
                {"F-04": 0.53, "F-18": 0.45})
    assert r.status == "supported" and r.via == "numbers"


def test_without_jev_numbers_still_find_expired_fact() -> None:
    [r] = check("Use code MONSOON20 for flat 20% off online orders till 30 September.", use_jev=False)
    assert (r.status, r.fact_code, r.band, r.probability) == ("outdated", "F-12", "human", None)


def test_without_jev_unverifiable_claims_go_to_a_human() -> None:
    rs = check("The best filter blend you will ever taste.", use_jev=False)
    assert [(r.status, r.band, r.via) for r in rs] == [("unsupported", "human", "no_jev")]


def test_failed_jev_call_is_not_a_no(monkeypatch) -> None:  # noqa: ANN001
    install(monkeypatch, FakeJev(fail_all=True))
    ctx = seed_ctx()
    [cl] = C.extract_claims("We now have 40 cafes across India.", ctx)
    r = asyncio.run(C.check_claim(cl, ctx, use_jev=True))
    assert r.error and r.band == "human" and r.probability is None


def test_dates_and_codes_are_not_claim_numbers() -> None:
    assert C.numbers("Use MONSOON20 for 20% off till 30 September 2026, Rs 1,299") == {"20", "1299"}


def test_banned_word_is_not_a_second_claim() -> None:
    from app.pipeline.tier0 import run_tier0_text

    text = "Our guaranteed fresh beans."
    ctx = seed_ctx(text)
    assert C.extract_claims(text, ctx, covered=run_tier0_text(text, ctx)) == []
