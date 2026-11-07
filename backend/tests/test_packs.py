"""Rule packs (app/packs.py, routers/packs.py): the India regulatory pack's JSON, apply idempotency, remove,
Tier 0 lexicon hits (English, Hinglish, Hindi), links to the brand's own rules, permissions and workspace
isolation. Jev and the LLM are off (conftest)."""
from __future__ import annotations

import copy
import json
import time
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import packs as P
from app import workspaces as W
from app.config import get_settings
from app.main import app
from app.pipeline import run
from app.pipeline.context import CheckContext, RuleSpec
from app.pipeline.tier0 import run_tier0_text

PACK_FILE = P.PACKS_DIR / "india_regulatory.json"


def raw() -> dict:
    return json.loads(PACK_FILE.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ schema
def test_pack_json_is_valid_and_cites_every_rule() -> None:
    pack = P.load_pack_file(PACK_FILE)
    assert pack.id == "india_regulatory" and 8 <= len(pack.rules) <= 10
    src = {s.id: s for s in pack.sources}
    for r in pack.rules:
        s = src[r.citation.source]
        assert s.url.startswith("https://") and s.retrieved.isoformat() == "2026-11-07"
        assert r.citation.clause and len(r.citation.quote) <= 160
        assert r.check_method in ("tier0", "jev", "claim")
        q = P.source_quote(pack, r)
        assert s.title in q and r.citation.clause in q
    codes = {r.code for r in pack.rules}
    assert {"IN-CCPA-01", "IN-ASCI-01"} <= codes
    false_urgency = next(r for r in pack.rules if r.code == "IN-CCPA-01")
    assert false_urgency.citation.clause.startswith("Annexure 1 (1): False Urgency")


@pytest.mark.parametrize("mutate, msg", [
    (lambda d: d["rules"][0]["citation"].update(source="nope"), "unknown source"),
    (lambda d: d["rules"].append(copy.deepcopy(d["rules"][0])), "duplicate rule code"),
    (lambda d: d["rules"][0]["params"].update(patterns=["(unclosed"]), ""),
    (lambda d: d["rules"][0]["params"].pop("patterns"), "needs params.patterns"),
    (lambda d: d["sources"][0].update(url="http://example.com"), "https"),
    (lambda d: d["rules"][0]["citation"].update(quote="x" * 400), ""),
    (lambda d: d["rules"][0]["params"].update(linked_rules=["R-1"]), "set by the pack loader"),
])
def test_pack_schema_rejects_bad_packs(mutate, msg: str) -> None:  # noqa: ANN001
    d = raw()
    mutate(d)
    with pytest.raises((ValidationError, Exception)) as e:
        P.Pack.model_validate(d)
    assert msg in str(e.value)


# ------------------------------------------------------------------ lexicon (pure)
def _pack_specs(linked: dict[str, list[str]] | None = None) -> list[RuleSpec]:
    pack = P.load_pack_file(PACK_FILE)
    out = []
    for r in pack.rules:
        f = P._fields(pack, r, (linked or {}).get(r.code, []))
        out.append(RuleSpec(code=r.code, category=f["category"], severity=f["severity"], statement=f["statement"],
                            check_method=f["check_method"], params=f["params"],
                            examples_violating=f["examples_violating"], examples_ok=f["examples_ok"]))
    return out


@pytest.mark.parametrize("text, phrase", [
    ("Only today: 20% off every cold brew.", "Only today"),
    ("Hurry, just 3 left at this price.", "Hurry"),
    ("Last few bags left of the monsoon roast.", "Last few bags"),
    ("Offer ends tonight.", "ends tonight"),
    ("12 people are viewing this right now.", "12 people are viewing this"),
    ("Sirf aaj! Flat 30% off on filter coffee.", "Sirf aaj"),
    ("Jaldi karo, stock khatam ho raha hai.", "Jaldi karo"),
    ("आज ही ऑर्डर करें।", "आज ही"),
    ("सिर्फ आज 20% की छूट!", "सिर्फ आज"),
    ("जल्दी करें, आखिरी मौका।", "जल्दी करें"),
])
def test_false_urgency_lexicon_hits(text: str, phrase: str) -> None:
    ctx = CheckContext(channel="instagram", rules=_pack_specs())
    hits = [h for h in run_tier0_text(text, ctx) if h.rule_code == "IN-CCPA-01"]
    assert hits, text
    assert any(phrase.lower() in h.quote.lower() for h in hits), [h.quote for h in hits]


@pytest.mark.parametrize("text", [
    "Our festive box is available until 31 October.",
    "We roast every Tuesday and Friday, so your bag is always fresh.",
    "Aaj ka special: masala chai cold brew.",
    "आज हमारी नई कॉफ़ी आई है।",
])
def test_false_urgency_lexicon_passes_calm_copy(text: str) -> None:
    ctx = CheckContext(channel="instagram", rules=_pack_specs())
    assert not [h for h in run_tier0_text(text, ctx) if h.rule_code == "IN-CCPA-01"]


@pytest.mark.parametrize("text, hit", [
    ("Rs 199 per cup, platform fee extra.", True),
    ("Tickets at Rs 499, extra charges apply.", True),
    ("Rs 199 per cup, incl. taxes and delivery.", False),
    ("Rs 499 per ticket plus a Rs 30 booking fee (Rs 529 in total).", False),
])
def test_drip_pricing_lexicon(text: str, hit: bool) -> None:
    ctx = CheckContext(channel="instagram", rules=_pack_specs())
    assert bool([h for h in run_tier0_text(text, ctx) if h.rule_code == "IN-CCPA-02"]) is hit


def test_linked_pack_rule_stays_quiet_where_the_brand_rule_fired() -> None:
    brand = RuleSpec(code="R-URG", category="claims", severity="major", statement="No pressure.", check_method="tier0",
                     params={"patterns": [r"\bonly today\b", r"\bhurry\b"], "case_insensitive": True})
    ctx = CheckContext(channel="instagram", rules=[brand, *_pack_specs({"IN-CCPA-01": ["R-URG"]})])
    text = "Only today, hurry before stock runs out. सिर्फ आज!"
    codes = [(h.rule_code, h.quote) for h in run_tier0_text(text, ctx)]
    assert ("IN-CCPA-01", "सिर्फ आज") in codes  # the brand rule misses Hindi: the pack still catches it
    assert not [c for c in codes if c[0] == "IN-CCPA-01" and "stock" in c[1]]  # same sentence as R-URG: quiet
    assert any(c[0] == "R-URG" for c in codes)


# ------------------------------------------------------------------ API, demo brand (shared DB)
from conftest import login  # noqa: E402


def _flawed_findings(client: TestClient) -> list[dict]:
    items = client.get("/api/items").json()
    items = items["items"] if isinstance(items, dict) else items
    it = next(i for i in items if i.get("key") == "demo_flawed_post")
    vid = it["latest_version_id"]
    fs = client.get(f"/api/versions/{vid}/findings").json()
    if not fs:  # the unit-test DB doesn't pre-run the demo checks: run Tier 0 (Jev is off) now
        assert client.post(f"/api/versions/{vid}/check").status_code == 200
        _wait(client, vid)
        fs = client.get(f"/api/versions/{vid}/findings").json()
    return fs


def test_apply_is_idempotent_links_brand_rules_and_remove_cleans_up(client: TestClient) -> None:
    login(client, "admin")
    n_before = len(client.get("/api/rules").json())
    packs = client.get("/api/rules/packs").json()
    ind = next(p for p in packs if p["id"] == "india_regulatory")
    assert ind["applied"] is False and ind["rule_count"] == 9 and len(ind["sources"]) >= 5

    r = client.post("/api/rules/packs/india_regulatory/apply")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["added"] == 9 and out["updated"] == 0 and out["skipped"] == []
    assert out["linked"].get("IN-CCPA-01") == ["R-CLAIM-03"]  # demo brand's own urgency rule
    assert "R-CLAIM-01" in out["linked"].get("IN-ASCI-02", [])  # demo brand's superlative rule

    again = client.post("/api/rules/packs/india_regulatory/apply").json()
    assert (again["added"], again["updated"], again["unchanged"]) == (0, 0, 9)
    rules = client.get("/api/rules").json()
    assert len(rules) == n_before + 9
    assert len({r["code"] for r in rules}) == len(rules)
    pack_rules = [r for r in rules if r["pack_id"] == "india_regulatory"]
    assert len(pack_rules) == 9 and all(r["params"]["citation"]["url"].startswith("https://") for r in pack_rules)

    # an admin edit is overwritten (and versioned) by a re-apply
    fu = next(r for r in pack_rules if r["code"] == "IN-CCPA-01")
    assert client.patch(f"/api/rules/{fu['id']}", json={"statement": "edited"}).status_code == 200
    again = client.post("/api/rules/packs/india_regulatory/apply").json()
    assert (again["added"], again["updated"]) == (0, 1)
    fu2 = next(r for r in client.get("/api/rules").json() if r["code"] == "IN-CCPA-01")
    assert fu2["statement"] != "edited" and fu2["version"] == fu["version"] + 2

    # the seeded flawed post's existing "only today" finding now shows the CCPA citation
    fs = _flawed_findings(client)
    urg = [f for f in fs if f["rule_code"] == "R-CLAIM-03" and "only today" in f["quote"].lower()]
    assert urg, [(f["rule_code"], f["quote"]) for f in fs]
    assert not [f for f in fs if f["rule_code"] == "IN-CCPA-01"]  # linked: flagged once, by the brand rule
    regs = urg[0]["regulations"]
    assert regs and regs[0]["code"] == "IN-CCPA-01" and regs[0]["pack_id"] == "india_regulatory"
    cit = regs[0]["citation"]
    assert cit["clause"].startswith("Annexure 1 (1): False Urgency")
    assert "Dark Patterns, 2023" in cit["document"] and cit["url"].startswith("https://ccpa.doca.gov.in/")
    assert cit["retrieved"] == "2026-11-07"

    st = next(p for p in client.get("/api/rules/packs").json() if p["id"] == "india_regulatory")
    assert st["applied"] is True and st["applied_count"] == 9 and st["applied_version"] == 1

    rm = client.delete("/api/rules/packs/india_regulatory")
    assert rm.status_code == 200 and rm.json()["removed"] == 9
    assert len(client.get("/api/rules").json()) == n_before
    assert all(f["regulations"] == [] for f in _flawed_findings(client))
    assert client.delete("/api/rules/packs/india_regulatory").json()["removed"] == 0


def test_pack_writes_are_admin_only_and_unknown_pack_404(client: TestClient) -> None:
    login(client, "creator")
    assert client.get("/api/rules/packs").status_code == 200
    assert client.post("/api/rules/packs/india_regulatory/apply").status_code == 403
    assert client.delete("/api/rules/packs/india_regulatory").status_code == 403
    login(client, "admin")
    assert client.post("/api/rules/packs/nope/apply").status_code == 404
    assert client.delete("/api/rules/packs/nope").status_code == 404


def test_code_clash_with_a_brand_rule_is_skipped_not_overwritten(client: TestClient) -> None:
    login(client, "admin")
    r = client.post("/api/rules", json={"code": "IN-CCPA-02", "category": "legal", "severity": "minor",
                                         "statement": "Our own rule with the same code.", "check_method": "jev"})
    assert r.status_code == 201, r.text
    try:
        out = client.post("/api/rules/packs/india_regulatory/apply").json()
        assert out["skipped"] == ["IN-CCPA-02"] and out["added"] == 8
        own = next(x for x in client.get("/api/rules").json() if x["code"] == "IN-CCPA-02")
        assert own["pack_id"] is None and own["statement"] == "Our own rule with the same code."
    finally:
        client.delete("/api/rules/packs/india_regulatory")


# ------------------------------------------------------------------ workspaces: isolation + blank brand
@pytest.fixture()
def ws_clients(tmp_path_factory: pytest.TempPathFactory) -> Iterator[list[TestClient]]:
    s = get_settings()
    saved = (s.workspaces_enabled, s.workspaces_dir)
    saved_loop, saved_prerun = run._main_loop, run._prerun_started
    s.workspaces_enabled, s.workspaces_dir = True, str(tmp_path_factory.mktemp("ws-packs"))
    W.clear_for_tests()
    try:
        with ExitStack() as st:
            yield [st.enter_context(TestClient(app)) for _ in range(2)]
    finally:
        W.clear_for_tests()
        s.workspaces_enabled, s.workspaces_dir = saved
        run._main_loop, run._prerun_started = saved_loop, saved_prerun


def _wait(c: TestClient, vid: int, timeout: float = 30.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = c.get(f"/api/versions/{vid}").json()
        if v["check_status"] in ("done", "error"):
            return v
        time.sleep(0.2)
    raise AssertionError("check did not finish")


def test_pack_is_per_workspace_and_works_in_a_blank_brand(ws_clients: list[TestClient]) -> None:
    a, b = ws_clients
    a.get("/api/me"), b.get("/api/me")
    assert a.post("/api/workspaces", json={"kind": "blank"}).status_code == 201
    login(a, "admin")
    login(b, "admin")
    out = a.post("/api/rules/packs/india_regulatory/apply").json()
    assert out["added"] == 9 and out["linked"] == {}  # a blank brand has no rules to link to
    assert next(p for p in a.get("/api/rules/packs").json() if p["id"] == "india_regulatory")["applied"] is True
    assert next(p for p in b.get("/api/rules/packs").json() if p["id"] == "india_regulatory")["applied"] is False
    assert not [r for r in b.get("/api/rules").json() if r["pack_id"]]

    login(a, "creator")
    r = a.post("/api/items", data={"title": "Sale", "channel": "instagram",
                                   "text": "Sirf aaj! Flat 30% off on filter coffee. Platform fee extra."})
    assert r.status_code == 201, r.text
    vid = r.json()["version_id"]
    assert _wait(a, vid)["check_status"] == "done"
    fs = a.get(f"/api/versions/{vid}/findings").json()
    by = {f["rule_code"]: f for f in fs}
    assert {"IN-CCPA-01", "IN-CCPA-02"} <= set(by), list(by)
    cit = by["IN-CCPA-01"]["rule"]["citation"]
    assert by["IN-CCPA-01"]["rule"]["pack_id"] == "india_regulatory" and "False Urgency" in cit["clause"]
    assert "Annexure 1 (1)" in by["IN-CCPA-01"]["rule"]["source_quote"]


def test_flag_only_pack_rules_drop_the_human_band() -> None:
    from app.pipeline.engine import _Run

    weak = next(s for s in _pack_specs() if s.code == "IN-ASCI-01")
    strong = next(s for s in _pack_specs() if s.code == "IN-CCPA-03")
    assert weak.params.get("flag_only") is True and not strong.params.get("flag_only")
    assert _Run.cap_band(None, weak, "human") == "pass"  # type: ignore[arg-type]
    assert _Run.cap_band(None, weak, "flag") == "flag"  # type: ignore[arg-type]
    assert _Run.cap_band(None, strong, "human") == "human"  # type: ignore[arg-type]


def test_pack_jev_rules_are_asked_as_advertising_rules() -> None:
    from app.pipeline.questions import rule_question

    q = rule_question(next(s for s in _pack_specs() if s.code == "IN-CCPA-03"))
    assert q.instructions.startswith("Does `text` break this advertising rule:")
    assert "silly to skip" in q.criteria["true"]
