"""Round 3 improvement 2: unclear-rule detection (pipeline/clarity.py, routers/clarity.py).
Jev and the LLM are faked at the raw-call level (jev._call_api, explain._chat); the live smoke test at the
bottom is marked `live` (RUN_LIVE=1)."""
from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import get_engine
from app.models import AICall, AuditEvent, Rule, RuleClarity
from app.pipeline import clarity as C
from app.pipeline import explain, jev
from app.pipeline import questions as Q
from conftest import login
from pipeline_helpers import SEED

VAGUE = {"R-TONE-05", "R-MSG-03", "R-MSG-04", "R-MSG-05", "R-VIS-05"}
SEED_RULES = json.loads((SEED / "rules.json").read_text())


# ------------------------------------------------------------------ 1. lexicon + heuristics
@pytest.mark.parametrize("statement,terms", [
    ("Avoid inappropriate humour.", ["inappropriate"]),
    ("Posts should feel premium.", ["should", "feel", "premium"]),
    ("Keep everything we publish on-brand.", ["on-brand"]),
    ("Do not be too salesy.", ["too", "salesy"]),
    ("Use engaging copy where possible, etc.", ["engaging", "where possible", "etc."]),
    ("Imagery should have a modern look and feel.", ["should", "modern", "look and feel"]),
])
def test_lexicon_hits_are_listed_in_reading_order(statement: str, terms: list[str]) -> None:
    v = C.vague_signal(statement)
    assert [h["term"].lower() for h in v.hits] == terms
    assert v.score >= 0.5


def test_lexicon_ignores_words_inside_other_words() -> None:
    v = C.vague_signal("No mayonnaise, no shoulder pads, no toothpaste: at most 2 per post.")
    assert v.hits == []
    assert v.measurable


@pytest.mark.parametrize("statement,expected", [
    ("At most one exclamation mark per post.", True),          # number word
    ("Reading grade must not exceed 8.", True),                # digit
    ("Never write 'S&S'.", True),                              # quoted term
    ("Never push, pressure or guilt readers into buying.", True),  # list
    ("PDF headings use Fraunces.", True),                      # named thing
    ("Copy must sound warm and confident, like a friendly barista.", False),  # "and" before the comma: not a list
    ("Keep everything we publish on-brand.", False),
])
def test_measurable_criterion(statement: str, expected: bool) -> None:
    assert C.measurable(statement) is expected


def test_vague_score_parts() -> None:
    v = C.vague_signal("Avoid inappropriate humour.")  # 1 subjective hit + no measure + no example + short
    assert v.score == pytest.approx(0.35 + 0.25 + 0.10 + 0.10)
    v2 = C.vague_signal("Avoid inappropriate humour.", has_stored_examples=True)
    assert v2.score == pytest.approx(0.35 + 0.25 + 0.10)
    v3 = C.vague_signal("Posts should feel very premium, overly polished and engaging.")
    assert v3.score <= 1.0 and len(v3.hits) >= 5  # hit weight is capped at 0.7


def test_seeded_vague_rules_have_vague_words_and_clear_rules_have_none() -> None:
    for r in SEED_RULES:
        v = C.vague_signal(r["statement"], bool(r["examples_ok"]) and bool(r["examples_violating"]))
        if r["code"] in VAGUE:
            assert v.hits and v.score >= 0.5, r["code"]
        else:
            assert not v.hits and v.score <= 0.25, r["code"]


# ------------------------------------------------------------------ 2. Jev self-disagreement math
def _probes(*kinds: str) -> list[C.Probe]:
    return [C.Probe(f"probe {i}", k) for i, k in enumerate(kinds)]


def test_identical_answers_across_wordings_are_clear() -> None:
    d = C.disagreement_signal([[0.05, 0.05, 0.05], [0.95, 0.95, 0.95]], _probes("follows", "breaks"))
    assert d is not None
    assert d.score == 0 and d.flip_rate == 0 and d.mean_spread == 0 and d.mismatch_rate == 0


