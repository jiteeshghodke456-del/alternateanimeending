"""Brand Gate guided tour (Playwright, Python). Runs the whole tour from the landing page with
"Do it for me" on every step, and checks each step's outcome against the API.

    python frontend/e2e/tour.py --base http://localhost:7860 --width 1280
    python frontend/e2e/tour.py --base http://localhost:7860 --width 390 --shots /tmp/tour

Each run opens its own fresh demo workspace, so one server can serve several runs.
Prints one PASS/FAIL line per step and exits non-zero on any failure or console error.
"""
import argparse, json, re, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:7860")
ap.add_argument("--width", type=int, default=1280)
ap.add_argument("--shots", default=str(Path(__file__).resolve().parents[1] / "screenshots"))
ap.add_argument("--tag", default=None)
a = ap.parse_args()
mobile = a.width < 600
tag = a.tag or ("mobile" if mobile else "desktop")
Path(a.shots).mkdir(parents=True, exist_ok=True)
results, errors = [], []
TOTAL = 9


def run(name, fn):
    t0 = time.time()
    try:
        note = fn()
        results.append((name, "PASS", note or "", round(time.time() - t0, 1)))
    except Exception as e:  # noqa: BLE001
        results.append((name, "FAIL", str(e).splitlines()[0][:220], round(time.time() - t0, 1)))
        try:
            pg.screenshot(path=f"{a.shots}/tour-FAIL-{re.sub(r'[^a-z0-9]+', '_', name.lower())}-{tag}.png")
        except Exception:  # noqa: BLE001
            pass
    r = results[-1]
    print(f"{r[1]}  {name}  ({r[3]} s)  {r[2]}", flush=True)
    return r[1] == "PASS"


