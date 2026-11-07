"""Claims ledger + fact impact (Round 3, improvement 1): ledger rows written by checks (supported too),
contradicted refinement, GET /claims, used_by, expiry, impact, send-back (admin only, audit, state
machine), re-check replaces rows, and workspace isolation. Jev/LLM are fakes (or off)."""
from __future__ import annotations

import asyncio
import time
from contextlib import ExitStack
from datetime import date, timedelta
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import ledger as L
from app.db import get_engine
from app.models import AuditEvent, ClaimRecord, Comment, ContentItem, Fact
from app.pipeline import claims as C
from conftest import login
from fakes import FakeJev, install
from pipeline_helpers import seed_ctx


def create(client: TestClient, text: str, title: str = "ledger test", channel: str = "website") -> dict:
    login(client, "creator")
    r = client.post("/api/items", data={"title": title, "channel": channel, "text": text})
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


def records(version_id: int) -> list[ClaimRecord]:
    with Session(get_engine()) as s:
        return list(s.exec(select(ClaimRecord).where(ClaimRecord.version_id == version_id)).all())


def fact_id(code: str) -> int:
    with Session(get_engine()) as s:
        return s.exec(select(Fact).where(Fact.code == code)).one().id


def set_state(item_id: int, state: str) -> None:
    with Session(get_engine()) as s:
        it = s.get(ContentItem, item_id)
        it.state = state
        s.add(it)
        s.commit()


@pytest.fixture()
def restore_facts() -> Iterator[None]:
    with Session(get_engine()) as s:
        saved = {f.id: (f.statement, f.value, f.valid_until) for f in s.exec(select(Fact)).all()}
    yield
    with Session(get_engine()) as s:
        for f in s.exec(select(Fact)).all():
            if f.id in saved:
                f.statement, f.value, f.valid_until = saved[f.id]
                s.add(f)
        s.commit()


# ------------------------------------------------------------------ pure helpers
def test_contradicted_is_a_code_refinement_of_unsupported(monkeypatch) -> None:  # noqa: ANN001
    install(monkeypatch, FakeJev())  # no fact supports anything
    ctx = seed_ctx()
    [cl] = C.extract_claims("Free shipping on online orders above Rs 599.", ctx)
    cr = asyncio.run(C.check_claim(cl, ctx, use_jev=True))
    assert cr.status == "unsupported"  # the finding keeps its status
    assert C.ledger_status(cr, ctx) == "contradicted" and C.relied_facts(cr, ctx) == ["F-17"]  # F-14 (Rs 499) is expired


def test_unsupported_without_a_matching_fact_stays_unsupported(monkeypatch) -> None:  # noqa: ANN001
    install(monkeypatch, FakeJev())
    ctx = seed_ctx()
    rs = [asyncio.run(C.check_claim(cl, ctx, use_jev=True)) for cl in C.extract_claims("India's #1 coffee.", ctx)]
    assert [C.ledger_status(r, ctx) for r in rs] == ["unsupported"] and C.relied_facts(rs[0], ctx) == []


def test_claim_backed_by_two_facts_relies_on_both(monkeypatch) -> None:  # noqa: ANN001
    install(monkeypatch, FakeJev(support={"F-04": 0.53, "F-18": 0.45}))
    ctx = seed_ctx()
    [cl] = C.extract_claims("We have 12 cafes in Pune, Mumbai and Bengaluru, open from 8 am to 10 pm every day.", ctx)
    cr = asyncio.run(C.check_claim(cl, ctx, use_jev=True))
    assert cr.status == "supported" and set(C.relied_facts(cr, ctx)) == {"F-04", "F-18"}


def test_partly_supported_jev_claim_relies_on_its_best_current_fact(monkeypatch) -> None:  # noqa: ANN001
    install(monkeypatch, FakeJev(support={"F-03": 0.55, "F-02": 0.35}))
    ctx = seed_ctx()
    cr = asyncio.run(C.check_claim(C.unit_claim("Freshly roasted in Pune.", ctx, "objective"), ctx, use_jev=True))
    assert cr.status == "supported" and cr.fact_code is None and C.relied_facts(cr, ctx) == ["F-03"]


