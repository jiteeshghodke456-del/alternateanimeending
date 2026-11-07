"""Brand Score, per-channel targets, channel limits (R-CH-*) and the PDF font check."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import workspaces as W
from app.config import get_settings
from app.db import get_engine
from app.main import app
from app.models import AuditEvent, Brand, Finding
from app.pipeline import channels as CH
from app.pipeline import engine as E
from app.pipeline import run
from app.pipeline import score as SC
from app.services import current_brand
from pipeline_helpers import seed_ctx, text_units
from test_workflow import make_finding, make_item, mock_start_check, who  # noqa: F401  (fixtures)

IG = CH.effective_limits("instagram", {})


# ------------------------------------------------------------------ score formula
@pytest.mark.parametrize("severity,band,points", [
    ("blocking", "flag", 25), ("blocking", "human", 12), ("major", "flag", 10), ("major", "human", 5),
    ("minor", "flag", 3), ("minor", "human", 1), ("blocking", "pass", 0), ("minor", "pass", 0),
])
def test_score_weight_truth_table(severity: str, band: str, points: int) -> None:
    assert SC.compute([{"severity": severity, "band": band, "status": "open"}]).score == 100 - points


@pytest.mark.parametrize("status,counts", [("open", True), ("accepted", True), ("edited", True),
                                           ("dismissed", False), ("overridden", False)])
def test_score_counts_only_unresolved_statuses(status: str, counts: bool) -> None:
    s = SC.compute([{"severity": "major", "band": "flag", "status": status}]).score
    assert s == (90 if counts else 100)


def test_score_formula_text_bands_and_floor() -> None:
    fs = [{"severity": "blocking", "band": "flag", "status": "open"}] * 2 + [
        {"severity": "major", "band": "human", "status": "open"}, {"severity": "minor", "band": "flag", "status": "open"}]
    sc = SC.compute(fs)
    assert sc.score == 100 - 50 - 5 - 3 == 42
    assert sc.formula == "100 − 2×25 (blocking, flagged) − 1×5 (major, human look) − 1×3 (minor, flagged) = 42"
    assert (sc.band, sc.label) == ("poor", "Off brand")
    assert SC.compute([]).formula == "100 (no open findings)" and SC.compute([]).band == "good"
    floor = SC.compute([{"severity": "blocking", "band": "flag", "status": "open"}] * 5)
    assert floor.score == 0 and floor.formula.endswith("= -25, shown as 0")
    assert [SC.score_band(x) for x in (80, 79, 60, 59)] == ["good", "fair", "fair", "poor"]


# ------------------------------------------------------------------ channel text limits
def codes(issues: list[CH.ChannelIssue]) -> list[str]:
    return [i.rule_code for i in issues]


def test_instagram_caption_length_edges() -> None:
    assert codes(CH.check_text("instagram", "a" * 2200, IG)) == []
    out = CH.check_text("instagram", "a" * 2201, IG)
    assert codes(out) == ["R-CH-IG-CAPTION"] and out[0].severity == "major"
    assert "2,201 characters" in out[0].detail and "Meta IG User Media" in out[0].detail
    assert codes(CH.check_text("instagram", "a\r\n" * 1100, IG)) == []  # CRLF counted once: 2,200


def test_instagram_hashtag_and_mention_edges() -> None:
    tags = lambda n: " ".join(f"#tag{i}" for i in range(n))  # noqa: E731
    assert CH.count_hashtags(tags(30)) == 30 and codes(CH.check_text("instagram", tags(30), IG)) == []
    assert codes(CH.check_text("instagram", tags(31), IG)) == ["R-CH-IG-HASHTAGS"]
    assert CH.count_hashtags("We're #1 in town and open 24#7, call #2") == 0  # numbers are not hashtags
    ats = lambda n: " ".join(f"@friend{i}" for i in range(n))  # noqa: E731
    assert codes(CH.check_text("instagram", ats(20), IG)) == []
    assert codes(CH.check_text("instagram", ats(21), IG)) == ["R-CH-IG-MENTIONS"]
    assert CH.count_mentions("Write to hello@saffron.example or @saffron") == 1  # an email is not a tag


def test_linkedin_and_email_limits() -> None:
    li = CH.effective_limits("linkedin", {})
    assert codes(CH.check_text("linkedin", "x" * 3000, li)) == []
    assert codes(CH.check_text("linkedin", "x" * 3001, li)) == ["R-CH-LI-LENGTH"]
    em = CH.effective_limits("email", {})
    ok = "Subject: " + "s" * 60 + "\n\nHello"
    long = "Subject: " + "s" * 61 + "\n\nHello"
    assert codes(CH.check_text("email", ok, em)) == []
    out = CH.check_text("email", long, em)
    assert codes(out) == ["R-CH-EMAIL-SUBJECT"] and out[0].severity == "minor"
    assert out[0].extra["span"] == [9, 70] and out[0].quote == "s" * 61
    assert codes(CH.check_text("email", "s" * 500, em)) == []  # no subject line, nothing to measure
    assert CH.check_text("website", "x" * 9000, CH.effective_limits("website", {})) == []  # no website limits


# ------------------------------------------------------------------ images
@pytest.mark.parametrize("size,expected", [
    ((1080, 1080), []), ((1080, 1350), []), ((1080, 566), []),
    ((1080, 1351), ["R-CH-IG-RATIO"]), ((1080, 565), ["R-CH-IG-RATIO"]), ((1080, 1920), ["R-CH-IG-RATIO"]),
    ((320, 320), []), ((319, 319), ["R-CH-IG-WIDTH"]), ((300, 600), ["R-CH-IG-RATIO", "R-CH-IG-WIDTH"]),
])
def test_instagram_image_aspect_and_width(size: tuple[int, int], expected: list[str]) -> None:
    assert codes(CH.check_image("instagram", size, IG)) == expected


def test_image_checks_only_for_instagram_and_severities() -> None:
    assert CH.check_image("linkedin", (1080, 1920), CH.effective_limits("linkedin", {})) == []
    out = CH.check_image("instagram", (300, 600), IG)
    assert [i.severity for i in out] == ["major", "minor"] and "too tall" in out[0].detail


# ------------------------------------------------------------------ per-brand overrides
def test_brand_overrides_limits_and_target() -> None:
    targets = {"instagram": {"max_grade": 8, "target_score": 90,
                             "limits": {"caption_max_chars": 100, "hashtags_max": "lots", "aspect_ratio": [1.0, 1.0]}}}
    eff = CH.effective_limits("instagram", targets)
    assert eff["caption_max_chars"] == 100 and eff["hashtags_max"] == 30 and eff["aspect_ratio"] == [1.0, 1.0]
    out = CH.check_text("instagram", "a" * 101, eff)
    assert codes(out) == ["R-CH-IG-CAPTION"] and "Limit set by your brand (platform default 2,200" in out[0].detail
    assert codes(CH.check_image("instagram", (1080, 1350), eff)) == ["R-CH-IG-RATIO"]
    assert CH.target_score("instagram", targets) == 90 and CH.target_score("email", targets) == 80
    assert CH.target_score("instagram", {"instagram": {"target_score": 150}}) == 80  # invalid -> default


def test_every_check_has_a_source_url_and_date() -> None:
    for code in CH.catalog()["checks"]:
        c = CH.citation(code)
        assert c and c["url"].startswith("https://") and c["retrieved"] == "2026-11-07" and c["quote"]


# ------------------------------------------------------------------ PDF fonts
def make_pdf(path: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(72, 760, "Monsoon menu")
    c.setFont("Times-Roman", 12)
    c.drawString(72, 730, "Fresh roast, every morning.")
    c.showPage()
    c.setFont("Helvetica", 12)
    c.drawString(72, 760, "Page two")
    c.showPage()
    c.save()


@pytest.mark.parametrize("raw,family", [
    ("ABCDEF+Inter-SemiBold", "Inter"), ("BAAAAA+Fraunces-Bold", "Fraunces"), ("ArialMT", "Arial"),
    ("Arial,BoldItalic", "Arial"), ("OpenSans-BoldItalic", "OpenSans"), ("Times-Roman", "Times"), ("Helvetica", "Helvetica"),
])
def test_normalise_font(raw: str, family: str) -> None:
    assert CH.normalise_font(raw) == family


def test_pdf_font_extraction_and_finding(tmp_path: Path) -> None:
    pdf = tmp_path / "flyer.pdf"
    make_pdf(pdf)
    assert CH.pdf_fonts(str(pdf)) == {"Helvetica": [1, 2], "Times": [1]}
    out = CH.check_pdf_fonts(str(pdf), ["Fraunces", "Inter"], None)
    assert len(out) == 1 and out[0].rule_code == CH.FONT_RULE_CODE and out[0].severity == "minor"
    assert "Helvetica (pages 1, 2); Times (page 1)" in out[0].detail
    assert CH.check_pdf_fonts(str(pdf), ["Helvetica", "Times"], None) == []
    assert CH.font_allowed("OpenSans", ["Open Sans"])
    assert CH.check_pdf_fonts(str(pdf), [], None) == []  # brand lists no fonts: nothing to compare


def test_seeded_promo_pdf_uses_off_brand_fonts() -> None:
    promo = Path(__file__).resolve().parents[1] / "app" / "seed" / "content" / "promo.pdf"
    out = CH.check_pdf_fonts(str(promo), ["Fraunces", "Inter"], None)
    assert out and "DejaVuSans" in out[0].detail


# ------------------------------------------------------------------ engine: code-only findings
def test_engine_emits_channel_finding_without_ai() -> None:
    text = "Fresh roast in Pune. " * 110  # 2,310 chars
    ctx = seed_ctx(text, channel="instagram")
    ctx.channel_limits = CH.effective_limits("instagram", {})
    res = asyncio.run(E.analyze(text_units(text), ctx, use_jev=False, use_llm=False))
    ch = [f for f in res.findings if f.rule_code == "R-CH-IG-CAPTION"]
    assert len(ch) == 1 and ch[0].severity == "major" and ch[0].band == "flag" and ch[0].local_only
    assert ch[0].explanation.startswith("This breaks R-CH-IG-CAPTION: Instagram captions can be at most 2,200")
    ctx.channel_limits = None  # eval runner / old callers: channel checks off
    res2 = asyncio.run(E.analyze(text_units(text), ctx, use_jev=False, use_llm=False))
    assert not [f for f in res2.findings if f.rule_code and f.rule_code.startswith("R-CH-")]


def test_engine_pdf_font_uses_brand_rule(tmp_path: Path) -> None:
    pdf = tmp_path / "flyer.pdf"
    make_pdf(pdf)
    ctx = seed_ctx("Monsoon menu", channel="website", version_kind="pdf")
    ctx.channel_limits, ctx.brand_fonts, ctx.file_path = {}, ["Fraunces", "Inter"], str(pdf)
    res = asyncio.run(E.analyze(text_units("Monsoon menu"), ctx, use_jev=False, use_llm=False))
    font = [f for f in res.findings if f.category == "visual" and "fonts that are not in the brand kit" in f.explanation]
    assert len(font) == 1 and font[0].rule_code == "R-VIS-04"  # the seeded brand's font rule, cited with its page
    for r in ctx.rules:
        if r.code == "R-VIS-04":
            r.enabled = False
    res2 = asyncio.run(E.analyze(text_units("Monsoon menu"), ctx, use_jev=False, use_llm=False))
    assert not [f for f in res2.findings if "fonts that are not" in f.explanation]  # disabled rule: no built-in either


# ------------------------------------------------------------------ API: target + reason, overrides, citation
def _set_target(channel: str, target: int | None) -> None:
    with Session(get_engine()) as s:
        b = current_brand(s)
        t = {k: dict(v) for k, v in (b.channel_targets or {}).items()}
        cur = dict(t.get(channel) or {})
        cur.pop("target_score", None) if target is None else cur.update(target_score=target)
        t[channel] = cur
        b.channel_targets = t
        s.add(b)
        s.commit()


def test_submit_below_target_requires_reason_and_reviewer_sees_it(who) -> None:  # noqa: F811
    item_id, vid = make_item("rohan", channel="instagram")
    for _ in range(3):
        make_finding(vid, 0, 5, severity="major")  # 100 - 3x10 = 70 < 80
    rohan, meera = who("rohan"), who("meera")
    detail = rohan.get(f"/api/items/{item_id}").json()
    assert detail["item"]["score"] == 70 and detail["item"]["score_band"] == "fair" and detail["item"]["target_score"] == 80
    assert detail["versions"][0]["score"]["formula"] == "100 − 3×10 (major, flagged) = 70"
    r = rohan.post(f"/api/items/{item_id}/submit")
    assert r.status_code == 422 and "below the instagram target of 80" in r.json()["detail"]
    assert rohan.post(f"/api/items/{item_id}/submit", json={"reason": " "}).status_code == 422
    ok = rohan.post(f"/api/items/{item_id}/submit", json={"reason": "Launch copy approved by legal on call"})
    assert ok.status_code == 200, ok.text
    seen = meera.get(f"/api/items/{item_id}").json()["item"]
    assert seen["state"] == "in_review" and seen["submit_reason"] == "Launch copy approved by legal on call"
    with Session(get_engine()) as s:
        ev = s.exec(select(AuditEvent).where(AuditEvent.item_id == item_id, AuditEvent.action == "submitted")).one()
        assert ev.detail["below_target_reason"] == "Launch copy approved by legal on call"
        assert ev.detail["score"] == 70 and ev.detail["target_score"] == 80


def test_submit_at_or_above_target_needs_no_reason_and_dismissed_findings_raise_score(who) -> None:  # noqa: F811
    item_id, vid = make_item("rohan", channel="email")
    make_finding(vid, 0, 5, severity="major")
    make_finding(vid, 0, 5, severity="major")
    fid = make_finding(vid, 0, 5, severity="major")
    rohan = who("rohan")
    assert rohan.get(f"/api/items/{item_id}").json()["item"]["score"] == 70
    with Session(get_engine()) as s:
        f = s.get(Finding, fid)
        f.status = "dismissed"
        s.add(f)
        s.commit()
    assert rohan.get(f"/api/items/{item_id}").json()["item"]["score"] == 80
    r = rohan.post(f"/api/items/{item_id}/submit")
    assert r.status_code == 200, r.text
    assert r.json()["item"]["submit_reason"] is None


def test_blocking_findings_still_block_even_with_reason(who) -> None:  # noqa: F811
    item_id, vid = make_item("rohan")
    make_finding(vid, 0, 5, severity="blocking")
    r = who("rohan").post(f"/api/items/{item_id}/submit", json={"reason": "please"})
    assert r.status_code == 409 and "blocking" in r.json()["detail"]


def test_channels_endpoint_overrides_and_permissions(who) -> None:  # noqa: F811
    asha, rohan = who("asha"), who("rohan")
    got = rohan.get("/api/brand/channels").json()
    ig = next(c for c in got["channels"] if c["channel"] == "instagram")
    assert ig["target_score"] == 80 and {lim["code"] for lim in ig["limits"]} >= {"R-CH-IG-CAPTION", "R-CH-IG-RATIO"}
    assert all(lim["citation"]["url"].startswith("https://") for lim in ig["limits"])
    assert got["not_included"]
    assert rohan.put("/api/brand/channels/instagram", json={"target_score": 90}).status_code == 403
    assert asha.put("/api/brand/channels/instagram", json={"limits": {"nope": 1}}).status_code == 422
    assert asha.put("/api/brand/channels/instagram", json={"limits": {"caption_max_chars": -5}}).status_code == 422
    r = asha.put("/api/brand/channels/instagram", json={"target_score": 90, "limits": {"caption_max_chars": 1500}})
    assert r.status_code == 200, r.text
    ig = next(c for c in r.json()["channels"] if c["channel"] == "instagram")
    cap = next(lim for lim in ig["limits"] if lim["key"] == "caption_max_chars")
    assert ig["target_score"] == 90 and cap["value"] == 1500 and cap["overridden"] and cap["default"] == 2200
    with Session(get_engine()) as s:
        b = s.exec(select(Brand)).first()
        assert b.channel_targets["instagram"]["max_grade"] == 8  # readability target kept
        ctx = run.brand_context(s, b.id, "instagram")
        assert ctx.channel_limits["caption_max_chars"] == 1500 and ctx.brand_fonts == ["Fraunces", "Inter"]
    item_id, _ = make_item("rohan", channel="instagram")
    assert rohan.get(f"/api/items/{item_id}").json()["item"]["target_score"] == 90
    # reset to defaults
    r = asha.put("/api/brand/channels/instagram", json={"target_score": None, "limits": {"caption_max_chars": None}})
    ig = next(c for c in r.json()["channels"] if c["channel"] == "instagram")
    assert ig["target_score"] == 80 and not any(lim["overridden"] for lim in ig["limits"])


def test_channel_finding_cites_platform_page(who) -> None:  # noqa: F811
    item_id, vid = make_item("rohan")
    make_finding(vid, 0, 5, severity="major", rule="R-CH-IG-CAPTION", category="channel")
    fs = who("rohan").get(f"/api/versions/{vid}/findings").json()
    f = next(x for x in fs if x["rule_code"] == "R-CH-IG-CAPTION")
    assert f["rule"]["pack_id"] == "platform"
    assert f["rule"]["citation"]["url"].startswith("https://developers.facebook.com/")


def test_insights_average_score(who) -> None:  # noqa: F811
    out = who("asha").get("/api/insights").json()
    assert out["scored_items"] >= 1 and 0 <= out["avg_score"] <= 100
    assert out["score_trend"] and all(0 <= d["avg_score"] <= 100 for d in out["score_trend"])


# ------------------------------------------------------------------ workspace isolation
@pytest.fixture()
def ws_clients(tmp_path_factory: pytest.TempPathFactory) -> Iterator[list[TestClient]]:
    s = get_settings()
    saved = (s.workspaces_enabled, s.workspaces_dir, s.max_workspaces, s.workspace_ttl_h)
    saved_loop, saved_prerun = run._main_loop, run._prerun_started
    s.workspaces_enabled, s.workspaces_dir = True, str(tmp_path_factory.mktemp("ws-score"))
    W.clear_for_tests()
    try:
        with ExitStack() as st:
            yield [st.enter_context(TestClient(app)) for _ in range(2)]
    finally:
        W.clear_for_tests()
        s.workspaces_enabled, s.workspaces_dir, s.max_workspaces, s.workspace_ttl_h = saved
        run._main_loop, run._prerun_started = saved_loop, saved_prerun


def _login(c: TestClient, role: str) -> None:
    u = next(x for x in c.get("/api/me").json()["users"] if x["role"] == role)
    assert c.post("/api/demo/login", json={"user_id": u["id"]}).status_code == 200


def test_channel_settings_are_per_workspace(ws_clients: list[TestClient]) -> None:
    a, b = ws_clients
    _login(a, "admin")
    _login(b, "admin")
    assert a.get("/api/workspace").json()["id"] != b.get("/api/workspace").json()["id"]
    r = a.put("/api/brand/channels/linkedin", json={"target_score": 95, "limits": {"post_max_chars": 1200}})
    assert r.status_code == 200, r.text
    la = next(c for c in a.get("/api/brand/channels").json()["channels"] if c["channel"] == "linkedin")
    lb = next(c for c in b.get("/api/brand/channels").json()["channels"] if c["channel"] == "linkedin")
    assert la["target_score"] == 95 and lb["target_score"] == 80
    assert la["limits"][0]["value"] == 1200 and lb["limits"][0]["value"] == 3000


