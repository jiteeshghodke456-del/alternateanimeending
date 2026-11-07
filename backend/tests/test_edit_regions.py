"""Edit regions: accepting every suggested fix on the demo post gives a clean, grammatical v2.

Regression for the demo bug: word-level fixes written in isolation collided on resubmit
("India's a coffee we're proud of coffee"), and "#1" was swapped for "India's favourite".
"""
from __future__ import annotations

import asyncio
import re

from app import workflow as W
from app.models import Finding
from app.pipeline import engine as E
from app.pipeline import selfcheck as S
from app.pipeline.jev import CallLog
from fakes import FakeJev, FakeLLM, install
from pipeline_helpers import DEMO_TEXT, seed_ctx, text_units

# Sentence-level rewrites a good writer model returns for each demo region (keyed by words in the region).
REWRITES = [
    ("#1", "Saffron Signature Roast is a coffee we are proud to serve."),
    ("Rated 4.8", "Roasted fresh in Pune, it is a smooth filter blend. Affordable? Yes, and it is a lovely cup."),
    ("MONSOON20", "Grab a fresh bag of Saffron Signature Roast online."),
    ("Order now", "Order online today."),
]


def demo_llm(first: dict[str, str] | None = None) -> FakeLLM:
    """`first`: {region keyword: bad first attempt}; the retry returns the good rewrite."""
    def fix_for(region: str, attempt: int) -> str | None:
        for key, good in REWRITES:
            if key in region:
                return (first or {}).get(key, good) if attempt == 0 else good
        return None
    return FakeLLM(fix_for=fix_for)


def demo_jev() -> FakeJev:
    return FakeJev(support={"F-12": 0.94, "F-13": 0.9},
                   rules={"R-TONE-02": lambda t: 0.95 if ("silly" in t or "hurry" in t) else 0.05})


def check(text: str, monkeypatch, llm: FakeLLM) -> E.EngineResult:  # noqa: ANN001
    install(monkeypatch, demo_jev(), llm)
    return asyncio.run(E.analyze(text_units(text), seed_ctx(text), calls=CallLog()))


def as_rows(res: E.EngineResult) -> list[Finding]:
    ds = sorted(res.findings, key=lambda d: (d.span_start or -1, d.rule_code or ""))
    return [Finding(id=i + 1, version_id=1, rule_code=d.rule_code, severity=d.severity, band=d.band,
                    span_start=d.span_start, span_end=d.span_end, quote=d.quote, fix=d.fix, fix_verified=d.fix_verified,
                    fix_group=d.fix_group, status="accepted" if d.fix else "open") for i, d in enumerate(ds)]


def accept_all(text: str, res: E.EngineResult) -> tuple[str, W.ApplyResult]:
    out = W.apply_findings(text, as_rows(res))
    return out.text, out


def grammatical(text: str) -> None:
    assert not re.search(r"\b(\w+)\s+\1\b", text, re.I), "doubled word"
    for para in text.split("\n\n"):
        assert para[:1].isupper() and para.rstrip()[-1] in ".!?", para


def test_accept_all_fixes_on_demo_post_gives_clean_v2(monkeypatch) -> None:  # noqa: ANN001
    v1 = check(DEMO_TEXT, monkeypatch, demo_llm())
    text_fixes = [d for d in v1.findings if d.span_start is not None and d.fix]
    assert text_fixes and all(d.fix_verified for d in text_fixes)
    # findings in one sentence share ONE fix, and their replacement spans never overlap
    groups = {(d.span_start, d.span_end, d.fix) for d in text_fixes}
    spans = sorted((s, e) for s, e, _ in groups)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))
    assert any(len([d for d in text_fixes if d.fix_group == g]) >= 3 for g in {d.fix_group for d in text_fixes})
    v2, applied = accept_all(DEMO_TEXT, v1)
    assert applied.skipped == []  # every member of a region counts as applied, the region fix is applied once
    assert v2 == ("Saffron Signature Roast is a coffee we are proud to serve.\n\n"
                  "Roasted fresh in Pune, it is a smooth filter blend. Affordable? Yes, and it is a lovely cup.\n\n"
                  "Grab a fresh bag of Saffron Signature Roast online.\n\n"
                  "Order online today.")
    grammatical(v2)
    assert not S.superlatives_in(v2, seed_ctx(v2))
    # re-check v2: far fewer findings, nothing blocking, the call to action survives
    r2 = check(v2, monkeypatch, demo_llm())
    live1 = [d for d in v1.findings if d.band != "pass"]
    live2 = [d for d in r2.findings if d.band != "pass"]
    assert len(live2) <= len(live1) // 3
    assert not [d for d in live2 if d.severity == "blocking"]
    assert "Order" in v2


