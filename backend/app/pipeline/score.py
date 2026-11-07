"""Brand Score (0-100) for one version, computed in code from its findings. Never a model output.

    score = max(0, 100 - sum(weight[severity, band] for every finding that counts))

| severity | flag | needs a human look |
|----------|------|--------------------|
| blocking |  25  |  12                |
| major    |  10  |   5                |
| minor    |   3  |   1                |

- Counted: findings with status open, accepted or edited (an accepted fix lands in the NEXT version, so
  this version's text still has the problem). Not counted: dismissed and overridden findings, and
  pass-band rows. A human look weighs about half a flag: it may be fine.
- Bands: >= 80 "On brand", 60-79 "Needs work", < 60 "Off brand" (label + icon in the UI, never colour alone).
- It summarises the open findings; it is not an accuracy number.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

WEIGHTS: dict[tuple[str, str], int] = {
    ("blocking", "flag"): 25, ("blocking", "human"): 12,
    ("major", "flag"): 10, ("major", "human"): 5,
    ("minor", "flag"): 3, ("minor", "human"): 1,
}
COUNTED_STATUSES = ("open", "accepted", "edited")
SEVERITY_ORDER = ("blocking", "major", "minor")
BAND_LABEL = {"flag": "flagged", "human": "human look"}
GOOD_AT, FAIR_AT = 80, 60


def weight(severity: str, band: str) -> int:
    return WEIGHTS.get((severity, band), 0)


def score_band(score: Optional[int]) -> Optional[str]:
    if score is None:
        return None
    return "good" if score >= GOOD_AT else "fair" if score >= FAIR_AT else "poor"


SCORE_LABEL = {"good": "On brand", "fair": "Needs work", "poor": "Off brand"}


@dataclass
class Deduction:
    severity: str
    band: str
    count: int
    weight: int

    @property
    def points(self) -> int:
        return self.count * self.weight


@dataclass
class Score:
    score: int
    band: str
    label: str
    deductions: list[Deduction] = field(default_factory=list)
    formula: str = "100"

    def as_dict(self) -> dict[str, Any]:
        return {"score": self.score, "band": self.band, "label": self.label, "formula": self.formula,
                "deductions": [{"severity": d.severity, "band": d.band, "count": d.count, "weight": d.weight,
                                "points": d.points} for d in self.deductions]}


def compute(findings: Iterable[Any]) -> Score:
    """`findings`: objects or dicts with severity, band and status."""
    counts: dict[tuple[str, str], int] = {}
    for f in findings:
        g = f.get if isinstance(f, dict) else (lambda k, _f=f: getattr(_f, k, None))
        sev, band, status = g("severity"), g("band"), g("status") or "open"
        if status not in COUNTED_STATUSES or weight(str(sev), str(band)) == 0:
            continue
        counts[(sev, band)] = counts.get((sev, band), 0) + 1
    deds = [Deduction(sev, band, counts[(sev, band)], weight(sev, band))
            for sev in SEVERITY_ORDER for band in ("flag", "human") if counts.get((sev, band))]
    total = sum(d.points for d in deds)
    value = max(0, 100 - total)
    parts = " ".join(f"− {d.count}×{d.weight} ({d.severity}, {BAND_LABEL[d.band]})" for d in deds)
    formula = f"100 {parts} = {100 - total}" if deds else "100 (no open findings)"
    if 100 - total < 0:
        formula += ", shown as 0"
    band = score_band(value) or "poor"
    return Score(score=value, band=band, label=SCORE_LABEL[band], deductions=deds, formula=formula)
