"""Regression (golden path, 8 Nov): after "accept all fixes", a rewrite of the urgency paragraph sometimes ADDED
"Order now." while the last paragraph still said "Order now.", so v2 broke the one-call-to-action rule
(R-MSG-02) and could not be submitted. The self-check now rejects, in code, a fix that asks the reader to do
something the replaced text didn't ask; the retry is told why."""
from __future__ import annotations

import asyncio

from app.pipeline import engine as E
from app.pipeline import selfcheck as S
from app.pipeline.jev import CallLog
from fakes import FakeJev, FakeLLM, install
from pipeline_helpers import DEMO_TEXT, seed_ctx, text_units

URGENCY = "Only today, hurry, last chance to grab it before stock runs out!!!"


def test_cta_sentences_are_found_in_code() -> None:
    assert S.cta_sentences("Order now. You would be silly to skip this.") == ["Order now."]
    assert S.cta_sentences("Visit your nearest cafe this weekend.") == ["Visit your nearest cafe this weekend."]
    assert S.cta_sentences("Use code MONSOON20 at checkout.") == ["Use code MONSOON20 at checkout."]
    assert S.cta_sentences(f"Flat 20% off with code MONSOON20. {URGENCY}") == []
    assert S.cta_sentences("Our app lets you order in minutes. We try hard.") == []


def test_added_cta_is_a_reason_and_names_the_existing_one() -> None:
    ctx = seed_ctx(DEMO_TEXT)
    unit = next(u for u in text_units(DEMO_TEXT) if URGENCY in u.text)
    s = unit.text.index(URGENCY)
    why = S.added_cta(unit.text, s, s + len(URGENCY), "Grab it while stocks last. Order now.", ctx)
    assert why and "“Order now.”" in why and "already asks the reader to act elsewhere" in why
    assert S.added_cta(unit.text, s, s + len(URGENCY), "Available while stocks last.", ctx) is None
    # rewording the post's own call to action is not "adding" one
    last = "Order now. You would be silly to skip this."
    i = DEMO_TEXT.index(last)
    assert S.added_cta(DEMO_TEXT, i, i + len(last), "Order now.", ctx) is None


def test_fix_that_adds_order_now_is_rejected_and_the_retry_is_used(monkeypatch) -> None:  # noqa: ANN001
    def fix(quote: str, attempt: int) -> str:
        if "hurry" in quote:
            # the MONSOON20 offer has expired, so a good fix drops it
            return "Fresh stock is in at our cafes. Order now." if attempt == 0 else "Fresh stock is in at our cafes."
        return "a coffee we love"

    llm = FakeLLM(fix_for=fix)
    install(monkeypatch, FakeJev(), llm)
    res = asyncio.run(E.analyze(text_units(DEMO_TEXT), seed_ctx(DEMO_TEXT), calls=CallLog()))
    ds = [d for d in res.findings if d.quote and "hurry" in d.quote and d.fix_group]
    assert ds, "the urgency paragraph has a fix region"
    assert all(d.fix == "Fresh stock is in at our cafes." and d.fix_verified for d in ds)
    assert [c["retry"] for c in llm.calls if "hurry" in c["quote"]] == [False, True]


def test_fix_that_keeps_adding_a_cta_ends_as_manual_fix(monkeypatch) -> None:  # noqa: ANN001
    llm = FakeLLM(fix_for=lambda q, a: "Fresh stock is in at our cafes. Order now." if "hurry" in q else "a coffee we love")
    install(monkeypatch, FakeJev(), llm)
    res = asyncio.run(E.analyze(text_units(DEMO_TEXT), seed_ctx(DEMO_TEXT), calls=CallLog()))
    ds = [d for d in res.findings if d.quote and "hurry" in d.quote and d.fix_group]
    assert ds and all(d.fix is None and not d.fix_verified for d in ds)
    assert any("call to action" in r for d in ds for r in d.selfcheck_reasons)


def test_accept_all_after_the_retry_keeps_one_call_to_action(monkeypatch) -> None:  # noqa: ANN001
    from test_edit_regions import accept_all, demo_llm

    llm = demo_llm(first={"MONSOON20": "Fresh bags of Saffron Signature Roast are in stock online. Order now."})
    install(monkeypatch, FakeJev(support={"F-12": 0.94, "F-13": 0.9}), llm)
    res = asyncio.run(E.analyze(text_units(DEMO_TEXT), seed_ctx(DEMO_TEXT), calls=CallLog()))
    v2, _ = accept_all(DEMO_TEXT, res)
    assert len(S.cta_sentences(v2)) == 1, v2
