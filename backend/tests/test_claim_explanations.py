"""Honest claim explanations (real-brand test, 8 Nov): the LLM said "The brand word list flags '10'" and that an
unverified number "cannot stand as written". The code knows the reason (claim_basis); the prompt states it, and an
explanation that blames a word list or calls an unverified figure false is replaced by the code's own words."""
from __future__ import annotations

import asyncio
import json

from app.pipeline import engine as E
from app.pipeline import explain as X
from app.pipeline.jev import CallLog
from fakes import FakeJev, install
from pipeline_helpers import seed_ctx, seed_rules, text_units

RULE = next(r for r in seed_rules() if r.code == "R-CLAIM-02")


def _req(**kw) -> X.ExplainRequest:  # noqa: ANN003
    base = dict(rule=None, category="claims", problem="No approved fact matches the figure “10x” yet.", unit_text="Rajkot grew 10x.",
                quote="10x", channel="website", claim_basis="unverified")
    return X.ExplainRequest(**{**base, **kw})


def test_prompt_names_the_real_reason_not_a_word_list() -> None:
    user = X.build_messages(_req())[-1]["content"]
    assert "word list flagged" not in user.replace("never say a word list flagged it", "")
    assert "name the approved-facts check" in user and "unverified, not false" in user
    user = X.build_messages(_req(rule=RULE))[-1]["content"]
    assert "name R-CLAIM-02" in user and "cannot stand as written" in user  # the prompt forbids the phrase
    # non-claim findings keep the word-list wording
    assert "the brand word list" in X.build_messages(_req(claim_basis=None, category="terms"))[-1]["content"]


def test_region_prompt_labels_rule_less_claims_as_the_facts_check() -> None:
    req = X.RegionRequest(items=[X.RegionItem("1", None, "claims", "No approved fact matches it yet.", "10", claim_basis="unverified")],
                          unit_text="Rajkot grew 10x.", region_text="Rajkot grew 10x.", channel="website")
    user = X.build_region_messages(req)[-1]["content"]
    assert "1. The approved-facts check" in user and "1. The brand word list" not in user
    assert "unverified, not false" in user


def test_dishonest_claim_explanations_are_rejected_in_code() -> None:
    bad = ["The brand word list flags '10'.", "This number cannot stand as written.", "The figure is false.",
           "This claim may be misleading without proof.", "A made-up statistic."]
    for b in bad:
        assert not X.honest_claim_explanation(b, "unverified"), b
    good = "No approved fact matches “7x” yet, and R-CLAIM-02 asks for one. Add it with a source or edit the figure."
    assert X.honest_claim_explanation(good, "unverified")
    assert X.honest_claim_explanation("Fact F-04 says 12 cafes, so 40 is wrong.", "contradicted")  # evidence exists
    assert not X.honest_claim_explanation("The word list flags it.", "contradicted")
    assert X.honest_claim_explanation("The figure is false.", None)  # not a claim finding: not checked here


def test_engine_keeps_the_codes_words_when_the_llm_misattributes(monkeypatch) -> None:  # noqa: ANN001
    text = "Every order is delivered in 9 minutes."

    async def chat(messages, *, model):  # noqa: ANN001, ANN202
        user = messages[-1]["content"]
        assert "no approved fact matches it yet" in user
        if "explanations" in user:
            return json.dumps({"explanations": {"1": "The brand word list flags '9'. It cannot stand as written."},
                               "fix": "Every order is delivered quickly."}), 100, 20
        return json.dumps({"explanation": "The brand word list flags '9'.", "fix": None}), 100, 20

    install(monkeypatch, FakeJev())
    monkeypatch.setattr(X, "available", lambda: True)
    monkeypatch.setattr(X, "_chat", chat)
    res = asyncio.run(E.analyze(text_units(text), seed_ctx(text), calls=CallLog()))
    [d] = [d for d in res.findings if d.tier == "claim"]
    assert d.rule_code == "R-CLAIM-02" and d.claim_basis == "unverified"
    assert "word list" not in d.explanation and "cannot stand" not in d.explanation
    assert "No approved fact matches this claim yet, and R-CLAIM-02 asks for one" in d.explanation


def test_contradicted_claim_names_the_fact(monkeypatch) -> None:  # noqa: ANN001
    text = "We now have 40 cafes across India."
    install(monkeypatch, FakeJev(support={"F-04": 0.1}))
    res = asyncio.run(E.analyze(text_units(text), seed_ctx(text), calls=CallLog(), use_llm=False))
    [d] = [d for d in res.findings if d.tier == "claim"]
    assert d.claim_basis == "contradicted" and "F-04" in d.explanation and "12 cafes" in d.explanation