with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context(viewport={"width": a.width, "height": 844 if mobile else 900})
    pg = ctx.new_page()
    pg.on("console", lambda m: errors.append(f"console.{m.type}: {m.text[:160]}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {str(e)[:160]}"))
    pg.on("response", lambda r: errors.append(f"HTTP {r.status} {r.url[-80:]}") if r.status >= 500 else None)
    card = pg.locator("[data-tour-card]")

    def api(path):
        r = ctx.request.get(f"{a.base}/api{path}")
        assert r.ok, f"{path} {r.status} {r.text()[:160]}"
        return r.json()

    def at_step(n, timeout=60000):
        expect(card).to_have_attribute("data-tour-step", str(n), timeout=timeout)
        expect(card.get_by_test_id("tour-counter")).to_have_text(f"Step {n} of {TOTAL}")
        # wait until the step has opened (signed in, navigated)
        pg.wait_for_function("() => { const c = document.querySelector('[data-tour-card]'); return c && c.dataset.tourStatus !== 'entering'; }", timeout=timeout)
        st = card.get_attribute("data-tour-status")
        assert st != "error", "step error: " + card.inner_text()[:200]

    def do_it(timeout):
        card.get_by_role("button", name=re.compile("^Do it for me")).click()
        pg.wait_for_function(
            "() => { const s = document.querySelector('[data-tour-card]')?.dataset.tourStatus; return s === 'done' || s === 'error'; }",
            timeout=timeout)
        assert card.get_attribute("data-tour-status") == "done", "step error: " + card.inner_text()[:240]

    def who():
        return card.get_by_test_id("tour-who").inner_text()

    def shot(n, name):
        pg.wait_for_timeout(700)  # let the highlight settle
        pg.screenshot(path=f"{a.shots}/tour-{n}-{name}-{tag}.png")
        w = pg.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
        assert w[0] <= w[1] + 1, f"horizontal overflow at step {n}: {w}"
        # the card never hides the step's highlighted element completely
        hl = pg.get_by_test_id("tour-highlight")
        if hl.count():
            hb, cb = hl.bounding_box(), card.bounding_box()
            if hb and cb:
                ix = max(0, min(hb["x"] + hb["width"], cb["x"] + cb["width"]) - max(hb["x"], cb["x"]))
                iy = max(0, min(hb["y"] + hb["height"], cb["y"] + cb["height"]) - max(hb["y"], cb["y"]))
                assert ix * iy < 0.9 * hb["width"] * hb["height"], f"card covers the highlight at step {n}"

    def nxt():
        card.get_by_role("button", name="Next").click()

    def item_url():
        m = re.search(r"/items/(\d+)", pg.url)
        assert m, f"not on an item page: {pg.url}"
        return int(m.group(1))

    S = {"t0": time.time()}

    def s0():
        pg.goto(a.base + "/")
        btn = pg.get_by_role("button", name="Take the 2-minute tour")
        expect(btn).to_be_visible(timeout=30000)
        btn.click()
        at_step(1)
        txt = card.inner_text()
        for k in ("Audience Preview", "Logo Fidelity", "Guideline PDF import", "the code decides"):
            assert k in txt, f"intro misses {k!r}"
        assert "Rohan" in who() and "Creator" in who(), who()
        assert api("/workspace")["kind"] == "demo"
        shot(1, "intro")
        return who()

    def s2():
        nxt(); at_step(2)
        expect(pg).to_have_url(re.compile(r"/content/new"))
        do_it(180000)
        S["item"] = item_url()
        it = api(f"/items/{S['item']}")
        S["v1"] = it["item"]["latest_version_id"]
        v = api(f"/versions/{S['v1']}")
        assert v["check_status"] == "done"
        fs = [f for f in api(f"/versions/{S['v1']}/findings") if f["band"] != "pass"]
        assert len(fs) >= 5, f"only {len(fs)} findings"
        expect(pg.locator("article").first).to_be_visible(timeout=20000)
        S["cost_check1"] = v["stats"].get("cost_usd", 0)
        shot(2, "check")
        return f"{len(fs)} findings, {v['stats'].get('elapsed_ms')} ms, ${v['stats'].get('cost_usd')}"

    def s3():
        nxt(); at_step(3)
        expect(pg.locator("[data-tour=first-blocking]")).to_be_visible(timeout=20000)
        expect(pg.get_by_test_id("tour-highlight")).to_be_visible(timeout=10000)
        txt = card.inner_text()
        assert re.search(r"[RF]-\w+", txt), "no rule code in the card: " + txt[:200]
        assert re.search(r"0\.\d\d", txt), "no probability in the card"
        shot(3, "finding")
        return txt.split("\n")[3][:120] if len(txt.split("\n")) > 3 else ""

    def s4():
        nxt(); at_step(4)
        do_it(180000)
        it = api(f"/items/{S['item']}")
        assert len(it["versions"]) >= 2, "no version 2"
        S["v2"] = it["item"]["latest_version_id"]
        v1o = [f for f in api(f"/versions/{S['v1']}/findings") if f["band"] != "pass" and f["severity"] == "blocking" and f["status"] == "open"]
        v2 = [f for f in api(f"/versions/{S['v2']}/findings") if f["band"] != "pass" and f["status"] == "open"]
        assert not [f for f in v2 if f["severity"] == "blocking"], "blocking findings left in v2"
        expect(pg.locator("[data-tour=diff]")).to_be_visible(timeout=20000)
        S["cost_check2"] = api(f"/versions/{S['v2']}")["stats"].get("cost_usd", 0)
        shot(4, "v2")
        return f"v2 open findings {len(v2)} (v1 blocking left open {len(v1o)})"

    def s5():
        nxt(); at_step(5)
        do_it(240000)
        a1, a2 = api(f"/versions/{S['v1']}/audience"), api(f"/versions/{S['v2']}/audience")
        assert a1["status"] == "done" and a2["status"] == "done", (a1["status"], a2["status"])
        assert a2["compare_to_previous_version"], "no v1 vs v2 compare"
        expect(pg.locator("[data-tour=audience-compare]")).to_be_visible(timeout=30000)
        S["cost_aud"] = (a1.get("cost_usd") or 0) + (a2.get("cost_usd") or 0)
        shot(5, "audience")
        return f"mean {a1['overall']['mean_score']:.2f} -> {a2['overall']['mean_score']:.2f}, {len(a2['segments'])} groups, ${S['cost_aud']:.4f}"

    def s6():
        nxt(); at_step(6)
        pg.wait_for_selector("svg polygon", timeout=30000)
        assert pg.locator("text=Genuine logo").count() > 0, "no 'Genuine logo' label"
        good = item_url()
        shot(6, "logo-good")
        do_it(30000)
        pg.wait_for_function(f"() => /\\/items\\/(?!{good}\\b)\\d+/.test(location.pathname)", timeout=15000)
        expect(pg.get_by_text(re.compile("Altered logo|Logo not found")).first).to_be_visible(timeout=30000)
        # Task B: the logo finding has no text to rewrite, so no "use Edit" advice and no Edit button
        assert pg.get_by_text("Use Edit to write your own").count() == 0, "image finding tells the user to use Edit"
        shot(6, "logo-wrong")
        return "genuine outline + wrong-logo finding"

    def s7():
        nxt(); at_step(7)
        assert "Asha" in who() and "Admin" in who(), who()
        expect(pg).to_have_url(re.compile(r"/brand"))
        do_it(150000)
        expect(pg.locator("[data-tour=import-summary]")).to_be_visible(timeout=10000)
        txt = card.inner_text()
        m = re.search(r"(\d+) rules proposed, (\d+) quotes verified", txt)
        assert m and int(m.group(1)) > 0 and int(m.group(2)) > 0, txt[:200]
        shot(7, "pdf-import")
        return m.group(0)

    def s8():
        nxt(); at_step(8)
        do_it(90000)
        it = api(f"/items/{S['item']}")["item"]
        assert it["state"] == "approved", it["state"]
        assert "Kabir" in who() and "Approver" in who(), who()
        coms = api(f"/items/{S['item']}/comments")
        meera = next(u["id"] for u in api("/me")["users"] if u["role"] == "reviewer")
        assert any(c["user_id"] == meera for c in coms), "no comment from Meera"
        own = item_url()
        assert own != S["item"], "not on Kabir's own item"
        btn = pg.get_by_role("button", name="Approve")
        expect(btn).to_have_count(1, timeout=15000)
        assert btn.is_disabled(), "Approve enabled on Kabir's own item"
        assert "server refuses it too" in card.inner_text(), "no server refusal shown"
        shot(8, "workflow")
        return f"approved; own item {own} blocked"

    def s9():
        nxt(); at_step(9)
        txt = card.inner_text()
        m = re.search(r"\$(\d+\.\d+)", txt)
        S["cost_ui"] = float(m.group(1)) if m else None
        expect(card.get_by_role("button", name="Now try your own brand")).to_be_visible()
        shot(9, "finish")
        card.get_by_role("button", name="Explore freely").click()
        expect(card).to_have_count(0, timeout=5000)
        return f"tour cost shown ${S['cost_ui']}; total {time.time() - S['t0']:.0f} s"

    def s10():
        # Task B: on the approved item, findings are read-only with a quiet outcome line, even for its creator
        rohan = next(u["id"] for u in api("/me")["users"] if u["role"] == "creator")
        pg.evaluate("""async (uid) => {
            const ws = sessionStorage.getItem('bg-ws');
            const r = await fetch('/api/demo/login', {method: 'POST', credentials: 'include',
              headers: {'Content-Type': 'application/json', ...(ws ? {'X-BG-Workspace': ws} : {})}, body: JSON.stringify({user_id: uid})});
            sessionStorage.setItem('bg-token', (await r.json()).token);
        }""", rohan)
        pg.goto(f"{a.base}/items/{S['item']}?v={S['v2']}")
        expect(pg.locator("article").first).to_be_visible(timeout=20000)
        arts = pg.locator("article")
        for name in ("Accept fix", "Edit", "Dismiss"):
            assert arts.get_by_role("button", name=re.compile(f"^{name}")).count() == 0, f"{name} shown on an approved item"
        assert pg.locator("[data-testid=manual-fix]").count() == 0, "manual-fix banner on an approved item"
        outs = pg.locator("[data-testid=finding-outcome]").all_inner_texts()
        assert outs, "no outcome line"
        pg.goto(f"{a.base}/items/{S['item']}?v={S['v1']}")
        expect(pg.locator("[data-testid=finding-outcome]").first).to_be_visible(timeout=20000)
        v1outs = pg.locator("[data-testid=finding-outcome]").all_inner_texts()
        assert any("Fixed in v2" in t for t in v1outs), v1outs[:3]
        shot("10", "approved-readonly")
        w = pg.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
        assert w[0] <= w[1] + 1, f"horizontal overflow {w}"
        return f"v2: {sorted(set(outs))}; v1: {len(v1outs)} outcome lines"

    def s11():
        assert not errors, "; ".join(errors[:5])

    ok = True
    for name, fn in [("1 intro", s0), ("2 check (Rohan)", s2), ("3 read a finding", s3), ("4 accept + v2", s4),
                     ("5 audience v1 vs v2", s5), ("6 logo fidelity", s6), ("7 PDF import (Asha)", s7),
                     ("8 workflow (Rohan, Meera, Kabir)", s8), ("9 finish", s9)]:
        ok = run(name, fn) and ok
        if not ok and name.startswith(("1", "2")):
            break
    if "item" in S and "v2" in S:
        run("approved item: findings read-only (Task B)", s10)
    run("no console errors", s11)
    br.close()

bad = [r for r in results if r[1] == "FAIL"]
print(f"\n{len(results) - len(bad)}/{len(results)} passed ({tag}, {a.width}px)")
json.dump({"results": results, "costs": {k: v for k, v in S.items() if k.startswith("cost")}}, open(f"{a.shots}/tour-{tag}.json", "w"), indent=1)
sys.exit(1 if bad else 0)