def test_spread_flips_unsettled_and_mismatch() -> None:
    answers = [[0.1, 0.5, 0.8], [0.9, 0.85, 0.95], [0.4, 0.45, 0.5], None]
    d = C.disagreement_signal(answers, _probes("follows", "breaks", "borderline", "follows"))
    assert d is not None
    assert d.probes == 3 and d.failed_calls == 1 and d.wordings == 3
    assert d.mean_spread == pytest.approx((0.7 + 0.1 + 0.1) / 3, abs=1e-3)
    assert d.flip_rate == pytest.approx(1 / 3, abs=1e-3)  # only probe 0 lands in different bands (pass/human/flag)
    # unsettled counts only clear-cut probes: one answer (0.5) in 0.30-0.70 out of 6 clear-cut answers
    assert d.unsettled_rate == pytest.approx(1 / 6, abs=1e-3)
    assert d.mismatch_rate == 0  # probe 0 (follows) has mean 0.47 < 0.5, probe 1 (breaks) 0.9
    d2 = C.disagreement_signal([[0.8, 0.8, 0.8]], _probes("follows"))
    assert d2 is not None and d2.mismatch_rate == 1 and d2.flip_rate == 0


def test_all_calls_failed_is_no_signal_not_clear() -> None:
    assert C.disagreement_signal([None, None], _probes("follows", "breaks")) is None


def test_band_helper() -> None:
    assert [C.jev_band(p) for p in (0.1, 0.3, 0.69, 0.7)] == ["pass", "human", "human", "flag"]


# ------------------------------------------------------------------ 3. people
def test_people_signal_counts_and_shrinkage() -> None:
    assert C.people_signal([]).score is None
    rows = [("dismissed", "false_alarm", "flag"), ("dismissed", "approved_exception", "flag"),
            ("overridden", None, "flag"), ("open", None, "human"), ("accepted", None, "flag")]
    p = C.people_signal(rows)
    assert p.findings == 5 and p.dismissed == 2 and p.dismissed_unclear == 1 and p.overridden == 1 and p.human == 1
    assert p.dismiss_rate == 0.4 and p.override_rate == 0.2 and p.human_share == 0.2
    assert p.score == pytest.approx((1 + 1 + 0.5) / (5 + C.PEOPLE_PRIOR), abs=1e-3)


# ------------------------------------------------------------------ combine
def test_combine_weights_and_renormalisation() -> None:
    assert C.combine(0.0, 0.0, 0.0) == (100.0, "clear")
    assert C.combine(1.0, 1.0, 1.0) == (0.0, "unclear")
    s, _ = C.combine(1.0, 0.0, 0.0)
    assert s == pytest.approx(100 * (1 - C.WEIGHTS["vague"]))
    s2, _ = C.combine(1.0, None, None)  # only the wording signal: it carries the whole score
    assert s2 == 0.0
    s3, _ = C.combine(0.5, 0.5, None)
    assert s3 == 50.0


def test_bands() -> None:
    assert C.band_for(70) == "clear" and C.band_for(69.9) == "could_be_clearer"
    assert C.band_for(45) == "could_be_clearer" and C.band_for(44.9) == "unclear"


def test_reasons_are_plain_and_name_the_terms() -> None:
    v = C.vague_signal("Do not be too salesy.")
    d = C.disagreement_signal([[0.1, 0.6, 0.9]] * 4, _probes("follows", "breaks", "follows", "breaks"))
    notes = C.reasons(v, d, C.people_signal([("dismissed", "false_alarm", "flag")]))
    assert notes[0].startswith("Vague wording: “too”, “salesy”")
    assert any("Jev answered differently" in n for n in notes)
    assert any("1 dismissed" in n for n in notes)


# ------------------------------------------------------------------ questions (skill: high = problem, one condition)
def test_clarity_questions_vary_only_the_rule_wording() -> None:
    qs = Q.clarity_questions(["Rule A.", "Rule B."], Q.CLARITY_FIELD_TEXT)
    assert list(qs) == ["w0", "w1"]
    assert qs["w0"].instructions == "Does `text` break this brand rule: Rule A."
    assert qs["w0"].criteria == qs["w1"].criteria
    assert "goes against" in qs["w0"].criteria["true"] and "pass" in qs["w0"].criteria["false"]
    img = Q.clarity_rule_question("Rule.", Q.CLARITY_FIELD_IMAGE)
    assert "`image_description`" in img.instructions
    assert Q.clarity_state("hi", "image_description") == {"image_description": "hi"}
    assert C.probe_field("visual") == "image_description" and C.probe_field("tone") == "text"


