"""AI-image provenance e2e (Playwright, Python): upload the AI-labelled demo fixture (a synthetic PNG with a
C2PA caBX manifest: Adobe Firefly + trainedAlgorithmicMedia) without and with an AI disclosure in the caption.

    python frontend/e2e/provenance.py --base http://127.0.0.1:7990 [--width 390] --tag x --shots /tmp/shots

Items are created as confidential, so the run makes zero external AI calls. Prints PASS/FAIL per step.
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
FIX = Path(__file__).resolve().parents[2] / "backend/tests/fixtures/provenance"
Path(a.shots).mkdir(parents=True, exist_ok=True)
mobile = a.width < 600
results, errors = [], []
S = {}


def shot(pg, name):
    pg.screenshot(path=f"{a.shots}/prov-{name}-{a.tag}.png", full_page=True)


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


def wait_check(ctx, vid, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = api(ctx, f"/versions/{vid}")
        if v["check_status"] in ("done", "error"):
            assert v["check_status"] == "done", "check errored"
            return v
        time.sleep(0.5)
    raise AssertionError("check timed out")


with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context(viewport={"width": a.width, "height": 900 if not mobile else 844})
    pg = ctx.new_page()
    S["pg"] = pg
    pg.on("console", lambda m: errors.append(f"console.{m.type}: {m.text[:160]}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {str(e)[:160]}"))
    pg.on("response", lambda r: errors.append(f"HTTP {r.status} {r.url[-80:]}") if r.status >= 500 else None)

    pg.goto(a.base + "/")
    creator = next(u for u in api(ctx, "/me")["users"] if u["role"] == "creator")
    api(ctx, "/demo/login", "post", data={"user_id": creator["id"]})

    def upload(fixture, caption, alt="A glass of cold brew at sunrise"):
        pg.goto(a.base + "/content/new")
        pg.get_by_role("tab", name="Image").click()
        pg.set_input_files("input[aria-label='Choose an image']", str(FIX / fixture))
        pg.fill("#caption", caption)
        pg.fill("#alt", alt)
        sw = pg.get_by_role("switch", name="Confidential")
        if sw.get_attribute("aria-checked") != "true":
            sw.click()
        pg.get_by_role("button", name="Check", exact=True).click()
        pg.wait_for_url(re.compile(r"/items/\d+"), timeout=30000)
        item = int(re.search(r"/items/(\d+)", pg.url).group(1))
        vid = api(ctx, f"/items/{item}")["item"]["latest_version_id"]
        v = wait_check(ctx, vid)
        pg.reload()
        expect(pg.locator("[data-testid=provenance-chip]")).to_be_visible(timeout=20000)
        return item, vid, v

    @step("1 AI image, caption without a label -> chip + R-AI-LABEL finding with a fix")
    def _():
        item, vid, v = upload("ai_c2pa_demo_banner.png", "Meet our new cold brew. Order today.")
        S["item"], S["v1"] = item, vid
        chip = pg.locator("[data-testid=provenance-chip]").inner_text()
        assert "AI-generated" in chip and "Adobe Firefly" in chip, chip
        panel = pg.locator("[data-testid=provenance-panel]").inner_text()
        assert "No AI label" in panel and "signature not verified" in panel, panel
        card = pg.locator("article", has_text="R-AI-LABEL")
        expect(card.first).to_be_visible(timeout=20000)
        txt = card.first.inner_text()
        assert "Content Credentials say it was made with Adobe Firefly" in txt, txt[:200]
        assert v["stats"]["external_calls"] == 0, v["stats"]["external_calls"]
        shot(pg, "1-unlabelled")
        return f"chip='{chip}'"

    @step("2 AI used panel: local check note, no external services")
    def _():
        pg.get_by_role("button", name="AI used").click()
        expect(pg.locator("[data-testid=provenance-local-note]")).to_be_visible(timeout=10000)
        body = pg.locator("[role=dialog]").inner_text()
        assert "No external services used" in body, body[:200]
        shot(pg, "2-ai-used")
        pg.keyboard.press("Escape")

    @step("3 accept the label fix -> resubmit -> v2 labelled, no finding")
    def _():
        card = pg.locator("article", has_text="R-AI-LABEL").first
        card.get_by_role("button", name=re.compile(r"Accept fix")).click()
        pg.wait_for_timeout(1200)
        pg.get_by_role("button", name=re.compile(r"Resubmit as version")).click()
        t0 = time.time()
        while time.time() - t0 < 20 and api(ctx, f"/items/{S['item']}")["item"]["latest_version_id"] == S["v1"]:
            time.sleep(0.3)
        v2 = api(ctx, f"/items/{S['item']}")["item"]["latest_version_id"]
        assert v2 != S["v1"], "no v2"
        v = wait_check(ctx, v2)
        assert v["text"].endswith("Image: AI-generated."), v["text"]
        pg.reload()
        expect(pg.locator("[data-testid=provenance-panel]")).to_contain_text("Labelled as AI-generated", timeout=20000)
        fs = [f for f in api(ctx, f"/versions/{v2}/findings") if f["rule_code"] == "R-AI-LABEL"]
        assert not fs, "R-AI-LABEL still flagged on v2"
        shot(pg, "3-v2-labelled")
        return v["text"]

    @step("4 AI image, caption WITH a disclosure -> labelled, no finding")
    def _():
        item, vid, v = upload("ai_c2pa_demo_banner.png", "Meet our new cold brew. #AIgenerated")
        expect(pg.locator("[data-testid=provenance-panel]")).to_contain_text("Labelled as AI-generated")
        assert pg.locator("article", has_text="R-AI-LABEL").count() == 0
        assert not [f for f in api(ctx, f"/versions/{vid}/findings") if f["rule_code"] == "R-AI-LABEL"]
        shot(pg, "4-disclosed")

    @step("5 image without metadata -> 'No provenance data', no finding")
    def _():
        item, vid, v = upload("no_metadata.png", "Meet our new cold brew.")
        expect(pg.locator("[data-testid=provenance-panel]")).to_contain_text("No provenance data (metadata may have been stripped)")
        assert not [f for f in api(ctx, f"/versions/{vid}/findings") if f["rule_code"] == "R-AI-LABEL"]

    @step("6 no horizontal overflow on item page")
    def _():
        pg.goto(f"{a.base}/items/{S['item']}")
        pg.wait_for_timeout(1500)
        w = pg.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
        assert w[0] <= w[1] + 1, f"scrollWidth {w[0]} > {w[1]}"

    @step("7 no console errors")
    def _():
        assert not errors, "; ".join(errors[:5])
    br.close()

bad = [r for r in results if r[1] == "FAIL"]
print(f"\n{len(results)-len(bad)}/{len(results)} passed ({a.tag}, {a.width}px)")
json.dump(results, open(f"{a.shots}/provenance-{a.tag}.json", "w"), indent=1)
sys.exit(1 if bad else 0)
