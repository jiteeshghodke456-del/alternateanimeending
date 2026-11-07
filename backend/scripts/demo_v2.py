"""Accept-every-fix round trip on the "Try the demo" post (demo_flawed_post).

Runs the check pipeline on v1, accepts every finding that has a fix, applies them with the same
code the resubmit endpoint uses (workflow.apply_findings), re-checks v2 and prints both side by side
with finding counts. Uses the real keys from .env (Jev + LLM), so it costs a few cents at most.

    cd backend && python -m scripts.demo_v2            # live
    cd backend && python -m scripts.demo_v2 --no-llm   # Tier 0 + Jev only
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from app import workflow as W  # noqa: E402
from app.models import Finding  # noqa: E402
from app.pipeline import engine as E  # noqa: E402
from app.pipeline import jev  # noqa: E402
from pipeline_helpers import DEMO_TEXT, seed_ctx, text_units  # noqa: E402


REASONS: dict[int, list[str]] = {}


def rows(res: E.EngineResult) -> list[Finding]:
    out = []
    for i, d in enumerate(sorted(res.findings, key=lambda d: (d.span_start or -1, d.rule_code or ""))):
        out.append(Finding(id=i + 1, version_id=1, unit_id=None, rule_code=d.rule_code, category=d.category,
                           severity=d.severity, band=d.band, span_start=d.span_start, span_end=d.span_end,
                           quote=d.quote, fix=d.fix, fix_verified=d.fix_verified,
                           status="accepted" if d.fix is not None else "open"))
        REASONS[id(out[-1])] = d.selfcheck_reasons
    return out


def counts(fs: list[Finding]) -> str:
    c = Counter(f.band for f in fs)
    blocking = sum(1 for f in fs if f.severity == "blocking" and f.band != "pass")
    return f"{len(fs)} findings (flag {c['flag']}, human {c['human']}), blocking {blocking}"


def show(title: str, fs: list[Finding]) -> None:
    print(f"\n--- {title}: {counts(fs)}")
    for f in fs:
        tag = "verified" if f.fix_verified else ("MANUAL" if f.fix is not None else "no fix")
        print(f"  [{f.span_start}:{f.span_end}] {f.rule_code or f.category:<12} {f.severity:<8} {f.band:<5} "
              f"{tag:<8} {f.quote[:50]!r} -> {f.fix!r}")
        if not f.fix_verified and REASONS.get(id(f)):
            print(f"      selfcheck: {REASONS[id(f)]}")


async def check(text: str, use_llm: bool) -> tuple[list[Finding], jev.CallLog]:
    calls = jev.CallLog(version_id=1)
    res = await E.analyze(text_units(text), seed_ctx(text), calls=calls, use_llm=use_llm)
    return rows(res), calls


async def main(use_llm: bool) -> int:
    v1 = DEMO_TEXT
    f1, c1 = await check(v1, use_llm)
    show("v1", f1)
    applied = W.apply_findings(v1, f1)
    v2 = applied.text
    print(f"\napplied {len(applied.applied)}, skipped {applied.skipped}")
    f2, c2 = await check(v2, use_llm=False)
    show("v2", f2)
    print("\n=== v1 ===\n" + v1 + "\n\n=== v2 ===\n" + v2)
    cost = sum(r.cost_usd for r in c1.records + c2.records)
    print(f"\nv1: {counts(f1)}\nv2: {counts(f2)}\ncost ${cost:.4f}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(not a.no_llm)))
