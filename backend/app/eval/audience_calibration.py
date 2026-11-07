"""Audience Preview calibration (live Jev; needs TYPESAFE_API_KEY). OWNER: AI/ML.

    python -m app.eval.audience_calibration                 # all posts, all 240 readers (~$0.01 a post)
    python -m app.eval.audience_calibration --every 4       # every 4th reader (60), for quick probing
    python -m app.eval.audience_calibration --only swiggy_page,scam_prize --out /tmp/x.json

Runs audience.simulate() (no LLM "why") on app/eval/audience_calibration.json and prints, per post, the
mean credibility (1 believes ... 5 misleading), the level counts, and the confused / not-for-them shares,
next to the post's draft label. The labels are AI-assisted drafts (believe / mixed / doubt), not survey data.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Optional

from app.config import APP_DIR

DATASET = APP_DIR / "eval" / "audience_calibration.json"

# Draft label -> the mean credibility range that counts as "in line" (code, for the printout only). "believe"
# is the app's own trust band (questions.AUDIENCE_TRUST_MAX 2.5); a plain, honest post should also stay <= 2.2.
EXPECT_RANGE = {
    "believe": (1.0, 2.5), "believe_or_mixed": (1.0, 3.2), "mixed": (2.0, 3.8),
    "mixed_or_believe": (1.0, 3.5), "mixed_or_doubt": (2.5, 5.0), "doubt": (4.0, 5.0),
}


def load(path: Path = DATASET) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["posts"]


def paragraphs(text: str) -> list[dict[str, Any]]:
    from app.pipeline.units import split_text

    return [{"text": p.text, "char_start": p.char_start, "char_end": p.char_end} for p in split_text(text)]


async def run_post(post: dict[str, Any], readers: list[dict]) -> dict[str, Any]:
    from app.pipeline import audience as A

    res = await A.simulate(post["text"], paragraphs(post["text"]), personas=readers, use_llm=False,
                           channel=post.get("channel"))
    o = res["overall"]
    lo, hi = EXPECT_RANGE.get(post.get("expect", ""), (1.0, 5.0))
    return {"id": post["id"], "expect": post.get("expect"), "tune": post.get("tune", False),
            "mean": round(o["mean_score"], 2), "levels": o["levels"], "confused_pct": o["confused_pct"],
            "not_for_them_pct": o["not_for_them_pct"], "in_range": lo <= o["mean_score"] <= hi,
            "segments": {s["key"]: round(s["mean_score"], 2) for s in res["segments"]},
            "cost_usd": res["cost_usd"], "warnings": res["warnings"]}


async def main(every: int, only: Optional[set[str]], out: Optional[str]) -> None:
    from app.pipeline import audience as A

    readers = list(A.load_readers())[::max(1, every)]
    posts = [p for p in load() if not only or p["id"] in only]
    rows = []
    for p in posts:  # one post at a time keeps the semaphore for the readers of one post
        rows.append(await run_post(p, readers))
        r = rows[-1]
        print(f"{r['id']:<20} {r['expect']:<17} mean {r['mean']:.2f} {'ok ' if r['in_range'] else 'OFF'} "
              f"levels {r['levels']} confused {r['confused_pct']:.0%} not-for-them {r['not_for_them_pct']:.0%}", flush=True)
    total = sum(r["cost_usd"] for r in rows)
    print(f"{sum(r['in_range'] for r in rows)}/{len(rows)} in range; readers {len(readers)}; cost ${total:.4f}")
    if out:
        Path(out).write_text(json.dumps({"readers": len(readers), "rows": rows}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=1)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    asyncio.run(main(a.every, {x for x in a.only.split(",") if x} or None, a.out))
