"""Check-worthiness gate (claims.gate): Jev classifies each claim in the claim's own call, code decides.
Regression (real-brand test, 8 Nov): under an "every number needs a fact" rule, every statistic in a brand's own
year-in-review was flagged "Claim unsupported" and rewritten away."""
from __future__ import annotations

import asyncio

from app.pipeline import claims as C
from app.pipeline import engine as E
from app.pipeline import questions as Q
from app.pipeline.jev import CallLog
from fakes import FakeJev, install
from pipeline_helpers import seed_ctx, text_units


def _run(text: str, monkeypatch, confidential: bool = False, **fake) -> tuple[list[E.FindingDraft], FakeJev, E.EngineResult]:  # noqa: ANN001
    fj = FakeJev(**fake)
    install(monkeypatch, fj)
    ctx = seed_ctx(text, confidential=confidential)
    res = asyncio.run(E.analyze(text_units(text), ctx, calls=CallLog(), use_llm=False, explain_findings=False))
    return [d for d in res.findings if d.tier == "claim"], fj, res


STAT = "1 in every 127 orders on our app included a bag of cold brew."


def test_first_party_statistic_is_a_reminder_even_under_an_every_number_rule(monkeypatch) -> None:  # noqa: ANN001
    ds, _, _ = _run(STAT, monkeypatch, kind=Q.KIND_FIRST_PARTY)
    assert ds, "the figure is still pointed out"
    for d in ds:  # R-CLAIM-02 would flag it; the brand's own data is unverified, not false
        assert (d.band, d.rule_code, d.severity, d.local_only, d.fix) == ("human", None, "minor", True, None)


def test_service_promise_still_flags_under_the_rule(monkeypatch) -> None:  # noqa: ANN001
    ds, _, _ = _run("Every order is delivered in 9 minutes.", monkeypatch, kind=Q.KIND_SERVICE)
    assert [(d.rule_code, d.band) for d in ds] == [("R-CLAIM-02", "flag")]


def test_puffery_and_non_claims_make_no_claim_finding(monkeypatch) -> None:  # noqa: ANN001
    ds, _, res = _run(STAT, monkeypatch, kind=Q.KIND_PUFFERY)
    assert ds == [] and res.claims == []  # not a claim: not in the ledger either
    ds, _, _ = _run(STAT, monkeypatch, kind=Q.KIND_SERVICE, checkable=0.1)
    assert ds == []


def test_words_a_rule_names_are_decided_by_that_rule(monkeypatch) -> None:  # noqa: ANN001
    # "best" is in R-CLAIM-01's own patterns: even if Jev calls it puffery, the brand rule decides.
    ds, _, _ = _run("The best filter blend you will ever taste.", monkeypatch, kind=Q.KIND_PUFFERY, checkable=0.1)
    assert any(d.rule_code == "R-CLAIM-01" and d.band == "flag" for d in ds)


def test_outdated_and_contradicted_figures_keep_flagging(monkeypatch) -> None:  # noqa: ANN001
    ds, _, _ = _run("Customers rate us 4.8 stars on our ordering app.", monkeypatch, kind=Q.KIND_FIRST_PARTY,
                    support={"F-13": 0.95})
    assert [(d.claim_status, d.band, d.rule_code) for d in ds] == [("outdated", "flag", "R-CLAIM-02")]
    ds, _, _ = _run("We now have 40 cafes across India.", monkeypatch, kind=Q.KIND_FIRST_PARTY, support={"F-04": 0.1})
    assert [(d.band, d.rule_code) for d in ds] == [("flag", "R-CLAIM-02")]  # F-04 says 12 cafes


def test_unsure_whether_it_is_a_claim_caps_at_human(monkeypatch) -> None:  # noqa: ANN001
    ds, _, _ = _run("Every order is delivered in 9 minutes.", monkeypatch, kind=Q.KIND_SERVICE, checkable=0.5)
    assert [(d.rule_code, d.band) for d in ds] == [("R-CLAIM-02", "human")]


def test_gate_questions_ride_in_the_claim_call(monkeypatch) -> None:  # noqa: ANN001
    _, fj, _ = _run("Rated 4.6 on Google by 3,200 guests.", monkeypatch, support={"F-06": 0.93})
    claim_calls = [c for c in fj.calls if any(q.startswith("support_") for q in c["questions"])]
    assert claim_calls and all({"claim_kind", "claim_checkable"} <= set(c["questions"]) for c in claim_calls)
    assert len(claim_calls) == 1  # one call per claim, many questions
    q = Q.claim_kind_question("Some paragraph.")
    assert "Some paragraph." in q.instructions and list(q.criteria)[0] == Q.KIND_COMPARATIVE


def test_confidential_items_keep_the_code_only_table(monkeypatch) -> None:  # noqa: ANN001
    ds, fj, _ = _run(STAT, monkeypatch, confidential=True, kind=Q.KIND_PUFFERY)
    assert fj.calls == [] and ds and all(d.rule_code == "R-CLAIM-02" for d in ds)


def test_failed_gate_call_is_not_a_no(monkeypatch) -> None:  # noqa: ANN001
    ds, _, res = _run(STAT, monkeypatch, fail_all=True)
    assert ds and all(d.rule_code == "R-CLAIM-02" for d in ds)  # ungated, as before
    assert any("claim check failed" in w for w in res.warnings)


def test_gate_truth_table_is_pure_code() -> None:
    ctx = seed_ctx()

    def cr(text: str, rule: str | None = "R-CLAIM-02", status: str = "unsupported", band: str = "flag") -> C.ClaimResult:
        [cl] = C.extract_claims(text, ctx)
        return C.ClaimResult(claim=cl, status=status, rule_code=rule, band=band, probability=1.0)

    assert C.gate(cr(STAT), Q.KIND_FIRST_PARTY, 0.9, ctx).band == "human"
    assert C.gate(cr(STAT), Q.KIND_FIRST_PARTY, 0.9, ctx).rule_code is None
    assert C.gate(cr(STAT), Q.KIND_HEALTH, 0.9, ctx).band == "flag"
    assert C.gate(cr(STAT), Q.KIND_PUFFERY, 0.9, ctx).status == C.NOT_A_CLAIM
    assert C.gate(cr(STAT, status="supported", band="pass"), Q.KIND_PUFFERY, 0.1, ctx).status == "supported"
    assert C.gate(cr(STAT), None, None, ctx).band == "flag"  # no answers: unchanged
    assert C.gate(cr("India's #1 coffee."), Q.KIND_PUFFERY, 0.05, ctx).band == "flag"  # R-CLAIM-01 names "#1"


def test_record_noul_turns_a_ranking_shaped_record_into_a_reminder(monkeypatch) -> None:  # noqa: ANN001
    # "the fastest delivery happened in 3 minutes" reads like a ranking but reports the brand's own data
    text = "Our fastest delivery this year happened in Pune in 3 minutes flat."
    ds, _, _ = _run(text, monkeypatch, kind=Q.KIND_COMPARATIVE, rules={"claim_record": 0.9})
    assert ds and all((d.band, d.rule_code) == ("human", None) for d in ds)
    ds, _, _ = _run(text, monkeypatch, kind=Q.KIND_COMPARATIVE, rules={"claim_record": 0.5})  # unsettled
    assert ds and all((d.band, d.rule_code) == ("human", "R-CLAIM-02") for d in ds)
    ds, _, _ = _run(text, monkeypatch, kind=Q.KIND_HEALTH, rules={"claim_record": 0.9})  # health always needs evidence
    assert ds and all((d.band, d.rule_code) == ("flag", "R-CLAIM-02") for d in ds)
