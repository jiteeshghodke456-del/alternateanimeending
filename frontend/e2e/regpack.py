"""India regulatory pack, end to end (Playwright, Python). Run against a server on a FRESH DB:

    python frontend/e2e/regpack.py --base http://127.0.0.1:7981 [--width 390] --tag x --shots <dir> [--recheck]

Demo brand: Brand Kit -> Rule packs -> Apply; the seeded flawed post's "Only today" urgency finding shows the
CCPA False Urgency citation; re-apply adds nothing; optional live re-check (--recheck, uses Jev/LLM);
Remove. Blank brand: the setup wizard's "Add the India regulatory pack", then a Hinglish post gets cited
pack findings. Prints PASS/FAIL per step, exits non-zero on any failure or console error.
"""
import argparse, json, re, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:7860")
ap.add_argument("--width", type=int, default=1280)
ap.add_argument("--tag", default="desktop")
ap.add_argument("--shots", default=str(Path(__file__).resolve().parents[1] / "screenshots"))
ap.add_argument("--recheck", action="store_true", help="re-run the flawed post's check live (Jev + LLM)")
a = ap.parse_args()
Path(a.shots).mkdir(parents=True, exist_ok=True)
mobile = a.width < 600
results, errors = [], []
S = {}


def shot(pg, name, full=True):
    pg.screenshot(path=f"{a.shots}/rp-{name}-{a.tag}.png", full_page=full)


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

    @step("1 demo admin: Brand Kit shows the Rule packs card, not applied")
    def _():
        pg.goto(a.base + "/")
        login(ctx, "admin")
        S["rules0"] = len(api(ctx, "/rules"))
        pg.goto(a.base + "/brand")
        card = pg.get_by_test_id("rule-packs")
        expect(card).to_be_visible(timeout=30000)
        expect(card).to_contain_text("India regulatory pack")
        expect(card).to_contain_text("Not applied")
        card.get_by_text("Rules and sources").click()
        expect(card.get_by_role("link", name=re.compile("Dark Patterns, 2023"))).to_be_visible()
        no_overflow(pg)
        shot(pg, "1-card")
        return f"{S['rules0']} brand rules"

    @step("2 Apply -> 9 rules added, linked to the brand's own rules")
    def _():
        pg.get_by_test_id("pack-apply").click()
        expect(pg.get_by_test_id("rule-packs")).to_contain_text("Applied: 9 rules", timeout=20000)
        rules = api(ctx, "/rules")
        assert len(rules) == S["rules0"] + 9, len(rules)
        st = next(x for x in api(ctx, "/rules/packs") if x["id"] == "india_regulatory")
        assert st["linked"].get("IN-CCPA-01") == ["R-CLAIM-03"], st["linked"]
        expect(pg.get_by_text("Regulation", exact=True).first).to_be_visible()
        return f"linked {st['linked']}"

    @step("3 re-apply (Update) adds nothing")
    def _():
        pg.get_by_test_id("pack-apply").click()
        expect(pg.get_by_text(re.compile("already up to date"))).to_be_visible(timeout=20000)
        assert len(api(ctx, "/rules")) == S["rules0"] + 9

    @step("4 flawed post: 'Only today' finding cites CCPA False Urgency")
    def _():
        items = api(ctx, "/items", params={"key": "demo_flawed_post"})
        it = items[0]
        S["item"], S["vid"] = it["id"], it["latest_version_id"]
        v = api(ctx, f"/versions/{S['vid']}")
        if v["check_status"] != "done":
            if v["check_status"] == "not_run":
                api(ctx, f"/versions/{S['vid']}/check", "post")
            wait_check(ctx, S["vid"])
        fs = api(ctx, f"/versions/{S['vid']}/findings")
        urg = [f for f in fs if f["rule_code"] == "R-CLAIM-03" and "only today" in f["quote"].lower()]
        assert urg, [(f["rule_code"], f["quote"][:30]) for f in fs]
        cit = urg[0]["regulations"][0]["citation"]
        assert cit["clause"].startswith("Annexure 1 (1): False Urgency"), cit
        pg.goto(f"{a.base}/items/{S['item']}")
        card = pg.locator("article").filter(has=pg.get_by_test_id("regulation-chip")).filter(has_text=re.compile("only today", re.I)).first
        expect(card).to_be_visible(timeout=30000)
        expect(card).to_contain_text("Guidelines for Prevention and Regulation of Dark Patterns, 2023")
        link = card.get_by_role("link", name=re.compile("Official source"))
        assert "ccpa.doca.gov.in" in (link.get_attribute("href") or "")
        card.scroll_into_view_if_needed()
        no_overflow(pg)
        card.screenshot(path=f"{a.shots}/rp-4-citation-{a.tag}.png")
        shot(pg, "4-item")
        return f"{len(fs)} findings"

    if a.recheck:
        @step("5 live re-check: urgency flagged once, still cited")
        def _():
            login(ctx, "creator")
            api(ctx, f"/versions/{S['vid']}/check", "post")
            time.sleep(1)
            wait_check(ctx, S["vid"])
            fs = api(ctx, f"/versions/{S['vid']}/findings")
            codes = [f["rule_code"] for f in fs]
            urg = [f for f in fs if f["rule_code"] == "R-CLAIM-03" and "only today" in f["quote"].lower()]
            assert urg and urg[0]["regulations"], codes
            pack = sorted({c for c in codes if c and c.startswith("IN-")})
            login(ctx, "admin")
            return f"{len(fs)} findings; pack findings: {pack or 'none'}; R-CLAIM-03 cites {urg[0]['regulations'][0]['code']}"

    @step("6 Remove -> pack rules gone, citation gone")
    def _():
        pg.goto(a.base + "/brand")
        pg.get_by_test_id("pack-remove").click()
        expect(pg.get_by_test_id("rule-packs")).to_contain_text("Not applied", timeout=20000)
        assert len(api(ctx, "/rules")) == S["rules0"]
        fs = api(ctx, f"/versions/{S['vid']}/findings")
        assert all(not f.get("regulations") for f in fs)

    @step("7 blank brand: setup wizard adds the pack; a Hinglish post gets cited findings")
    def _():
        api(ctx, "/workspaces", "post", data={"kind": "blank"})
        login(ctx, "admin")
        pg.goto(a.base + "/setup?step=3")
        btn = pg.get_by_test_id("setup-pack-apply")
        expect(btn).to_be_visible(timeout=30000)
        no_overflow(pg)
        btn.click()
        expect(btn).to_contain_text("India regulatory pack added", timeout=20000)
        shot(pg, "7-setup")
        rules = api(ctx, "/rules")
        assert sum(1 for r in rules if r["pack_id"] == "india_regulatory") == 9
        login(ctx, "creator")
        it = api(ctx, "/items", "post", multipart={"title": "Festive sale", "channel": "instagram",
                                                   "text": "Sirf aaj! Flat 30% off on filter coffee. Platform fee extra."})
        wait_check(ctx, it["version_id"])
        fs = api(ctx, f"/versions/{it['version_id']}/findings")
        by = {f["rule_code"]: f for f in fs}
        assert {"IN-CCPA-01", "IN-CCPA-02"} <= set(by), list(by)
        assert "False Urgency" in by["IN-CCPA-01"]["rule"]["citation"]["clause"]
        assert "Drip Pricing" in by["IN-CCPA-02"]["rule"]["citation"]["clause"]
        pg.goto(f"{a.base}/items/{it['item_id']}")
        expect(pg.get_by_test_id("regulation-chip").first).to_be_visible(timeout=30000)
        no_overflow(pg)
        shot(pg, "7-blank-item")
        return ", ".join(sorted(c for c in by if c))

    @step("8 no console errors")
    def _():
        assert not errors, "; ".join(errors[:5])
    br.close()

bad = [r for r in results if r[1] == "FAIL"]
print(f"\n{len(results)-len(bad)}/{len(results)} passed ({a.tag}, {a.width}px)")
json.dump(results, open(f"{a.shots}/regpack-{a.tag}.json", "w"), indent=1)
sys.exit(1 if bad else 0)
