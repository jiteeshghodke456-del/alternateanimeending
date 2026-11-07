"""Landing page keyboard check (Playwright, Python): every CTA and role card is ONE tab stop, a real
<button> with a visible focus ring, and works with Enter and with Space.

    python frontend/e2e/landing_keyboard.py http://127.0.0.1:7860

Each case uses a fresh browser context (no cookie), tabs from the top of the page to the control,
presses the key and expects the route to change. Exits non-zero on any failure.
"""
import sys

from playwright.sync_api import sync_playwright

base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:7860"
CASES = [
    ("Try the demo brand", "Enter", "/content/new"), ("Try the demo brand", "Space", "/content/new"),
    ("Start fresh with your brand", "Enter", "/setup"), ("Start fresh with your brand", "Space", "/setup"),
    ("Rohan", "Enter", "/content/new"), ("Kabir", "Space", "/review"),
]
FOCUSED = """() => { const e = document.activeElement, s = getComputedStyle(e);
  return { tag: e.tagName, text: (e.innerText || '').trim().slice(0, 60), outline: s.outlineStyle + ' ' + s.outlineWidth } }"""

ok = True
with sync_playwright() as p:
    browser = p.chromium.launch()
    for label, key, expect in CASES:
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        pg = ctx.new_page()
        pg.goto(base + "/", wait_until="networkidle")
        stops, hit = [], None
        for _ in range(30):
            pg.keyboard.press("Tab")
            f = pg.evaluate(FOCUSED)
            stops.append(f)
            if label in f["text"]:
                hit = f
                break
        if hit is None:
            print("FAIL", label, "not reachable with Tab")
            ok = False
            ctx.close()
            continue
        problems = []
        if hit["tag"] != "BUTTON":
            problems.append(f"first tab stop is a {hit['tag']}, not a button")
        if not hit["outline"].startswith("solid") or hit["outline"].endswith(" 0px"):
            problems.append(f"no visible focus ring ({hit['outline']})")
        pg.keyboard.press(key)
        for _ in range(80):  # up to 8 s; wait_for_timeout keeps Playwright's event loop running
            if expect in pg.url:
                break
            pg.wait_for_timeout(100)
        else:
            problems.append(f"{key} did nothing (still at {pg.url.replace(base, '') or '/'})")
        print("FAIL" if problems else "PASS", f"{label!r} + {key}", "; ".join(problems))
        ok &= not problems
        ctx.close()
    browser.close()
sys.exit(0 if ok else 1)
