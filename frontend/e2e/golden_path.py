"""Rulemint golden path (Playwright, Python). Run against a server started on a FRESH DB:

    DB_PATH=/tmp/fresh.db uvicorn app.main:app --port 7860
    python frontend/e2e/golden_path.py --width 1280 --tag desktop
    python frontend/e2e/golden_path.py --width 390 --tag mobile     # needs another fresh DB

Prints one PASS/FAIL line per step and exits non-zero on any failure or console error.
"""
import argparse, json, re, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:7860")
ap.add_argument("--width", type=int, default=1280)
ap.add_argument("--tag", default="desktop")
ap.add_argument("--shots", default=str(Path(__file__).resolve().parents[1] / "screenshots"))
ap.add_argument("--skip-audience", action="store_true")
a = ap.parse_args()
SEED = Path(__file__).resolve().parents[2] / "backend/app/seed"
Path(a.shots).mkdir(parents=True, exist_ok=True)
mobile = a.width < 600
results, errors = [], []
S = {}


def shot(pg, name, full=True):
    pg.screenshot(path=f"{a.shots}/gp-{name}-{a.tag}.png", full_page=full)


def step(name):
    def deco(fn):
        t0 = time.time()
        try:
            note = fn()
            results.append((name, "PASS", note or "", time.time() - t0))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", str(e).splitlines()[0][:200], time.time() - t0))
            try:
                shot(S["pg"], "FAIL-" + re.sub(r"\W+", "_", name))
            except Exception:  # noqa: BLE001
                pass
        print(f"{results[-1][1]}  {name}  {results[-1][2]}", flush=True)
        return fn
    return deco


def login(ctx, uid):
    r = ctx.request.post(f"{a.base}/api/demo/login", data={"user_id": uid})
    assert r.ok, r.text()
    return r.json()["token"]


def api(ctx, path, method="get", **kw):
    r = getattr(ctx.request, method)(f"{a.base}/api{path}", **kw)
    assert r.ok, f"{path} {r.status} {r.text()[:200]}"
    return r.json()


