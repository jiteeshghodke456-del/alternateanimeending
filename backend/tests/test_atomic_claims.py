"""Atomic claims (claims.atomic): a clause with several figures is judged figure by figure, so a covered
number is not dragged down by an uncovered one next to it (real-brand test, 8 Nov: "Rajkot 10x, Ludhiana 7x,
Bhubaneswar 4x" was one finding over all three)."""
from __future__ import annotations

import asyncio
from datetime import date

from app.pipeline import claims as C
from app.pipeline import engine as E
from app.pipeline.context import FactSpec
from app.pipeline.jev import CallLog
from fakes import FakeJev, FakeLLM, install
from pipeline_helpers import seed_ctx, text_units

RAJKOT = FactSpec(code="F-90", statement="Rajkot orders grew 10x in 2025", value="10x", source="Order report",
                  valid_until=date(2027, 12, 31))
TEXT = "Orders grew 7x in Ludhiana, 10x in Rajkot and 4x in Surat."


def _ctx(text: str, **kw):  # noqa: ANN003, ANN202
    ctx = seed_ctx(text, **kw)
    ctx.facts = [*ctx.facts, RAJKOT]
    return ctx


def test_figures_keep_scale_words_and_unit_tails() -> None:
    t = "A Hyderabadi spent ₹4.3 lakh on iPhones; Rajkot grew 10x and gochujang +491%."
    figs = [f for cl in C.extract_claims(t, seed_ctx(t)) for f in cl.figures]
    assert [(f.label, set(f.values)) for f in figs] == [("₹4.3 lakh", {"430000"}), ("10x", {"10"}), ("491%", {"491"})]


def test_only_the_uncovered_figures_are_reported(monkeypatch) -> None:  # noqa: ANN001
    fj = FakeJev(support={"F-90": 0.35}, figures={"F-90": 0.92})
    install(monkeypatch, fj)
    ctx = _ctx(TEXT)
    [cl] = C.extract_claims(TEXT, ctx)
    cr = asyncio.run(C.check_claim(cl, ctx, use_jev=True))
    assert cr.status == "unsupported"
    assert [TEXT[s:e] for s, e in cr.spans] == ["7x", "4x"]  # 10x is covered by F-90
    asked = [q for c in fj.calls for q in c["questions"] if q.startswith("fig_")]
    assert asked == ["fig_F-90_1"]  # code asks only where the fact holds the figure's digits


def test_jev_must_confirm_the_figure_not_just_the_digits(monkeypatch) -> None:  # noqa: ANN001
    install(monkeypatch, FakeJev(support={"F-90": 0.35}, figures={"F-90": 0.1}))
    ctx = _ctx(TEXT)
    [cl] = C.extract_claims(TEXT, ctx)
    cr = asyncio.run(C.check_claim(cl, ctx, use_jev=True))
    assert cr.status == "unsupported" and cr.spans == []  # nothing covered: the whole claim, as before


def test_supported_clause_still_reports_a_figure_no_fact_holds(monkeypatch) -> None:  # noqa: ANN001
    text = "Rajkot orders grew 10x in 2025 and Surat orders grew 4x."
    install(monkeypatch, FakeJev(support={"F-90": 0.9}))
    ctx = _ctx(text)
    crs = [asyncio.run(C.check_claim(cl, ctx, use_jev=True)) for cl in C.extract_claims(text, ctx)]
    [cr] = [c for c in crs if c.is_finding]
    assert [text[s:e] for s, e in cr.spans] == ["4x"] and cr.via == "figures" and cr.band == "flag"


def test_all_figures_covered_is_supported(monkeypatch) -> None:  # noqa: ANN001
    t = "We run 12 cafes and every barista completes 40 hours of training."
    install(monkeypatch, FakeJev(support={"F-04": 0.2, "F-19": 0.2}, figures={"F-04": 0.9, "F-19": 0.9}))
    ctx = seed_ctx(t)
    [cl] = C.extract_claims(t, ctx)
    cr = asyncio.run(C.check_claim(cl, ctx, use_jev=True))
    assert cr.status == "supported" and cr.via == "figures"


def test_without_jev_partial_coverage_narrows_by_numbers() -> None:
    ctx = _ctx(TEXT)
    [cl] = C.extract_claims(TEXT, ctx)
    cr = asyncio.run(C.check_claim(cl, ctx, use_jev=False))
    assert C.top_facts(cl.text, [RAJKOT])[0][1] >= C.NO_JEV_FACT_SCORE
    assert [TEXT[s:e] for s, e in cr.spans] == ["7x", "4x"] and cr.band == "human" and cr.via == "no_jev"


def test_atomic_findings_share_one_fix_region(monkeypatch) -> None:  # noqa: ANN001
    install(monkeypatch, FakeJev(support={"F-90": 0.35}, figures={"F-90": 0.92}),
            FakeLLM(fix_for=lambda excerpt, attempt: "Orders grew 10x in Rajkot."))
    res = asyncio.run(E.analyze(text_units(TEXT), _ctx(TEXT), calls=CallLog()))
    ds = [d for d in res.findings if d.tier == "claim"]
    assert sorted(TEXT[d.hit_start:d.hit_end] for d in ds) == ["4x", "7x"]
    assert len({d.fix_group for d in ds}) == 1 and all(d.quote == TEXT for d in ds)
