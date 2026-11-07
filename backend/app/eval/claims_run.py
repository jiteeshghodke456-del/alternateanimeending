"""Claim-flagging eval: does each claim get the band a reviewer expects? OWNER: pipeline-agent.

    python -m app.eval.claims_run                  # live Jev if TYPESAFE_API_KEY is set (detection only, no LLM)
    python -m app.eval.claims_run --no-jev         # code-only (what a confidential item gets)
    python -m app.eval.claims_run --tag before     # writes results/claims-before.json

Dataset: app/eval/claims_dataset.jsonl (see claims_README.md). Each label is a span in the text with the band a
reviewer expects for that claim: flag (a rule asks for evidence and none matches), human (a reminder to verify a
figure), none (no claim finding: opinion, not a claim, or covered by an approved fact).

Brand context (fixed, no database): the India regulatory pack's ranking rule IN-ASCI-02, plus a generic "every
number needs a fact" brand rule (R-EVAL-NUM, like the demo brand's R-CLAIM-02), plus the eval facts in
claims_facts.csv; an item only sees the facts it lists in `facts`.

Scoring (code): every label is one row; its predicted band is the strongest band of the claim findings
(tier "claim") that overlap the label's span (the flagged words, hit_* when a fix region widened the span).
A claim finding that overlaps no label is an extra row with expected band "none". From the rows:
flag precision/recall (expected flag vs predicted flag), shown precision/recall (flag or human counts as
shown) and band accuracy.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import time
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import APP_DIR

DATASET = APP_DIR / "eval" / "claims_dataset.jsonl"
FACTS = APP_DIR / "eval" / "claims_facts.csv"
RESULTS = APP_DIR / "eval" / "results"
TODAY = date(2026, 11, 8)
BANDS = ("none", "human", "flag")
RANK = {b: i for i, b in enumerate(BANDS)}

EVAL_NUMBER_RULE = {
    "code": "R-EVAL-NUM", "category": "claims", "severity": "major", "check_method": "claim",
    "statement": "Every number, rating, price, offer, delivery time and health or safety claim must match a current "
                 "approved fact.",
    "params": {"needs_supporting_fact": True, "reject_expired_facts": True},
}


def load_rows(split: str = "all", path: Path = DATASET) -> list[dict]:
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [r for r in rows if split == "all" or r.get("split") == split]


def load_facts(path: Path = FACTS) -> list[Any]:
    from app.pipeline.context import FactSpec

    out = []
    for r in csv.DictReader(path.open(encoding="utf-8")):
        out.append(FactSpec(code=r["code"], statement=r["statement"], value=r["value"], source=r["source"],
                            valid_until=date.fromisoformat(r["valid_until"]) if r["valid_until"] else None))
    return out


def eval_rules() -> list[Any]:
    from app.packs import _pack_spec, get_pack
    from app.pipeline.context import RuleSpec

    pack = get_pack("india_regulatory")
    asci = next(r for r in pack.rules if r.code == "IN-ASCI-02")
    return [_pack_spec(asci), RuleSpec(**EVAL_NUMBER_RULE)]


def label_rows(text: str, labels: list[dict]) -> list[dict]:
    out = []
    for lab in labels:
        s = text.find(lab["span"])
        if s < 0:
            raise ValueError(f"label span not in text: {lab['span']!r}")
        out.append({**lab, "start": s, "end": s + len(lab["span"])})
    return out


def _overlap(a0: int, a1: int, b0: int, b1: int) -> bool:
    return a0 < b1 and b0 < a1


def score_item(labels: list[dict], preds: list[dict]) -> list[dict]:
    """Rows {expected, predicted, span, kind}. `preds`: [{start, end, band}] (claim findings, text offsets)."""
    rows, used = [], [False] * len(preds)
    for lab in labels:
        got = "none"
        for i, p in enumerate(preds):
            if _overlap(lab["start"], lab["end"], p["start"], p["end"]):
                used[i] = True
                if RANK[p["band"]] > RANK[got]:
                    got = p["band"]
        rows.append({"expected": lab["band"], "predicted": got, "span": lab["span"], "kind": lab.get("kind")})
    for p, u in zip(preds, used):
        if not u:
            rows.append({"expected": "none", "predicted": p["band"], "span": p.get("quote", ""), "kind": "unlabelled"})
    return rows


def prf(tp: int, n_pred: int, n_true: int) -> dict[str, Any]:
    p = tp / n_pred if n_pred else 1.0
    r = tp / n_true if n_true else 1.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3), "tp": tp, "n_pred": n_pred, "n_true": n_true}


def metrics(rows: list[dict]) -> dict[str, Any]:
    flag_tp = sum(1 for r in rows if r["expected"] == "flag" and r["predicted"] == "flag")
    shown = ("flag", "human")
    shown_tp = sum(1 for r in rows if r["expected"] in shown and r["predicted"] in shown)
    confusion: dict[str, int] = {}
    for r in rows:
        k = f"{r['expected']}->{r['predicted']}"
        confusion[k] = confusion.get(k, 0) + 1
    return {
        "flag": prf(flag_tp, sum(1 for r in rows if r["predicted"] == "flag"), sum(1 for r in rows if r["expected"] == "flag")),
        "shown": prf(shown_tp, sum(1 for r in rows if r["predicted"] in shown), sum(1 for r in rows if r["expected"] in shown)),
        "band_accuracy": round(sum(1 for r in rows if r["expected"] == r["predicted"]) / len(rows), 3) if rows else None,
        "first_party_flagged": sum(1 for r in rows if r["kind"] == "first_party_statistic" and r["predicted"] == "flag"),
        "n_rows": len(rows),
        "confusion": dict(sorted(confusion.items())),
    }


async def evaluate(split: str = "all", *, use_jev: Optional[bool] = None, limit: Optional[int] = None,
                   ids: Optional[list[str]] = None) -> dict[str, Any]:
    from app.pipeline import engine as E
    from app.pipeline import jev
    from app.pipeline.context import CheckContext, UnitIn
    from app.pipeline.units import split_text

    rows_in = [r for r in load_rows(split) if not ids or r["id"] in ids][: limit or None]
    facts = {f.code: f for f in load_facts()}
    base = CheckContext(channel="website", rules=eval_rules(), today=TODAY)
    calls = jev.CallLog()
    items, all_rows = [], []
    for row in rows_in:
        text = row["text"]
        ctx = replace(base, channel=row.get("channel", "website"), post_text=text,
                      facts=[facts[c] for c in row.get("facts", [])])
        units = [UnitIn(id=None, kind="text", idx=u.idx, text=u.text, char_start=u.char_start) for u in split_text(text)]
        t0 = time.perf_counter()
        res = await E.analyze(units, ctx, calls=calls, use_jev=use_jev, use_llm=False, explain_findings=False)
        ms = (time.perf_counter() - t0) * 1000
        preds = [{"start": d.hit_start if d.hit_start is not None else d.span_start,
                  "end": d.hit_end if d.hit_end is not None else d.span_end,
                  "band": d.band, "quote": d.quote, "rule": d.rule_code, "p": d.probability}
                 for d in res.findings if d.tier == "claim" and d.band in ("flag", "human") and d.span_start is not None]
        rows = score_item(label_rows(text, row["labels"]), preds)
        all_rows += [{**r, "item": row["id"], "split": row["split"], "source": row["source"]} for r in rows]
        items.append({"id": row["id"], "preds": preds, "rows": rows, "latency_ms": round(ms),
                      "claims": [{"text": c["text"], "status": c["status"], "via": c["via"], "kind": c.get("kind"),
                                  "p_checkable": c.get("p_checkable"), "p_record": c.get("p_record"), "gated": c.get("gated")}
                                 for c in res.claims], "warnings": res.warnings})
    out = {
        "run_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "split": split,
        "jev": jev.model_name() if (jev.available() if use_jev is None else use_jev) else "not used (code only)",
        "metrics": {"all": metrics(all_rows),
                    "by_source": {s: metrics([r for r in all_rows if r["source"] == s]) for s in ("swiggy_blog", "synthetic")},
                    "by_split": {s: metrics([r for r in all_rows if r["split"] == s]) for s in ("dev", "test")}},
        "jev_calls": sum(1 for r in calls.records if r.provider == "typesafe"),
        "cost_usd": round(calls.cost_usd, 6),
        "n_items": len(items),
        "errors": [r["item"] for r in all_rows if r["expected"] != r["predicted"]],
        "items": items,
    }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--split", default="all", choices=["dev", "test", "all"])
    ap.add_argument("--no-jev", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--tag", default="latest")
    ap.add_argument("--ids", help="comma-separated item ids (debugging)")
    a = ap.parse_args()
    out = asyncio.run(evaluate(a.split, use_jev=False if a.no_jev else None, limit=a.limit,
                         ids=a.ids.split(",") if a.ids else None))
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"claims-{a.tag}.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    m = out["metrics"]["all"]
    print(f"{out['jev']}: flag P={m['flag']['precision']} R={m['flag']['recall']} | shown P={m['shown']['precision']} "
          f"R={m['shown']['recall']} | band acc={m['band_accuracy']} | first-party flagged={m['first_party_flagged']} | "
          f"rows={m['n_rows']} calls={out['jev_calls']} cost=${out['cost_usd']} -> {path}")


if __name__ == "__main__":
    main()
