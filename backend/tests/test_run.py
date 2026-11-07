"""run.py against the real DB and API: background check, SSE order and replay, confidential
routing, AICall logging, errors, the demo post, and the 8-second budget (fakes sleep like the APIs)."""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import get_engine
from app.models import AICall, AuditEvent, ContentItem, Finding, Version
from app.pipeline import engine as E
from app.pipeline import run as R
from app.pipeline.jev import CallLog
from conftest import login
from fakes import FakeJev, FakeLLM, install
from pipeline_helpers import DEMO_TEXT, seed_ctx, text_units


def create(client: TestClient, text: str, confidential: bool = False, channel: str = "instagram") -> dict:
    login(client, "creator")
    r = client.post("/api/items", data={"title": "pipeline test", "channel": channel, "text": text,
                                        "confidential": str(confidential).lower()})
    assert r.status_code == 201, r.text
    return r.json()


def wait_done(client: TestClient, version_id: int, timeout: float = 20) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = client.get(f"/api/versions/{version_id}").json()
        if v["check_status"] in ("done", "error"):
            return v
        time.sleep(0.05)
    raise AssertionError("check did not finish")


def sse(client: TestClient, version_id: int) -> list[tuple[str, dict]]:
    events = []
    with client.stream("GET", f"/api/versions/{version_id}/events") as r:
        name = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                events.append((name, json.loads(line.split(":", 1)[1])))
                if name in ("done", "error"):
                    break
    return events


def findings(client: TestClient, version_id: int) -> list[dict]:
    return client.get(f"/api/versions/{version_id}/findings").json()


# ------------------------------------------------------------------ tier0 only (no keys)
def test_check_runs_in_background_and_sse_completes(client: TestClient) -> None:
    ids = create(client, "Try our Steam Classic, it is cheap!!!\n\nOrder now.")
    events = sse(client, ids["version_id"])
    names = [n for n, _ in events]
    assert names[-1] == "done"
    v = wait_done(client, ids["version_id"])
    assert v["check_status"] == "done"
    fs = findings(client, ids["version_id"])
    codes = {f["rule_code"] for f in fs}
    assert {"R-TERM-01", "R-TERM-02", "R-TONE-04"} <= codes
    f = next(f for f in fs if f["rule_code"] == "R-TERM-01")
    assert v["text"][f["span_start"]:f["span_end"]] == "Steam Classic" and f["probability"] == 1.0 and f["band"] == "flag"
    assert f["rule"]["page"] == 4 and f["rule"]["source_quote"]
    stats = v["stats"]
    assert stats["units_total"] == 2 and stats["units_done"] == 2 and stats["jev"] == "not_configured"
    assert stats["findings_by_band"]["flag"] >= 3 and "elapsed_ms" in stats and stats["cost_usd"] == 0
    item = client.get(f"/api/items/{ids['item_id']}").json()["item"]
    assert item["state"] == "findings_ready"
    with Session(get_engine()) as s:
        actions = [a.action for a in s.exec(select(AuditEvent).where(AuditEvent.version_id == ids["version_id"])).all()]
    assert "check_started" in actions and "check_done" in actions


def test_sse_replay_after_finish_is_summary_findings_done(client: TestClient) -> None:
    ids = create(client, "A cheap brew.")
    wait_done(client, ids["version_id"])
    events = sse(client, ids["version_id"])
    assert events[0][0] == "units" and events[-1][0] == "done"
    middle = events[1:-1]
    assert middle and all(n == "selfcheck" and e["finding"] for n, e in middle)
    assert len(middle) == len(findings(client, ids["version_id"]))


