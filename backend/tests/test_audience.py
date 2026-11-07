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
    """Distrust is high when the content has '#1', with a small per-segment shift; the culprit
    Choice picks the option whose paragraph contains '#1'."""

    def __init__(self, fail_requests: int = 0) -> None:
        self.calls: list[dict] = []
        self.fail_requests = fail_requests

    async def __call__(self, state: Any, questions: dict, *, model: Any, extra_headers: Any, timeout: float) -> Any:
        is_audience = isinstance(state, dict) and "content" in state
        if is_audience:
            self.calls.append({"state": state, "questions": questions})
        if is_audience and self.fail_requests > 0:
            self.fail_requests -= 1
            from typesafe_sdk import TypeSafeInternalServerError
            import httpx2

            raise TypeSafeInternalServerError(500, {"error": "boom"}, httpx2.Headers())
        content = state.get("content", "") if isinstance(state, dict) else ""
        bad = "#1" in content
        nouls, choices = {}, {}
        for name, q in questions.items():
            if name == "culprit":
                opts = list(q.criteria)
                pick = next((o for o in opts if "#1" in q.criteria[o]), opts[0])
                choices[name] = SimpleNamespace(choice=pick, confidence=0.6,
                                                probabilities={o: (0.6 if o == pick else 0.4 / (len(opts) - 1)) for o in opts})
            elif name == "any_distrust":
                nouls[name] = SimpleNamespace(noul=0.95 if bad else 0.1)
            elif name.endswith("_distrust"):
                idx = int(name[1:].split("_")[0])
                young = "Person: 2" in q.instructions or "Person: 1" in q.instructions
                p = (0.85 if bad else 0.2) + (0.05 if young else 0.0) + (idx % 3) * 0.01
                nouls[name] = SimpleNamespace(noul=min(p, 0.99))
            else:
                nouls[name] = SimpleNamespace(noul=0.1)
        return SimpleNamespace(model="jev-1.14.0", nouls=nouls, choices=choices, usage=SimpleNamespace(input_tokens=8000))


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


def test_each_persona_gets_its_own_two_questions_in_chunks_of_20() -> None:
    ps = list(A.load_personas())
    paras = [p.strip() for p in DEMO_TEXT.split("\n\n")]
    reqs = A.build_requests(ps, paras, is_image=False)
    assert len(reqs) == 12
    assert all(len(chunk) == 20 for _, chunk in reqs)
    qs0, chunk0 = reqs[0]
    assert set(qs0) >= {"any_distrust", "culprit"}
    assert sum(1 for k in qs0 if k.endswith("_distrust") and k != "any_distrust") == 20
    p = chunk0[0]
    q = qs0[f"p{p['idx']}_distrust"]
    assert A.persona_bio(p) in q.instructions and "`content`" in q.instructions
    assert all("culprit" not in qs for qs, _ in reqs[1:])
    # options are descriptive (opening words), never positional
    assert all(not k.startswith(("p1", "option", "a_")) for k in qs0["culprit"].criteria)


def test_option_names_are_unique() -> None:
    names = A.option_names(["Order now.", "Order now.", "Something else here"])
    assert len(names) == 3 and sorted(names.values()) == [0, 1, 2]


def test_wording_lives_in_questions_module() -> None:
    q = Q.audience_distrust_question("34-year-old shop owner")
    assert "misleading or hard to trust" in q.instructions
    assert "at face value" in q.criteria["false"]  # a concrete situation, not "not misleading"


def test_aggregate_thresholds_and_worst() -> None:
    ps = [{"idx": i, "id": str(i), "segment": "a" if i < 4 else "b", "age": 30, "sex": "Male", "occupation": "Clerk",
           "district": "Pune", "state": "Maharashtra", "persona": "x"} for i in range(8)]
    answers = {0: (0.9, 0.1), 1: (0.5, 0.8), 2: (0.1, 0.1), 3: (0.75, 0.2), 4: (0.2, 0.1), 5: (0.2, 0.1)}
    out = A.aggregate(ps, answers)
    a = next(s for s in out["segments"] if s["key"] == "a")
    assert a["distrust_pct"] == 0.5 and a["unsure_pct"] == 0.25 and a["confused_pct"] == 0.25
    assert [w["idx"] for w in a["worst"]] == [0, 3, 1]
    b = next(s for s in out["segments"] if s["key"] == "b")
    assert b["answered"] == 2 and b["n"] == 4  # 6 and 7 were not answered
    assert out["overall"]["answered"] == 6
    sig = {r["idx"]: r["signal"] for r in out["personas"]}
    assert sig[0] == "distrust" and sig[1] == "confused" and sig[2] == "ok" and sig[6] is None


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


def test_compare_delta_sign() -> None:
    cur = {"overall": {"n": 2, "answered": 2, "distrust_pct": 0.0, "confused_pct": 0.0, "unsure_pct": 0.5, "mean_distrust": 0.3, "mean_confused": 0.1},
           "segments": []}
    prev = {"overall": {**cur["overall"], "distrust_pct": 1.0, "mean_distrust": 0.8}, "segments": []}
    c = A.compare(cur, prev, version_id=1, version_n=1, run_id=1, created_at=None)
    assert c["delta"]["distrust_pct"] == -1.0 and c["delta"]["mean_distrust"] == -0.5


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
    assert out["overall"]["distrust_pct"] == 1.0
    assert all(len(s["worst"]) == 3 for s in out["segments"])
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
    assert cmp["delta"]["distrust_pct"] == -1.0 and cmp["delta"]["mean_distrust"] < -0.5
    assert len(cmp["segments"]) == 6 and cmp["culprit_paragraph"]["index"] == 0
    assert out["culprit_paragraph"] is None  # any_distrust 0.1 -> no paragraph singled out
    # explicit compare with any version (e.g. a separately created clean post)
    other = _item(client, CLEAN)
    client.post(f"/api/versions/{other}/audience")
    _wait(client, other)
    out2 = client.get(f"/api/versions/{v1}/audience?compare_to={other}").json()
    assert out2["compare_to_previous_version"]["delta"]["distrust_pct"] == 1.0


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
