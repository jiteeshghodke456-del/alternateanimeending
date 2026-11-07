"""Claims ledger + fact impact e2e (Playwright, Python). Run against a server started on a FRESH workspace
dir with Jev configured (the demo pre-run fills the ledger):

    python frontend/e2e/claims.py --base http://127.0.0.1:7951 --tag desktop --shots /tmp/shots
    python frontend/e2e/claims.py --base http://127.0.0.1:7952 --width 390 --tag mobile --shots /tmp/shots

Judge test (BUILD.md §12.1): expire a fact and watch the affected items get flagged.
Steps: open the ledger -> expire F-04 in Brand Kit -> impact lists the approved about page -> send back
-> the item is "changes requested", its history says "fact expired", and the re-check marks the claim outdated.
"""
import argparse, json, re, sys, time
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
ABOUT = "Our cafes: website about page"


def shot(pg, name, full=True):
    pg.screenshot(path=f"{a.shots}/claims-{name}-{a.tag}.png", full_page=full)


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


def login(ctx, uid):
    return api(ctx, "/demo/login", "post", data={"user_id": uid})


def wait_checked(ctx, vids, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = [api(ctx, f"/versions/{v}")["check_status"] for v in vids]
        if all(s in ("done", "error") for s in st):
            return st
        time.sleep(2)
    raise AssertionError(f"checks not done: {st}")


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

    @step("0 seeded items are checked (demo pre-run)")
    def _():
        as_user("asha")
        items = api(ctx, "/items")
        S["items"] = {i["title"]: i for i in items}
        assert ABOUT in S["items"], "seed item missing"
        st = wait_checked(ctx, [i["latest_version_id"] for i in items])
        return f"{len(items)} items, {st.count('done')} checked"

    @step("1 ledger: grouped by fact, all four statuses, approved item listed")
    def _():
        pg.goto(a.base + "/claims")
        expect(pg.get_by_role("heading", name="Claims ledger")).to_be_visible(timeout=20000)
        expect(pg.locator("[data-testid=group-unsupported]")).to_be_visible(timeout=20000)
        led = api(ctx, "/claims")
        t = led["totals"]
        assert t["supported"] > 0 and t["outdated"] > 0 and t["unsupported"] > 0, t
        expect(pg.locator("[data-testid=group-F-12]")).to_contain_text("Outdated")
        # the approved about page relies on F-04 (12 cafes); fall back to another of its facts if Jev chose differently
        codes = [g["fact"]["code"] for g in led["groups"] if g["fact"] and any(c["item_title"] == ABOUT for c in g["claims"])]
        assert codes, "the approved about page relies on no fact"
        S["fact"] = "F-04" if "F-04" in codes else codes[0]
        expect(pg.locator(f"[data-testid=group-{S['fact']}]")).to_contain_text(ABOUT)
        body = pg.inner_text("main")
        assert "Expired" in body and "Supported" in body
        shot(pg, "1-ledger")
        pg.emulate_media(color_scheme="dark"); pg.wait_for_timeout(300); shot(pg, "1-ledger-dark", full=False)
        pg.emulate_media(color_scheme="light")
        return f"totals {t}, {len(led['groups'])} groups; about page relies on {codes}"

    @step("2 filter by status")
    def _():
        pg.get_by_role("button", name=re.compile(r"^\d+\s*Outdated$")).click()
        pg.wait_for_timeout(800)
        badges = pg.locator("main section li span[title]").all_inner_texts()
        assert badges and all(b.strip() == "Outdated" for b in badges), badges[:5]
        pg.get_by_role("button", name=re.compile(r"^\d+\s*Outdated$")).click()
        return f"{len(badges)} outdated claims"

    @step("3 brand kit: the fact is used by the approved page; expire it -> impact opens")
    def _():
        F = S["fact"]
        pg.goto(a.base + "/brand")
        pg.get_by_role("tab", name="Facts").click()
        row = pg.locator("li", has=pg.get_by_text(F, exact=True))
        expect(row).to_contain_text(re.compile(r"Used by \d+ items? \(1 approved\)"), timeout=15000)
        pg.get_by_role("button", name=f"Edit {F}").click()
        pg.get_by_role("button", name="Mark as expired").click()
        pg.get_by_role("button", name="Save").click()
        dlg = pg.get_by_role("dialog")
        expect(dlg).to_contain_text(f"Fact impact: {F}", timeout=15000)
        expect(dlg.locator("[data-testid=impact-items]")).to_contain_text(ABOUT)
        expect(dlg).to_contain_text("expired on")
        expect(dlg.locator("[data-testid=impact-counts]")).to_contain_text("1 approved")
        shot(pg, "3-impact", full=False)
        return dlg.locator("[data-testid=impact-counts]").inner_text()

    @step("4 send back: approved -> changes requested, audit + re-check")
    def _():
        dlg = pg.get_by_role("dialog")
        dlg.get_by_role("button", name="Send affected items back for review").click()
        expect(dlg.locator("[data-testid=impact-results]")).to_contain_text("Approved → Changes requested", timeout=15000)
        shot(pg, "4-sent-back", full=False)
        it = S["items"][ABOUT]
        assert api(ctx, f"/items/{it['id']}")["item"]["state"] == "changes_requested"
        hist = api(ctx, f"/items/{it['id']}/history")
        ev = next(e for e in hist if e["action"] == "fact_expired")
        assert ev["detail"]["fact"] == S["fact"] and ev["detail"]["from"] == "approved", ev
        pg.keyboard.press("Escape")
        wait_checked(ctx, [it["latest_version_id"]])
        fs = api(ctx, f"/versions/{it['latest_version_id']}/findings")
        out = [f for f in fs if f["claim_status"] == "outdated"]
        assert out, f"re-check did not flag the {S['fact']} claim"
        return f"{len(out)} outdated finding(s) after re-check: {[f['fact_code'] for f in out]}"

    @step("5 item page shows the new state and the fact event")
    def _():
        it = S["items"][ABOUT]
        pg.goto(f"{a.base}/items/{it['id']}")
        expect(pg.get_by_text("Changes requested").first).to_be_visible(timeout=20000)
        pg.get_by_role("tab", name="History").click()
        expect(pg.get_by_text("Fact expired").first).to_be_visible(timeout=10000)
        shot(pg, "5-item-history")

    @step("6 ledger now shows the about page claim as outdated under the expired fact")
    def _():
        pg.goto(a.base + f"/claims?fact={S['fact']}")
        grp = pg.locator(f"[data-testid=group-{S['fact']}]")
        expect(grp).to_contain_text("Outdated", timeout=20000)
        expect(grp).to_contain_text("Changes requested")
        shot(pg, "6-ledger-after")

    @step("7 dashboard: expiring / expired facts card")
    def _():
        pg.goto(a.base + "/dashboard")
        card = pg.locator("[data-testid=expiring-card]")
        expect(card).to_contain_text(S["fact"], timeout=20000)
        expect(card).to_contain_text("F-11")  # Diwali offer, expires 15 Nov 2026
        shot(pg, "7-dashboard")

    @step("8 non-admin sees the impact but cannot send back")
    def _():
        as_user("rohan")
        pg.goto(a.base + "/claims")
        pg.get_by_role("button", name="Impact of F-12").click()
        dlg = pg.get_by_role("dialog")
        expect(dlg).to_contain_text("Only a Brand Admin")
        assert dlg.get_by_role("button", name="Send affected items back for review").count() == 0
        r = ctx.request.post(f"{a.base}/api/facts/{api(ctx, '/claims/expiring')['expired'][0]['fact']['id']}/impact/send-back", data={})
        assert r.status == 403, r.status
        pg.keyboard.press("Escape")

    @step("9 no horizontal overflow on the ledger")
    def _():
        pg.goto(a.base + "/claims")
        pg.wait_for_timeout(1500)
        w = pg.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
        assert w[0] <= w[1] + 1, f"scrollWidth {w[0]} > {w[1]}"

    @step("10 no console errors")
    def _():
        assert not errors, "; ".join(errors[:5])
    br.close()

bad = [r for r in results if r[1] == "FAIL"]
print(f"\n{len(results)-len(bad)}/{len(results)} passed ({a.tag}, {a.width}px)")
json.dump(results, open(f"{a.shots}/claims-{a.tag}.json", "w"), indent=1)
sys.exit(1 if bad else 0)
