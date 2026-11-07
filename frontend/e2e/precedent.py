"""Precedent memory, end to end (Playwright, Python). Run against a server on a FRESH DB:

    python frontend/e2e/precedent.py --base http://127.0.0.1:8005 [--width 390] --tag x --shots <dir>

Demo brand: Brand Kit -> Precedents lists the seeded dismissal; a new free-shipping post shows "Seen before"
(Meera) with a lowered band; the creator dismisses "cheap" with a reason on one post (in the UI); a similar
new post shows "Seen before" + "Needs a human look"; the admin revokes it in the Brand Kit; a re-check of
that post no longer shows it. Prints PASS/FAIL per step, exits non-zero on any failure or console error.
"""
import argparse, re, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:7860")
ap.add_argument("--width", type=int, default=1280)
ap.add_argument("--tag", default="desktop")
ap.add_argument("--shots", default=str(Path(__file__).resolve().parents[1] / "screenshots"))
a = ap.parse_args()
Path(a.shots).mkdir(parents=True, exist_ok=True)
mobile = a.width < 600
results, errors = [], []
S = {}
CHEAP = "Our cold brew is cheap and cheerful for the whole family. Find your nearest cafe."
CHEAP2 = "Our cold brew is cheap and cheerful for the whole family! Find your nearest cafe today."
SHIP = "Free shipping on online orders above Rs 599. Order today at our nearest cafe."


def shot(pg, name, full=True):
    pg.screenshot(path=f"{a.shots}/pr-{name}-{a.tag}.png", full_page=full)


def step(name):
    def deco(fn):
        t0 = time.time()
        try:
            note = fn()
            results.append((name, "PASS", note or "", time.time() - t0))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", str(e).splitlines()[0][:220], time.time() - t0))
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


def login(ctx, role):
    users = api(ctx, "/me")["users"]
    uid = next(u["id"] for u in users if u["role"] == role)
    api(ctx, "/demo/login", "post", data={"user_id": uid})


def wait_check(ctx, vid, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = api(ctx, f"/versions/{vid}")
        if v["check_status"] in ("done", "error"):
            assert v["check_status"] == "done", "check errored"
            return v
        time.sleep(1)
    raise AssertionError("check timed out")


def post(ctx, title, text):
    login(ctx, "creator")
    it = api(ctx, "/items", "post", multipart={"title": title, "channel": "instagram", "text": text})
    wait_check(ctx, it["version_id"])
    return it


def finding(ctx, vid, rule):
    fs = [f for f in api(ctx, f"/versions/{vid}/findings") if f["rule_code"] == rule]
    assert fs, f"no {rule} finding"
    return fs[0]


def card(pg, rule):
    return pg.locator(f'article[aria-label*="{rule}"]').first


def no_overflow(pg):
    w = pg.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1] + 1, f"horizontal overflow: scrollWidth {w[0]} > {w[1]}"


