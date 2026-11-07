"""Re-check each proposed fix before showing it as "verified". OWNER: pipeline-agent.

Intended API:

    async def verify_fix(original_unit_text: str, fixed_unit_text: str, rule: Rule,
                         blocking_rules: list[Rule], ctx) -> SelfCheckResult
        1. Tier 0 on the fixed text: the same hit must be gone and no new blocking hit appear.
        2. One Jev call on the fixed text: Noul for `rule` + every blocking rule (all must be
           < 0.30) and jev.MEANING_QUESTION on {"original": a, "revised": b} (must be >= 0.70).
        3. If it fails, ask explain() once more for a new fix; then give up with
           fix_verified=False and note "manual fix needed".
        Returns SelfCheckResult(verified: bool, fix: str | None, reasons: list[str]).
"""
from __future__ import annotations


async def verify_fix(*args, **kwargs):  # noqa: ANN002, ANN003, ANN201
    raise NotImplementedError("pipeline-agent: implement selfcheck.verify_fix")
