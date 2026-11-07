"""Brand Score + channel limits e2e (Playwright, Python). Run against a server on a FRESH workspace dir:

    python frontend/e2e/score.py --base http://127.0.0.1:8016 --tag desktop --shots /tmp/shots
    python frontend/e2e/score.py --base http://127.0.0.1:8017 --width 375 --tag mobile --shots /tmp/shots

Steps: the demo post shows its score (formula on expand) and list badges -> accept fixes, resubmit, v2 scores
higher (v1 -> v2 in the switcher) -> admin raises the Instagram target in Brand Kit -> Channels -> Submit asks
for a reason, the reviewer sees it -> an over-long Instagram caption gets the R-CH-IG-CAPTION finding with its
official source. Live cost: one v2 check (the long caption is checked as confidential: local checks only).
"""
import argparse, re, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:7860")
ap.add_argument("--width", type=int, default=1280)
ap.add_argument("--tag", default="desktop")
ap.add_argument("--shots", default=str(Path(__file__).resolve().parents[1] / "screenshots"))
a = ap.parse_args()
Path(a.shots).mkdir(parents=True, exist_ok=True)
mobile = a.width < 600
results, errors = [], []
S = {}


def shot(pg, name, full=True):
    pg.screenshot(path=f"{a.shots}/score-{name}-{a.tag}.png", full_page=full)


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


def api(ctx, path, method="get", **kw):
    r = getattr(ctx.request, method)(f"{a.base}/api{path}", **kw)
    assert r.ok, f"{path} {r.status} {r.text()[:200]}"
    return r.json()