def test_live_sse_event_order(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    install(monkeypatch, FakeJev(sleep=0.2), FakeLLM(sleep=0.1))
    ids = create(client, "India's #1 coffee is cheap.\n\nVisit us today.")
    events = sse(client, ids["version_id"])
    names = [n for n, _ in events]
    assert names[0] in ("queued", "units", "tier0") and names[-1] == "done"
    order = ["queued", "units", "tier0", "jev", "claims", "explain", "selfcheck", "done"]
    first_seen = [order.index(n) for i, n in enumerate(names) if n in order and n not in names[:i]]
    assert first_seen == sorted(first_seen)
    with_finding = [e for _, e in events if e.get("finding")]
    assert with_finding and len({e["finding"]["id"] for e in with_finding}) == len(with_finding)
    for key in ("units_done", "units_total", "judgments", "elapsed_ms", "cost_usd"):
        assert key in events[-1][1]
    assert events[-1][1]["judgments"] > 0 and events[-1][1]["units_done"] == 2


def test_non_confidential_logs_ai_calls_without_content(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    install(monkeypatch, FakeJev(), FakeLLM())
    ids = create(client, "Rated 4.8 stars, the best brew. Mail rohan@example.com")
    v = wait_done(client, ids["version_id"])
    with Session(get_engine()) as s:
        rows = s.exec(select(AICall).where(AICall.version_id == ids["version_id"])).all()
    providers = {r.provider for r in rows}
    assert providers == {"typesafe", "llm"}
    assert all(r.model and r.purpose and r.latency_ms >= 0 for r in rows)
    assert v["stats"]["pii_masked"] >= 1 and v["stats"]["cost_usd"] > 0


def test_confidential_makes_no_external_calls(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    jf, lf = FakeJev(), FakeLLM()
    install(monkeypatch, jf, lf)
    ids = create(client, DEMO_TEXT, confidential=True)
    v = wait_done(client, ids["version_id"])
    with Session(get_engine()) as s:
        rows = s.exec(select(AICall).where(AICall.version_id == ids["version_id"])).all()
    assert rows == [] and jf.calls == [] and lf.calls == []
    assert v["stats"]["jev"] == "skipped_confidential" and v["stats"]["external_calls"] == 0
    fs = findings(client, ids["version_id"])
    assert fs and all(f["tier"] in ("tier0", "claim") for f in fs)
    usage = client.get(f"/api/items/{ids['item_id']}/ai-usage")
    if usage.status_code == 200:
        assert usage.json()["no_external_services"] is True


def test_demo_post_full_pipeline_with_mocks(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    install(monkeypatch, FakeJev(support={"F-12": 0.94, "F-13": 0.9}, rules={"R-TONE-02": lambda t: 0.95 if "silly" in t else 0.05}),
            FakeLLM(fix_for=lambda q, a: "a coffee we love"))
    ids = create(client, DEMO_TEXT)
    v = wait_done(client, ids["version_id"])
    fs = findings(client, ids["version_id"])
    by = {(f["rule_code"], f["fact_code"], f["claim_status"]) for f in fs}
    quotes = {f["rule_code"]: [x["quote"] for x in fs if x["rule_code"] == f["rule_code"]] for f in fs}
    assert "Steam Classic" in quotes["R-TERM-01"]  # old product name
    assert any(q.lower().startswith("cheap") or "guaranteed" in q for q in quotes["R-TERM-02"])  # banned word
    assert any("#1" in q for q in quotes["R-CLAIM-01"])  # #1 claim
    assert any(c == "outdated" and fc in ("F-12", "F-13") for _, fc, c in by)  # expired-fact claim
    assert quotes.get("R-CLAIM-04")  # promotion without an end date (tier0)
    assert "R-TONE-02" in quotes  # off-tone line (Jev)
    outdated = next(f for f in fs if f["claim_status"] == "outdated")
    assert outdated["fact"]["expired"] is True and outdated["fact"]["valid_until"]
    assert all(f["explanation"] for f in fs)
    assert any(f["fix_verified"] for f in fs)
    assert v["stats"]["rule_scan"] and v["stats"]["tones"]


def test_image_jev_failure_adds_info_finding(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    from pipeline_helpers import SEED

    install(monkeypatch, FakeJev(fail_images=True), FakeLLM())
    login(client, "creator")
    data = (SEED / "content" / "ad_offpalette.png").read_bytes()
    r = client.post("/api/items", data={"title": "img", "channel": "instagram", "confidential": "false", "caption": "Fresh roast."},
                    files={"file": ("ad.png", data, "image/png")})
    ids = r.json()
    v = wait_done(client, ids["version_id"])
    fs = findings(client, ids["version_id"])
    assert any("Jev image check unavailable" in f["explanation"] for f in fs)
    assert any(f["rule_code"] == "R-VIS-02" for f in fs)  # palette is code, still runs
    assert any(f["rule_code"] == "R-ACC-01" for f in fs)  # no alt text
    assert any("image check failed" in w for w in v["stats"]["warnings"])


def test_error_marks_version_and_item(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    async def boom(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("kaboom")

    monkeypatch.setattr(E, "analyze", boom)
    ids = create(client, "Plain text.")
    events = sse(client, ids["version_id"])
    assert events[-1][0] == "error" and "try running the check again" in events[-1][1]["message"]
    v = client.get(f"/api/versions/{ids['version_id']}").json()
    assert v["check_status"] == "error" and v["stats"]["error"] is True
    assert client.get(f"/api/items/{ids['item_id']}").json()["item"]["state"] == "findings_ready"


def test_check_endpoint_reruns_and_is_permissioned(client: TestClient) -> None:
    ids = create(client, "A cheap brew.")
    wait_done(client, ids["version_id"])
    login(client, "reviewer")
    assert client.post(f"/api/versions/{ids['version_id']}/check").status_code == 403
    login(client, "admin")
    r = client.post(f"/api/versions/{ids['version_id']}/check")
    assert r.status_code == 200 and r.json()["ok"]
    v = wait_done(client, ids["version_id"])
    assert v["check_status"] == "done" and len(findings(client, ids["version_id"])) == 1  # replaced, not duplicated


def test_seeded_versions_stay_not_run_without_keys(client: TestClient) -> None:
    with Session(get_engine()) as s:
        seeded = s.exec(select(Version).join(ContentItem).where(ContentItem.key == "demo_flawed_post")).first()
    if seeded is not None:
        assert seeded.check_status in ("not_run", "done")
    assert R.start_prerun() is False  # DEMO_MODE on but no key in tests


def test_start_check_from_a_thread_without_loop(client: TestClient) -> None:
    ids = create(client, "A cheap brew.")
    wait_done(client, ids["version_id"])
    import threading

    out = {}
    t = threading.Thread(target=lambda: out.setdefault("ok", R.start_check(ids["version_id"])))
    t.start()
    t.join()
    assert out["ok"] is True
    wait_done(client, ids["version_id"])


# ------------------------------------------------------------------ performance budget
TYPICAL_CAPTION = ("Cold Steam Brew is back, steeped for 18 hours and served over ice.\n\n"
                   "It is the best way to cool down, and it is cheap at just Rs 199!!!\n\n"
                   "Visit your nearest cafe this weekend.")


@pytest.mark.parametrize("text,units", [(TYPICAL_CAPTION, 3), (DEMO_TEXT, 4)])
def test_typical_caption_under_8_seconds(monkeypatch, text, units) -> None:  # noqa: ANN001
    """Jev ~0.5 s per call, LLM ~2 s per call (the task's assumption; measured live: 0.4-0.5 s / ~1.2 s)."""
    jf = FakeJev(sleep=0.5, support={"F-21": 0.9})
    lf = FakeLLM(sleep=2.0, fix_for=lambda q, a: "a cool")
    install(monkeypatch, jf, lf)
    ctx = seed_ctx(text)
    us = text_units(text)
    assert len(us) == units
    t0 = time.perf_counter()
    res = asyncio.run(E.analyze(us, ctx, calls=CallLog()))
    elapsed = time.perf_counter() - t0
    print(f"\n{units} units: {elapsed:.2f}s, {len(res.findings)} findings, {len(jf.calls)} Jev calls, {len(lf.calls)} LLM calls")
    assert elapsed < 8.0
