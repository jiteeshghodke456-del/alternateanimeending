"""Precedent memory: matching thresholds, band truth table (blocking floor), exclusions, API flows
(dismiss -> similar post shows "Seen before" and a lower band -> revoke -> gone), permissions, the seeded
demo precedent, and the rule-clarity people signal (each decision counted once). Jev and the LLM are off."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import get_engine
from app.models import ContentItem, Finding, Precedent, Rule, Version
from app.pipeline import clarity as C
from app.pipeline import precedent as P
from app.pipeline import run as R
from conftest import login

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def spec(**kw) -> P.PrecedentSpec:  # noqa: ANN003
    base = dict(id=1, rule_code="R-X", rule_version=1, quote="Free shipping",
                context="Free shipping on online orders above Rs 599.", decision="dismissed",
                reason="approved_exception", note="standing policy", by="Meera", at=NOW - timedelta(days=1),
                item_id=10, version_id=100)
    base.update(kw)
    return P.PrecedentSpec(**base)


# ------------------------------------------------------------------ text + matching
def test_normalise_keeps_indic_marks_percent_and_rupee() -> None:
    assert P.normalise("  Flat 20% OFF!!! — ₹599 ") == "flat 20% off ₹599"
    assert P.normalise("मुफ़्त डिलीवरी!") == P.normalise("मुफ़्त  डिलीवरी")
    assert "ि" in P.normalise("डिलीवरी")  # vowel signs survive (they aren't punctuation)


def test_context_window_is_the_sentence() -> None:
    t = "Hello there. Free shipping on orders above Rs 599. Order now!"
    s = t.index("Free")
    assert P.context_window(t, s, s + 13) == "Free shipping on orders above Rs 599."
    assert P.context_window(t, None, None) == ""
    long = "word " * 200
    assert len(P.context_window(long, 500, 504)) <= P.MAX_CONTEXT_CHARS


@pytest.mark.parametrize("prec,new,ok", [
    ("free shipping on online orders above rs 599", "free shipping on online orders above rs 599", True),
    ("free shipping on online orders above rs 599", "free shipping on all online orders above rs 599 this week", True),
    # token_set_ratio says 100 for a subset; the coverage guard stops a broader sentence inheriting it
    ("free shipping on online orders above rs 599 for members", "free shipping on orders", False),
    ("free shipping on online orders above rs 599", "free shipping on all orders today only", False),
    ("only today", "only today hurry", True),  # short: phrase containment
    ("only today", "today only", False),
    ("last chance", "lastchance offer", False),
])
def test_text_match_thresholds(prec: str, new: str, ok: bool) -> None:
    assert P.text_match(prec, new) is ok


def test_fuzz_threshold_is_90() -> None:
    a = "steam classic is a warm and friendly roast for everyone"
    assert P.text_match(a, "steam classic is a warm and friendly roast for everyone today")
    assert not P.text_match(a, "steam classic is a bold and smoky roast for night owls")


def test_find_match_needs_rule_words_and_sentence() -> None:
    ps = [spec()]
    ctx = "Free shipping on all online orders above Rs 599 this week."
    assert P.find_match("R-X", 1, "Free shipping", ctx, ps, now=NOW) is not None
    assert P.find_match("R-Y", 1, "Free shipping", ctx, ps, now=NOW) is None  # other rule
    assert P.find_match("R-X", 1, "Flat 20% off", "Flat 20% off with code MONSOON20.", ps, now=NOW) is None
    assert P.find_match("R-X", 1, "Free shipping", "Free shipping today only, hurry.", ps, now=NOW) is None
    assert P.find_match(None, 1, "Free shipping", ctx, ps, now=NOW) is None


def test_find_match_prefers_the_newest() -> None:
    old, new = spec(id=1, at=NOW - timedelta(days=9)), spec(id=2, at=NOW - timedelta(days=2))
    assert P.find_match("R-X", 1, "Free shipping", "Free shipping on online orders above Rs 599.", [old, new], now=NOW).id == 2


# ------------------------------------------------------------------ exclusions
def test_exclusions() -> None:
    assert P.eligible(spec(), 1, now=NOW)
    assert not P.eligible(spec(active=False), 1, now=NOW)  # revoked
    assert not P.eligible(spec(at=NOW - timedelta(days=181)), 1, now=NOW, max_age_days=180)  # too old
    assert P.eligible(spec(at=NOW - timedelta(days=181)), 1, now=NOW, max_age_days=0)  # 0 = no limit
    assert not P.eligible(spec(reason="rule_outdated"), 2, now=NOW)  # outdated + rule rewritten since
    assert P.eligible(spec(reason="rule_outdated"), 1, now=NOW)  # outdated, rule not yet rewritten
    assert P.eligible(spec(reason="false_alarm"), 2, now=NOW)  # other reasons survive a rewrite (card says so)
    assert not P.eligible(spec(), 1, now=NOW, version_id=100)  # never cites its own version


# ------------------------------------------------------------------ band truth table
@pytest.mark.parametrize("band,severity,out", [
    ("flag", "minor", "human"), ("flag", "major", "human"), ("flag", "blocking", "human"),
    ("human", "minor", "pass"), ("human", "major", "pass"), ("human", "blocking", "human"),
    ("pass", "major", "pass"), ("pass", "blocking", "pass"),
])
def test_lower_band_truth_table(band: str, severity: str, out: str) -> None:
    assert P.lower_band(band, severity) == out


def test_band_notes() -> None:
    assert "Previously accepted" in P.band_note("human", "pass", "major", "dismissed")
    assert "a person must still look" in P.band_note("human", "human", "blocking", "overridden")
    assert "Lowered from" in P.band_note("flag", "human", "major", "dismissed")


# ------------------------------------------------------------------ clarity: counted once
def test_people_rows_counts_each_decision_once() -> None:
    findings = [(1, "dismissed", "false_alarm", "flag", None), (2, "open", None, "human", "flag")]
    precedents = [(1, "dismissed", "false_alarm", "flag"),  # same decision as finding 1: not again
                  (99, "dismissed", "other", "flag"),  # its finding was replaced by a re-check: counts
                  (None, "overridden", "override", "flag")]
    rows = C.people_rows(findings, precedents)
    assert len(rows) == 4
    assert rows[1] == ("open", None, "flag")  # lowered by a precedent: counted with its original band
    p = C.people_signal(rows)
    assert (p.dismissed, p.dismissed_unclear, p.overridden, p.human) == (2, 2, 1, 0)


# ------------------------------------------------------------------ API
def wait_done(client: TestClient, vid: int, timeout: float = 20) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if client.get(f"/api/versions/{vid}").json()["check_status"] in ("done", "error"):
            return
        time.sleep(0.05)
    raise AssertionError("check did not finish")


def post(client: TestClient, text: str, confidential: bool = False) -> dict:
    login(client, "creator")
    r = client.post("/api/items", data={"title": "precedent test", "channel": "instagram", "text": text,
                                        "confidential": str(confidential).lower()})
    assert r.status_code == 201, r.text
    item = r.json()
    wait_done(client, item["version_id"])
    return item


def findings(client: TestClient, vid: int, rule: str) -> list[dict]:
    return [f for f in client.get(f"/api/versions/{vid}/findings").json() if f["rule_code"] == rule]


def test_seeded_precedent_shows_on_a_similar_post(client: TestClient) -> None:
    item = post(client, "Free shipping on online orders above Rs 599. Order today at our nearest cafe.")
    [f] = findings(client, item["version_id"], "R-CLAIM-04")
    assert f["precedent"] is not None and f["precedent"]["by"] == "Meera"
    assert f["precedent"]["decision"] == "dismissed" and f["precedent"]["reason"] == "approved_exception"
    assert f["band_before"] == "flag" and f["band"] == "human" and "Lowered" in f["band_note"]
    assert f["status"] == "open" and f["severity"] == "major"  # never auto-dismissed, severity untouched
    assert f["precedent"]["rule_changed"] is False


def test_demo_post_has_no_precedent(client: TestClient) -> None:
    with Session(get_engine()) as s:
        item = s.exec(select(ContentItem).where(ContentItem.key == "demo_flawed_post")).first()
        v = s.exec(select(Version).where(Version.item_id == item.id)).first()
        vid = v.id
    asyncio.run(R.run_check(vid))
    with Session(get_engine()) as s:
        rows = s.exec(select(Finding).where(Finding.version_id == vid)).all()
        assert rows and all(f.precedent_id is None for f in rows)


def test_seeded_dismissal_links_on_first_check(client: TestClient) -> None:
    with Session(get_engine()) as s:
        p = s.exec(select(Precedent).where(Precedent.seeded == True)).first()  # noqa: E712
        assert p is not None and p.finding_id is None
        vid = p.version_id
    asyncio.run(R.run_check(vid))
    with Session(get_engine()) as s:
        p = s.exec(select(Precedent).where(Precedent.seeded == True)).first()  # noqa: E712
        f = s.get(Finding, p.finding_id)
        assert f.status == "dismissed" and f.dismiss_reason == "approved_exception" and f.precedent_id is None
        fid = f.id
    asyncio.run(R.run_check(vid))  # a re-check leaves the new finding open (no repeat auto-dismissal)
    with Session(get_engine()) as s:
        assert all(f.status == "open" for f in s.exec(select(Finding).where(Finding.version_id == vid)).all())
        p = s.exec(select(Precedent).where(Precedent.seeded == True)).first()  # noqa: E712
        assert p.seed_linked and p.finding_id is None and fid  # forgets the replaced finding (ids get reused)


def test_dismiss_teaches_then_revoke_forgets(client: TestClient) -> None:
    text = "Our cold brew is cheap and cheerful for the whole family. Find your nearest cafe."
    a = post(client, text)
    [f] = findings(client, a["version_id"], "R-TERM-02")
    assert f["band"] == "flag" and f["precedent"] is None
    login(client, "reviewer")
    r = client.post(f"/api/findings/{f['id']}/dismiss", json={"reason": "false_alarm", "note": "cheerful, not low quality"})
    assert r.status_code == 200, r.text
    ps = client.get("/api/precedents").json()["precedents"]
    mine = next(p for p in ps if p["rule_code"] == "R-TERM-02" and p["item_id"] == a["item_id"])
    assert mine["quote"].lower() == "cheap" and mine["by"] == "Meera" and mine["applies"] and mine["uses"] == 0
    assert "cheerful" in mine["context"]

    b = post(client, "Our cold brew is cheap and cheerful for the whole family! Visit today.")
    [g] = findings(client, b["version_id"], "R-TERM-02")
    assert g["precedent"]["id"] == mine["id"] and g["precedent"]["note"] == "cheerful, not low quality"
    assert g["band"] == "human" and g["precedent"]["item_id"] == a["item_id"]
    other = post(client, "Cheap beans make sad coffee, so we never buy them.")
    assert all(x["precedent"] is None for x in findings(client, other["version_id"], "R-TERM-02"))
    login(client, "admin")
    assert next(p for p in client.get("/api/precedents").json()["precedents"] if p["id"] == mine["id"])["uses"] == 1

    login(client, "reviewer")
    assert client.post(f"/api/precedents/{mine['id']}/revoke").status_code == 403
    login(client, "admin")
    r = client.post(f"/api/precedents/{mine['id']}/revoke")
    assert r.status_code == 200 and r.json()["active"] is False and r.json()["applies"] is False
    c = post(client, "Our cold brew is cheap and cheerful for the whole family! Visit today.")
    [h] = findings(client, c["version_id"], "R-TERM-02")
    assert h["precedent"] is None and h["band"] == "flag"


def test_human_band_becomes_previously_accepted_but_stays_listed(client: TestClient) -> None:
    text = "Our new gift tin is Rs 799 incl. taxes. Find your nearest cafe."
    a = post(client, text)
    hs = findings(client, a["version_id"], "R-LEGAL-03")
    if not hs or hs[0]["band"] != "human":
        pytest.skip("claim check didn't produce a human-band finding for this text")
    login(client, "reviewer")
    assert client.post(f"/api/findings/{hs[0]['id']}/dismiss", json={"reason": "approved_exception", "note": "new price, fact pending"}).status_code == 200
    b = post(client, text)
    [g] = findings(client, b["version_id"], "R-LEGAL-03")  # still listed without include_pass
    assert g["band"] == "pass" and g["band_before"] == "human" and "Previously accepted" in g["band_note"]
    summary = client.get(f"/api/items/{b['item_id']}").json()["item"]
    assert summary["open_findings"] == len([f for f in client.get(f"/api/versions/{b['version_id']}/findings").json()
                                            if f["band"] != "pass" and f["status"] == "open"])


def test_blocking_override_never_goes_below_human(client: TestClient) -> None:
    text = "Saffron & Steam is India's number one coffee for slow mornings. Find your nearest cafe."
    a = post(client, text)
    bs = [f for f in client.get(f"/api/versions/{a['version_id']}/findings").json() if f["severity"] == "blocking"]
    if not bs:
        pytest.skip("no blocking finding for this text without Jev")
    login(client, "reviewer")
    r = client.post(f"/api/findings/{bs[0]['id']}/override", json={"reason": "Award confirmed by legal for this campaign"})
    assert r.status_code == 200, r.text
    b = post(client, text)
    [g] = [f for f in findings(client, b["version_id"], bs[0]["rule_code"]) if f["quote"] == bs[0]["quote"]]
    assert g["precedent"]["decision"] == "overridden" and g["band"] == "human" and g["severity"] == "blocking"
    item = client.get(f"/api/items/{b['item_id']}").json()["item"]
    assert item["blocking_open"] >= 1 and item["state"] == "findings_ready"  # workflow untouched


def test_rule_rewrite_excludes_outdated_and_flags_rule_changed(client: TestClient) -> None:
    text = "No bargain hunting needed: every bag is roasted this week. Visit today."
    a = post(client, text)
    [f] = findings(client, a["version_id"], "R-TERM-02")
    login(client, "reviewer")
    assert client.post(f"/api/findings/{f['id']}/dismiss", json={"reason": "rule_outdated", "note": "bargain is fine now"}).status_code == 200
    b = post(client, text)
    assert findings(client, b["version_id"], "R-TERM-02")[0]["precedent"] is not None
    with Session(get_engine()) as s:  # the rule is rewritten (version bump)
        rule = s.exec(select(Rule).where(Rule.code == "R-TERM-02")).first()
        rule.version += 1
        s.add(rule)
        s.commit()
    try:
        c = post(client, text)
        assert findings(client, c["version_id"], "R-TERM-02")[0]["precedent"] is None
        login(client, "admin")
        p = next(p for p in client.get("/api/precedents").json()["precedents"] if p["reason"] == "rule_outdated")
        assert p["applies"] is False and "rewritten" in p["excluded_why"]
    finally:
        with Session(get_engine()) as s:
            rule = s.exec(select(Rule).where(Rule.code == "R-TERM-02")).first()
            rule.version -= 1
            s.add(rule)
            s.commit()


def test_confidential_items_teach_nothing_and_accept_undoes(client: TestClient) -> None:
    a = post(client, "Guaranteed freshness in every bag we roast. Visit today.", confidential=True)
    [f] = findings(client, a["version_id"], "R-TERM-02")
    before = len(client.get("/api/precedents").json()["precedents"])
    login(client, "creator")
    assert client.post(f"/api/findings/{f['id']}/dismiss", json={"reason": "false_alarm"}).status_code == 200
    assert len(client.get("/api/precedents").json()["precedents"]) == before

    b = post(client, "Guaranteed freshness in every bag we roast. Visit today.")
    [g] = findings(client, b["version_id"], "R-TERM-02")
    login(client, "creator")
    assert client.post(f"/api/findings/{g['id']}/dismiss", json={"reason": "false_alarm"}).status_code == 200
    p = next(p for p in client.get("/api/precedents").json()["precedents"] if p["item_id"] == b["item_id"])
    assert p["active"]
    assert client.post(f"/api/findings/{g['id']}/edit", json={"text": "Fresh"}).status_code == 200
    p = next(p for p in client.get("/api/precedents").json()["precedents"] if p["id"] == p["id"] and p["item_id"] == b["item_id"])
    assert p["active"] is False


def test_clarity_counts_dismissal_once_after_recheck(client: TestClient) -> None:
    login(client, "admin")
    rid = next(r["id"] for r in client.get("/api/rules").json() if r["code"] == "R-TONE-04")

    def people() -> dict:
        r = client.get("/api/rules/clarity").json()
        rows = r if isinstance(r, list) else r.get("rules", [])
        return next(x for x in rows if x["rule_id"] == rid)["signals"]["people"]

    a = post(client, "Fresh roast is here!! Find your nearest cafe.")
    [f] = findings(client, a["version_id"], "R-TONE-04")
    login(client, "reviewer")
    assert client.post(f"/api/findings/{f['id']}/dismiss", json={"reason": "false_alarm", "note": "two is fine here"}).status_code == 200
    login(client, "admin")
    p1 = people()
    asyncio.run(R.run_check(a["version_id"]))  # the re-check deletes the dismissed finding
    p2 = people()
    assert p2["dismissed"] == p1["dismissed"]  # still counted, from the precedent, and only once
