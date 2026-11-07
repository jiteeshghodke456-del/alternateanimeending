"""Evaluation runner (BUILD.md P0-10). OWNER: pipeline-agent.

    python -m app.eval.run --split test            # held-out numbers for the Evaluation page
    python -m app.eval.run --split dev --tune      # tune per-category thresholds on dev only

Runs the real pipeline in-process (engine.analyze) on app/eval/dataset.jsonl with the brand rules
from the database (seeded if empty). Detection only by default (tier0 + Jev + claims); pass
--with-explain to include LLM explanations/fixes and self-checks in latency and cost.

Matching: a shown finding (band flag or human) matches a label when the label's code equals the
finding's rule_code or fact_code and the spans overlap. Post-level or image findings (no span)
match any label with the same code. Writes app/eval/results/latest.json plus a timestamped copy.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import APP_DIR

log = logging.getLogger("brandgate.eval")

DATASET = APP_DIR / "eval" / "dataset.jsonl"
RESULTS = APP_DIR / "eval" / "results"
LATEST = RESULTS / "latest.json"
THRESHOLDS = RESULTS / "thresholds.json"
SHOWN = ("flag", "human")


@dataclass
class Pred:
    codes: set[str]
    category: str
    band: str
    start: Optional[int]
    end: Optional[int]
    quote: str
    p: Optional[float]


def load_dataset(split: str, path: Path = DATASET) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if split == "all" or r.get("split") == split]


def label_spans(text: str, labels: list[dict]) -> list[dict]:
    out, used = [], {}
    for lab in labels:
        span = lab.get("span") or ""
        start = text.find(span, used.get(span, 0)) if span else -1
        if start < 0 and span:
            start = text.lower().find(span.lower())
        if start < 0:
            log.warning("label span not found: %r", span)
            out.append({**lab, "start": None, "end": None})
            continue
        used[span] = start + 1
        out.append({**lab, "start": start, "end": start + len(span)})
    return out


def overlaps(a0: Optional[int], a1: Optional[int], b0: Optional[int], b1: Optional[int]) -> bool:
    if None in (a0, a1, b0, b1):
        return True  # post-level / image findings, or a label without a span: codes decide
    return a0 < b1 and b0 < a1  # type: ignore[operator]


def match(preds: list[Pred], labels: list[dict]) -> tuple[list[bool], list[bool]]:
    """(pred_is_tp, label_is_found)."""
    p_ok = [False] * len(preds)
    l_ok = [False] * len(labels)
    for i, p in enumerate(preds):
        for j, lab in enumerate(labels):
            if lab["code"] in p.codes and overlaps(p.start, p.end, lab["start"], lab["end"]):
                p_ok[i] = l_ok[j] = True
    return p_ok, l_ok


def prf(tp_pred: int, n_pred: int, tp_lab: int, n_lab: int) -> dict[str, Any]:
    precision = tp_pred / n_pred if n_pred else (1.0 if not n_lab else 0.0)
    recall = tp_lab / n_lab if n_lab else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3), "n_findings": n_pred, "n_labels": n_lab}


def percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


CLAIM_CODES = {"R-CLAIM-01", "R-CLAIM-02", "R-CLAIM-05", "R-LEGAL-03"}


def expected_claim_status(start: int, end: int, labels: list[dict]) -> str:
    hits = [lab for lab in labels if overlaps(start, end, lab["start"], lab["end"]) and lab["start"] is not None]
    if any(lab["code"].startswith("F-") for lab in hits):
        return "outdated"
    if any(lab["code"] in CLAIM_CODES for lab in hits):
        return "unsupported"
    return "supported"


def _category_of(code: str, rules: dict[str, Any]) -> str:
    if code.startswith("F-"):
        return "claims"
    r = rules.get(code)
    return r.category if r else "other"


async def evaluate(split: str = "test", *, with_explain: bool = False, use_jev: Optional[bool] = None,
                   limit: Optional[int] = None, write: bool = True) -> dict[str, Any]:
    from sqlmodel import Session

    from app.db import get_engine, init_db
    from app.pipeline import engine as E
    from app.pipeline import explain as X
    from app.pipeline import jev
    from app.pipeline.bands import thresholds
    from app.pipeline.context import UnitIn
    from app.pipeline.run import brand_context
    from app.pipeline.units import split_text
    from app.seed import seed_if_empty
    from app.services import current_brand

    init_db()
    seed_if_empty()
    rows = load_dataset(split)[: limit or None]
    with Session(get_engine()) as s:
        brand = current_brand(s)
        base = {ch: brand_context(s, brand.id, ch) for ch in ("instagram", "email", "website", "linkedin")}
    rules = {r.code: r for r in base["instagram"].rules}

    calls = jev.CallLog()
    per_item: list[dict] = []
    for row in rows:
        text = row["text"]
        labels = label_spans(text, row.get("labels", []))
        ctx = base[row.get("channel", "instagram")]
        from dataclasses import replace

        ctx = replace(ctx, post_text=text, channel=row.get("channel", "instagram"))
        units = [UnitIn(id=None, kind="text", idx=u.idx, text=u.text, char_start=u.char_start) for u in split_text(text)]
        before = calls.cost_usd
        t0 = time.perf_counter()
        res = await E.analyze(units, ctx, calls=calls, use_jev=use_jev, use_llm=None if with_explain else False,
                              explain_findings=with_explain)
        latency = (time.perf_counter() - t0) * 1000
        starts = {u.idx: u.char_start or 0 for u in units}
        preds = [Pred({c for c in (d.rule_code, d.fact_code) if c}, d.category, d.band, d.span_start, d.span_end, d.quote, d.probability)
                 for d in res.findings if d.band in SHOWN]
        judg = [{"code": j.rule_code, "category": j.category, "p": j.p, "kind": j.kind, "status": j.claim_status,
                 "fact": j.fact_code,
                 "start": None if j.unit_idx is None or j.local_start is None else starts[j.unit_idx] + j.local_start,
                 "end": None if j.unit_idx is None or j.local_end is None else starts[j.unit_idx] + j.local_end,
                 "vague": bool((rules.get(j.rule_code or "") and (rules[j.rule_code].params or {}).get("vague")))}
                for j in res.judgments]
        fixed = [p for p, d in zip(preds, [d for d in res.findings if d.band in SHOWN])
                 if d.tier == "tier0" or d.probability is None]
        claims = [{**c, "start": starts[c["unit_idx"]] + c["local_start"], "end": starts[c["unit_idx"]] + c["local_end"]}
                  for c in res.claims]
        per_item.append({"id": row["id"], "channel": row.get("channel"), "text": text, "labels": labels, "preds": preds,
                         "fixed": fixed, "judgments": judg, "claims": claims, "latency_ms": latency,
                         "cost_usd": calls.cost_usd - before, "warnings": res.warnings})

    metrics, misses = score(per_item, rules)
    flag_t, human_t = thresholds(None)
    out = {
        "run_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "split": split,
        "models": {"jev": jev.model_name() if (jev.available() if use_jev is None else use_jev) else "not configured (Tier 0 only)",
                   "llm": X.model_name() if with_explain and X.available() else "not used (detection only)"},
        "metrics": {**metrics, "latency_ms": {"median": round(percentile([i["latency_ms"] for i in per_item], 0.5)),
                                              "p95": round(percentile([i["latency_ms"] for i in per_item], 0.95))},
                    "latency_median_ms": round(percentile([i["latency_ms"] for i in per_item], 0.5)),
                    "latency_p95_ms": round(percentile([i["latency_ms"] for i in per_item], 0.95)),
                    "cost_per_item_usd": round(calls.cost_usd / len(per_item), 6) if per_item else 0.0,
                    "total_cost_usd": round(calls.cost_usd, 6), "jev_calls": sum(1 for r in calls.records if r.provider == "typesafe"),
                    "judgments": calls.judgments, "n_items": len(per_item),
                    "stage": "full (detection + explain + self-check)" if with_explain else "detection (tier0 + Jev + claims)",
                    "thresholds": {"default": {"flag": flag_t, "human": human_t}, "file": THRESHOLDS.is_file()},
                    "warnings": sum(len(i["warnings"]) for i in per_item)},
        "misses": misses,
    }
    if write:
        write_results(out)
    out["_items"] = per_item  # for --tune (not written)
    return out


def score(per_item: list[dict], rules: dict[str, Any]) -> tuple[dict[str, Any], list[dict]]:
    tp_p = n_p = tp_l = n_l = 0
    flag_tp_p = flag_n_p = flag_tp_l = 0
    cats: dict[str, list[int]] = {}
    misses: list[dict] = []
    claim_ok = claim_n = 0
    confusion: dict[str, int] = {}
    cal = [[0.0, 0, 0] for _ in range(10)]  # sum p, n, positives
    for it in per_item:
        preds, labels = it["preds"], it["labels"]
        p_ok, l_ok = match(preds, labels)
        tp_p += sum(p_ok)
        n_p += len(preds)
        tp_l += sum(l_ok)
        n_l += len(labels)
        fp_ok, fl_ok = match([p for p in preds if p.band == "flag"], labels)
        flag_tp_p += sum(fp_ok)
        flag_n_p += len(fp_ok)
        flag_tp_l += sum(fl_ok)
        for p, ok in zip(preds, p_ok):
            c = cats.setdefault(p.category or "other", [0, 0, 0, 0])
            c[0] += ok
            c[1] += 1
            if not ok:
                misses.append({"item": it["id"], "type": "false_positive", "code": sorted(p.codes)[0] if p.codes else None,
                               "quote": p.quote[:120], "band": p.band, "p": p.p})
        for lab, ok in zip(labels, l_ok):
            c = cats.setdefault(_category_of(lab["code"], rules), [0, 0, 0, 0])
            c[2] += ok
            c[3] += 1
            if not ok:
                misses.append({"item": it["id"], "type": "missed", "code": lab["code"], "quote": lab.get("span", "")[:120]})
        for cl in it["claims"]:
            exp = expected_claim_status(cl["start"], cl["end"], labels)
            claim_n += 1
            claim_ok += exp == cl["status"]
            key = f"{exp}->{cl['status']}"
            confusion[key] = confusion.get(key, 0) + 1
        for j in it["judgments"]:
            if j["kind"] == "claim":
                positive = expected_claim_status(j["start"], j["end"], labels) != "supported"
            else:
                positive = any(lab["code"] == j["code"] and overlaps(j["start"], j["end"], lab["start"], lab["end"]) for lab in labels)
            b = min(9, int(j["p"] * 10))
            cal[b][0] += j["p"]
            cal[b][1] += 1
            cal[b][2] += positive
    per_category = {k: prf(v[0], v[1], v[2], v[3]) for k, v in sorted(cats.items())}
    metrics = {
        **prf(tp_p, n_p, tp_l, n_l),
        "bands_counted": list(SHOWN),
        "flag_only": prf(flag_tp_p, flag_n_p, flag_tp_l, n_l),
        "per_category": per_category,
        "claim_accuracy": round(claim_ok / claim_n, 3) if claim_n else None,
        "claim_n": claim_n,
        "claim_confusion": confusion,
        "calibration": [{"bin": f"{i / 10:.1f}-{(i + 1) / 10:.1f}", "predicted": round(c[0] / c[1], 3) if c[1] else None,
                         "observed": round(c[2] / c[1], 3), "n": c[1]} for i, c in enumerate(cal) if c[1]],
    }
    misses.sort(key=lambda m: (m["type"], m["item"]))
    return metrics, misses[:100]


def write_results(out: dict[str, Any]) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in out.items() if not k.startswith("_")}
    stamp = data["run_at"].replace(":", "").replace("-", "")[:15]
    copy = RESULTS / f"run-{data['split']}-{stamp}.json"
    text = json.dumps(data, indent=2, default=str)
    copy.write_text(text, encoding="utf-8")
    tmp = LATEST.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(LATEST)
    try:
        from sqlmodel import Session

        from app.db import get_engine
        from app.models import EvalRun

        with Session(get_engine()) as s:
            s.add(EvalRun(split=data["split"], metrics=data["metrics"]))
            s.commit()
    except Exception:  # noqa: BLE001
        log.warning("eval: could not record EvalRun row")
    return copy


# ------------------------------------------------------------------ threshold tuning (dev only)
GRID_HUMAN = [round(0.30 + 0.05 * i, 2) for i in range(9)]  # 0.30 .. 0.70
GRID_FLAG = [round(0.50 + 0.05 * i, 2) for i in range(9)]  # 0.50 .. 0.90
MIN_LABELS_TO_TUNE = 3


def tune(per_item: list[dict], rules: dict[str, Any]) -> dict[str, Any]:
    """Pick per-category thresholds that maximise F1 on dev from the recorded Jev probabilities.
    Tier 0 findings are fixed; vague rules are left out (they never flag on their own)."""
    cats = sorted({j["category"] for it in per_item for j in it["judgments"] if not j["vague"]})
    result: dict[str, Any] = {}
    for cat in cats:
        n_lab = sum(1 for it in per_item for lab in it["labels"] if _category_of(lab["code"], rules) == cat)
        if n_lab < MIN_LABELS_TO_TUNE:
            continue

        def f1_at(t: float) -> float:
            tp_p = n_p = tp_l = 0
            for it in per_item:
                preds = [p for p in it["fixed"] if p.category == cat]
                for j in it["judgments"]:
                    if j["category"] != cat or j["vague"] or j["p"] < t:
                        continue
                    if j["kind"] == "claim" and j["status"] == "supported":
                        continue
                    preds.append(Pred({c for c in (j["code"], j["fact"]) if c}, cat, "x", j["start"], j["end"], "", j["p"]))
                labels = [lab for lab in it["labels"] if _category_of(lab["code"], rules) == cat]
                p_ok, l_ok = match(preds, labels)
                tp_p += sum(p_ok)
                n_p += len(preds)
                tp_l += sum(l_ok)
            return prf(tp_p, n_p, tp_l, n_lab)["f1"]

        human = max(GRID_HUMAN, key=lambda t: (f1_at(t), -abs(t - 0.30)))
        flag = max([t for t in GRID_FLAG if t >= human], key=lambda t: (f1_at(t), -abs(t - 0.70)))
        result[cat] = {"human": human, "flag": flag, "f1_at_human": f1_at(human), "n_labels": n_lab}
    return result


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="Brand Gate evaluation")
    ap.add_argument("--split", choices=["dev", "test", "all"], default="test")
    ap.add_argument("--tune", action="store_true", help="tune per-category thresholds (dev split only)")
    ap.add_argument("--with-explain", action="store_true", help="include LLM explanations and self-checks")
    ap.add_argument("--no-jev", action="store_true", help="Tier 0 only")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    if args.tune and args.split != "dev":
        ap.error("--tune runs on the dev split only (thresholds must never see the test split)")
    out = asyncio.run(evaluate(args.split, with_explain=args.with_explain, use_jev=False if args.no_jev else None,
                               limit=args.limit, write=not args.tune))
    m = out["metrics"]
    print(f"{args.split}: P={m['precision']} R={m['recall']} F1={m['f1']} claims={m['claim_accuracy']} "
          f"p50={m['latency_ms']['median']}ms p95={m['latency_ms']['p95']}ms cost/item=${m['cost_per_item_usd']} [{out['models']}]")
    for cat, v in m["per_category"].items():
        print(f"  {cat:14} P={v['precision']} R={v['recall']} F1={v['f1']} (findings {v['n_findings']}, labels {v['n_labels']})")
    if args.tune:
        from app.pipeline.engine import _Run  # noqa: F401  (import check only)

        rules: dict[str, Any] = {}
        from sqlmodel import Session

        from app.db import get_engine
        from app.pipeline.run import brand_context
        from app.services import current_brand

        with Session(get_engine()) as s:
            rules = {r.code: r for r in brand_context(s, current_brand(s).id, "instagram").rules}
        cats = tune(out["_items"], rules)
        if not cats:
            print("tune: no Jev judgments with enough labels (is TYPESAFE_API_KEY set?); thresholds unchanged")
            return
        data = {"default": {"flag": 0.70, "human": 0.30}, "categories": {k: {"flag": v["flag"], "human": v["human"]} for k, v in cats.items()},
                "tuned_on": "dev", "run_at": out["run_at"], "n_items": len(out["_items"]), "detail": cats}
        RESULTS.mkdir(parents=True, exist_ok=True)
        THRESHOLDS.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"tune: wrote {THRESHOLDS} -> {data['categories']}")


if __name__ == "__main__":
    main()
