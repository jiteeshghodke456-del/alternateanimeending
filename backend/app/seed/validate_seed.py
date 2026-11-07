"""Validate the demo seed data. Run: python backend/app/seed/validate_seed.py  (needs pdfplumber, opencv-python-headless, numpy)"""
import csv, json, logging, re, sys
from collections import Counter
from pathlib import Path

SEED = Path(__file__).resolve().parent
EVAL = SEED.parent / "eval" / "dataset.jsonl"
TODAY = "2026-11-01"
errors: list[str] = []
logging.getLogger("pdfminer").setLevel(logging.ERROR)


def check(cond, msg):
    if not cond:
        errors.append(msg)
    return cond


def norm(s): return re.sub(r"\s+", " ", s).strip()
def load(n): return json.loads((SEED / n).read_text(encoding="utf-8"))


def sift_inliers(template, scene, ratio=0.75):
    import cv2, numpy as np
    a = cv2.imread(str(template), cv2.IMREAD_GRAYSCALE); b = cv2.imread(str(scene), cv2.IMREAD_GRAYSCALE)
    s = cv2.SIFT_create(); k1, d1 = s.detectAndCompute(a, None); k2, d2 = s.detectAndCompute(b, None)
    good = [m for m, n in cv2.BFMatcher().knnMatch(d1, d2, k=2) if m.distance < ratio * n.distance]
    if len(good) < 4: return 0
    src = np.float32([k1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2); dst = np.float32([k2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    return int(mask.sum()) if mask is not None else 0


def main():
    brand = load("brand.json")
    check(brand["tone_labels"] == ["warm", "confident", "playful", "formal", "pushy", "sarcastic"], "brand tone_labels")
    check(set(brand["channel_targets"]) == {"instagram", "email", "website", "linkedin"}, "brand channels")
    check("fictional" in brand["name"].lower(), "brand name should say fictional")
    users = load("users.json")
    check([(u["id"], u["name"], u["role"]) for u in users] == [(1, "Asha", "admin"), (2, "Rohan", "creator"), (3, "Meera", "reviewer"), (4, "Kabir", "approver")], "users.json")

    rules = load("rules.json")
    cats = {"claims", "tone", "terminology", "messaging", "visual", "accessibility", "legal"}
    check(24 <= len(rules) <= 32, f"rule count {len(rules)}")
    codes = [r["code"] for r in rules]
    check(len(set(codes)) == len(codes), "duplicate rule codes")
    for r in rules:
        for k in ("code", "category", "severity", "statement", "source_quote", "page", "check_method", "params", "examples_violating", "examples_ok"):
            check(k in r, f"{r.get('code')} missing {k}")
        check(r["category"] in cats, f"{r['code']} category")
        check(r["severity"] in ("blocking", "major", "minor"), f"{r['code']} severity")
        check(r["check_method"] in ("tier0", "jev", "claim", "visual"), f"{r['code']} check_method")
        check(len(r["examples_violating"]) == 2 and len(r["examples_ok"]) == 2, f"{r['code']} examples")
    check(sum(1 for r in rules if r["params"].get("vague")) == 5, "need exactly 5 vague rules")
    check(set(r["category"] for r in rules) == cats, "all categories used")

    import pdfplumber
    with pdfplumber.open(SEED / "guideline.pdf") as pdf:
        pages = [norm(p.extract_text() or "") for p in pdf.pages]
    check(8 <= len(pages) <= 10, f"guideline pages {len(pages)}")
    check("fictional demo brand" in pages[0].lower(), "cover must say Fictional demo brand")
    for r in rules:
        pg = r["page"]
        check(1 <= pg <= len(pages) and norm(r["source_quote"]) in pages[pg - 1], f"{r['code']} quote not found on page {pg}")

    tiers = load("terms.json")
    check(12 <= len(tiers) <= 20 and all(t["kind"] in ("banned", "preferred", "capitalise") and {"value", "replacement"} <= set(t) for t in tiers), "terms.json")
    pal = load("palette.json"); check(5 <= len(pal) <= 6 and all(re.fullmatch(r"#[0-9A-Fa-f]{6}", p["hex"]) for p in pal), "palette.json")
    check(isinstance(load("fonts.json"), list) and load("fonts.json"), "fonts.json")

    with open(SEED / "facts.csv", encoding="utf-8") as f:
        facts = list(csv.DictReader(f))
    check(list(facts[0].keys()) == ["code", "statement", "value", "source", "valid_until"], "facts columns")
    check(22 <= len(facts) <= 30, f"facts count {len(facts)}")
    expired = [x["code"] for x in facts if x["valid_until"] < TODAY]
    check(len(expired) == 3, f"expired facts must be exactly 3, got {expired}")
    check(all(x["valid_until"] >= "2026-11-01" for x in facts if x["code"] not in expired), "non-expired facts must be valid")
    fact_codes = {x["code"] for x in facts}

    # content
    items = json.loads((SEED / "content" / "items.json").read_text(encoding="utf-8"))
    check(len(items) == 8, f"content items {len(items)}")
    check(all({"title", "channel", "creator", "state", "confidential", "text", "file"} <= set(i) for i in items), "item keys")
    check({"draft", "in_review", "approved", "changes_requested"} <= {i["state"] for i in items}, "mixed states")
    check(any(i["creator"] == "Kabir" for i in items), "a Kabir item")
    for i in items:
        if i["file"]: check((SEED / "content" / i["file"]).exists(), f"missing {i['file']}")
    demo = [i for i in items if i.get("key") == "demo_flawed_post"]
    if check(len(demo) == 1, "demo_flawed_post item"):
        d = demo[0]; t = d["text"]
        check(d["state"] == "draft" and d["creator"] == "Rohan" and d["channel"] == "instagram", "demo post meta")
        check(len(t.split()) < 120, "demo post < 120 words")
        for needle in ("#1", "Steam Classic", "Cheap", "Only today", "4.8 stars", "MONSOON20"):
            check(needle in t, f"demo post lacks {needle}")
        check(not re.search(r"(till|until|ends?)\s+\d", t), "demo post must not state an end date")
    from PIL import Image
    for n in ("ad_good", "ad_offpalette", "ad_wronglogo"):
        check(Image.open(SEED / "content" / f"{n}.png").size == (1080, 1080), f"{n} size")
    with pdfplumber.open(SEED / "content" / "promo.pdf") as pdf:
        check(len(pdf.pages) == 1, "promo.pdf one page")

    # logo fidelity
    good = sift_inliers(SEED / "logo.png", SEED / "content" / "ad_good.png")
    good2 = sift_inliers(SEED / "logo.png", SEED / "content" / "ad_offpalette.png")
    wrong = sift_inliers(SEED / "logo.png", SEED / "content" / "ad_wronglogo.png")
    print(f"SIFT inliers (true logo template): ad_good={good} ad_offpalette={good2} ad_wronglogo={wrong}")
    check(good >= 40 and good2 >= 40, "true logo inliers < 40")
    check(wrong < 15, f"wrong logo inliers {wrong} >= 15")

    # personas (optional)
    pp = SEED / "personas.json"
    if pp.exists():
        pers = json.loads(pp.read_text(encoding="utf-8"))
        check(len(pers) == 240, f"personas {len(pers)}")
        check(len(Counter(p["segment"] for p in pers)) == 6, "6 persona segments")
        check(all(len(p["persona"]) <= 300 and p["_source"] == "nvidia/Nemotron-Personas-India (CC-BY-4.0)" for p in pers), "persona fields")

    # eval dataset
    rows = [json.loads(l) for l in EVAL.read_text(encoding="utf-8").splitlines() if l.strip()]
    check(len(rows) == 60, f"eval rows {len(rows)}")
    sp = Counter(r["split"] for r in rows); check(sp == {"dev": 42, "test": 18}, f"split {sp}")
    clean = sum(1 for r in rows if not r["labels"]); check(13 <= clean <= 17, f"clean items {clean}")
    check(len({r["id"] for r in rows}) == 60, "eval ids unique")
    check(len({r["channel"] for r in rows}) == 4, "eval channels")
    valid = set(codes) | fact_codes
    for r in rows:
        check(r["labelled_by"] == "draft-ai" and r["channel"] in brand["channel_targets"], f"{r['id']} meta")
        for l in r["labels"]:
            check(l["code"] in valid, f"{r['id']} unknown code {l['code']}")
            check(l["span"] in r["text"], f"{r['id']} span not in text: {l['span']!r}")

    print(f"rules={len(rules)} vague=5 pages={len(pages)} facts={len(facts)} expired={expired} items={len(items)} eval={len(rows)} (clean {clean})")
    if errors:
        print("FAILED:"); [print(" -", e) for e in errors]; sys.exit(1)
    print("OK: seed validation passed")


if __name__ == "__main__":
    main()
