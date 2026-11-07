"""Brand Gate "Web page" source (Playwright, Python). Run against a server whose page fetcher can reach the web:

    python frontend/e2e/webpage.py --base http://127.0.0.1:7860 --url https://basecamp.com/ [--width 390] [--dark]

Steps: validation messages, a blocked private address (human error), a real page -> item page with the source
card, highlighted text, images and accessibility findings, and the AI-used drawer's fetch entry.
Prints one PASS/FAIL line per step and exits non-zero on any failure or console error.
"""
import argparse, json, re, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:7860")
ap.add_argument("--url", default="https://basecamp.com/")
ap.add_argument("--width", type=int, default=1280)
ap.add_argument("--dark", action="store_true")
ap.add_argument("--tag", default="desktop")
ap.add_argument("--shots", default=str(Path(__file__).resolve().parents[1] / "screenshots"))
a = ap.parse_args()
Path(a.shots).mkdir(parents=True, exist_ok=True)
results, errors = [], []
S = {}


def shot(pg, name, full=True):
    pg.screenshot(path=f"{a.shots}/web-{name}-{a.tag}.png", full_page=full)


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


with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context(viewport={"width": a.width, "height": 900 if a.width >= 600 else 844},
                         color_scheme="dark" if a.dark else "light")
    if a.dark:
        ctx.add_init_script("try { localStorage.setItem('bg-theme', 'dark') } catch (e) {}")
    pg = ctx.new_page()
    S["pg"] = pg
    pg.on("console", lambda m: errors.append(f"console.{m.type}: {m.text[:160]}") if m.type == "error" and "422" not in m.text and "400" not in m.text else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {str(e)[:160]}"))
    pg.on("response", lambda r: errors.append(f"HTTP {r.status} {r.url[-80:]}") if r.status >= 500 else None)

    pg.goto(a.base + "/")
    users = api(ctx, "/me")["users"]
    creator = next(u["id"] for u in users if u["role"] == "creator")
    api(ctx, "/demo/login", "post", data={"user_id": creator})

    @step("1 web tab: validation messages")
    def _():
        pg.goto(a.base + "/content/new")
        pg.get_by_role("tab", name="Web page").click()
        field = pg.get_by_label("Web page address")
        field.fill("ftp://example.com/file")
        expect(pg.get_by_role("alert")).to_contain_text("Only http:// and https://")
        field.fill("https://user:pw@example.com/")
        expect(pg.get_by_role("alert")).to_contain_text("user name or password")
        field.fill("http://example.com:6379/")
        expect(pg.get_by_role("alert")).to_contain_text("Port 6379")
        assert pg.get_by_role("button", name="Fetch and check").is_disabled()
        shot(pg, "1-validation", full=False)

    @step("2 blocked private address -> human message")
    def _():
        pg.get_by_label("Web page address").fill("http://169.254.169.254/latest/meta-data/")
        pg.get_by_role("button", name="Fetch and check").click()
        expect(pg.locator("#web-url-help")).to_contain_text("private or internal network", timeout=15000)  # inline, not only a toast
        assert "/content/new" in pg.url
        shot(pg, "2-blocked", full=False)

    @step("3 real page -> item with source card and findings")
    def _():
        pg.get_by_label("Web page address").fill(a.url)
        t0 = time.time()
        pg.get_by_role("button", name="Fetch and check").click()
        pg.wait_for_url(re.compile(r"/items/\d+"), timeout=40000)
        S["item"] = int(re.search(r"/items/(\d+)", pg.url).group(1))
        card = pg.get_by_role("region", name="Web page source")
        expect(card).to_be_visible(timeout=20000)
        expect(card).to_contain_text("Fetched")
        expect(card).to_contain_text("no third-party service")
        vid = api(ctx, f"/items/{S['item']}")["item"]["latest_version_id"]
        while time.time() - t0 < 150:
            v = api(ctx, f"/versions/{vid}")
            if v["check_status"] in ("done", "error"):
                break
            time.sleep(1)
        assert v["check_status"] == "done", v["check_status"]
        assert v["kind"] == "web" and v["source"]["paragraphs"] > 0
        pg.reload()
        pg.wait_for_selector("article", timeout=30000)
        n = pg.locator("article").count()
        fs = api(ctx, f"/versions/{vid}/findings")
        acc = [f for f in fs if f["category"] == "accessibility"]
        S["v"] = v
        shot(pg, "3-item")
        return (f"{n} finding cards, {len(acc)} accessibility; {v['source']['paragraphs']} paragraphs, {v['source']['images']} images; "
                f"{time.time() - t0:.1f}s; ${v['stats'].get('cost_usd')}")

    @step("4 AI-used drawer lists the page fetch")
    def _():
        pg.get_by_role("button", name=re.compile("AI used", re.I)).first.click()
        expect(pg.get_by_text(re.compile(r"Fetched https?://.*\(no third-party service\)"))).to_be_visible(timeout=10000)
        shot(pg, "4-ai-used", full=False)
        pg.keyboard.press("Escape")

    @step("5 no horizontal overflow")
    def _():
        for path in ("/content/new", f"/items/{S['item']}"):
            pg.goto(a.base + path)
            pg.wait_for_timeout(1200)
            w = pg.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
            assert w[0] <= w[1] + 1, f"{path}: scrollWidth {w[0]} > {w[1]}"

    @step("6 no console errors")
    def _():
        assert not errors, "; ".join(errors[:5])
    br.close()

bad = [r for r in results if r[1] == "FAIL"]
print(f"\n{len(results)-len(bad)}/{len(results)} passed ({a.tag}, {a.width}px)")
json.dump(results, open(f"{a.shots}/webpage-{a.tag}.json", "w"), indent=1)
sys.exit(1 if bad else 0)