def test_superlative_swap_is_rejected_then_retried(monkeypatch) -> None:  # noqa: ANN001
    llm = demo_llm(first={"#1": "Saffron Signature Roast is India’s favourite coffee."})
    res = check(DEMO_TEXT, monkeypatch, llm)
    [d] = {d.fix_group: d for d in res.findings if d.rule_code == "R-CLAIM-01" and "#1" in d.quote}.values()
    assert d.fix == "Saffron Signature Roast is a coffee we are proud to serve." and d.fix_verified
    assert [c["retry"] for c in llm.calls if "#1" in c["quote"]] == [False, True]


def test_region_rejected_twice_is_manual_fix(monkeypatch) -> None:  # noqa: ANN001
    llm = FakeLLM(fix_for=lambda q, a: "Saffron Signature Roast is the leading coffee." if "#1" in q else None)
    res = check(DEMO_TEXT, monkeypatch, llm)
    region = [d for d in res.findings if d.span_start == 0]
    assert len(region) >= 3 and all(d.fix is None and not d.fix_verified for d in region)
    assert any("superlative" in r for r in region[0].selfcheck_reasons)


def test_garbling_fix_is_rejected_by_wellformedness() -> None:
    text = "Steam Classic is India's #1 coffee, hands down!!!"
    assert S.well_formed(text, 0, len(text), "saffron Signature Roast is great")  # lower-case start, no full stop
    assert any("doubles" in r for r in S.well_formed("We love coffee. Try it.", 0, 15, "We love love coffee."))
    assert S.well_formed("Cheap? No way.", 0, 6, "Expensive? No way.")  # repeats the next words
    assert S.well_formed(text, 0, len(text), "Saffron Signature Roast is a coffee we are proud to serve.") == []


def test_superlative_list_catches_swaps() -> None:
    for t in ("India’s favourite coffee", "the leading filter blend", "our most loved roast", "the top choice",
              "the best brew", "No. 1 in Pune"):
        assert S.superlatives_in(t, seed_ctx(t)), t
    assert not S.superlatives_in("a smooth filter blend roasted in Pune", None)


def test_edit_regions_merge_overlapping_and_adjacent() -> None:
    text = "Cheap? No way, it is a guaranteed miracle. Order now."
    spans = [E.sentence_span(text, 0, 5), E.sentence_span(text, 21, 31), E.sentence_span(text, 43, 48)]
    assert spans[0] == (0, 6) and spans[1] == (7, 42)
    groups = E.edit_regions(spans, text)
    assert [g for g, _ in groups] == [(0, 53)]  # all touching sentences -> one region
    assert E.edit_regions([(0, 6), (10, 20), (15, 25)]) == [((0, 6), [0]), ((10, 25), [1, 2])]


def test_resubmit_applies_region_once_and_edit_wins() -> None:
    text = "Steam Classic is India's #1 coffee!!! Order now."
    a = Finding(id=1, version_id=1, span_start=0, span_end=37, fix="Saffron Signature Roast is great.", fix_group="g", status="accepted")
    b = Finding(id=2, version_id=1, span_start=0, span_end=37, fix="Saffron Signature Roast is great.", fix_group="g", status="accepted")
    out = W.apply_findings(text, [a, b])
    assert out.text == "Saffron Signature Roast is great. Order now." and sorted(out.applied) == [1, 2] and out.skipped == []
    b.status, b.edited_text = "edited", "Saffron Signature Roast, fresh from Pune."
    out = W.apply_findings(text, [a, b])
    assert out.text == "Saffron Signature Roast, fresh from Pune. Order now." and out.skipped == []


def test_region_fix_that_brings_back_an_old_name_is_rejected(monkeypatch) -> None:  # noqa: ANN001
    llm = demo_llm(first={"MONSOON20": "Steam Classic is worth a try, so order a pack."})
    res = check(DEMO_TEXT, monkeypatch, llm)
    [d] = {d.fix_group: d for d in res.findings if d.quote.startswith("Flat 20%")}.values()
    assert d.fix == "Grab a fresh bag of Saffron Signature Roast online." and d.fix_verified