# ------------------------------------------------------------------ LLM output validation
def _probe_json(n_follow: int = 3, n_break: int = 3, n_border: int = 2) -> str:
    probes = ([{"text": f"follows {i}", "expect": "follows"} for i in range(n_follow)]
              + [{"text": f"breaks {i}", "expect": "breaks"} for i in range(n_break)]
              + [{"text": f"border {i}", "expect": "borderline"} for i in range(n_border)])
    return json.dumps({"paraphrases": ["Reading one.", "Reading two."], "probes": probes})


def test_probe_set_validation() -> None:
    ok = C.parse_probe_set(_probe_json())
    assert len(ok.probes) == 8 and ok.paraphrases == ["Reading one.", "Reading two."]
    with pytest.raises(Exception):
        C.parse_probe_set(_probe_json(n_follow=1))
    with pytest.raises(Exception):
        C.parse_probe_set(json.dumps({"paraphrases": ["Same.", "same."], "probes": json.loads(_probe_json())["probes"]}))
    with pytest.raises(Exception):
        C.parse_probe_set(json.dumps({"paraphrases": ["a", "b"], "probes": [{"text": "x" * 300, "expect": "follows"}] * 8}))


def test_rewrite_validation_rejects_unchanged_and_missing_examples() -> None:
    good = {"statement": "Don't use the hard-sell phrases 'buy now' or 'act fast'.", "example_follows": "Come by.",
            "example_breaks": "Buy now!", "what_changed": "Named the phrases.", "paraphrases": ["a b c", "d e f"]}
    assert C.parse_rewrite("Do not be too salesy.")(json.dumps(good)).statement.startswith("Don't")
    with pytest.raises(Exception):
        C.parse_rewrite(good["statement"])(json.dumps(good))
    with pytest.raises(Exception):
        C.parse_rewrite("x")(json.dumps({**good, "example_breaks": ""}))


def test_template_paraphrases_and_fallback_probes() -> None:
    assert len(C.template_paraphrases("Do not be too salesy.")) == 2
    ps = C.fallback_probes(["ok 1", "ok 2"], ["bad 1"])
    assert [p.expect for p in ps] == ["follows", "follows", "breaks"]


# ------------------------------------------------------------------ fakes for the API tests
class ClarityJev:
    """Answers each wording Noul: vague rules (by a word in the wording) get answers that move between
    wordings; others agree. The intent Noul answers `intent`."""

    def __init__(self, intent: float = 0.2, fail: bool = False) -> None:
        self.intent, self.fail, self.calls = intent, fail, []

    async def __call__(self, state: Any, questions: dict, **_: Any) -> Any:
        self.calls.append({"state": state, "questions": list(questions)})
        if self.fail:
            raise RuntimeError("boom")
        nouls = {}
        text = " ".join(str(v) for v in state.values())
        original = questions["w0"].instructions.lower() if "w0" in questions else ""
        wobbly = any(w in original for w in ("salesy", "premium", "inappropriate", "authentic", "on-brand"))
        for i, (name, q) in enumerate(questions.items()):
            if name == "intent_changed":
                p = self.intent
            else:
                base = 0.9 if "breaks" in text else 0.1
                p = min(0.99, base + (0.25 * i if wobbly else 0.0)) if base < 0.5 else max(0.01, base - (0.25 * i if wobbly else 0.0))
            nouls[name] = SimpleNamespace(noul=p)
        return SimpleNamespace(model="jev-1.14.0", nouls=nouls, choices={}, usage=SimpleNamespace(input_tokens=200))


class ClarityLLM:
    def __init__(self, bad_first: int = 0) -> None:
        self.calls, self.bad_first = [], bad_first

    async def __call__(self, messages: list, *, model: str) -> tuple[str, int, int]:
        self.calls.append(messages)
        if self.bad_first > 0:
            self.bad_first -= 1
            return "not json", 100, 10
        user = messages[-1]["content"]
        if '"probes"' in user:
            return _probe_json().replace("follows 0", "a calm post that follows").replace("breaks 0", "a post that breaks"), 300, 200
        return json.dumps({"statement": "Don't use hard-sell phrases such as 'buy now', 'act fast' or 'last chance'.",
                           "example_follows": "Our new roast is in. Come by when you like.",
                           "example_breaks": "Buy now and act fast!",
                           "what_changed": "Named the hard-sell phrases instead of 'too salesy'.",
                           "paraphrases": ["Never use hard-sell phrases like 'buy now', 'act fast' or 'last chance'.",
                                           "Keep 'buy now', 'act fast' and 'last chance' out of the copy."]}), 400, 150