def test_supported_by_one_fact_also_relies_on_the_fact_holding_its_other_numbers(monkeypatch) -> None:  # noqa: ANN001
    install(monkeypatch, FakeJev(support={"F-18": 0.74, "F-04": 0.2}))
    ctx = seed_ctx()
    [cl] = C.extract_claims("We run 12 cafes across Pune, Mumbai and Bengaluru, open from 8 am to 10 pm every day.", ctx)
    cr = asyncio.run(C.check_claim(cl, ctx, use_jev=True))
    assert cr.status == "supported" and cr.fact_code == "F-18" and C.relied_facts(cr, ctx) == ["F-18", "F-04"]


def test_number_from_an_expired_fact_makes_a_supported_claim_outdated(monkeypatch) -> None:  # noqa: ANN001
    from dataclasses import replace

    install(monkeypatch, FakeJev(support={"F-18": 0.74, "F-04": 0.2}))
    ctx = seed_ctx()
    ctx.facts = [replace(f, valid_until=date(2020, 1, 1)) if f.code == "F-04" else f for f in ctx.facts]
    [cl] = C.extract_claims("We run 12 cafes across Pune, Mumbai and Bengaluru, open from 8 am to 10 pm every day.", ctx)
    cr = asyncio.run(C.check_claim(cl, ctx, use_jev=True))
    assert (cr.status, cr.fact_code, cr.band, cr.via) == ("outdated", "F-04", "human", "numbers") and cr.is_finding


def test_expiry_helpers() -> None:
    t = date(2026, 11, 7)
    assert L.days_left(date(2026, 11, 15), t) == 8 and L.days_left(None, t) is None
    assert L.is_expired(date(2026, 11, 6), t) and not L.is_expired(date(2026, 11, 7), t)


