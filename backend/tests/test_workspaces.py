"""Per-visitor workspaces (app/workspaces.py): isolation, cookie-less demo workspace, blank brand setup,
reset scope, eviction/TTL, background checks writing to the right workspace. Jev and the LLM are off
(conftest), so checks run the code (Tier 0 + logo) checks only."""
from __future__ import annotations

import io
import time
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlmodel import Session, select

from app import workspaces as W
from app.config import get_settings
from app.main import app
from app.models import Finding, Version
from app.pipeline import run


@pytest.fixture(scope="module")
def ws_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    s = get_settings()
    saved = (s.workspaces_enabled, s.workspaces_dir, s.max_workspaces, s.workspace_ttl_h)
    saved_loop, saved_prerun = run._main_loop, run._prerun_started
    root = tmp_path_factory.mktemp("ws")
    s.workspaces_enabled, s.workspaces_dir = True, str(root)
    W.clear_for_tests()
    try:
        yield root
    finally:
        W.clear_for_tests()
        s.workspaces_enabled, s.workspaces_dir, s.max_workspaces, s.workspace_ttl_h = saved
        run._main_loop, run._prerun_started = saved_loop, saved_prerun


@pytest.fixture()
def clients(ws_env: Path) -> Iterator[list[TestClient]]:
    """Three independent browsers (own cookie jars) on one running app (lifespan builds the templates)."""
    with ExitStack() as st:
        cs = [st.enter_context(TestClient(app)) for _ in range(3)]
        yield cs


def login(c: TestClient, role: str) -> dict:
    users = c.get("/api/me").json()["users"]
    u = next(x for x in users if x["role"] == role)
    r = c.post("/api/demo/login", json={"user_id": u["id"]})
    assert r.status_code == 200, r.text
    return r.json()["user"]


