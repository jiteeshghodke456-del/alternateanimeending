"""Tier 0: deterministic, local checks. OWNER: pipeline-agent. Runs for confidential items too.

Intended API (pure functions, easy to unit-test):

    @dataclass
    class Tier0Hit:
        rule_code: str | None      # e.g. the rule whose check_method == "tier0" and params match
        category: str              # "words" | "claims" | "readability" | "palette" | "accessibility" ...
        severity: str              # copied from the Rule, never decided by a model
        span_start: int | None     # offsets into Unit.text (caller adds Unit.char_start)
        span_end: int | None
        quote: str
        message: str               # template explanation used when the LLM is unavailable
        fix: str | None            # e.g. preferred-term replacement
        is_claim_trigger: bool = False

    check_terms(text: str, terms: list[Term]) -> list[Tier0Hit]
        Exact, case-insensitive whole-word match for kind=banned; kind=preferred flags `value`
        and suggests `replacement` (old product name -> new); kind=capitalise flags any casing
        that differs from `value`.
    claim_triggers(text: str) -> list[Tier0Hit]
        Numbers, %, "best", "#1", "guaranteed", "cheapest", comparatives (-er than, "more ... than").
    readability(text: str, channel: str, channel_targets: dict) -> Tier0Hit | None
        textstat.flesch_kincaid_grade(text) > channel_targets[channel]["max_grade"] -> hit.
    dominant_colours(image_path: str, k: int = 5) -> list[tuple[str, float]]
        k-means on downsampled pixels (Pillow + numpy), returns (hex, share).
    palette_check(colours, palette: list[str], threshold: float = 12.0) -> list[Tier0Hit]
        CIEDE2000 delta-E vs the nearest palette colour; > threshold -> off-palette hit.
    alt_text_check(alt_text: str | None) -> Tier0Hit | None
    run_tier0(unit: Unit, ctx: CheckContext) -> list[Tier0Hit]
"""
from __future__ import annotations


def run_tier0(*args, **kwargs):  # noqa: ANN002, ANN003, ANN201
    raise NotImplementedError("pipeline-agent: implement tier0")
