"""Indian-language validation set: runs the real pipeline on tests/fixtures/indic_eval.jsonl.

    cd backend && DB_PATH=/tmp/x.db python3 scripts/indic_eval.py [--no-jev] [--explain] [--out results.json]

For each post: shown findings vs labels (same matching as app/eval/run.py), false reading-level
findings (R-ACC-02 with no label), claim statuses vs `expect_claims`, and, with --explain, whether
each LLM fix keeps the script of the text it replaces (Devanagari stays Devanagari, Latin stays Latin).
Self-contained script detection, so it also runs against older code for a before/after comparison.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SET = HERE.parent / "tests" / "fixtures" / "indic_eval.jsonl"


def script_of(text: str) -> str:
    """Dominant script of the letters and vowel signs in `text` (Unicode character names)."""
    c: Counter[str] = Counter()
    for ch in text:
        if ch.isalpha() or unicodedata.category(ch) in ("Mn", "Mc"):
            name = unicodedata.name(ch, "")
            c[name.split(" ")[0] if name else "?"] += 1
    return c.most_common(1)[0][0] if c else "none"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default=str(DEFAULT_SET))
    ap.add_argument("--no-jev", action="store_true")
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    from sqlmodel import Session

    from app.db import get_engine, init_db
    from app.eval.run import SHOWN, label_spans, overlaps
    from app.pipeline import engine as E
    from app.pipeline import jev
    from app.pipeline.context import UnitIn
    from app.pipeline.run import brand_context
    from app.pipeline.units import split_text
    from app.seed import seed_if_empty
    from app.services import current_brand

    init_db()
    seed_if_empty()
    rows = [json.loads(x) for x in Path(a.set).read_text(encoding="utf-8").splitlines() if x.strip()]
    with Session(get_engine()) as s:
        brand = current_brand(s)
        base = {ch: brand_context(s, brand.id, ch) for ch in ("instagram", "email", "website", "linkedin")}
    calls = jev.CallLog()
    items = []
    for row in rows:
        text = row["text"]
        ch = row.get("channel", "instagram")
        ctx = replace(base[ch], post_text=text, channel=ch)
        units = [UnitIn(id=None, kind="text", idx=u.idx, text=u.text, char_start=u.char_start) for u in split_text(text)]
        before = calls.cost_usd
        t0 = time.perf_counter()
        res = await E.analyze(units, ctx, calls=calls, use_jev=False if a.no_jev else None,
                              use_llm=None if a.explain else False, explain_findings=a.explain)
        ms = (time.perf_counter() - t0) * 1000
        # the text the pipeline indexed (newer code may normalise it); offsets point into this
        vtext = "\n\n".join(u.text for u in units) if len(units) > 1 else (units[0].text if units else text)
        labels = label_spans(vtext, row.get("labels", []))
        shown = [d for d in res.findings if d.band in SHOWN]
        preds = []
        for d in shown:
            s0 = d.hit_start if d.hit_start is not None else d.span_start
            e0 = d.hit_end if d.hit_end is not None else d.span_end
            preds.append({"codes": [c for c in (d.rule_code, d.fact_code) if c], "start": s0, "end": e0, "quote": d.quote,
                          "band": d.band, "tier": d.tier, "fix": d.fix, "verified": d.fix_verified,
                          "reasons": d.selfcheck_reasons, "kind": "readability" if d.rule_code == "R-ACC-02" else d.tier})
        lab_found = [any(lab["code"] in p["codes"] and overlaps(p["start"], p["end"], lab["start"], lab["end"]) for p in preds)
                     for lab in labels]
        pred_ok = [any(lab["code"] in p["codes"] and overlaps(p["start"], p["end"], lab["start"], lab["end"]) for lab in labels)
                   for p in preds]
        fixes = []
        for p in preds:
            if p["fix"]:
                src = vtext[p["start"]:p["end"]] if p["start"] is not None else p["quote"]
                fixes.append({"from": src, "fix": p["fix"], "src_script": script_of(src), "fix_script": script_of(p["fix"]),
                              "same_script": script_of(src) == script_of(p["fix"]) or script_of(src) == "none",
                              "verified": p["verified"]})
        claims = Counter(c["status"] for c in res.claims)
        items.append({
            "id": row["id"], "lang": row.get("lang"), "ms": round(ms), "cost": calls.cost_usd - before,
            "labels": len(labels), "labels_found": sum(lab_found),
            "false_findings": [f"{','.join(p['codes']) or p['tier']}:{p['quote'][:30]}" for p, ok in zip(preds, pred_ok) if not ok],
            "readability_false": sum(1 for p, ok in zip(preds, pred_ok) if p["kind"] == "readability" and not ok),
            "tier0_hits_on_labels": sum(1 for p, ok in zip(preds, pred_ok) if ok and p["tier"] == "tier0"),
            "claims": dict(claims), "claims_detail": [(c["status"], c["via"], c.get("fact_code")) for c in res.claims],
            "expect_claims": row.get("expect_claims") or {},
            "claims_as_expected": all(claims.get(k, 0) >= v for k, v in (row.get("expect_claims") or {}).items()),
            "fixes": fixes, "warnings": res.warnings,
        })
    tot = {
        "items": len(items),
        "labels": sum(i["labels"] for i in items), "labels_found": sum(i["labels_found"] for i in items),
        "false_findings": sum(len(i["false_findings"]) for i in items),
        "readability_false": sum(i["readability_false"] for i in items),
        "tier0_hits_on_labels": sum(i["tier0_hits_on_labels"] for i in items),
        "claims_as_expected": sum(1 for i in items if i["claims_as_expected"] and i["expect_claims"]),
        "claims_expected_items": sum(1 for i in items if i["expect_claims"]),
        "fixes": sum(len(i["fixes"]) for i in items),
        "fixes_same_script": sum(1 for i in items for f in i["fixes"] if f["same_script"]),
        "fixes_verified": sum(1 for i in items for f in i["fixes"] if f["verified"]),
        "cost_usd": round(calls.cost_usd, 5),
        "jev": not a.no_jev and jev.available(),
    }
    for i in items:
        print(f"{i['id']:6} {i['lang']:9} labels {i['labels_found']}/{i['labels']}  false {i['false_findings']}  "
              f"claims {i['claims_detail']}  fixes {[(f['fix'][:50], f['fix_script'], f['verified']) for f in i['fixes']]}")
    print(json.dumps(tot, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps({"total": tot, "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    sys.path.insert(0, str(HERE.parent))
    asyncio.run(main())
