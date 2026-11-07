"""Claims: fact matching + Jev support check. OWNER: pipeline-agent.

Intended API:

    def top_facts(claim_text: str, facts: list[Fact], k: int = 3) -> list[tuple[Fact, float]]
        rapidfuzz.fuzz.token_set_ratio, highest first.
    def is_expired(fact: Fact, today: date | None = None) -> bool
    async def check_claim(unit: Unit, claim_text: str, facts: list[Fact], ctx) -> ClaimResult
        One Jev call with one Noul per candidate fact ("Does fact F support this claim?").
        status = "supported" if any p >= 0.7 and that fact not expired;
                 "outdated"  if the supporting fact has expired;
                 "unsupported" otherwise.
        Returns ClaimResult(status, fact_code | None, probability, candidates).
    Confidential items / Jev missing: fall back to top_facts score only and mark band "human".
"""
from __future__ import annotations

from datetime import date


def is_expired(valid_until: date | None, today: date | None = None) -> bool:
    """True if `valid_until` is set and strictly before today."""
    if valid_until is None:
        return False
    return valid_until < (today or date.today())


async def check_claim(*args, **kwargs):  # noqa: ANN002, ANN003, ANN201
    raise NotImplementedError("pipeline-agent: implement claims.check_claim")
