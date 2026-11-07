"""Re-check each proposed fix before it is shown as "verified". OWNER: pipeline-agent.

`verify_fix()` applies the fix to the unit text and checks, in code:
1. Tier 0 on the fixed unit: no hit of the same rule overlaps the fixed region, and no more
   blocking hits than before.
2. Claims in the fixed region are re-checked (claims.check_claim): each must be "supported".
   This catches a fix that swaps "#1" for another unsupported superlative.
3. With Jev: one call on {"original", "revised", "channel"} with the rule's Noul and every blocking
   Jev rule asked about `revised` (each must land in the "pass" band), plus the meaning questions
   (questions.meaning_kept truth table; skipped for word-list fixes such as a product rename). A failed call is not a pass: the fix stays unverified.
The engine retries the LLM once with the reasons; after that the UI shows "manual fix needed".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.pipeline import claims as C
from app.pipeline import jev
from app.pipeline import questions as Q
from app.pipeline.bands import band as to_band
from app.pipeline.context import CheckContext, RuleSpec, UnitIn
from app.pipeline.privacy import mask_pii
from app.pipeline.tier0 import PostState, run_tier0_text


@dataclass
class SelfCheckResult:
    verified: bool
    reasons: list[str] = field(default_factory=list)
    fixed_text: str = ""


def apply_fix(text: str, start: int, end: int, fix: str) -> str:
    return text[:start] + fix + text[end:]


def text_rules_for_jev(ctx: CheckContext) -> list[RuleSpec]:
    return [r for r in ctx.enabled_rules() if r.check_method == "jev" and r.category != "visual"]


async def verify_fix(
    unit: UnitIn,
    start: int,
    end: int,
    fix: str,
    rule: Optional[RuleSpec],
    ctx: CheckContext,
    *,
    use_jev: bool,
    calls: Optional[jev.CallLog] = None,
    deterministic: bool = False,
) -> SelfCheckResult:
    """`deterministic` = the fix came from the brand word list (e.g. old -> new product name), so the
    meaning check is skipped: changing that word is the point of the fix."""
    original = unit.text
    fixed = apply_fix(original, start, end, fix)
    region = (start, start + len(fix))
    reasons: list[str] = []
    if fixed.strip() == original.strip():
        return SelfCheckResult(False, ["the fix doesn't change the text"], fixed)
    dup = boundary_duplicate(original, start, end, fix)
    if dup:
        return SelfCheckResult(False, [f"the fix repeats “{dup}”, which is already next to it"], fixed)

    # 1. Tier 0
    post = ctx.post_text
    if unit.char_start is not None and post:
        post = post[:unit.char_start] + fixed + post[unit.char_start + len(original):]
    fctx = _with_post(ctx, post or fixed)
    before = run_tier0_text(original, _with_post(ctx, ctx.post_text or original), PostState())
    after = run_tier0_text(fixed, fctx, PostState())
    code = rule.code if rule else None
    for h in after:
        if code and h.rule_code == code and h.span_start is not None and h.span_start < region[1] and region[0] < (h.span_end or 0):
            reasons.append(f"the fix still breaks {code} (“{h.quote}”)")
            break
    if _blocking(after) > _blocking(before):
        reasons.append("the fix adds a new blocking problem")

    # 2. Claims inside the fixed region (claims that were already there are their own findings)
    old_sigs = {_sig(c) for c in C.extract_claims(original, _with_post(ctx, ctx.post_text or original), covered=before)}
    for cl in C.extract_claims(fixed, fctx, covered=after):
        if cl.end <= region[0] or cl.start >= region[1] or _sig(cl) in old_sigs:
            continue
        masked = mask_pii(cl.text)[0] if use_jev else None
        res = await C.check_claim(cl, fctx, use_jev=use_jev, calls=calls, unit_id=unit.id, masked_text=masked, purpose="selfcheck")
        if res.status != "supported":
            reasons.append(f"the fix makes a claim that no current approved fact supports (“{cl.text[:80]}”)")
            break

    # 3. Jev
    if use_jev and not reasons:
        qs: dict = {} if deterministic else dict(Q.MEANING_QUESTIONS)
        targets = [r for r in text_rules_for_jev(ctx) if r.severity == "blocking"]
        if rule is not None and rule.check_method == "jev" and rule not in targets:
            targets.append(rule)
        for r in targets:
            qs[r.code] = Q.rule_question(r, field="revised")
        res = await jev.ask(Q.revision_state(mask_pii(original)[0], mask_pii(fixed)[0], ctx.channel), qs,
                            purpose="selfcheck", calls=calls, unit_id=unit.id)
        if not res.ok:
            reasons.append("the self-check couldn't run (Jev error)")
        else:
            if not deterministic and not Q.meaning_kept(res.nouls):
                reasons.append("the fix changes the core message or call to action")
            for r in targets:
                p = res.nouls.get(r.code)
                if p is None or to_band(p, r.category) != "pass":
                    reasons.append(f"the fixed text may still break {r.code}")
    return SelfCheckResult(not reasons, reasons, fixed)


def boundary_duplicate(text: str, start: int, end: int, fix: str, min_len: int = 4) -> str:
    """The LLM sometimes rewrites past the excerpt ("Cheap" -> "Expensive? No way." before "? No way.").
    Returns the repeated words, or "" if the fix fits cleanly."""
    after, before = text[end:].lstrip(), text[:start].rstrip()
    f = fix.strip()
    for n in range(min(len(f), len(after)), min_len - 1, -1):
        if f[-n:].lower() == after[:n].lower() and not f[-n:].isspace():
            if len(f[-n:].strip()) >= min_len and any(ch.isalpha() for ch in f[-n:]):
                return f[-n:].strip()
    for n in range(min(len(f), len(before)), min_len - 1, -1):
        if f[:n].lower() == before[-n:].lower() and len(f[:n].strip()) >= min_len and any(ch.isalpha() for ch in f[:n]):
            return f[:n].strip()
    return ""


def _sig(cl: C.Claim) -> tuple[str, tuple[str, ...]]:
    return cl.group, tuple(sorted(t.text.lower() for t in cl.triggers))


def _blocking(hits: list) -> int:  # noqa: ANN001
    return sum(1 for h in hits if h.severity == "blocking")


def _with_post(ctx: CheckContext, post: str) -> CheckContext:
    from dataclasses import replace

    return replace(ctx, post_text=post)