def wait_done(c: TestClient, vid: int, timeout: float = 30.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = c.get(f"/api/versions/{vid}").json()
        if v["check_status"] in ("done", "error"):
            return v
        time.sleep(0.1)
    raise AssertionError(f"version {vid} not checked in time")


def post_text(c: TestClient, text: str, title: str = "Post") -> dict:
    r = c.post("/api/items", data={"title": title, "channel": "instagram", "text": text})
    assert r.status_code == 201, r.text
    return r.json()


def make_logo() -> Image.Image:
    """A synthetic logo with plenty of corners for SIFT."""
    img = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((20, 20, 380, 380), fill=(20, 60, 140, 255))
    d.polygon([(200, 50), (330, 300), (70, 300)], fill=(240, 180, 30, 255))
    for i in range(6):
        d.rectangle((90 + i * 38, 310, 110 + i * 38, 350), fill=(250, 250, 250, 255))
    d.ellipse((160, 150, 240, 230), fill=(200, 30, 40, 255))
    d.line((60, 200, 340, 120), fill=(255, 255, 255, 255), width=9)
    return img


def png(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, format="PNG")
    return b.getvalue()


def ad_with(logo: Image.Image) -> bytes:
    ad = Image.new("RGB", (1080, 1080), (236, 228, 214))
    d = ImageDraw.Draw(ad)
    d.rectangle((80, 700, 1000, 980), fill=(60, 40, 30))
    small = logo.resize((300, 300))
    ad.paste(small, (390, 220), small)
    return png(ad)


# ------------------------------------------------------------------ tests
def test_cookieless_request_gets_a_demo_workspace(clients: list[TestClient]) -> None:
    c = clients[0]
    assert c.get("/api/health").status_code == 200
    assert "bg_ws" not in c.cookies  # health never creates a workspace
    r = c.get("/api/me")
    assert r.status_code == 200 and "bg_ws" in r.cookies
    assert r.json()["users"], "demo users come from the workspace"
    ws = c.get("/api/workspace").json()
    assert ws["kind"] == "demo" and ws["brand"]["name"] and ws["expires_at"] and ws["limits"]["max_workspaces"] >= 1
    assert c.get("/api/workspace").json()["id"] == ws["id"]  # the cookie sticks
    assert r.headers["x-bg-workspace"] == ws["token"]
    # cookies blocked (cross-site iframe): the header / ?ws= token selects the same workspace
    bare = TestClient(app)
    assert bare.get("/api/workspace", headers={"X-BG-Workspace": ws["token"]}).json()["id"] == ws["id"]
    assert bare.get(f"/api/workspace?ws={ws['token']}").json()["id"] == ws["id"]


def test_two_visitors_are_isolated_and_background_checks_land_in_their_own_workspace(clients: list[TestClient]) -> None:
    a, b = clients[0], clients[1]
    login(a, "creator")
    login(b, "creator")
    assert a.get("/api/workspace").json()["id"] != b.get("/api/workspace").json()["id"]
    n_b = len(b.get("/api/items").json())
    ia = post_text(a, "Our coffee is the best in India!!! Buy now", "A's post")
    ib = post_text(b, "Hello from visitor B.", "B's post")
    assert ia["version_id"] == ib["version_id"]  # both copies of the same template: the same ids
    va, vb = wait_done(a, ia["version_id"]), wait_done(b, ib["version_id"])
    assert va["check_status"] == vb["check_status"] == "done"
    titles_b = {i["title"] for i in b.get("/api/items").json()}
    assert "B's post" in titles_b and "A's post" not in titles_b and len(titles_b) == n_b + 1
    assert "A's post" in {i["title"] for i in a.get("/api/items").json()}
    # each check read and wrote its own workspace's version text
    assert a.get(f"/api/versions/{ia['version_id']}").json()["text"].startswith("Our coffee")
    assert b.get(f"/api/versions/{ib['version_id']}").json()["text"].startswith("Hello from")
    fa = a.get(f"/api/versions/{ia['version_id']}/findings").json()
    fb = b.get(f"/api/versions/{ib['version_id']}/findings").json()
    assert fa and not any(f["quote"] and f["quote"] not in "Hello from visitor B." for f in fb)


def test_background_task_binds_the_scheduling_workspace(ws_env: Path) -> None:
    a, b = W.create("demo"), W.create("demo")

    async def which() -> str | None:
        return W.current_id()

    import asyncio

    with W.use(a.id):
        coro = W.bind(which())
    with W.use(b.id):  # a different workspace is current when the task actually runs
        assert asyncio.run(coro) == a.id


def test_blank_workspace_setup_flow(clients: list[TestClient]) -> None:
    c = clients[2]
    c.get("/api/me")
    login(c, "creator")
    r = c.post("/api/workspaces", json={"kind": "blank"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) >= {"id", "kind", "brand", "created_at"} and body["kind"] == "blank"
    assert body["brand"]["name"] == "Your brand"
    assert "bg_session" in r.headers.get("set-cookie", "")  # the old sign-in is cleared
    assert c.get("/api/workspace").json()["id"] == body["id"]
    me = c.get("/api/me").json()
    assert me["user"] is None
    assert {(u["id"], u["name"], u["role"]) for u in me["users"]} == {
        ("admin", "Admin", "admin"), ("writer", "Writer", "creator"), ("reviewer", "Reviewer", "reviewer"),
        ("approver", "Approver", "approver")}
    login(c, "admin")
    b = c.get("/api/brand").json()
    assert b["is_blank_setup"] is True and b["counts"] == {"rules": 0, "facts": 0, "terms": 0, "items": 0}
    assert b["palette"] == [] and b["logo_url"] is None and b["tagline"] == "" and b["demo"] is False

    # brand
    b = c.patch("/api/brand", json={"name": "Chai Point Test", "tagline": "Tea, done right", "description": "A tea brand."}).json()
    assert (b["name"], b["tagline"], b["description"]) == ("Chai Point Test", "Tea, done right", "A tea brand.")
    assert c.patch("/api/brand", json={"name": ""}).status_code == 422
    # palette
    assert c.put("/api/brand/palette", json={"colours": [{"name": "Bad", "hex": "#12345G"}]}).status_code == 422
    assert c.put("/api/brand/palette", json={"colours": []}).status_code == 422
    assert c.put("/api/brand/palette", json={"colours": [{"name": f"c{i}", "hex": "#000000"} for i in range(13)]}).status_code == 422
    pal = [{"name": "Navy", "hex": "#143C8c"}, {"name": "Saffron", "hex": "#F0B41E"}, {"name": "Red", "hex": "#C81E28"},
           {"name": "White", "hex": "#FAFAFA"}]
    b = c.put("/api/brand/palette", json={"colours": pal}).json()
    assert [p["hex"] for p in b["palette"]] == ["#143C8C", "#F0B41E", "#C81E28", "#FAFAFA"]
    # logo
    assert c.post("/api/brand/logo", files={"file": ("x.gif", b"GIF89a....", "image/gif")}).status_code == 422
    logo = make_logo()
    b = c.post("/api/brand/logo", files={"file": ("logo.png", png(logo), "image/png")}).json()
    assert b["logo_url"] and c.get(b["logo_url"]).status_code == 200
    stored = Image.open(io.BytesIO(c.get(b["logo_url"]).content))
    assert stored.mode == "RGB" and stored.format == "PNG"
    # facts CSV with bad rows
    csv = ("statement,value,source,valid_until\n"
           "We serve 40 cafes in Bengaluru,40 cafes,Ops report 2026,2027-03-31\n"
           ",,,\n"
           "Our tea is grown in Assam,,,\n"
           "Founded in 2010,2010,Company deck,31/12/2027\n"
           "We serve 40 cafes in Bengaluru,,,\n")
    r = c.post("/api/facts/import", files={"file": ("facts.csv", csv.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["imported"] == 2 and [s["row"] for s in out["skipped"]] == [2, 4, 5]
    assert c.post("/api/facts/import", files={"file": ("f.csv", b"name,value\nx,1\n", "text/csv")}).status_code == 422
    big = "statement\n" + "".join(f"Fact {i}\n" for i in range(501))
    assert c.post("/api/facts/import", files={"file": ("f.csv", big.encode(), "text/csv")}).status_code == 422
    facts = c.get("/api/facts").json()
    assert {f["code"] for f in facts} == {"F-01", "F-02"}
    # rules and words
    r = c.post("/api/rules", json={"code": "R-01", "category": "tone", "severity": "major",
                                    "statement": "Never call the tea 'cheap'.", "check_method": "tier0",
                                    "params": {"banned": ["cheap"]}})
    assert r.status_code == 201, r.text
    assert c.post("/api/terms", json={"kind": "banned", "value": "bestest"}).status_code == 201
    b = c.get("/api/brand").json()
    assert b["counts"] == {"rules": 1, "facts": 2, "terms": 1, "items": 0} and b["is_blank_setup"] is False
    assert c.get("/api/eval/latest").json()["brand_note"] == "Evaluation uses the built-in demo brand's labelled test set."

    # check content: text
    login(c, "creator")
    it = post_text(c, "Our cheap tea is the bestest.")
    v = wait_done(c, it["version_id"])
    assert v["check_status"] == "done"
    codes = {f["rule_code"] for f in c.get(f"/api/versions/{it['version_id']}/findings").json()}
    assert "R-01" in codes
    # check content: an ad with the uploaded logo -> logo fidelity ran on the uploaded logo
    r = c.post("/api/items", data={"title": "Ad", "channel": "instagram", "caption": "New tea"},
               files={"file": ("ad.png", ad_with(logo), "image/png")})
    assert r.status_code == 201, r.text
    v = wait_done(c, r.json()["version_id"])
    assert v["check_status"] == "done", v
    logo_res = v["stats"]["logo"]
    assert logo_res and logo_res[0]["verdict"] == "genuine", logo_res
    # the demo workspace of another visitor is untouched
    assert c.get("/api/brand").json()["counts"]["items"] == 2


def test_empty_brand_checks_run_with_a_clear_note(clients: list[TestClient]) -> None:
    c = clients[0]
    assert c.post("/api/workspaces", json={"kind": "blank"}).status_code == 201
    login(c, "creator")
    it = post_text(c, "We are the No. 1 tea in India, 50% off today only! Call +91 98765 43210.")
    v = wait_done(c, it["version_id"])
    assert v["check_status"] == "done"
    assert any("Add rules first" in w for w in v["stats"]["warnings"])
    r = c.post("/api/items", data={"title": "Ad", "channel": "instagram"},
               files={"file": ("ad.png", ad_with(make_logo()), "image/png")})
    v = wait_done(c, r.json()["version_id"])  # no logo, no palette, no rules: still finishes
    assert v["check_status"] == "done"
    assert c.get("/api/insights").status_code == 200


def test_reset_only_touches_the_current_workspace(clients: list[TestClient]) -> None:
    a, b = clients[0], clients[1]
    for c in (a, b):
        c.post("/api/workspaces", json={"kind": "demo"})
        login(c, "creator")
    seeded = len(a.get("/api/items").json())
    post_text(a, "A again")
    post_text(b, "B again")
    login(a, "admin")
    r = a.post("/api/demo/reset")
    assert r.status_code == 200 and "demo brand" in r.json()["message"]
    assert len(a.get("/api/items").json()) == seeded
    assert len(b.get("/api/items").json()) == seeded + 1
    # a blank workspace resets to blank
    a.post("/api/workspaces", json={"kind": "blank"})
    login(a, "admin")
    a.patch("/api/brand", json={"name": "Changed"})
    assert "blank brand" in a.post("/api/demo/reset").json()["message"]
    assert a.get("/api/brand").json()["name"] == "Your brand"


def test_unknown_or_expired_cookie_gets_a_new_demo_workspace(clients: list[TestClient]) -> None:
    c = clients[0]
    old = c.get("/api/workspace").json()["id"]
    m = W.get_meta(old)
    m.last_used -= W.ttl_s() + 10  # idle for longer than WORKSPACE_TTL_H
    new = c.get("/api/workspace").json()
    assert new["id"] != old and new["kind"] == "demo"
    assert not (Path(get_settings().workspaces_dir) / old).exists()
    c.cookies.set("bg_ws", "forged-value")
    assert c.get("/api/workspace").json()["id"] not in (old, new["id"])


def test_lru_eviction_and_engine_cache(ws_env: Path) -> None:
    s = get_settings()
    saved = s.max_workspaces
    try:
        s.max_workspaces = 3
        W.enforce_limits()
        made = [W.create("demo") for _ in range(3)]
        assert {m.id for m in W.all_meta()} == {m.id for m in made}
        made[0].hits, made[0].last_used = 10, made[0].last_used - 3600  # established, oldest
        made[1].last_used -= 1200  # a one-off visit, idle 20 min: goes first
        extra = W.create("blank")
        assert {m.id for m in W.all_meta()} == {made[0].id, made[2].id, extra.id}
        assert not (Path(s.workspaces_dir) / made[1].id).exists()
        extra2 = W.create("demo")  # then plain LRU
        assert {m.id for m in W.all_meta()} == {made[2].id, extra.id, extra2.id}
        for m in W.all_meta():
            W.engine_for(m.id)
        assert len(W._engines) <= W.ENGINE_CACHE
    finally:
        s.max_workspaces = saved


def test_copied_workspace_never_inherits_a_running_check(ws_env: Path) -> None:
    from app.db import get_engine

    with W.use(W.TEMPLATE_IDS["demo"]):
        with Session(get_engine()) as sess:
            v = sess.exec(select(Version)).first()
            v.check_status = "running"
            sess.add(v)
            sess.commit()
            vid = v.id
    try:
        m = W.create("demo")
        with W.use(m.id), Session(get_engine()) as sess:
            assert sess.get(Version, vid).check_status == "not_run"
            assert sess.exec(select(Finding)).all() is not None
    finally:
        with W.use(W.TEMPLATE_IDS["demo"]), Session(get_engine()) as sess:
            v = sess.get(Version, vid)
            v.check_status = "not_run"
            sess.add(v)
            sess.commit()


def test_demo_template_respects_seed_dir(ws_env: Path, tmp_path: Path) -> None:
    import json

    from app.db import get_engine
    from app.services import current_brand

    kit = tmp_path / "kit"
    kit.mkdir()
    (kit / "brand.json").write_text(json.dumps({"name": "Kit Brand", "tone_labels": [], "channel_targets": {}}), encoding="utf-8")
    (kit / "users.json").write_text(json.dumps([{"id": "kim", "name": "Kim", "role": "admin"}]), encoding="utf-8")
    (kit / "rules.json").write_text("[]", encoding="utf-8")
    s = get_settings()
    saved = s.seed_dir
    try:
        s.seed_dir = str(kit)
        W.build_template("demo")
        m = W.create("demo")
        with W.use(m.id), Session(get_engine()) as sess:
            assert current_brand(sess).name == "Kit Brand"
    finally:
        s.seed_dir = saved
        W.build_template("demo")  # back to the default demo brand (signature changed -> rebuilt)


def test_template_prerun_result_reaches_workspaces_copied_before_it(ws_env: Path) -> None:
    """Cold start: a visitor's demo copy is made before the demo pre-run has checked the seeded items.
    When the template's check finishes, its results are copied into that workspace."""
    import asyncio

    from app.db import get_engine

    tpl = W.TEMPLATE_IDS["demo"]
    with W.use(tpl), Session(get_engine()) as sess:
        v = sess.exec(select(Version).where(Version.kind == "text", Version.check_status == "not_run")).first()
        assert v is not None
        vid = v.id
    early = W.create("demo")
    with W.use(early.id), Session(get_engine()) as sess:
        assert sess.get(Version, vid).check_status == "not_run"
    with W.use(tpl):
        asyncio.run(run.run_check(vid))  # Tier 0 only (no keys in tests); then propagated
    with W.use(tpl), Session(get_engine()) as sess:
        tpl_findings = sorted((f.rule_code, f.span_start) for f in sess.exec(select(Finding).where(Finding.version_id == vid)))
    with W.use(early.id), Session(get_engine()) as sess:
        v = sess.get(Version, vid)
        assert v.check_status == "done" and v.stats.get("findings_by_band") is not None
        got = sorted((f.rule_code, f.span_start) for f in sess.exec(select(Finding).where(Finding.version_id == vid)))
    assert got == tpl_findings and got
    late = W.create("demo")  # a later copy already has the result, and is not touched twice
    with W.use(late.id), Session(get_engine()) as sess:
        assert len(sess.exec(select(Finding).where(Finding.version_id == vid)).all()) == len(tpl_findings)
    assert W.propagate_template_check(vid) == 0


def test_reset_is_refused_while_a_job_of_this_workspace_runs(clients: list[TestClient]) -> None:
    """Audit #8: a reset during a running check let the old task write into the fresh database
    (29 findings, 15 duplicates). Now the reset waits: 409 with a human message, other workspaces unaffected."""
    from app.routers import innovations

    a, b = clients[0], clients[1]
    for c in (a, b):
        c.post("/api/workspaces", json={"kind": "demo"})
        login(c, "admin")
    ws_a = a.get("/api/workspace").json()["id"]
    msg = "A check is still running in this workspace. Wait for it to finish, then reset."
    for start, stop in (
        (lambda: run._running.add((ws_a, 1)), lambda: run._running.discard((ws_a, 1))),
        (lambda: innovations._active.add((ws_a, 1)), lambda: innovations._active.discard((ws_a, 1))),
        (lambda: innovations._imports.__setitem__("t1", {"id": "t1", "status": "running", "ws": ws_a}),
         lambda: innovations._imports.pop("t1", None)),
    ):
        start()
        try:
            r = a.post("/api/demo/reset")
            assert r.status_code == 409 and r.json()["detail"] == msg
            assert b.post("/api/demo/reset").status_code == 200  # only this workspace is blocked
        finally:
            stop()
        assert a.post("/api/demo/reset").status_code == 200


def test_reset_after_a_real_recheck_leaves_no_duplicate_findings(clients: list[TestClient]) -> None:
    a = clients[0]
    a.post("/api/workspaces", json={"kind": "demo"})
    login(a, "admin")
    item = a.get("/api/items?key=demo_flawed_post").json()[0]
    vid = a.get(f"/api/items/{item['id']}").json()["versions"][-1]["id"]
    r = a.post(f"/api/versions/{vid}/check")
    assert r.status_code in (200, 202), r.text
    codes = []
    t0 = time.time()
    while True:
        r = a.post("/api/demo/reset")
        codes.append(r.status_code)
        if r.status_code == 200:
            break
        assert r.status_code == 409 and time.time() - t0 < 30
        time.sleep(0.05)
    time.sleep(0.3)  # any straggling writes from the old task would land now
    b = clients[1]
    b.post("/api/workspaces", json={"kind": "demo"})
    login(b, "admin")
    fresh = b.get(f"/api/versions/{vid}/findings").json()
    after = a.get(f"/api/versions/{vid}/findings").json()
    assert a.get(f"/api/versions/{vid}").json()["check_status"] == b.get(f"/api/versions/{vid}").json()["check_status"]
    assert len(after) == len(fresh), "the reset workspace differs from a fresh copy of the demo"