def wait_check(ctx, vid, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = api(ctx, f"/versions/{vid}")
        if v["check_status"] in ("done", "error"):
            assert v["check_status"] == "done", "check errored"
            return v
        time.sleep(1)
    raise AssertionError("check timed out")


with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context(viewport={"width": a.width, "height": 900 if not mobile else 844})
    pg = ctx.new_page()
    S["pg"] = pg
    pg.on("console", lambda m: errors.append(f"console.{m.type}: {m.text[:160]}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {str(e)[:160]}"))
    pg.on("response", lambda r: errors.append(f"HTTP {r.status} {r.url[-80:]}") if r.status >= 500 else None)

    def as_user(uid):
        login(ctx, uid)
        pg.goto(a.base + "/"); pg.evaluate("sessionStorage.clear()")

    # ------------------------------------------------------------ 1 demo -> check
    @step("1 demo: Try the demo -> Check -> findings")
    def _():
        pg.goto(a.base + "/")
        pg.get_by_role("button", name="Try the demo").first.click()
        pg.wait_for_url(re.compile(r"/content/new"))
        pg.get_by_role("button", name="Check", exact=True).click()
        pg.wait_for_url(re.compile(r"/items/\d+"), timeout=30000)
        S["item"] = int(re.search(r"/items/(\d+)", pg.url).group(1))
        expect(pg.locator("article").first).to_be_visible(timeout=120000)
        pg.wait_for_function("() => !document.body.innerText.includes('Findings appear here')", timeout=120000)
        # wait until check is done (findings stop arriving)
        item = api(ctx, f"/items/{S['item']}")
        S["v1"] = item["item"]["latest_version_id"]
        wait_check(ctx, S["v1"])
        pg.reload(); pg.wait_for_selector("article", timeout=30000)
        n = pg.locator("article").count()
        assert n >= 8, f"only {n} finding cards"
        shot(pg, "1-findings")
        return f"{n} cards"

    @step("1b grouped fixes: hit words highlighted, groups linked")
    def _():
        hits = pg.locator("mark [data-hit]").count()
        lead = pg.locator("[data-testid=fix-group-lead]").count()
        linked = pg.locator("[data-testid=fix-group-linked]").count()
        assert hits > 0, "no hit_start..hit_end highlights"
        assert lead > 0 and linked > 0, f"group links lead={lead} linked={linked}"
        txt = pg.locator("[data-testid=fix-group-lead]").first.inner_text()
        assert "also resolves" in txt
        return f"{hits} hit marks, {lead} group leads, {linked} linked"

    # ------------------------------------------------------------ audience v1
    if not a.skip_audience:
        @step("7a audience: run on v1")
        def _():
            pg.get_by_role("tab", name="Audience").click()
            pg.get_by_role("button", name=re.compile("Run audience preview")).click()
            t0 = time.time()
            while time.time() - t0 < 150:
                au = api(ctx, f"/versions/{S['v1']}/audience")
                if au["status"] in ("done", "error", "unavailable"):
                    break
                time.sleep(2)
            assert au["status"] == "done", au.get("message")
            o = au["overall"]
            assert 1.0 <= o["mean_score"] <= 5.0 and o["answered"] > 0, o
            segs = au["segments"]
            assert segs and all(1.0 <= s["mean_score"] <= 5.0 and "vs_overall" in s for s in segs), "bad group scores"
            expect(pg.locator("[role=meter][aria-label='All simulated readers, average reaction']")).to_be_visible(timeout=20000)
            S["au1"] = o
            S["au1_groups"] = {s["key"]: s["mean_score"] for s in segs}
            pg.get_by_role("tab", name="Findings").click()
            rng = max(S["au1_groups"].values()) - min(S["au1_groups"].values())
            return (f"mean {o['mean_score']:.2f} sd {o['sd']:.2f} trust {o['trust_pct']:.0%} doubt {o['doubt_pct']:.0%}; "
                    f"group range {rng:.2f}; cost ${au.get('cost_usd')}")

    # ------------------------------------------------------------ 2 accept all
    @step("2 accept all fixes (grouped accept refetches)")
    def _():
        accepted, guard = 0, 0
        while guard < 40:
            guard += 1
            btn = pg.get_by_role("button", name=re.compile(r"Accept fix")).and_(pg.locator(":not([disabled])"))
            if btn.count() == 0:
                break
            btn.first.click()
            pg.wait_for_timeout(1200)
            accepted += 1
        fs = api(ctx, f"/versions/{S['v1']}/findings")
        open_fix = [f for f in fs if f["status"] == "open" and f["fix"] and f["band"] != "pass"]
        assert not open_fix, f"{len(open_fix)} open findings still have a fix"
        manual = [f for f in fs if f["status"] == "open" and not f["fix"] and f["band"] != "pass"]
        if manual:
            assert pg.locator("[data-testid=manual-fix]").count() > 0, "Manual fix needed not shown"
        shot(pg, "2-accepted")
        return f"{accepted} accept clicks, {sum(f['status']=='accepted' for f in fs)} accepted, {len(manual)} manual-fix"

    # ------------------------------------------------------------ 3 resubmit v2 clean
    @step("3 resubmit v2 -> clean (0 blocking)")
    def _():
        pg.get_by_role("button", name=re.compile(r"Resubmit as version")).click()
        pg.wait_for_function("() => /Version 2 created|v2|Version 2/.test(document.body.innerText)", timeout=20000)
        item = api(ctx, f"/items/{S['item']}")
        S["v2"] = item["item"]["latest_version_id"]
        assert S["v2"] != S["v1"]
        wait_check(ctx, S["v2"])
        pg.reload(); pg.wait_for_timeout(1500)
        fs = [f for f in api(ctx, f"/versions/{S['v2']}/findings") if f["band"] != "pass" and f["status"] == "open"]
        blocking = [f for f in fs if f["severity"] == "blocking"]
        assert not blocking, f"{len(blocking)} blocking in v2"
        shot(pg, "3-v2")
        return f"v2 open findings: {len(fs)}"

    @step("4a submit for review")
    def _():
        item = api(ctx, f"/items/{S['item']}")["item"]
        if item["blocking_open"]:
            raise AssertionError("blocking open")
        pg.get_by_role("button", name=re.compile("Submit for review")).click()
        # Brand Score: v2 can land just under the channel target (live Jev bands vary), then Submit asks for a reason
        below = item.get("score") is not None and item["score"] < item.get("target_score", 80)
        if below:
            pg.fill("#note-field", "Demo: remaining findings are minor; reviewer please check tone.")
            pg.get_by_role("button", name="Submit with reason").click()
        pg.wait_for_function("() => document.body.innerText.includes('Sent for review') || document.body.innerText.includes('In review')", timeout=15000)
        after = api(ctx, f"/items/{S['item']}")["item"]
        assert after["state"] == "in_review"
        return f"score {item.get('score')} / target {item.get('target_score')}" + (" (reason given)" if below else "")

    # ------------------------------------------------------------ audience v2
    if not a.skip_audience:
        @step("7b audience: run on v2, compare v1 vs v2 (colours, honest text)")
        def _():
            pg.get_by_role("tab", name="Audience").click()
            pg.get_by_role("button", name=re.compile("Run audience preview")).click()
            t0 = time.time()
            while time.time() - t0 < 150:
                au = api(ctx, f"/versions/{S['v2']}/audience")
                if au["status"] in ("done", "error", "unavailable"):
                    break
                time.sleep(2)
            assert au["status"] == "done", au.get("message")
            pg.reload(); pg.get_by_role("tab", name="Audience").click()
            sec = pg.locator("section[aria-label^='Before and after']")
            expect(sec).to_be_visible(timeout=20000)
            big = sec.locator("p.text-\\[44px\\]")
            cls = big.get_attribute("class")
            d = au["overall"]["mean_score"] - S["au1"]["mean_score"]
            if d < 0:
                assert "text-flag-text" not in cls, f"improved but big number is red: {cls}"
            summary = sec.locator("p.text-footnote").first.inner_text()
            if d <= -0.15:
                assert "react better" in summary, summary
            elif d >= 0.15:
                assert "react worse" in summary, summary
            cmp = au["compare_to_previous_version"]
            assert cmp, "no compare"
            assert len(cmp["segments"]) == len(au["segments"]), "groups missing from the compare"
            assert sec.locator("li [role=meter]").count() == len(cmp["segments"]), "per-group bars missing"
            shot(pg, "7-audience-compare")
            g = "; ".join(f"{s['label'][:14]} {s['previous']['mean_score']:.2f}->{s['previous']['mean_score'] + s['delta']['mean_score']:.2f}"
                          for s in cmp["segments"])
            return f"mean {S['au1']['mean_score']:.2f}->{au['overall']['mean_score']:.2f}; '{summary[:80]}'; {g}"

    # ------------------------------------------------------------ 5 Meera / Kabir
    @step("5a Meera comments")
    def _():
        as_user("meera")
        pg.goto(f"{a.base}/items/{S['item']}")
        pg.get_by_role("tab", name="Comments").click()
        pg.get_by_placeholder("Add a comment").fill("Looks good. The claims now match our approved facts.")
        pg.get_by_role("button", name="Comment", exact=True).click()
        pg.wait_for_selector("text=Looks good. The claims now match", timeout=10000)

    @step("5b Kabir approves")
    def _():
        as_user("kabir")
        pg.goto(f"{a.base}/items/{S['item']}")
        pg.wait_for_selector("article >> nth=0", timeout=30000) if api(ctx, f"/versions/{S['v2']}/findings") else None
        pg.get_by_role("button", name="Approve").click()
        pg.wait_for_function("() => document.body.innerText.includes('Approved')", timeout=15000)
        assert api(ctx, f"/items/{S['item']}")["item"]["state"] == "approved"
        shot(pg, "5-approved")

    @step("6 Kabir's own item is blocked")
    def _():
        items = api(ctx, "/items")
        mine = [i for i in (items if isinstance(items, list) else items.get("items", [])) if i["title"].startswith("Cold Steam Brew")]
        assert mine, "seed item missing"
        pg.goto(f"{a.base}/items/{mine[0]['id']}")
        pg.wait_for_selector("h1", timeout=15000)
        pg.wait_for_timeout(1200)
        btn = pg.get_by_role("button", name="Approve")
        assert btn.count() == 1
        assert btn.is_disabled() or btn.get_attribute("aria-disabled") == "true", "Approve enabled on own item"
        r = ctx.request.post(f"{a.base}/api/items/{mine[0]['id']}/approve", data={})
        assert r.status in (403, 409, 422), f"API allowed own approval: {r.status}"
        shot(pg, "6-own-blocked")
        return f"UI disabled; API {r.status}"

    # ------------------------------------------------------------ 8 logo overlays
    all_items = api(ctx, "/items")
    all_items = all_items if isinstance(all_items, list) else all_items.get("items", [])

    def find_item(prefix):
        m = [i for i in all_items if i["title"].startswith(prefix)]
        assert m, prefix
        return m[0]["id"]

    @step("8a logo overlay: genuine logo (green outline)")
    def _():
        pg.goto(f"{a.base}/items/{find_item('Slow mornings ad (good)')}")
        pg.wait_for_selector("svg polygon", timeout=30000)
        assert pg.locator("text=Genuine logo").count() > 0
        shot(pg, "8a-logo-good")

    @step("8b logo overlay: wrong logo (finding + clear note)")
    def _():
        pg.goto(f"{a.base}/items/{find_item('Slow mornings ad (logo check)')}")
        pg.wait_for_selector("article", timeout=30000)
        body = pg.inner_text("body")
        assert "Altered logo" in body or "Logo not found" in body, "no logo verdict shown"
        has_poly = pg.locator("svg polygon").count() > 0
        assert has_poly or re.search(r"outline", body, re.I), "neither outline nor note"
        shot(pg, "8b-logo-wrong")
        return "outline drawn" if has_poly else "no outline, note shown"

    # ------------------------------------------------------------ 9 PDF import (admin)
    @step("9 PDF import (Asha): upload guideline.pdf, confirm")
    def _():
        as_user("asha")
        pg.goto(a.base + "/brand")
        pg.get_by_role("button", name=re.compile("Import")).first.click()
        pg.set_input_files("[data-testid=pdf-import-input]", str(SEED / "guideline.pdf"))
        pg.wait_for_selector("input[type=checkbox], [role=checkbox]", timeout=90000)
        shot(pg, "9-pdf-import")
        pg.get_by_role("button", name=re.compile(r"^(Add|Import|Create|Confirm)", re.I)).last.click()
        pg.wait_for_selector("text=Back to the rules", timeout=20000)
        pg.get_by_role("button", name="Back to the rules").click()
        rules = api(ctx, "/rules")
        assert any(r["code"].startswith("R-IMP") for r in rules), "no imported rules"
        return f"{sum(r['code'].startswith('R-IMP') for r in rules)} rules imported"

    # ------------------------------------------------------------ 10 confidential
    @step("10 confidential item: local checks only")
    def _():
        as_user("rohan")
        pg.goto(a.base + "/content/new")
        pg.locator("textarea").first.fill("Our new roast is the best in India!!! Buy now, guaranteed miracle.")
        pg.get_by_role("switch", name="Confidential").click()
        pg.get_by_role("button", name="Check", exact=True).click()
        pg.wait_for_url(re.compile(r"/items/\d+"), timeout=30000)
        cid = int(re.search(r"/items/(\d+)", pg.url).group(1))
        v = api(ctx, f"/items/{cid}")["item"]["latest_version_id"]
        wait_check(ctx, v)
        pg.reload(); pg.wait_for_selector("article", timeout=20000)
        assert pg.get_by_text("Confidential").count() > 0
        assert pg.get_by_role("tab", name="Audience").count() == 0, "audience offered for confidential"
        st = api(ctx, f"/versions/{v}")["stats"]
        assert st.get("llm") != "used" and st.get("jev") != "used", f"AI used: jev={st.get('jev')} llm={st.get('llm')}"
        shot(pg, "10-confidential")
        return f"jev={st.get('jev')} llm={st.get('llm')}"

    # ------------------------------------------------------------ 11 pages
    for nm, path, needle in [("dashboard", "/dashboard", None), ("brand kit", "/brand", "Saffron"), ("eval", "/eval", None)]:
        @step(f"11 {nm} loads")
        def _(nm=nm, path=path, needle=needle):
            as_user("asha")
            pg.goto(a.base + path)
            pg.wait_for_load_state("networkidle")
            pg.wait_for_timeout(1500)
            body = pg.inner_text("body")
            assert "Something went wrong" not in body and "Can't" not in body, body[:200]
            if needle:
                assert needle in body
            shot(pg, "11-" + nm.replace(" ", "-"))

    @step("12 no horizontal overflow on item page")
    def _():
        pg.goto(f"{a.base}/items/{S['item']}")
        pg.wait_for_timeout(1500)
        w = pg.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
        assert w[0] <= w[1] + 1, f"scrollWidth {w[0]} > {w[1]}"

    @step("13 no console errors")
    def _():
        assert not errors, "; ".join(errors[:5])
    br.close()

bad = [r for r in results if r[1] == "FAIL"]
print(f"\n{len(results)-len(bad)}/{len(results)} passed ({a.tag}, {a.width}px)")
json.dump(results, open(f"{a.shots}/golden-{a.tag}.json", "w"), indent=1)
sys.exit(1 if bad else 0)
