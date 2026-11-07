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

import asyncio
import re
from dataclasses import dataclass, field
from typing import Optional

from app.pipeline import claims as C
from app.pipeline import jev
from app.pipeline import lang as L
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


# Deterministic superlative list (code, not a model). A fix may never contain one of these: the brand
# has no approved fact that ranks it above others, and R-CLAIM-01's own patterns miss some forms
# ("favourite" alone, curly apostrophes, "leading").
SUPERLATIVES = [
    r"#\s?1\b", r"\bnumber (one|1)\b", r"\bno\.? ?1\b", r"\bbest\b", r"\bfinest\b", r"\bgreatest\b",
    r"\bfavou?rites?\b", r"\bleading\b", r"\bthe top\b", r"\btop[- ](rated|choice|pick|brand|quality|notch|selling)\b",
    r"\bmost (loved|popular|trusted|awarded|famous|preferred)\b", r"\bbest[- ]selling\b", r"\bunbeatable\b",
    r"\bunmatched\b", r"\bunrivall?ed\b", r"\bultimate\b", r"\bworld[- ]class\b", r"\blike no other\b",
    r"\bsecond to none\b", r"\bever (taste|tasted|had|try|tried)\b",
]


def superlatives_in(text: str, ctx: Optional[CheckContext] = None) -> list[str]:
    """Superlative phrases in `text` (apostrophes normalised), plus the brand's own R-CLAIM-01 patterns."""
    t = text.replace("\u2019", "'").replace("\u2018", "'")
    pats = list(SUPERLATIVES)
    if ctx is not None:
        for r in ctx.enabled_rules():
            pats += list((r.params or {}).get("superlative_patterns") or [])
    found: list[str] = []
    for p in pats:
        try:
            for m in re.finditer(p, t, re.I):
                if m.group(0) not in found:
                    found.append(m.group(0))
        except re.error:
            continue
    return found


_TERMINAL = ".!?…।॥"  # includes the Devanagari danda and double danda
_SENT_END = _TERMINAL + "\"”')"
_DOUBLED = re.compile(r"\b(\w+)\s+\1\b", re.I)


