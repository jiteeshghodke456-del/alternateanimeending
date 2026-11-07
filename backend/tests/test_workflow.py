"""Workflow rules (BUILD.md P0-7/P0-8/P0-9), one test per rule. start_check is mocked."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import get_engine
from app.main import app
from app.models import AICall, ContentItem, Finding, Override, Unit, Version
from app.services import current_brand


SELF_APPROVE = "You can't approve your own content."


@pytest.fixture(autouse=True)
def mock_start_check(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr("app.pipeline.run.start_check", lambda vid: calls.append(vid))
    return calls


@pytest.fixture()
def who(client):
    """who("rohan") -> a TestClient that always acts as that user (bearer token, no cookie)."""
    cache: dict[str, TestClient] = {}

    def _who(uid: str) -> TestClient:
        if uid not in cache:
            r = client.post("/api/demo/login", json={"user_id": uid})
            assert r.status_code == 200, r.text
            c = TestClient(app, headers={"Authorization": f"Bearer {r.json()['token']}"})
            cache[uid] = c
        return cache[uid]

    return _who


def make_item(creator: str = "rohan", state: str = "findings_ready", text: str = "Hello world. Cheap coffee is the best!", confidential: bool = False,
              check_status: str = "done", channel: str = "instagram") -> tuple[int, int]:
    with Session(get_engine()) as s:
        item = ContentItem(brand_id=current_brand(s).id, title="t", channel=channel, creator_id=creator, state=state, confidential=confidential)
        s.add(item)
        s.flush()
        v = Version(item_id=item.id, n=1, text=text, check_status=check_status, stats={"elapsed_ms": 1000, "judgments": 4})
        s.add(v)
        s.flush()
        s.add(Unit(version_id=v.id, kind="text", idx=0, text=text, char_start=0, char_end=len(text)))
        s.commit()
        return item.id, v.id


def make_finding(version_id: int, start: int, end: int, *, severity: str = "minor", fix: str | None = "X", status: str = "open",
                 rule: str = "R-TEST-01", category: str = "tone", edited: str | None = None) -> int:
    with Session(get_engine()) as s:
        f = Finding(version_id=version_id, rule_code=rule, category=category, severity=severity, band="flag", span_start=start, span_end=end,
                    quote="q", fix=fix, status=status, edited_text=edited)
        s.add(f)
        s.commit()
        return f.id


def state_of(client, item_id: int) -> str:
    return client.get(f"/api/items/{item_id}").json()["item"]["state"]


def test_self_approval_blocked_with_exact_message(who):
    kabir = who("kabir")
    item = next(i for i in kabir.get("/api/items?mine=true").json() if i["state"] == "in_review")  # seeded: Kabir's own item
    r = kabir.post(f"/api/items/{item['id']}/approve")
    assert r.status_code == 403
    assert r.json()["detail"] == SELF_APPROVE
    assert kabir.get(f"/api/items/{item['id']}").json()["actions"]["approve"] == {"allowed": False, "reason": SELF_APPROVE}
    assert state_of(kabir, item["id"]) == "in_review"


def test_approve_by_other_approver_and_only_from_in_review(who):
    rohan, kabir, meera = who("rohan"), who("kabir"), who("meera")
    item_id, _ = make_item("rohan", state="in_review")
    assert meera.post(f"/api/items/{item_id}/approve").status_code == 403  # reviewers can't approve
    assert kabir.post(f"/api/items/{item_id}/approve").json()["item"]["state"] == "approved"
    again = kabir.post(f"/api/items/{item_id}/approve")
    assert again.status_code == 409
    draft_id, _ = make_item("rohan", state="findings_ready")
    assert kabir.post(f"/api/items/{draft_id}/approve").status_code == 409


def test_blocking_finding_blocks_submit(who):
    rohan = who("rohan")
    item_id, vid = make_item("rohan")
    fid = make_finding(vid, 0, 5, severity="blocking")
    r = rohan.post(f"/api/items/{item_id}/submit")
    assert r.status_code == 409 and "blocking issue" in r.json()["detail"]
    assert state_of(rohan, item_id) == "findings_ready"
    # dismissing it clears the block
    assert rohan.post(f"/api/findings/{fid}/dismiss", json={"reason": "false_alarm"}).status_code == 200
    ok = rohan.post(f"/api/items/{item_id}/submit")
    assert ok.status_code == 200 and ok.json()["item"]["state"] == "in_review"


def test_only_creator_can_submit(who):
    item_id, _ = make_item("rohan")
    r = who("meera").post(f"/api/items/{item_id}/submit")
    assert r.status_code == 403


def test_creator_cannot_override_and_reason_must_be_long_enough(who):
    rohan, meera = who("rohan"), who("meera")
    item_id, vid = make_item("rohan")
    fid = make_finding(vid, 0, 5, severity="blocking")
    r = rohan.post(f"/api/findings/{fid}/override", json={"reason": "I really need this one"})
    assert r.status_code == 403
    short = meera.post(f"/api/findings/{fid}/override", json={"reason": "too short"})
    assert short.status_code == 422 and "10 characters" in short.json()["detail"]
    # approver who authored the item can't override either
    k_item, k_v = make_item("kabir")
    k_f = make_finding(k_v, 0, 5, severity="blocking")
    assert who("kabir").post(f"/api/findings/{k_f}/override", json={"reason": "my own item, long reason"}).status_code == 403


def test_override_lets_submit_through_and_is_audited(who):
    rohan, meera = who("rohan"), who("meera")
    item_id, vid = make_item("rohan")
    fid = make_finding(vid, 0, 5, severity="blocking")
    assert rohan.post(f"/api/items/{item_id}/submit").status_code == 409
    r = meera.post(f"/api/findings/{fid}/override", json={"reason": "Approved by legal on 5 Nov"})
    assert r.status_code == 200 and r.json()["status"] == "overridden"
    ok = rohan.post(f"/api/items/{item_id}/submit")
    assert ok.status_code == 200 and ok.json()["item"]["state"] == "in_review"
    hist = rohan.get(f"/api/items/{item_id}/history").json()
    assert [e["action"] for e in hist] == ["submitted", "override"]  # newest first
    assert hist[0]["detail"]["from"] == "findings_ready" and hist[0]["detail"]["to"] == "in_review" and hist[0]["detail"]["version"] == 1
    assert hist[1]["user_name"] == "Meera"


def test_full_flow_request_changes_reject_and_comments(who):
    rohan, meera, kabir = who("rohan"), who("meera"), who("kabir")
    item_id, _ = make_item("rohan")
    assert rohan.post(f"/api/items/{item_id}/submit").status_code == 200
    assert who("rohan").post(f"/api/items/{item_id}/request-changes", json={"note": "x"}).status_code == 403
    assert meera.post(f"/api/items/{item_id}/request-changes", json={"note": ""}).status_code == 422
    r = meera.post(f"/api/items/{item_id}/request-changes", json={"note": "Fix the claim"})
    assert r.status_code == 200 and r.json()["item"]["state"] == "changes_requested"
    assert rohan.post(f"/api/items/{item_id}/comments", json={"body": "On it"}).status_code == 201
    bodies = [c["body"] for c in rohan.get(f"/api/items/{item_id}/comments").json()]
    assert bodies == ["Changes requested: Fix the claim", "On it"]
    other, _ = make_item("rohan", state="in_review")
    assert kabir.post(f"/api/items/{other}/reject", json={"note": "Off brand"}).json()["item"]["state"] == "rejected"


def test_queue_filters(who):
    meera = who("meera")
    item_id, _ = make_item("rohan", state="in_review")
    q = meera.get("/api/items?state=in_review").json()
    assert item_id in [i["id"] for i in q] and all(i["state"] == "in_review" for i in q)
    ts = [i["updated_at"] for i in q]
    assert ts == sorted(ts, reverse=True)
    assert all(i["creator_id"] == "rohan" for i in who("rohan").get("/api/items?mine=true").json())


def test_finding_actions_permissions_and_audit(who):
    rohan, meera = who("rohan"), who("meera")
    item_id, vid = make_item("rohan")
    other_item, other_v = make_item("kabir")
    fa = make_finding(vid, 0, 5)
    fo = make_finding(other_v, 0, 5)
    assert rohan.post(f"/api/findings/{fo}/accept").status_code == 403  # not his item
    assert rohan.post(f"/api/findings/{fa}/dismiss", json={"reason": "other"}).status_code == 422  # note required
    d = rohan.post(f"/api/findings/{fa}/dismiss", json={"reason": "other", "note": "Campaign exception"}).json()
    assert d["status"] == "dismissed" and d["dismiss_reason"] == "other"
    assert meera.post(f"/api/findings/{fa}/edit", json={"text": "Hi"}).json()["edited_text"] == "Hi"  # reviewers may act
    assert rohan.post(f"/api/findings/{fa}/accept").json()["status"] == "accepted"
    actions = [e["action"] for e in rohan.get(f"/api/items/{item_id}/history").json()]
    assert actions[:3] == ["finding_accepted", "finding_edited", "finding_dismissed"]


def test_resubmit_applies_fixes_and_edits_end_to_start(who, mock_start_check):
    rohan = who("rohan")
    text = "Steam Classic is the #1 cheap coffee. Order now!"
    item_id, vid = make_item("rohan", text=text)
    a, b = text.index("#1"), text.index("cheap")
    make_finding(vid, a, a + 2, fix="loved", status="accepted")
    make_finding(vid, b, b + 5, fix="x", status="edited", edited="fresh")
    make_finding(vid, 0, 5, fix="Nope", status="open")  # open: not applied
    out = rohan.post(f"/api/items/{item_id}/resubmit")
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["n"] == 2 and len(body["applied"]) == 2 and body["skipped"] == []
    assert mock_start_check == [body["version_id"]]
    new = rohan.get(f"/api/versions/{body['version_id']}").json()
    assert new["text"] == "Steam Classic is the loved fresh coffee. Order now!"
    assert "".join(s["text"] for s in body["diff"] if s["op"] != "insert") == text
    assert "".join(s["text"] for s in body["diff"] if s["op"] != "delete") == new["text"]
    assert {s["text"] for s in body["diff"] if s["op"] == "insert"} == {"loved", "fresh"}
    assert state_of(rohan, item_id) == "checking"
    assert rohan.post(f"/api/items/{item_id}/resubmit").status_code == 409  # still checking


def test_resubmit_skips_overlapping_later_span(who):
    from app.workflow import apply_findings

    text = "0123456789"
    f1 = Finding(id=1, version_id=1, span_start=2, span_end=6, fix="AA", status="accepted")
    f2 = Finding(id=2, version_id=1, span_start=5, span_end=8, fix="BB", status="accepted")  # overlaps f1, later
    f3 = Finding(id=3, version_id=1, span_start=8, span_end=10, fix="CC", status="accepted")  # touches f2 but f2 skipped
    f4 = Finding(id=4, version_id=1, span_start=6, span_end=6, edited_text="-", status="edited")  # insertion at f1's end
    res = apply_findings(text, [f3, f2, f1, f4])
    assert res.text == "01AA-67CC"
    assert res.applied == [1, 4, 3]
    assert [s["finding_id"] for s in res.skipped] == [2]
    # out-of-range span is skipped, not crashed
    bad = Finding(id=9, version_id=1, span_start=5, span_end=99, fix="Z", status="accepted")
    assert apply_findings(text, [bad]).skipped[0]["finding_id"] == 9


def test_insights_numbers_on_small_fixture(who, client):
    asha = who("asha")
    with Session(get_engine()) as s:  # start from an empty slate
        for m in (Override, Finding, AICall):
            for row in s.exec(select(m)).all():
                s.delete(row)
        for v in s.exec(select(Version)).all():
            v.check_status, v.stats = "not_run", {}
            s.add(v)
        s.commit()
    i1, v1 = make_item("rohan", channel="instagram")  # elapsed 1000, judgments 4
    i2, v2 = make_item("rohan", channel="email")
    with Session(get_engine()) as s:
        v = s.get(Version, v2)
        v.stats = {"elapsed_ms": 3000, "judgments": 6}
        s.add(v)
        s.add(AICall(version_id=v1, provider="jev", model="m", purpose="rules", input_tokens=100, cost_usd=0.25))
        s.add(AICall(version_id=v2, provider="llm", model="m", purpose="explain", input_tokens=50, cost_usd=0.5))
        s.commit()
    # R-A on instagram: 4 findings, 2 dismissed (rate .5, count>=3 => unclear); 3 have a fix, 1 of those accepted
    make_finding(v1, 0, 1, rule="R-A", category="tone", fix="f", status="accepted")
    make_finding(v1, 0, 1, rule="R-A", category="tone", fix="f", status="dismissed")
    make_finding(v1, 0, 1, rule="R-A", category="tone", fix="f", status="open")
    make_finding(v1, 0, 1, rule="R-A", category="tone", fix=None, status="dismissed")
    # R-B on email: 2 findings, both dismissed (rate 1.0 but count < 3 => not flagged)
    make_finding(v2, 0, 1, rule="R-B", category="claims", fix=None, status="dismissed")
    make_finding(v2, 0, 1, rule="R-B", category="claims", fix=None, status="dismissed")
    r = asha.get("/api/insights").json()
    assert r["kpis"] == {"items_checked": 2, "findings": 6, "fix_accept_rate": 0.333, "avg_check_ms": 2000,
                         "jev_judgments": 10, "total_cost_usd": 0.75}
    assert {b["key"]: b["count"] for b in r["by_category"]} == {"tone": 4, "claims": 2}
    assert [(b["key"], b["count"]) for b in r["top_rules"]] == [("R-A", 4), ("R-B", 2)]
    assert {b["key"]: b["count"] for b in r["by_channel"]} == {"instagram": 4, "email": 2}
    rates = {d["rule_code"]: d for d in r["dismissal_rate"]}
    assert rates["R-A"]["rate"] == 0.5 and rates["R-A"]["possibly_unclear"] is True
    assert rates["R-B"]["rate"] == 1.0 and rates["R-B"]["possibly_unclear"] is False
    client.post("/api/demo/login", json={"user_id": "asha"})
    assert client.post("/api/demo/reset").status_code == 200  # restore seeded state for later tests


def test_ai_usage_confidential_item(who):
    rohan = who("rohan")
    item_id, vid = make_item("rohan", confidential=True)
    out = rohan.get(f"/api/items/{item_id}/ai-usage").json()
    assert out["confidential"] is True and out["no_external_services"] is True
    assert out["external_calls"] == 0 and out["groups"] == [] and out["total_cost_usd"] == 0
    assert out["summary"].startswith("No external services used")


def test_ai_usage_groups_by_provider_and_purpose_with_pii(who):
    rohan = who("rohan")
    item_id, vid = make_item("rohan")
    with Session(get_engine()) as s:
        v = s.get(Version, vid)
        v.stats = {**v.stats, "pii_masked": 2}
        s.add(v)
        for _ in range(3):
            s.add(AICall(version_id=vid, provider="jev", model="jev-latest", purpose="rules", input_tokens=10, cost_usd=0.001))
        s.add(AICall(version_id=vid, provider="llm", model="gpt-x", purpose="explain", input_tokens=5, cost_usd=0.002))
        s.commit()
    out = rohan.get(f"/api/items/{item_id}/ai-usage").json()
    g = {(x["provider"], x["purpose"]): x for x in out["groups"]}
    assert g[("jev", "rules")]["count"] == 3 and g[("jev", "rules")]["input_tokens"] == 30
    assert out["no_external_services"] is False and out["pii_masked"] == 2 and out["total_cost_usd"] == 0.005
    assert "Personal data masked: 2" in out["summary"]


def test_seeded_states_have_consistent_history(who):
    assert who("asha").post("/api/demo/reset").status_code == 200  # only seeded items
    rohan = who("rohan")
    for it in rohan.get("/api/items").json():
        hist = rohan.get(f"/api/items/{it['id']}/history").json()
        actions = [e["action"] for e in hist]
        assert actions[-1] == "created"
        if it["state"] in ("in_review", "changes_requested", "approved"):
            assert "submitted" in actions
        if it["state"] == "approved":
            assert hist[0]["action"] == "approved" and hist[0]["user_name"] == "Kabir"
