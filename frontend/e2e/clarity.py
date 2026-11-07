"""Rule clarity (Round 3 improvement 2) end to end (Playwright, Python). Uses live Jev + LLM (~$0.02).

    python frontend/e2e/clarity.py --base http://127.0.0.1:7860 [--width 390] [--dark] --tag x --shots <dir>

Brand Kit -> Rule clarity: check all rules, the 5 planted vague rules rank least clear, suggest a rewrite
for the least clear rule (diff + measured effect), apply it (new version + history), and the dashboard's
"Unclear guidelines" card links back. Exits non-zero on any failure or console error.
"""
import argparse, re, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:7860")
ap.add_argument("--width", type=int, default=1280)
ap.add_argument("--tag", default="desktop")
ap.add_argument("--dark", action="store_true")
ap.add_argument("--shots", default=str(Path(__file__).resolve().parents[1] / "screenshots"))
a = ap.parse_args()
Path(a.shots).mkdir(parents=True, exist_ok=True)
VAGUE = {"R-TONE-05", "R-MSG-03", "R-MSG-04", "R-MSG-05", "R-VIS-05"}
results, errors, S = [], [], {}


def shot(pg, name, full=True):
    pg.screenshot(path=f"{a.shots}/clarity-{name}-{a.tag}.png", full_page=full)


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


with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context(viewport={"width": a.width, "height": 900 if a.width >= 600 else 844},
                         color_scheme="dark" if a.dark else "light")
    pg = ctx.new_page()
    S["pg"] = pg
    pg.on("console", lambda m: errors.append(f"console.{m.type}: {m.text[:160]}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {str(e)[:160]}"))
    pg.on("response", lambda r: errors.append(f"HTTP {r.status} {r.url[-80:]}") if r.status >= 500 else None)

    @step("1 admin opens Brand Kit -> Rule clarity")
    def _():
        users = ctx.request.get(f"{a.base}/api/me").json()["users"]
        admin = next(u for u in users if u["role"] == "admin")
        r = ctx.request.post(f"{a.base}/api/demo/login", data={"user_id": admin["id"]})
        assert r.ok, r.text()
        pg.goto(a.base + "/brand?tab=clarity")
        expect(pg.get_by_role("heading", name="Where the guidelines are unclear")).to_be_visible(timeout=30000)
        n = pg.locator("li[id^=clarity-]").count()
        assert n >= 5, f"only {n} rule cards before the Jev run"
        shot(pg, "1-before")
        return f"{n} non-clear rules shown from wording alone"

    @step("2 check all rules with Jev; vague rules rank least clear")
    def _():
        pg.get_by_role("button", name=re.compile(r"Check all rules|Re-check all rules")).click()
        t0 = time.time()
        while time.time() - t0 < 90:
            st = ctx.request.get(f"{a.base}/api/rules/clarity/run").json()
            if st["status"] != "running":
                break
            time.sleep(1)
        assert st["status"] == "done", st
        expect(pg.get_by_role("button", name="Re-check all rules")).to_be_visible(timeout=15000)
        rows = ctx.request.get(f"{a.base}/api/rules/clarity").json()
        bottom = [r["code"] for r in rows[:5]]
        assert set(bottom) == VAGUE, f"least clear 5 = {bottom}"
        bad = [(r["code"], r["band"]) for r in rows if (r["band"] != "clear") != (r["code"] in VAGUE)]
        assert not bad, f"band mismatches {bad}"
        expect(pg.locator(f"#clarity-{rows[0]['code']}")).to_be_visible()
        S["first"] = rows[0]["code"]
        shot(pg, "2-ranked")
        return f"{st['total']} rules in {st['elapsed_ms'] / 1000:.1f}s for ${st['cost_usd']:.4f}; least clear {bottom}"

    @step("3 Jev details table opens")
    def _():
        card = pg.locator(f"#clarity-{S['first']}")
        card.get_by_text(re.compile(r"How Jev read \d wordings")).click()
        expect(card.locator("table")).to_be_visible()
        return f"{card.locator('tbody tr').count()} test texts"

    @step("4 suggest clearer wording -> diff + measured effect")
    def _():
        card = pg.locator(f"#clarity-{S['first']}")
        card.get_by_role("button", name="Suggest clearer wording").click()
        expect(card.get_by_text("Suggested wording")).to_be_visible(timeout=60000)
        expect(card.get_by_text("Measured effect (same test texts)")).to_be_visible()
        assert card.locator("ins").count() > 0 or card.get_by_text("Suggested", exact=True).count() > 0, "no old/new wording"
        card.scroll_into_view_if_needed()
        shot(pg, "4-rewrite")
        rw = next(r for r in ctx.request.get(f"{a.base}/api/rules/clarity").json() if r["code"] == S["first"])["rewrite"]
        S["rw"] = rw
        return (f"score {rw['score_before']:.0f} -> {rw['score_after']:.0f}; disagreement {rw['disagreement_before']} -> "
                f"{rw['disagreement_after']}; intent_changed {rw['intent_changed']}")

    @step("5 apply -> new version, history kept")
    def _():
        card = pg.locator(f"#clarity-{S['first']}")
        card.get_by_role("button", name=re.compile(r"Apply as version \d")).click()
        expect(pg.get_by_text(re.compile(r"is now version 2"))).to_be_visible(timeout=15000)
        row = next(r for r in ctx.request.get(f"{a.base}/api/rules/clarity").json() if r["code"] == S["first"])
        assert row["version"] == 2 and row["history"] and row["statement"] == S["rw"]["statement"], row["version"]
        pg.get_by_role("button", name=re.compile(r"Show the \d+ clear rules too")).click()
        expect(pg.locator(f"#clarity-{S['first']}").get_by_text("Rewritten from version 1")).to_be_visible()
        pg.locator(f"#clarity-{S['first']}").scroll_into_view_if_needed()
        shot(pg, "5-applied", full=False)
        return f"{S['first']} v2: {row['score']:.0f} ({row['band']})"

    @step("6 dashboard card lists the least clear rules and links back")
    def _():
        pg.goto(a.base + "/dashboard")
        card = pg.locator("section[aria-labelledby=unclear-h]")
        expect(card).to_be_visible(timeout=30000)
        n = card.locator("li").count()
        assert 1 <= n <= 3, f"{n} rows"
        card.scroll_into_view_if_needed()
        shot(pg, "6-dashboard", full=False)
        card.locator("li a").first.click()
        pg.wait_for_url(re.compile(r"/brand\?tab=clarity"))
        expect(pg.get_by_role("heading", name="Where the guidelines are unclear")).to_be_visible()
        return f"{n} rules on the card"

    @step("7 no horizontal scroll")
    def _():
        sw = pg.evaluate("document.documentElement.scrollWidth")
        assert sw <= a.width + 1, f"scrollWidth {sw} > {a.width}"
        return f"scrollWidth {sw}"

    br.close()

errs = [e for e in errors if "favicon" not in e]
for e in errs:
    print("ERROR", e)
ok = all(r[1] == "PASS" for r in results) and not errs
print(f"{'ALL PASS' if ok else 'FAILED'}: {sum(r[1] == 'PASS' for r in results)}/{len(results)} steps, {len(errs)} console/server errors")
sys.exit(0 if ok else 1)