def well_formed(text: str, start: int, end: int, fix: str) -> list[str]:
    """Code checks that a replacement of text[start:end] yields well-formed text (no model involved)."""
    region = text[start:end]
    f = fix.strip()
    reasons: list[str] = []
    if not f:
        return ["the fix is empty"]
    if region.rstrip() and region.rstrip()[-1] in _TERMINAL and f[-1] not in _SENT_END:
        reasons.append("the fix doesn't end the sentence with punctuation")
    starts_sentence = not text[:start].strip() or text[:start].rstrip()[-1] in _TERMINAL + "\n"
    if starts_sentence and region[:1].isupper() and f[:1].isalpha() and f[:1].islower():
        reasons.append("the fix doesn't start like a sentence")
    if len(f) > max(2 * len(region), len(region) + 80):
        reasons.append("the fix is much longer than the text it replaces")
    lo, hi = max(0, start - 25), min(len(text), end + 25)
    before_win, after_win = text[lo:hi], text[lo:start] + f + text[end:hi]
    old = {m.group(0).lower() for m in _DOUBLED.finditer(before_win)}
    dbl = [m.group(0) for m in _DOUBLED.finditer(after_win) if m.group(0).lower() not in old]
    if dbl:
        reasons.append(f"the fix doubles a word (“{dbl[0]}”)")
    dup = boundary_duplicate(text, start, end, fix)
    if dup:
        reasons.append(f"the fix repeats “{dup}”, which is already next to it")
    return reasons


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
    extra_rules: Optional[list[RuleSpec]] = None,
    region: bool = False,
    meaning_original: Optional[str] = None,
    removal_required: bool = False,
) -> SelfCheckResult:
    """`deterministic` = the fix came from the brand word list (e.g. old -> new product name), so the
    meaning check is skipped: changing that word is the point of the fix.
    `extra_rules` = the other rules of an edit region (one fix for several findings): the fix must
    satisfy all of them. `region` = the fix rewrites whole sentences, so every claim left in it is
    re-checked (not only new ones) and the text must stay well-formed. `meaning_original` = the unit with
    the word-list renames already applied, so the meaning check doesn't read a product rename as a new topic."""
    original = unit.text
    fixed = apply_fix(original, start, end, fix)
    span = (start, start + len(fix))
    reasons: list[str] = []
    if fixed.strip() == original.strip():
        return SelfCheckResult(False, ["the fix doesn't change the text"], fixed)
    if region:
        wf = well_formed(original, start, end, fix)
        if wf:
            return SelfCheckResult(False, wf, fixed)
    else:
        dup = boundary_duplicate(original, start, end, fix)
        if dup:
            return SelfCheckResult(False, [f"the fix repeats “{dup}”, which is already next to it"], fixed)
    rules = [r for r in [rule, *(extra_rules or [])] if r is not None]
    rules = [r for i, r in enumerate(rules) if r.code not in {x.code for x in rules[:i]}]
    if not deterministic:
        # A fix stays in the language and script of the text it replaces (Marathi stays Marathi in Devanagari,
        # Hinglish stays Hinglish). Code check, before any model is asked.
        wrong = L.fix_language_problem(original[start:end], fix)
        if wrong:
            return SelfCheckResult(False, [wrong], fixed)
        sup = superlatives_in(fix, ctx)
        if sup:
            return SelfCheckResult(False, [f"the fix uses an unsupported superlative (“{sup[0]}”)"], fixed)

    # 1. Tier 0
    post = ctx.post_text
    if unit.char_start is not None and post:
        post = post[:unit.char_start] + fixed + post[unit.char_start + len(original):]
    fctx = _with_post(ctx, post or fixed)
    before = run_tier0_text(original, _with_post(ctx, ctx.post_text or original), PostState())
    after = run_tier0_text(fixed, fctx, PostState())
    codes = {r.code for r in rules}
    for h in after:
        inside = h.span_start is not None and h.span_start < span[1] and span[0] < (h.span_end or 0)
        if inside and h.rule_code in codes:
            reasons.append(f"the fix still breaks {h.rule_code} (“{h.quote}”)")
            break
        if inside and region and h.rule_code and h.kind != "readability":  # e.g. a rewrite that brings back "Steam Classic"
            reasons.append(f"the fix breaks {h.rule_code} (“{h.quote}”)")
            break
    if _blocking(after) > _blocking(before):
        reasons.append("the fix adds a new blocking problem")

    # 2. Claims inside the fixed region (claims that were already there are their own findings)
    old_sigs = {_sig(c) for c in C.extract_claims(original, _with_post(ctx, ctx.post_text or original), covered=before)}
    for cl in C.extract_claims(fixed, fctx, covered=after):
        if cl.end <= span[0] or cl.start >= span[1] or (_sig(cl) in old_sigs and not region):
            continue
        masked = mask_pii(cl.text)[0] if use_jev else None
        res = await C.check_claim(cl, fctx, use_jev=use_jev, calls=calls, unit_id=unit.id, masked_text=masked, purpose="selfcheck")
        if res.status != "supported":
            reasons.append(f"the fix makes a claim that no current approved fact supports (“{cl.text[:80]}”)")
            break

    # 3. Jev. Rule, superlative and same-topic questions are about the fixed paragraph; the call-to-action questions
    # are about the whole post, since a post keeps one call to action (R-MSG-02) and a fix may drop a second one.
    # Both calls run in parallel.
    if use_jev and not reasons:
        mo = meaning_original or original
        whole = bool(ctx.post_text and unit.char_start is not None and ctx.post_text.strip() != original.strip())
        m_state = (Q.revision_state(mask_pii(ctx.post_text[:unit.char_start] + mo + ctx.post_text[unit.char_start + len(original):])[0],
                                    mask_pii(post)[0], ctx.channel)
                   if whole and unit.char_start is not None else Q.revision_state(mask_pii(mo)[0], mask_pii(fixed)[0], ctx.channel))
        u_state = Q.revision_state(mask_pii(mo)[0], mask_pii(fixed)[0], ctx.channel)
        # same_topic is judged on the paragraph (on a long, mostly identical post it is noisy); the call to action
        # on both the paragraph and the whole post (questions.meaning_kept_split).
        meaning_qs: dict = {} if deterministic else {k: q for k, q in Q.MEANING_QUESTIONS.items() if k != "same_topic"}
        qs: dict = {} if deterministic else {**Q.MEANING_QUESTIONS,
                                             "same_topic": Q.same_topic_question(m_state["original"] if whole else None),
                                             "new_superlative": Q.new_superlative_question(mask_pii(fix)[0])}
        targets = [r for r in text_rules_for_jev(ctx) if r.severity == "blocking"]
        for r in rules:
            if r.check_method == "jev" and r.code not in {t.code for t in targets}:
                targets.append(r)
        for r in targets:
            qs[r.code] = Q.rule_question(r, field="revised")
        if not whole:
            meaning_qs = {}
        asks = [jev.ask(u_state, qs, purpose="selfcheck", calls=calls, unit_id=unit.id)] if qs else []
        if meaning_qs:
            asks.append(jev.ask(m_state, meaning_qs, purpose="selfcheck", calls=calls, unit_id=unit.id))
        results = await asyncio.gather(*asks)
        nouls: dict[str, float] = dict(results[0].nouls) if qs and results else {}
        post_nouls: dict[str, float] = dict(results[-1].nouls) if meaning_qs and results else {}
        if not all(res.ok for res in results):
            reasons.append("the self-check couldn't run (Jev error)")
        else:
            if not deterministic and not Q.meaning_kept_split(nouls, post_nouls, removal_required):
                reasons.append("the fix changes the core message or call to action")
            sp = nouls.get("new_superlative")
            if not deterministic and (sp is None or sp >= Q.SELFCHECK_RULE_MAX):
                reasons.append("the fix ranks the product above others (an unsupported superlative)")
            for r in targets:
                p = nouls.get(r.code)
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