def install(monkeypatch: pytest.MonkeyPatch, fake_jev: Any = None, llm: Any = None) -> None:
    monkeypatch.setattr(jev, "available", lambda: fake_jev is not None)
    if fake_jev is not None:
        monkeypatch.setattr(jev, "_call_api", fake_jev)
    monkeypatch.setattr(explain, "available", lambda: llm is not None)
    if llm is not None:
        monkeypatch.setattr(explain, "_chat", llm)


def _clarity(client: TestClient) -> list[dict]:
    r = client.get("/api/rules/clarity")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def temp_rule(client: TestClient):  # noqa: ANN201
    """A throwaway vague rule, so rewrite/apply never changes the shared seed rules other tests use."""
    login(client, "admin")
    r = client.post("/api/rules", json={"code": "R-TST-CLR", "category": "messaging", "severity": "minor",
                                        "statement": "Do not be too salesy.", "source_quote": "", "page": None,
                                        "check_method": "jev"})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    with Session(get_engine()) as s:
        rule = s.get(Rule, rid)
        rule.params = {"vague": True}
        rule.enabled = False  # never affects other tests' checks
        s.add(rule)
        s.commit()
    yield rid
    with Session(get_engine()) as s:
        for row in s.exec(select(RuleClarity).where(RuleClarity.rule_id == rid)).all():
            s.delete(row)
        rule = s.get(Rule, rid)
        if rule is not None:
            s.delete(rule)
        s.commit()