with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context(viewport={"width": a.width, "height": 900 if not mobile else 844})
    pg = ctx.new_page()
    S["pg"] = pg
    pg.on("console", lambda m: errors.append(f"console.{m.type}: {m.text[:160]}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {str(e)[:160]}"))
    pg.on("response", lambda r: errors.append(f"HTTP {r.status} {r.url[-80:]}") if r.status >= 500 else None)

    @step("1 Brand Kit -> Precedents lists the seeded dismissal (demo data)")
    def _():
        pg.goto(a.base + "/")
        login(ctx, "admin")
        pg.goto(a.base + "/brand?tab=precedents")
        panel = pg.get_by_test_id("precedents-panel")
        expect(panel).to_be_visible(timeout=30000)
        row = pg.get_by_test_id("precedent-row").filter(has_text="Free shipping").first
        expect(row).to_contain_text("Meera")
        expect(row).to_contain_text("Demo data")
        expect(row.get_by_role("button", name=re.compile("Revoke"))).to_be_visible()
        no_overflow(pg)
        shot(pg, "1-brandkit")

    @step("2 a new free-shipping post: 'Seen before' (Meera), band lowered, link to the banner")
    def _():
        it = post(ctx, "Weekend free shipping", SHIP)
        f = finding(ctx, it["version_id"], "R-CLAIM-04")
        assert f["precedent"] and f["precedent"]["by"] == "Meera", f["precedent"]
        assert f["band_before"] == "flag" and f["band"] == "human", (f["band_before"], f["band"])
        pg.goto(f"{a.base}/items/{it['item_id']}")
        c = card(pg, "R-CLAIM-04")
        expect(c).to_be_visible(timeout=30000)
        line = c.get_by_test_id("precedent-line")
        expect(line).to_contain_text("Seen before: dismissed by Meera")
        expect(line).to_contain_text("standing policy")
        expect(line.get_by_role("link")).to_have_attribute("href", re.compile(r"/items/\d+"))
        expect(c).to_contain_text("Needs a human look")
        c.scroll_into_view_if_needed()
        no_overflow(pg)
        c.screenshot(path=f"{a.shots}/pr-2-seen-before-{a.tag}.png")

    @step("3 the creator dismisses 'cheap' with a reason (UI)")
    def _():
        it = post(ctx, "Cold brew for families", CHEAP)
        S["a"] = it
        pg.goto(f"{a.base}/items/{it['item_id']}")
        c = card(pg, "R-TERM-02")
        expect(c).to_be_visible(timeout=30000)
        c.get_by_role("button", name="Dismiss").click()
        dlg = pg.get_by_role("dialog")
        dlg.get_by_label("False alarm").check()
        dlg.locator("#dismiss-note").fill("Cheap and cheerful is an idiom here, not a price claim.")
        dlg.get_by_role("button", name=re.compile("Dismiss finding")).click()
        expect(dlg).to_be_hidden(timeout=15000)
        ps = api(ctx, "/precedents")["precedents"]
        mine = next(x for x in ps if x["item_id"] == it["item_id"] and x["rule_code"] == "R-TERM-02")
        S["pid"] = mine["id"]
        return f"precedent {mine['id']}: '{mine['quote']}' by {mine['by']}"

    @step("4 a similar new post: 'Seen before' + lowered band")
    def _():
        it = post(ctx, "Cold brew, again", CHEAP2)
        S["b"] = it
        f = finding(ctx, it["version_id"], "R-TERM-02")
        assert f["precedent"] and f["precedent"]["id"] == S["pid"], f["precedent"]
        assert f["band"] == "human" and f["band_before"] == "flag" and f["status"] == "open"
        pg.goto(f"{a.base}/items/{it['item_id']}")
        c = card(pg, "R-TERM-02")
        expect(c.get_by_test_id("precedent-line")).to_contain_text("Seen before: dismissed by Rohan", timeout=30000)
        expect(c.get_by_test_id("precedent-band-note")).to_contain_text("Lowered from")
        c.scroll_into_view_if_needed()
        no_overflow(pg)
        c.screenshot(path=f"{a.shots}/pr-4-similar-{a.tag}.png")
        shot(pg, "4-item")

    @step("5 admin revokes it in Brand Kit -> Precedents (uses = 1)")
    def _():
        login(ctx, "admin")
        pg.goto(a.base + "/brand?tab=precedents")
        row = pg.get_by_test_id("precedent-row").filter(has_text="idiom").first
        expect(row).to_contain_text("Used 1 time", timeout=30000)
        row.get_by_role("button", name=re.compile("Revoke")).click()
        expect(row).to_contain_text("Revoked", timeout=15000)
        no_overflow(pg)
        shot(pg, "5-revoked")
        p = next(x for x in api(ctx, "/precedents")["precedents"] if x["id"] == S["pid"])
        assert p["active"] is False

    @step("6 re-check the similar post: 'Seen before' is gone, band back to Flag")
    def _():
        login(ctx, "creator")
        api(ctx, f"/versions/{S['b']['version_id']}/check", "post")
        time.sleep(1)
        wait_check(ctx, S["b"]["version_id"])
        f = finding(ctx, S["b"]["version_id"], "R-TERM-02")
        assert f["precedent"] is None and f["band"] == "flag", (f["precedent"], f["band"])
        pg.goto(f"{a.base}/items/{S['b']['item_id']}")
        c = card(pg, "R-TERM-02")
        expect(c).to_be_visible(timeout=30000)
        expect(c.get_by_test_id("precedent-line")).to_have_count(0)

    @step("7 no console errors")
    def _():
        assert not errors, "; ".join(errors[:5])
    br.close()

fails = [r for r in results if r[1] != "PASS"]
print(f"\n{len(results) - len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