# ------------------------------------------------------------------ ledger written by checks
def test_check_writes_ledger_rows_including_supported(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    install(monkeypatch, FakeJev(support={"F-04": 0.95, "F-12": 0.94}))
    text = ("We run 12 cafes across Pune, Mumbai and Bengaluru.\n\nUse code MONSOON20 for flat 20% off online orders.\n\n"
            "India's #1 coffee.")
    ids = create(client, text, "ledger mix")
    v = wait_done(client, ids["version_id"])
    recs = records(ids["version_id"])
    by = {r.status: r for r in recs}
    assert {"supported", "outdated", "unsupported"} <= set(by)
    assert by["supported"].fact_code == "F-04" and by["supported"].support_p == 0.95
    assert by["outdated"].fact_code == "F-12" and by["outdated"].fact_valid_until == date(2026, 9, 30)
    assert by["unsupported"].fact_code is None and by["unsupported"].fact_codes == []
    for r in recs:  # spans point into the version text
        assert r.item_id == ids["item_id"] and v["text"][r.span_start:r.span_end] and r.text

    login(client, "reviewer")  # every role can read the ledger
    led = client.get("/api/claims").json()
    groups = {(g["fact"] or {}).get("code"): g for g in led["groups"]}
    assert ids["item_id"] in {c["item_id"] for c in groups["F-04"]["claims"]}
    assert ids["item_id"] in {c["item_id"] for c in groups[None]["claims"]}
    assert groups["F-12"]["fact"]["expired"] is True and groups["F-12"]["fact"]["days_left"] < 0
    assert list(groups)[-1] is None  # "Unsupported claims" last
    only = client.get("/api/claims", params={"status": "outdated"}).json()
    assert only["groups"] and all(c["status"] == "outdated" for g in only["groups"] for c in g["claims"])
    assert client.get("/api/claims", params={"status": "nope"}).status_code == 422
    assert client.get("/api/claims", params={"channel": "email", "state": "approved"}).status_code == 200

    # re-running the check replaces the version's rows (no duplicates)
    login(client, "admin")
    assert client.post(f"/api/versions/{ids['version_id']}/check").status_code == 200
    wait_done(client, ids["version_id"])
    time.sleep(0.1)
    assert len(records(ids["version_id"])) == len(recs)


def test_facts_list_has_used_by_counts(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    install(monkeypatch, FakeJev(support={"F-19": 0.96}))
    ids = create(client, "Every barista completes 40 hours of training before serving guests.", "used by")
    wait_done(client, ids["version_id"])
    set_state(ids["item_id"], "approved")
    f19 = next(f for f in client.get("/api/facts").json() if f["code"] == "F-19")
    assert f19["used_by"] >= 1 and f19["used_by_approved"] >= 1


# ------------------------------------------------------------------ impact + send-back
def test_expire_fact_impact_and_send_back(client: TestClient, monkeypatch, restore_facts) -> None:  # noqa: ANN001
    install(monkeypatch, FakeJev(support={"F-21": 0.97}))
    approved = create(client, "Cold Steam Brew is steeped for 18 hours, then served cold.", "impact approved")
    draft = create(client, "Our Cold Steam Brew is steeped for 18 hours.", "impact draft")
    wait_done(client, approved["version_id"])
    wait_done(client, draft["version_id"])
    set_state(approved["item_id"], "approved")
    fid = fact_id("F-21")

    login(client, "admin")
    r = client.patch(f"/api/facts/{fid}", json={"valid_until": (date.today() - timedelta(days=1)).isoformat()})
    assert r.status_code == 200 and r.json()["expired"] and r.json()["used_by"] >= 2
    imp = client.get(f"/api/facts/{fid}/impact").json()
    assert imp["reason"] == "expired" and imp["approved"] >= 1
    rows = {i["item_id"]: i for i in imp["items"]}
    assert rows[approved["item_id"]]["action"] == "changes_requested" and rows[draft["item_id"]]["action"] == "recheck"
    assert all(c["stale"] for c in rows[approved["item_id"]]["claims"])  # supported by a fact that has expired since
    assert imp["items"][0]["state"] == "approved"  # approved items first

    for role in ("creator", "reviewer", "approver"):  # only a Brand Admin can send content back
        login(client, role)
        assert client.post(f"/api/facts/{fid}/impact/send-back", json={"recheck": False}).status_code == 403

    login(client, "admin")
    out = client.post(f"/api/facts/{fid}/impact/send-back", json={"recheck": True}).json()
    res = {x["item_id"]: x for x in out["results"]}
    assert res[approved["item_id"]]["from_state"] == "approved" and res[approved["item_id"]]["to_state"] == "changes_requested"
    assert out["moved"] >= 1 and out["reason"] == "expired"
    assert client.get(f"/api/items/{approved['item_id']}").json()["item"]["state"] == "changes_requested"
    with Session(get_engine()) as s:
        ev = s.exec(select(AuditEvent).where(AuditEvent.item_id == approved["item_id"], AuditEvent.action == "fact_expired")).one()
        assert ev.detail["fact"] == "F-21" and ev.detail["from"] == "approved" and ev.user_id == "asha"
        assert s.exec(select(Comment).where(Comment.item_id == approved["item_id"])).first() is not None
    # the re-check ran against the expired fact: the claim is now outdated (ledger + finding)
    wait_done(client, approved["version_id"])
    wait_done(client, draft["version_id"])
    time.sleep(0.1)
    assert [r.status for r in records(approved["version_id"])] == ["outdated"]
    assert client.get(f"/api/items/{draft['item_id']}").json()["item"]["state"] == "findings_ready"
    fs = client.get(f"/api/versions/{approved['version_id']}/findings").json()
    assert any(f["claim_status"] == "outdated" and f["fact_code"] == "F-21" for f in fs)
    # history shows the event in plain words
    hist = client.get(f"/api/items/{approved['item_id']}/history").json()
    assert any(e["action"] == "fact_expired" for e in hist)


def test_edited_fact_is_reported_as_changed(client: TestClient, monkeypatch, restore_facts) -> None:  # noqa: ANN001
    install(monkeypatch, FakeJev(support={"F-02": 0.95}))
    ids = create(client, "We roast in small batches of 30 kg.", "changed fact")
    wait_done(client, ids["version_id"])
    time.sleep(0.05)
    fid = fact_id("F-02")
    login(client, "admin")
    assert client.patch(f"/api/facts/{fid}", json={"value": "25 kg", "statement": "We roast in small batches of 25 kg"}).status_code == 200
    imp = client.get(f"/api/facts/{fid}/impact").json()
    assert imp["reason"] == "changed" and imp["changed_at"]
    row = next(i for i in imp["items"] if i["item_id"] == ids["item_id"])
    assert all(c["stale"] for c in row["claims"])
    out = client.post(f"/api/facts/{fid}/impact/send-back", json={"recheck": False}).json()
    r = next(x for x in out["results"] if x["item_id"] == ids["item_id"])
    assert r["to_state"] == r["from_state"] == "findings_ready" and not r["rechecked"]
    with Session(get_engine()) as s:
        assert s.exec(select(AuditEvent).where(AuditEvent.item_id == ids["item_id"], AuditEvent.action == "fact_changed")).first()


def test_expiring_soon_lists_facts_with_affected_items(client: TestClient, monkeypatch, restore_facts) -> None:  # noqa: ANN001
    install(monkeypatch, FakeJev(support={"F-07": 0.96}))
    ids = create(client, "Saffron Signature Roast won Silver at the Western Ghats Roasters Showcase 2026.", "expiring")
    wait_done(client, ids["version_id"])
    login(client, "admin")
    fid = fact_id("F-07")
    client.patch(f"/api/facts/{fid}", json={"valid_until": (date.today() + timedelta(days=5)).isoformat()})
    out = client.get("/api/claims/expiring").json()
    row = next(x for x in out["expiring"] if x["fact"]["code"] == "F-07")
    assert row["fact"]["days_left"] == 5 and row["items_approved"] + row["items_in_progress"] >= 1
    assert client.get(f"/api/facts/{fid}/impact").json()["reason"] == "expiring"
    assert client.get("/api/claims/expiring", params={"days": 2}).json()["expiring"] == [] or all(
        x["fact"]["days_left"] <= 2 for x in client.get("/api/claims/expiring", params={"days": 2}).json()["expiring"])


def test_unknown_fact_impact_is_404(client: TestClient) -> None:
    login(client, "admin")
    assert client.get("/api/facts/999999/impact").status_code == 404
    assert client.post("/api/facts/999999/impact/send-back", json={}).status_code == 404


# ------------------------------------------------------------------ workspaces
def test_ledger_is_per_workspace(tmp_path_factory: pytest.TempPathFactory) -> None:
    from app import workspaces as W
    from app.config import get_settings
    from app.main import app
    from app.pipeline import run

    s = get_settings()
    saved = (s.workspaces_enabled, s.workspaces_dir)
    saved_loop, saved_prerun = run._main_loop, run._prerun_started
    s.workspaces_enabled, s.workspaces_dir = True, str(tmp_path_factory.mktemp("ws-ledger"))
    W.clear_for_tests()
    try:
        with ExitStack() as st:
            a, b = (st.enter_context(TestClient(app)) for _ in range(2))
            for c in (a, b):
                login(c, "creator")
            r = a.post("/api/items", data={"title": "A", "channel": "website",
                                            "text": "Use code MONSOON20 for flat 20% off online orders till 30 September."})
            vid = r.json()["version_id"]
            wait_done(a, vid)
            time.sleep(0.1)
            ga = a.get("/api/claims").json()
            gb = b.get("/api/claims").json()
            assert any(g["fact"] and g["fact"]["code"] == "F-12" for g in ga["groups"])  # numbers match, no Jev
            assert gb["groups"] == [] and gb["items"] == 0
    finally:
        W.clear_for_tests()
        s.workspaces_enabled, s.workspaces_dir = saved
        run._main_loop, run._prerun_started = saved_loop, saved_prerun


def test_failed_recheck_keeps_the_previous_ledger_rows(client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    from app.pipeline import engine as E
    from app.pipeline import run as R

    install(monkeypatch, FakeJev(support={"F-05": 0.95}))
    ids = create(client, "Saffron & Steam was founded in Pune in 2019.", "keeps rows")
    wait_done(client, ids["version_id"])
    before = [(r.status, r.fact_code) for r in records(ids["version_id"])]
    assert before

    async def boom(*a, **kw):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("boom")

    monkeypatch.setattr(E, "analyze", boom)
    asyncio.run(R.run_check(ids["version_id"]))
    assert [(r.status, r.fact_code) for r in records(ids["version_id"])] == before
