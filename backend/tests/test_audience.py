"""P1-1 Audience Preview: request building, code-side aggregation and the API, with Jev and the
LLM faked at the raw-call level (jev._call_api, explain._chat)."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import get_engine
from app.models import AICall, AudienceRun, Version
from app.pipeline import audience as A
from app.pipeline import explain, jev
from app.pipeline import questions as Q
from app.routers import innovations
from conftest import login
from pipeline_helpers import DEMO_TEXT

CLEAN = ("Saffron Signature Roast is back on our shelves.\n\n"
         "Rated 4.6 on Google from 3,200 reviews.\n\nA 250g bag is Rs 495 incl. taxes.\n\nOrder online from our website.")


class AudienceJev:
    """Readers find the post hard to believe (Score level 4) when the content has '#1' and believable (level 1)
    otherwise, with a one-level shift for the 41-60 group; students say it isn't for them (relevance, which must
    not move credibility); the culprit Choice picks the option whose paragraph contains '#1'."""

    def __init__(self, fail_requests: int = 0) -> None:
        self.calls: list[dict] = []
        self.fail_requests = fail_requests

    async def __call__(self, state: Any, questions: dict, *, model: Any, extra_headers: Any, timeout: float) -> Any:
        is_audience = isinstance(state, dict) and ("content" in state or "caption" in state) and \
            any(k.endswith("_credibility") for k in questions)
        if is_audience:
            self.calls.append({"state": state, "questions": questions})
        if is_audience and self.fail_requests > 0:
            self.fail_requests -= 1
            from typesafe_sdk import TypeSafeInternalServerError
            import httpx2

            raise TypeSafeInternalServerError(500, {"error": "boom"}, httpx2.Headers())
        content = state.get("content", "") if isinstance(state, dict) else ""
        bad = "#1" in content
        nouls, choices, scores = {}, {}, {}
        for name, q in questions.items():
            if name == "culprit":
                opts = list(q.criteria)
                pick = next((o for o in opts if "#1" in q.criteria[o]), opts[0])
                choices[name] = SimpleNamespace(choice=pick, confidence=0.6,
                                                probabilities={o: (0.6 if o == pick else 0.4 / (len(opts) - 1)) for o in opts})
            elif name == "any_distrust":
                nouls[name] = SimpleNamespace(noul=0.95 if bad else 0.1)
            elif name.endswith("_credibility"):
                top = 3 if bad else 0
                if "Middle-aged adult" in q.instructions:
                    top += 1
                probs = {str(k): (0.8 if k == top else 0.05) for k in range(5)}
                scores[name] = SimpleNamespace(score=float(top), confidence=0.8, probabilities=probs)
            elif name.endswith("_not_for_them"):
                nouls[name] = SimpleNamespace(noul=0.9 if "pocket money" in q.instructions else 0.1)
            else:
                nouls[name] = SimpleNamespace(noul=0.1)
        return SimpleNamespace(model="jev-1.14.0", nouls=nouls, choices=choices, scores=scores,
                               usage=SimpleNamespace(input_tokens=8000))


def install_audience(monkeypatch, fake: AudienceJev, llm: Any = None) -> None:  # noqa: ANN001
    monkeypatch.setattr(jev, "available", lambda: True)
    monkeypatch.setattr(jev, "_call_api", fake)
    monkeypatch.setattr(explain, "available", lambda: llm is not None)
    if llm is not None:
        monkeypatch.setattr(explain, "_chat", llm)


# ------------------------------------------------------------------ pure parts
def test_personas_load_240_in_six_segments() -> None:
    ps = A.load_personas()
    assert len(ps) == 240
    assert len({p["segment"] for p in ps}) == 6


def test_fallback_readers_are_the_six_segments_with_traits() -> None:
    rs = A.load_readers()
    assert A.readers_source() == "default" and len(rs) == 240
    assert {r["segment"] for r in rs} == set(Q.AUDIENCE_FALLBACK_GROUPS)
    prof = A.reader_profile(rs[0])
    assert prof.startswith(f"{rs[0]['age']}-year-old") and "Trusts:" in prof and "Doubts:" in prof


def test_each_reader_gets_its_own_three_questions_reader_first_in_chunks_of_20() -> None:
    rs = list(A.load_readers())
    paras = [p.strip() for p in DEMO_TEXT.split("\n\n")]
    reqs = A.build_requests(rs, paras, is_image=False)
    assert len(reqs) == 12
    assert all(len(chunk) == 20 for _, chunk in reqs)
    qs0, chunk0 = reqs[0]
    assert set(qs0) >= {"any_distrust", "culprit"}
    for suffix in ("_credibility", "_not_for_them", "_confused"):
        assert sum(1 for k in qs0 if k.endswith(suffix)) == 20, suffix
    assert len(qs0) == 3 * 20 + 2
    r = chunk0[0]
    q = qs0[f"p{r['idx']}_credibility"]
    assert q.type == "score" and len(q.criteria) == 5
    assert q.instructions.startswith("Reader: " + A.reader_profile(r))  # the reader comes first
    assert "`content`" in q.instructions
    assert qs0[f"p{r['idx']}_confused"].instructions.startswith("Reader: ")
    nft = qs0[f"p{r['idx']}_not_for_them"]
    assert nft.type == "noul" and nft.instructions.startswith("Reader: " + A.reader_profile(r))
    assert all("culprit" not in qs for qs, _ in reqs[1:])
    assert all(not k.startswith(("p1", "option", "a_")) for k in qs0["culprit"].criteria)


def test_option_names_are_unique() -> None:
    names = A.option_names(["Order now.", "Order now.", "Something else here"])
    assert len(names) == 3 and sorted(names.values()) == [0, 1, 2]


def test_wording_lives_in_questions_module_and_has_no_brand_examples() -> None:
    q = Q.audience_confused_question("34-year-old shop owner")
    assert "misunderstand the main message" in q.instructions
    texts = [Q.question_text(q), Q.question_text(Q.audience_credibility_question("x")), Q.question_text(Q.AUDIENCE_ANY_DISTRUST)]
    blob = json.dumps(texts)
    assert "Rs 495" not in blob and "coffee" not in blob.lower()
    assert Q.AUDIENCE_CREDIBILITY_LEVELS[0].startswith("This reader finds the post clear and believable")
    assert "scam" in Q.AUDIENCE_CREDIBILITY_LEVELS[4]


def test_credibility_levels_measure_only_credibility() -> None:
    """The old Score anchored level 1 on acting and level 4 on "doesn't fit what they need", which made every
    ad from an unknown brand look doubted. Credibility levels must not mention acting or relevance."""
    blob = " ".join(Q.AUDIENCE_CREDIBILITY_LEVELS).lower()
    for word in ("would act", "open the link", "try the offer", "forward", "fit what they", "need"):
        assert word not in blob, word
    assert len(Q.AUDIENCE_CREDIBILITY_LEVELS) == 5 == len(Q.AUDIENCE_LEVEL_LABELS)
    rel = Q.audience_not_for_them_question("x")
    assert "even if they believe it" in rel.criteria["true"]  # names the case that caused the mix-up
    assert "believable" in Q.audience_credibility_question("x").instructions


def test_expected_score_math() -> None:
    assert A.expected_score({"0": 1.0}) == 1.0
    assert A.expected_score({"4": 1.0}) == 5.0
    assert A.expected_score({"0": 0.5, "4": 0.5}) == 3.0  # two peaks average to the middle
    assert abs(A.expected_score({"1": 0.2, "2": 0.8}) - 2.8) < 1e-9
    assert abs(A.expected_score({"0": 1, "1": 1}) - 1.5) < 1e-9  # unnormalised probabilities are normalised
    assert A.expected_score({"x": 1.0, "9": 1.0}, fallback=2.2) == 3.2  # unknown keys -> the API's 0-based score + 1
    assert A.expected_score({}, fallback=None) is None
    assert [A.level(x) for x in (1.0, 1.49, 1.5, 3.2, 4.6, 5.0)] == [1, 1, 2, 3, 5, 5]


def test_lean_truth_table() -> None:
    assert [A.lean(x) for x in (1.0, 2.5, 2.51, 3.0, 3.49, 3.5, 5.0)] == ["trust", "trust", "unsure", "unsure",
                                                                          "unsure", "doubt", "doubt"]
    assert A.lean(None) is None


def _reader(i: int, seg: str) -> dict:
    return {"idx": i, "id": str(i), "segment": seg, "age": 30, "sex": "Male", "occupation": "Clerk",
            "district": "Pune", "state": "Maharashtra", "group": {"label": seg.upper(), "description": "d", "traits": {}}}


def test_aggregate_relevance_is_reported_apart_from_credibility() -> None:
    rs = [_reader(i, "a" if i < 4 else "b") for i in range(8)]
    # same credibility everywhere; group a says it isn't for them (p 0.9), group b finds it relevant
    answers = {i: (1.5, 0.1, 0.9 if i < 4 else 0.1) for i in range(8)}
    answers[3] = (1.5, 0.1, 0.5)  # in the uncertain band: not counted as "not for them"
    out = A.aggregate(rs, answers)
    a, b = out["segments"]
    assert a["mean_score"] == b["mean_score"] == 1.5 and a["trust_pct"] == b["trust_pct"] == 1.0
    assert a["not_for_them_pct"] == 0.75 and b["not_for_them_pct"] == 0.0
    assert abs(a["mean_not_for_them"] - 0.8) < 1e-9
    assert out["overall"]["not_for_them_pct"] == 0.375
    rows = {r["idx"]: r for r in out["personas"]}
    assert rows[0]["not_for_them_flag"] is True and rows[3]["not_for_them_flag"] is False and rows[3]["not_for_them"] == 0.5


def test_worst_readers_are_by_credibility_only() -> None:
    rs = [_reader(i, "a") for i in range(4)]
    # reader 0 is the least convinced; readers 1-3 tie on credibility but differ in confusion / relevance
    out = A.aggregate(rs, {0: (4.0, 0.0, 0.0), 1: (2.0, 0.9, 0.9), 2: (2.0, 0.0, 0.0), 3: (3.0, 0.0, 0.95)})
    assert [w["idx"] for w in out["segments"][0]["worst"]] == [0, 3, 1]


def test_two_tuple_answers_still_aggregate() -> None:
    out = A.aggregate([_reader(0, "a")], {0: (2.0, 0.1)})
    assert out["overall"]["not_for_them_pct"] == 0.0 and out["personas"][0]["not_for_them"] is None


def test_aggregate_per_group_mean_spread_shares_and_vs_overall() -> None:
    rs = [_reader(i, "a" if i < 4 else "b") for i in range(8)]
    answers = {0: (1.0, 0.1), 1: (3.0, 0.8), 2: (5.0, 0.1), 3: (4.0, 0.2), 4: (2.0, 0.1), 5: (2.0, 0.1)}
    out = A.aggregate(rs, answers)
    a = next(s for s in out["segments"] if s["key"] == "a")
    assert a["mean_score"] == 3.25 and a["label"] == "A"
    assert abs(a["sd"] - 1.479) < 0.001
    assert a["trust_pct"] == 0.25 and a["unsure_pct"] == 0.25 and a["doubt_pct"] == 0.5 and a["confused_pct"] == 0.25
    assert a["levels"] == [1, 0, 1, 1, 1]
    assert [w["idx"] for w in a["worst"]] == [2, 3, 1]  # by credibility only
    b = next(s for s in out["segments"] if s["key"] == "b")
    assert b["answered"] == 2 and b["n"] == 4 and b["mean_score"] == 2.0 and b["sd"] == 0.0  # 6, 7 not answered
    assert out["overall"]["answered"] == 6 and abs(out["overall"]["mean_score"] - 17 / 6) < 1e-3
    assert abs(a["vs_overall"] - (3.25 - out["overall"]["mean_score"])) < 1e-3 and b["vs_overall"] < 0
    rows = {r["idx"]: r for r in out["personas"]}
    assert rows[0]["lean"] == "trust" and rows[2]["level"] == 5 and rows[1]["confused_flag"] and rows[6]["score"] is None


def test_culprit_truth_table() -> None:
    paras = [{"text": "Hello there friends", "char_start": 0, "char_end": 19}, {"text": "We are #1 forever", "char_start": 21, "char_end": 38}]
    names = list(A.option_names([p["text"] for p in paras]))
    ch = jev.ChoiceAnswer(choice=names[1], confidence=0.7, probabilities={names[0]: 0.3, names[1]: 0.7})

    def res(any_p: float) -> jev.JevResult:
        return jev.JevResult(ok=True, nouls={"any_distrust": any_p}, choices={"culprit": ch})

    assert A.culprit(paras, res(0.1)) is None
    maybe = A.culprit(paras, res(0.5))
    assert maybe["index"] == 1 and maybe["certain"] is False
    sure = A.culprit(paras, res(0.9))
    assert sure["certain"] and sure["char_start"] == 21 and sure["probabilities"][0] == {"index": 1, "p": 0.7}
    assert A.culprit(paras, jev.JevResult(ok=False, error="x")) is None


def test_compare_per_group_delta_sign() -> None:
    rs = [_reader(i, "a" if i < 2 else "b") for i in range(4)]
    prev = {**A.aggregate(rs, {0: (4.0, 0.1), 1: (5.0, 0.1), 2: (3.0, 0.1), 3: (3.0, 0.1)}), "schema": A.SCHEMA}
    cur = {**A.aggregate(rs, {0: (2.0, 0.1), 1: (2.0, 0.1), 2: (3.0, 0.1), 3: (4.0, 0.1)}), "schema": A.SCHEMA}
    c = A.compare(cur, prev, version_id=1, version_n=1, run_id=1, created_at=None)
    seg = {s["key"]: s["delta"] for s in c["segments"]}
    assert seg["a"]["mean_score"] == -2.5 and seg["a"]["doubt_pct"] == -1.0 and seg["a"]["trust_pct"] == 1.0
    assert seg["b"]["mean_score"] == 0.5
    assert c["delta"]["mean_score"] == -1.0
    assert A.compare(cur, {**prev, "schema": None}, version_id=1, version_n=1, run_id=1, created_at=None) is None


# ------------------------------------------------------------------ audiences.json
GROUPS = {"groups": [
    {"key": "students", "label": "Blind students", "description": "College student who is blind", "share": 3,
     "age_range": [18, 26], "occupations": ["college student"],
     "traits": {"needs": "audio textbooks", "trust_triggers": "friends", "doubt_triggers": "100% claims",
                "language": "screen reader"}},
    {"key": "elders", "label": "Older readers", "description": "Over 55 with low vision", "share": 1,
     "traits": {"needs": "simple steps", "trust_triggers": "family", "doubt_triggers": "jargon", "language": "Marathi"}},
]}


def test_allocate_by_share() -> None:
    assert A.allocate([3, 1], 240) == [180, 60]
    assert sum(A.allocate([1, 1, 1], 100)) == 100
    assert A.allocate([100, 0.001], 10)[1] >= 1  # every group gets a reader


def test_audiences_json_builds_groups_with_nemotron_variety(tmp_path) -> None:  # noqa: ANN001
    f = tmp_path / "audiences.json"
    f.write_text(json.dumps(GROUPS), encoding="utf-8")
    groups = A.load_groups(f)
    assert [g["key"] for g in groups] == ["students", "elders"]
    rs = A.build_readers(groups, A.load_personas(), 40)
    assert len(rs) == 40 and sum(r["segment"] == "students" for r in rs) == 30
    st = [r for r in rs if r["segment"] == "students"]
    assert all(18 <= r["age"] <= 26 for r in st) and all(r["occupation"] == "college student" for r in st)
    assert len({r["district"] for r in st}) > 5  # demographic variety from the personas
    assert [r["idx"] for r in rs] == list(range(40)) and len({r["id"] for r in rs}) == 40
    assert rs == A.build_readers(groups, A.load_personas(), 40)  # deterministic
    prof = A.reader_profile(st[0])
    assert prof.startswith(f"{st[0]['age']}-year-old") and "College student who is blind" in prof and "100% claims" in prof
    out = A.aggregate(rs, {r["idx"]: (2.0, 0.1) for r in rs})
    assert [s["label"] for s in out["segments"]] == ["Blind students", "Older readers"]


def test_audiences_json_missing_or_bad_falls_back(tmp_path) -> None:  # noqa: ANN001
    assert A.load_groups(tmp_path / "audiences.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert A.load_groups(bad) is None
    dup = tmp_path / "dup.json"
    dup.write_text(json.dumps({"groups": [GROUPS["groups"][0], GROUPS["groups"][0]]}), encoding="utf-8")
    assert A.load_groups(dup) is None


def test_seed_dir_audiences_json_is_used_and_not_inherited(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from app.config import get_settings

    (tmp_path / "audiences.json").write_text(json.dumps(GROUPS), encoding="utf-8")
    monkeypatch.setattr(get_settings(), "seed_dir", str(tmp_path))
    assert A.readers_source() == "brand" and len(A.load_readers()) == 240
    assert {r["segment"] for r in A.load_readers()} == {"students", "elders"}
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr(get_settings(), "seed_dir", str(other))  # no audiences.json there: the default groups
    assert A.readers_source() == "default"


# ------------------------------------------------------------------ API
def _item(client: TestClient, text: str, confidential: bool = False) -> int:
    login(client, "creator")
    r = client.post("/api/items", data={"title": "aud", "channel": "instagram", "text": text,
                                        "confidential": str(confidential).lower()})
    assert r.status_code == 201, r.text
    return r.json()["version_id"]


def _wait(client: TestClient, vid: int, q: str = "") -> dict:
    t0 = time.time()
    while time.time() - t0 < 20:
        out = client.get(f"/api/versions/{vid}/audience{q}").json()
        if out["status"] != "running":
            return out
        time.sleep(0.05)
    raise AssertionError("audience run did not finish")


def test_confidential_is_refused(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    fake = AudienceJev()
    install_audience(monkeypatch, fake)
    vid = _item(client, DEMO_TEXT, confidential=True)
    r = client.post(f"/api/versions/{vid}/audience")
    assert r.status_code == 409 and "confidential" in r.json()["detail"]
    assert client.get(f"/api/versions/{vid}/audience").json()["status"] == "unavailable"
    assert fake.calls == []


def test_full_run_masks_pii_logs_calls_and_labels(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    fake = AudienceJev()
    install_audience(monkeypatch, fake)
    vid = _item(client, DEMO_TEXT + "\n\nCall 98765 43210 to order.")
    r = client.post(f"/api/versions/{vid}/audience")
    assert r.status_code == 202 and r.json()["status"] in ("running", "done")
    out = _wait(client, vid)
    assert out["status"] == "done", out
    assert out["label"] == "Simulated audience, directional signal, not a survey"
    assert out["attribution"] == "Personas: NVIDIA Nemotron-Personas-India (CC-BY-4.0)"
    assert len(out["personas"]) == 240 and len(out["segments"]) == 6
    assert out["audience_source"] == "default"
    assert out["overall"]["doubt_pct"] == 1.0 and out["overall"]["mean_score"] > 3.5
    old = next(s for s in out["segments"] if s["key"] == "41-60")
    assert old["vs_overall"] > 0.5  # this group reacts worse in the fake, and the numbers show it
    assert all(len(s["worst"]) == 3 for s in out["segments"])
    stu = next(s for s in out["segments"] if s["key"] == "students")
    young = next(s for s in out["segments"] if s["key"] == "18-24 urban")
    assert stu["not_for_them_pct"] == 1.0 and young["not_for_them_pct"] == 0.0  # relevance is per group ...
    assert abs(stu["mean_score"] - young["mean_score"]) < 1e-6  # ... and does not move credibility
    assert out["personas"][0]["not_for_them"] is not None
    assert out["culprit_paragraph"]["index"] == 0 and out["culprit_paragraph"]["certain"]
    assert out["progress"] == {"done": 240, "total": 240}
    assert len(fake.calls) == 12
    assert all("98765" not in c["state"]["content"] and "[phone_1]" in c["state"]["content"] for c in fake.calls)
    with Session(get_engine()) as s:
        rows = s.exec(select(AICall).where(AICall.version_id == vid, AICall.purpose == "audience")).all()
    assert len(rows) == 12 and all(r.provider == "typesafe" and r.input_tokens == 8000 for r in rows)


def test_previous_version_delta_and_compare_to(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    install_audience(monkeypatch, AudienceJev())
    v1 = _item(client, DEMO_TEXT)
    client.post(f"/api/versions/{v1}/audience")
    _wait(client, v1)
    with Session(get_engine()) as s:  # a fixed v2 of the same item
        item_id = s.get(Version, v1).item_id
        v2 = Version(item_id=item_id, n=2, text=CLEAN, check_status="not_run")
        s.add(v2)
        s.commit()
        s.refresh(v2)
        v2id = v2.id
    client.post(f"/api/versions/{v2id}/audience")
    out = _wait(client, v2id)
    cmp = out["compare_to_previous_version"]
    assert cmp["version_id"] == v1 and cmp["version_n"] == 1
    assert cmp["delta"]["doubt_pct"] == -1.0 and cmp["delta"]["mean_score"] < -1.5
    assert all(sd["delta"]["mean_score"] < 0 for sd in cmp["segments"])
    assert len(cmp["segments"]) == 6 and cmp["culprit_paragraph"]["index"] == 0
    assert out["culprit_paragraph"] is None  # any_distrust 0.1 -> no paragraph singled out
    # explicit compare with any version (e.g. a separately created clean post)
    other = _item(client, CLEAN)
    client.post(f"/api/versions/{other}/audience")
    _wait(client, other)
    out2 = client.get(f"/api/versions/{v1}/audience?compare_to={other}").json()
    assert out2["compare_to_previous_version"]["delta"]["doubt_pct"] == 1.0


def test_partial_failure_is_reported_and_total_failure_is_an_error(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    install_audience(monkeypatch, AudienceJev(fail_requests=1))
    vid = _item(client, DEMO_TEXT)
    client.post(f"/api/versions/{vid}/audience")
    out = _wait(client, vid)
    assert out["status"] == "done" and out["overall"]["answered"] == 220
    assert any("could not be asked" in w for w in out["warnings"])

    install_audience(monkeypatch, AudienceJev(fail_requests=99))
    vid2 = _item(client, DEMO_TEXT)
    client.post(f"/api/versions/{vid2}/audience")
    out2 = _wait(client, vid2)
    assert out2["status"] == "error" and "failed" in out2["message"]
    with Session(get_engine()) as s:
        rows = s.exec(select(AICall).where(AICall.version_id == vid2, AICall.purpose == "audience")).all()
    assert rows and not any(r.ok for r in rows)  # failed calls are logged as failures, never as "no"


def test_llm_why_for_worst_segments(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    seen: list[str] = []

    async def llm(messages: list, *, model: str) -> tuple[str, int, int]:
        if messages[0]["content"] != A.WHY_SYSTEM:  # the item's own check (explain) also uses the LLM
            raise RuntimeError("not an audience call")
        seen.append(messages[-1]["content"])
        return json.dumps({"why": "The '#1' claim has no source, so it reads as hype."}), 600, 30

    install_audience(monkeypatch, AudienceJev(), llm)
    vid = _item(client, DEMO_TEXT + "\n\nWrite to me at someone@example.com")
    client.post(f"/api/versions/{vid}/audience")
    out = _wait(client, vid)
    whys = [s for s in out["segments"] if s["why"]]
    assert len(whys) == A.WHY_MAX_SEGMENTS and all(s["why_source"] == "llm" for s in whys)
    assert seen and all("someone@example.com" not in m for m in seen)
    with Session(get_engine()) as s:
        rows = s.exec(select(AICall).where(AICall.version_id == vid, AICall.provider == "llm",
                                           AICall.purpose == "audience")).all()
    assert len(rows) == A.WHY_MAX_SEGMENTS and all(r.ok for r in rows)


def test_llm_why_is_best_effort(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    async def llm(messages: list, *, model: str) -> tuple[str, int, int]:
        if messages[0]["content"] == A.WHY_SYSTEM:
            return "not json at all", 600, 30
        raise RuntimeError("not an audience call")

    install_audience(monkeypatch, AudienceJev(), llm)
    vid = _item(client, DEMO_TEXT)
    client.post(f"/api/versions/{vid}/audience")
    out = _wait(client, vid)
    assert out["status"] == "done"
    assert all(s["why"] is None and s["why_source"] is None for s in out["segments"])
    with Session(get_engine()) as s:
        rows = s.exec(select(AICall).where(AICall.version_id == vid, AICall.purpose == "audience",
                                           AICall.provider == "llm")).all()
    assert rows and not any(r.ok for r in rows)  # failed calls are still logged, as errors


def test_stale_running_run_becomes_an_error(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    install_audience(monkeypatch, AudienceJev())
    vid = _item(client, DEMO_TEXT)
    with Session(get_engine()) as s:
        s.add(AudienceRun(version_id=vid, status="running"))
        s.commit()
    assert vid not in innovations._active
    out = client.get(f"/api/versions/{vid}/audience").json()
    assert out["status"] == "error" and "restarted" in out["message"]


def test_sse_streams_progress_then_done(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    install_audience(monkeypatch, AudienceJev())
    vid = _item(client, DEMO_TEXT)
    client.post(f"/api/versions/{vid}/audience")
    names = []
    with client.stream("GET", f"/api/versions/{vid}/audience/events") as r:
        for line in r.iter_lines():
            if line.startswith("event:"):
                names.append(line.split(":", 1)[1].strip())
                if names[-1] in ("done", "error"):
                    break
    assert names[-1] == "done"


def test_image_post_runs_with_image_state_and_no_culprit(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    import io

    from PIL import Image as PILImage

    seen: list[Any] = []
    fake = AudienceJev()

    async def spy(state: Any, questions: dict, **kw: Any) -> Any:
        if isinstance(state, dict) and "caption" in state and any(k.endswith("_confused") for k in questions):
            seen.append((state, questions))
        return await fake(state, questions, **kw)

    install_audience(monkeypatch, fake)
    monkeypatch.setattr(jev, "_call_api", spy)
    buf = io.BytesIO()
    PILImage.new("RGB", (600, 400), (240, 230, 210)).save(buf, "PNG")
    login(client, "creator")
    r = client.post("/api/items", data={"title": "img", "channel": "instagram", "confidential": "false",
                                        "caption": "New roast, call 9876543210"},
                    files={"file": ("ad.png", buf.getvalue(), "image/png")})
    assert r.status_code == 201, r.text
    vid = r.json()["version_id"]
    assert client.post(f"/api/versions/{vid}/audience").status_code == 202
    out = _wait(client, vid)
    assert out["status"] == "done" and out["culprit_paragraph"] is None and len(out["personas"]) == 240
    assert len(seen) == 12
    state, qs = seen[0]
    assert "9876543210" not in state["caption"]  # PII masked before the external call
    assert "culprit" not in qs and "`image`" in next(iter(qs.values())).instructions


@pytest.mark.parametrize("old", [
    {"overall": {"distrust_pct": 1.0}, "segments": []},  # the distrust/unsure model (before 7 Nov)
    {"schema": 2, "overall": {"mean_score": 4.0, "trust_pct": 0.0, "doubt_pct": 1.0}, "segments": []},  # reaction score (7 Nov)
])
def test_old_shape_run_asks_for_a_rerun(client: TestClient, monkeypatch, old) -> None:  # noqa: ANN001
    install_audience(monkeypatch, AudienceJev())
    vid = _item(client, DEMO_TEXT)
    with Session(get_engine()) as s:
        s.add(AudienceRun(version_id=vid, status="done", results=old))
        s.commit()
    out = client.get(f"/api/versions/{vid}/audience").json()
    assert out["status"] == "none" and "Run it again" in out["message"]
    client.post(f"/api/versions/{vid}/audience")
    assert _wait(client, vid)["status"] == "done"