def wait_checked(ctx, vid, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = api(ctx, f"/versions/{vid}")["check_status"]
        if st in ("done", "error"):
            return st
        time.sleep(1.5)
    raise AssertionError(f"version {vid} not checked")


with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context(viewport={"width": a.width, "height": 900 if not mobile else 812})
    pg = ctx.new_page()
    S["pg"] = pg
    pg.on("console", lambda m: errors.append(f"console.{m.type}: {m.text[:160]}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {str(e)[:160]}"))

    def as_user(uid):
        api(ctx, "/demo/login", "post", data={"user_id": uid})
        pg.goto(a.base + "/"); pg.evaluate("sessionStorage.clear()")

    @step("1 demo post shows its Brand Score, formula on expand, badges in the list")
    def _():
        as_user("rohan")
        it = api(ctx, "/items", params={"key": "demo_flawed_post"})[0]
        S["item"], S["v1"] = it["id"], it["latest_version_id"]
        assert wait_checked(ctx, S["v1"]) == "done"
        it = api(ctx, f"/items/{S['item']}")["item"]
        S["s1"] = it["score"]
        pg.goto(f"{a.base}/items/{S['item']}")
        card = pg.locator("[data-testid=brand-score]")
        expect(card).to_be_visible(timeout=20000)
        expect(pg.locator("[data-testid=brand-score-value]")).to_have_text(str(it["score"]))
        assert re.search(r"On brand|Needs work|Off brand", card.inner_text()), "no band label"
        card.get_by_role("button", name=re.compile("How it's worked out")).click()
        formula = pg.locator("[data-testid=brand-score-formula]").inner_text()
        assert formula.startswith("100") and "=" in formula, formula
        shot(pg, "1-v1", full=False)
        pg.goto(f"{a.base}/content")
        expect(pg.locator("[data-testid=score-badge]").first).to_be_visible(timeout=15000)
        n = pg.locator("[data-testid=score-badge]").count()
        return f"v1 score {it['score']} ({formula}); {n} list badges"

    @step("2 accept fixes, resubmit: v2 scores higher, switcher shows v1 -> v2")
    def _():
        fs = api(ctx, f"/versions/{S['v1']}/findings")
        for f in fs:
            if f["status"] == "open" and f["fix"] and f["band"] != "pass":
                r = ctx.request.post(f"{a.base}/api/findings/{f['id']}/accept")
                assert r.ok or r.status == 409, r.text()[:200]
        out = api(ctx, f"/items/{S['item']}/resubmit", "post")
        S["v2"] = out["version_id"]
        assert wait_checked(ctx, S["v2"]) == "done"
        it = api(ctx, f"/items/{S['item']}")["item"]
        S["s2"] = it["score"]
        assert S["s2"] > S["s1"], f"v2 {S['s2']} not above v1 {S['s1']}"
        pg.goto(f"{a.base}/items/{S['item']}?v={S['v2']}")
        expect(pg.locator("[data-testid=brand-score-delta]")).to_contain_text(f"v1 {S['s1']} → {S['s2']}", timeout=20000)
        nav = pg.get_by_role("navigation", name="Versions")
        expect(nav.locator("[data-testid=score-badge]")).to_have_count(2)
        shot(pg, "2-v2", full=False)
        return f"v1 {S['s1']} -> v2 {S['s2']}"

    @step("3 admin raises the Instagram target in Brand Kit -> Channels")
    def _():
        as_user("asha")
        pg.goto(f"{a.base}/brand?tab=channels")
        ig = pg.locator("[data-testid=channel-instagram]")
        expect(ig).to_be_visible(timeout=15000)
        expect(ig.get_by_role("link", name=re.compile("Meta IG User Media"))).to_have_count(5)
        target = min(100, S["s2"] + 5)
        ig.get_by_label("Target Brand Score").fill(str(target))
        ig.get_by_role("button", name="Save Instagram").click()
        expect(pg.get_by_text("Instagram settings saved")).to_be_visible(timeout=10000)
        got = next(c for c in api(ctx, "/brand/channels")["channels"] if c["channel"] == "instagram")
        assert got["target_score"] == target, got["target_score"]
        S["target"] = target
        shot(pg, "3-channels")
        return f"target {target}"

    @step("4 below target: Submit asks for a reason; the reviewer sees it")
    def _():
        as_user("rohan")
        pg.goto(f"{a.base}/items/{S['item']}")
        expect(pg.locator("[data-testid=brand-score-target]")).to_contain_text(f"Below the Instagram target of {S['target']}", timeout=20000)
        pg.get_by_role("button", name=re.compile("Submit for review")).click()
        dlg = pg.get_by_role("dialog")
        expect(dlg).to_contain_text("Submit below the score target?")
        dlg.locator("#note-field").fill("Launch day; legal approved the tone by phone.")
        dlg.get_by_role("button", name="Submit with reason").click()
        expect(pg.get_by_text("Sent for review with your reason")).to_be_visible(timeout=15000)
        assert api(ctx, f"/items/{S['item']}")["item"]["state"] == "in_review"
        as_user("meera")
        pg.goto(f"{a.base}/items/{S['item']}")
        expect(pg.locator("[data-testid=submit-reason]")).to_contain_text("legal approved the tone", timeout=15000)
        hist = api(ctx, f"/items/{S['item']}/history")
        ev = next(e for e in hist if e["action"] == "submitted")
        assert ev["detail"]["below_target_reason"].startswith("Launch day"), ev["detail"]
        shot(pg, "4-reviewer", full=False)
        pg.emulate_media(color_scheme="dark"); pg.wait_for_timeout(300); shot(pg, "4-reviewer-dark", full=False)
        pg.emulate_media(color_scheme="light")
        api(ctx, "/demo/login", "post", data={"user_id": "asha"})
        api(ctx, "/brand/channels/instagram", "put", data={"target_score": None})
        return f"score {S['s2']} < target {S['target']}, reason stored and shown"

    @step("5 over-long Instagram caption gets the length finding with its source")
    def _():
        as_user("rohan")
        pg.goto(f"{a.base}/content/new")
        text = ("Slow mornings, steady brews at our Pune cafe. " * 50).strip()  # 2,299 characters
        pg.get_by_label("Content text").fill(text)
        pg.locator("#title").fill("Long caption test")
        pg.get_by_label("Confidential").click()  # local checks only: the channel limit is a code check
        pg.get_by_role("button", name="Check", exact=True).click()
        pg.wait_for_url(re.compile(r"/items/\d+"), timeout=20000)
        iid = int(re.search(r"/items/(\d+)", pg.url).group(1))
        vid = api(ctx, f"/items/{iid}")["item"]["latest_version_id"]
        assert wait_checked(ctx, vid) == "done"
        fs = api(ctx, f"/versions/{vid}/findings")
        f = next((x for x in fs if x["rule_code"] == "R-CH-IG-CAPTION"), None)
        assert f, [x["rule_code"] for x in fs]
        assert f["severity"] == "major" and "2,299 characters" in f["explanation"], f["explanation"]
        pg.reload()
        card = pg.locator(f"#finding-{f['id']}")
        expect(card).to_contain_text("Instagram captions can be at most 2,200 characters", timeout=20000)
        expect(card.get_by_text("Platform spec")).to_be_visible()
        href = card.get_by_role("link", name=re.compile("Official source")).get_attribute("href")
        assert href and href.startswith("https://developers.facebook.com/"), href
        card.scroll_into_view_if_needed()
        shot(pg, "5-long-caption", full=False)
        return f"{len(fs)} findings incl. R-CH-IG-CAPTION ({f['severity']})"

    @step("6 dashboard shows the average score; no horizontal overflow")
    def _():
        pg.goto(f"{a.base}/dashboard")
        expect(pg.locator("[data-testid=score-trend]")).to_be_visible(timeout=20000)
        ins = api(ctx, "/insights")
        assert ins["avg_score"] is not None and ins["scored_items"] >= 1
        over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert over <= 1, f"page overflows by {over}px"
        shot(pg, "6-dashboard", full=False)
        return f"avg {ins['avg_score']} over {ins['scored_items']} items, {len(ins['score_trend'])} day(s)"

    @step("7 no console errors")
    def _():
        bad = [e for e in errors if "favicon" not in e]
        assert not bad, bad[:3]

    br.close()

bad = [r for r in results if r[1] == "FAIL"]
print(f"\n{len(results) - len(bad)}/{len(results)} passed ({a.tag}, {a.width}px)")
sys.exit(1 if bad else 0)