# ------------------------------------------------------------------ API
def test_list_without_ai_uses_wording_and_reviewers_and_ranks_vague_rules_last(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    install(monkeypatch)
    login(client, "creator")  # any signed-in user can read
    rows = _clarity(client)
    seeded = [r for r in rows if r["code"] in {x["code"] for x in SEED_RULES}]
    assert len(seeded) == len(SEED_RULES)
    keys = {"rule_id", "code", "score", "band", "reasons", "computed_at", "stale", "measured", "partial", "rewrite", "history"}
    assert keys <= set(rows[0])
    assert {"vague_terms", "disagreement", "dismiss_rate", "override_rate", "notes"} <= set(rows[0]["reasons"])
    assert [r["score"] for r in rows] == sorted(r["score"] for r in rows)
    assert {r["code"] for r in seeded[:5]} == VAGUE
    for r in seeded:
        assert (r["band"] != "clear") == (r["code"] in VAGUE), (r["code"], r["score"])
        if not r["measured"]:
            assert r["stale"] and r["partial"] and r["reasons"]["disagreement"] is None


def test_writes_are_admin_only(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    install(monkeypatch)
    login(client, "creator")
    rid = _clarity(client)[0]["rule_id"]
    assert client.post("/api/rules/clarity/run").status_code == 403
    assert client.post(f"/api/rules/{rid}/clarity").status_code == 403
    assert client.post(f"/api/rules/{rid}/rewrite").status_code == 403
    assert client.post(f"/api/rules/{rid}/rewrite/apply").status_code == 403


def test_score_one_rule_with_fakes_logs_clarity_calls(client: TestClient, monkeypatch, temp_rule: int) -> None:  # noqa: ANN001
    fake, llm = ClarityJev(), ClarityLLM()
    install(monkeypatch, fake, llm)
    login(client, "admin")
    with Session(get_engine()) as s:
        before = len(s.exec(select(AICall).where(AICall.purpose == "clarity")).all())
    r = client.post(f"/api/rules/{temp_rule}/clarity")
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["measured"] and not o["stale"] and o["paraphrase_source"] == "llm" and o["probe_source"] == "llm"
    assert len(o["paraphrases"]) == 2 and o["band"] in ("unclear", "could_be_clearer")
    assert o["reasons"]["disagreement"] > 0.3 and o["reasons"]["vague_terms"] == ["too", "salesy"]
    assert len(fake.calls) == 8  # one call per probe ...
    assert all(c["questions"] == ["w0", "w1", "w2"] for c in fake.calls)  # ... with every wording in it
    assert len(llm.calls) == 1  # paraphrases + probes in one LLM call
    with Session(get_engine()) as s:
        calls = s.exec(select(AICall).where(AICall.purpose == "clarity")).all()
    assert len(calls) - before == 9 and {c.provider for c in calls} == {"typesafe", "llm"}
    # cached per rule version: a refresh asks Jev again but not the LLM
    client.post(f"/api/rules/{temp_rule}/clarity")
    assert len(llm.calls) == 1 and len(fake.calls) == 16


def test_failed_jev_is_an_error_not_clear(client: TestClient, monkeypatch, temp_rule: int) -> None:  # noqa: ANN001
    install(monkeypatch, ClarityJev(fail=True), ClarityLLM())
    login(client, "admin")
    o = client.post(f"/api/rules/{temp_rule}/clarity").json()
    assert not o["measured"] and o["stale"] and o["reasons"]["disagreement"] is None
    assert "Jev didn't answer" in (o["error"] or "")


def test_without_llm_uses_templates_and_stored_examples(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    fake = ClarityJev()
    install(monkeypatch, fake, None)
    login(client, "admin")
    row = next(r for r in _clarity(client) if r["code"] == "R-TONE-05")
    o = client.post(f"/api/rules/{row['rule_id']}/clarity").json()
    assert o["paraphrase_source"] == "template" and o["probe_source"] == "examples" and o["measured"]
    assert len(fake.calls) == 4  # the rule's 2 OK + 2 violating examples
    assert client.post(f"/api/rules/{row['rule_id']}/rewrite").status_code == 503


def test_rewrite_measures_effect_then_apply_makes_a_new_version(client: TestClient, monkeypatch, temp_rule: int) -> None:  # noqa: ANN001
    install(monkeypatch, ClarityJev(), ClarityLLM())
    login(client, "admin")
    assert client.post(f"/api/rules/{temp_rule}/rewrite/apply").status_code == 409  # nothing suggested yet
    r = client.post(f"/api/rules/{temp_rule}/rewrite")
    assert r.status_code == 200, r.text
    rw = r.json()["rewrite"]
    assert rw["for_version"] == 1 and "buy now" in rw["statement"]
    assert rw["example_follows"] and rw["example_breaks"]
    assert rw["disagreement_after"] < rw["disagreement_before"]
    assert rw["score_after"] > rw["score_before"] and rw["band_after"] == "clear"
    assert rw["intent_changed"] == 0.2 and rw["intent_warning"] is False
    a = client.post(f"/api/rules/{temp_rule}/rewrite/apply")
    assert a.status_code == 200, a.text
    o = a.json()
    assert o["version"] == 2 and o["statement"] == rw["statement"] and o["rewrite"] is None
    assert o["history"][0]["statement"] == "Do not be too salesy." and o["history"][0]["version"] == 1
    assert o["measured"] and not o["stale"] and o["band"] == "clear"  # the measured rewrite is the new version's signal
    with Session(get_engine()) as s:
        rule = s.get(Rule, temp_rule)
        assert rule.version == 2 and "vague" not in rule.params
        assert rule.examples_ok[0] == rw["example_follows"] and rule.examples_violating[0] == rw["example_breaks"]
        ev = s.exec(select(AuditEvent).where(AuditEvent.action == "rule_rewritten")).all()[-1]
        assert ev.detail["old_statement"] == "Do not be too salesy." and ev.detail["to_version"] == 2


def test_intent_warning_retries_once_then_warns(client: TestClient, monkeypatch, temp_rule: int) -> None:  # noqa: ANN001
    llm = ClarityLLM()
    install(monkeypatch, ClarityJev(intent=0.9), llm)
    login(client, "admin")
    rw = client.post(f"/api/rules/{temp_rule}/rewrite").json()["rewrite"]
    assert rw["intent_warning"] is True
    rewrite_calls = [m for m in llm.calls if '"probes"' not in m[-1]["content"]]
    assert len(rewrite_calls) == 2 and "previous draft was rejected" in rewrite_calls[1][-1]["content"]
    # dismiss drops it
    o = client.post(f"/api/rules/{temp_rule}/rewrite/dismiss").json()
    assert o["rewrite"] is None


def test_rewrite_goes_stale_when_the_rule_is_edited(client: TestClient, monkeypatch, temp_rule: int) -> None:  # noqa: ANN001
    install(monkeypatch, ClarityJev(), ClarityLLM())
    login(client, "admin")
    client.post(f"/api/rules/{temp_rule}/rewrite")
    client.patch(f"/api/rules/{temp_rule}", json={"statement": "Do not be overly salesy."})
    o = next(r for r in _clarity(client) if r["rule_id"] == temp_rule)
    assert o["stale"] and o["rewrite"] is None and o["version"] == 2
    assert client.post(f"/api/rules/{temp_rule}/rewrite/apply").status_code == 409


def test_run_all_in_background_and_poll(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    install(monkeypatch, ClarityJev(), ClarityLLM())
    login(client, "admin")
    r = client.post("/api/rules/clarity/run")
    assert r.status_code == 202, r.text
    assert r.json()["total"] >= len(SEED_RULES)
    for _ in range(100):
        st = client.get("/api/rules/clarity/run").json()
        if st["status"] != "running":
            break
        time.sleep(0.1)
    assert st["status"] == "done" and st["done"] == st["total"] and st["failed"] == 0
    assert st["cost_usd"] > 0
    rows = _clarity(client)
    seeded = [r for r in rows if r["code"] in {x["code"] for x in SEED_RULES}]
    assert all(r["measured"] for r in seeded)
    assert {r["code"] for r in seeded[:5]} == VAGUE
    for r in seeded:
        assert (r["band"] != "clear") == (r["code"] in VAGUE), (r["code"], r["score"])


def test_features_lists_rule_clarity(client: TestClient) -> None:
    assert client.get("/api/features").json()["rule_clarity"] is True


# ------------------------------------------------------------------ live smoke (RUN_LIVE=1)
LIVE = os.environ.get("RUN_LIVE") == "1"


@pytest.mark.live
@pytest.mark.skipif(not LIVE, reason="set RUN_LIVE=1 (uses TYPESAFE_API_KEY and LLM_API_KEY from the repo .env)")
def test_live_clarity_one_vague_one_clear(monkeypatch) -> None:  # noqa: ANN001
    from test_live_jev import _env

    from app.config import get_settings

    jkey, lkey = _env("TYPESAFE_API_KEY"), _env("LLM_API_KEY")
    if not (jkey and lkey):
        pytest.skip("needs TYPESAFE_API_KEY and LLM_API_KEY")
    monkeypatch.setattr(get_settings(), "typesafe_api_key", jkey)
    monkeypatch.setattr(get_settings(), "llm_api_key", lkey)
    jev._clients.clear()
    explain._clients.clear()
    rules = {r["code"]: r for r in SEED_RULES}

    async def score(code: str) -> tuple[float, C.DisagreementResult, float]:
        r = rules[code]
        calls = jev.CallLog()
        gen = await C.generate_probe_set(r["statement"], r["category"], "Saffron & Steam Coffee Co.", calls)
        assert gen is not None
        probes = [C.Probe(p.text, p.expect) for p in gen.probes]
        ans = await C.measure([r["statement"], *gen.paraphrases], probes, field=C.probe_field(r["category"]), calls=calls)
        d = C.disagreement_signal(ans, probes)
        assert d is not None and d.failed_calls == 0
        v = C.vague_signal(r["statement"], True)
        return C.combine(v.score, d.score, None)[0], d, calls.cost_usd

    t0 = time.perf_counter()
    (vague_score, dv, c1), (clear_score, dc, c2) = asyncio.run(score("R-MSG-05")), asyncio.run(score("R-TERM-02"))
    print(f"\n  R-MSG-05 score={vague_score} D={dv.score}  R-TERM-02 score={clear_score} D={dc.score}  "
          f"cost=${c1 + c2:.5f}  {time.perf_counter() - t0:.1f}s")
    assert vague_score < C.CLEAR_AT <= clear_score
