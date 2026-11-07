"""Claims: find claims, match approved facts, ask Jev whether each fact supports the claim,
and decide the status in code. OWNER: pipeline-agent.

Claims come from Tier 0 triggers (grouped per clause: superlative, eco, factual) or from Jev's
`has_claim` + `claim_type` (objective/comparative) on a unit with no trigger.

Decision table (code, not the model; p = Jev support probability, numbers = digits in the claim
excluding dates):
1. A current fact with p >= SUPPORT_AT                                  -> supported (no finding)
2. An expired fact with p >= SUPPORT_AT                                 -> outdated (cites fact + valid_until)
3. Every number in the claim appears in candidate facts with p >= HUMAN_AT
   (claims that combine two facts, e.g. "12 cafes, open 8 am to 10 pm") -> supported, or outdated
   if a number is only covered by an expired fact
4. otherwise                                                            -> unsupported, problem p = 1 - max current p
No candidate fact at all (token-set ratio < 40)                         -> unsupported, p = 1.0 (code)
Eco claims ("sustainable", "organic") only consider facts that mention the same term.
Claims Jev found without a trigger word: partial support (p >= HUMAN_AT) counts, at most "human".
Without Jev (no key, confidential, or a failed call): rule 3 runs on number matching alone
(candidates with ratio >= 50); everything else is "unsupported (no Jev)" in the human band.
Expiry is always compared in code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from rapidfuzz import fuzz

from app.pipeline import jev
from app.pipeline import questions as Q
from app.pipeline.bands import band as to_band
from app.pipeline.context import CheckContext, FactSpec, RuleSpec
from app.pipeline.tier0 import ClaimTrigger, Tier0Hit, _DATE, claim_triggers, sentences

MIN_FACT_SCORE = 40
NO_JEV_FACT_SCORE = 50
TOP_K = 3

FACTUAL_GROUPS = ("number", "price", "award")


def is_expired(valid_until: date | None, today: date | None = None) -> bool:
    """True if `valid_until` is set and strictly before today."""
    if valid_until is None:
        return False
    return valid_until < (today or date.today())


@dataclass
class Claim:
    group: str  # superlative|eco|factual
    rule_code: Optional[str]
    start: int  # local span (first..last trigger)
    end: int
    text: str  # the clause sent to Jev and used for fact matching
    triggers: list[ClaimTrigger] = field(default_factory=list)
    from_jev: bool = False  # found by has_claim/claim_type, not by a trigger


@dataclass
class ClaimResult:
    claim: Claim
    status: str  # supported|unsupported|outdated
    rule_code: Optional[str]
    fact_code: Optional[str] = None
    fact_valid_until: Optional[date] = None
    probability: Optional[float] = None  # probability of a problem (None = decided without Jev)
    band: str = "pass"
    via: str = "jev"  # jev|numbers|no_facts|no_jev
    candidates: list[dict] = field(default_factory=list)  # [{code, score, p, expired}]
    error: Optional[str] = None

    @property
    def is_finding(self) -> bool:
        return self.status != "supported" and self.band != "pass"


# ------------------------------------------------------------------ claim extraction
_CLAUSE_SPLIT = re.compile(r",\s+(?:and|but|while|so|plus)\s+|;\s+|\s+[–—-]\s+|:\s+")
_NUM_TOKEN = re.compile(r"(?<![A-Za-z\d])\d[\d,]*(?:\.\d+)?")


def clauses(text: str) -> list[tuple[int, int]]:
    out = []
    for s, e in sentences(text):
        pos = s
        for m in _CLAUSE_SPLIT.finditer(text, s, e):
            if text[pos:m.start()].strip():
                out.append((pos, m.start()))
            pos = m.end()
        if text[pos:e].strip():
            out.append((pos, e))
    return out or [(0, len(text))]


def _rule_for(rules: list[RuleSpec], key: str) -> Optional[RuleSpec]:
    return next((r for r in rules if r.enabled and key in (r.params or {})), None)


def claim_rule_codes(rules: list[RuleSpec]) -> dict[str, Optional[str]]:
    sup = _rule_for(rules, "superlative_patterns")
    eco = _rule_for(rules, "claim_terms")
    price = _rule_for(rules, "price_pattern")
    expired = _rule_for(rules, "reject_expired_facts")
    generic = next((r for r in rules if r.enabled and r.check_method == "claim" and (r.params or {}).get("needs_supporting_fact")), None)
    factual = expired or generic
    return {
        "superlative": (sup or factual).code if (sup or factual) else None,
        "eco": (eco or factual).code if (eco or factual) else None,
        "price": (price or factual).code if (price or factual) else None,
        "factual": factual.code if factual else None,
        "outdated": factual.code if factual else None,
    }


def extract_claims(text: str, ctx: CheckContext, covered: list[Tier0Hit] | None = None) -> list[Claim]:
    """Group trigger spans by clause into superlative / eco / factual claims.

    Triggers already fully covered by a banned-word hit (e.g. "guaranteed") are not claims again.
    """
    rules = ctx.enabled_rules()
    codes = claim_rule_codes(rules)
    covered = [h for h in (covered or []) if h.kind in ("banned", "term") and h.span_start is not None]
    trig = [t for t in claim_triggers(text, rules)
            if not any(h.span_start <= t.start and t.end <= h.span_end for h in covered)]  # type: ignore[operator]
    out: list[Claim] = []
    for cs, ce in clauses(text):
        inside = [t for t in trig if cs <= t.start < ce]
        if not inside:
            continue
        for group in ("superlative", "eco", "factual"):
            ts = [t for t in inside if (t.group in FACTUAL_GROUPS if group == "factual" else t.group == group)]
            if group == "factual":
                ts_meaningful = [t for t in ts if t.group != "award" or any(x.group != "award" for x in ts) or _award_is_claim(text, t)]
                ts = ts_meaningful
            if not ts:
                continue
            if group == "factual" and all(t.group == "award" for t in ts) and any(t.group == "eco" for t in inside):
                continue  # "certified organic": the eco claim covers it
            code = codes["price"] if group == "factual" and any(t.group == "price" for t in ts) else codes[group]
            out.append(Claim(group=group, rule_code=code, start=min(t.start for t in ts), end=max(t.end for t in ts),
                             text=text[cs:ce].strip(), triggers=ts))
    return out


def _award_is_claim(text: str, t: ClaimTrigger) -> bool:
    """A lone award word counts ("won Gold"), a lone "rated" only with a number nearby."""
    return t.text.lower() != "rated"


def unit_claim(text: str, ctx: CheckContext, claim_type: str) -> Claim:
    """A claim Jev found in a unit with no trigger: the whole unit (decided more leniently, see decide)."""
    codes = claim_rule_codes(ctx.enabled_rules())
    group = "superlative" if claim_type == "comparative" else "factual"
    return Claim(group=group, rule_code=codes[group], start=0, end=len(text), text=text.strip(), from_jev=True)


# ------------------------------------------------------------------ fact matching
def _fact_text(f: FactSpec) -> str:
    return f"{f.statement} {f.value}".strip()


def top_facts(claim_text: str, facts: list[FactSpec], k: int = TOP_K, min_score: float = MIN_FACT_SCORE) -> list[tuple[FactSpec, float]]:
    scored = [(f, float(fuzz.token_set_ratio(claim_text.lower(), _fact_text(f).lower()))) for f in facts]
    scored = [x for x in scored if x[1] >= min_score]
    return sorted(scored, key=lambda x: -x[1])[:k]


def numbers(text: str) -> set[str]:
    """Digits in a text, normalised ("1,299" -> "1299"), ignoring dates and codes like MONSOON20."""
    no_dates = _DATE.sub(" ", text)
    return {m.group(0).replace(",", "").rstrip(".") for m in _NUM_TOKEN.finditer(no_dates)}


def _coverage(claim_nums: set[str], cands: list[tuple[FactSpec, float, Optional[float]]], today: date,
              min_p: Optional[float]) -> Optional[tuple[str, Optional[FactSpec]]]:
    """Rule 3. Returns ("supported", None) / ("outdated", expired_fact) / None if not covered."""
    if not claim_nums:
        return None
    usable = [(f, p) for f, _, p in cands if min_p is None or (p is not None and p >= min_p)]
    current = set().union(*[numbers(_fact_text(f)) for f, _ in usable if not is_expired(f.valid_until, today)] or [set()])
    expired_facts = [(f, p) for f, p in usable if is_expired(f.valid_until, today)]
    allnums = current | set().union(*[numbers(_fact_text(f)) for f, _ in expired_facts] or [set()])
    if not claim_nums <= allnums:
        return None
    if claim_nums <= current:
        return "supported", None
    missing = claim_nums - current
    best = max((x for x in expired_facts if numbers(_fact_text(x[0])) & missing), key=lambda x: x[1] or 0, default=None)
    return "outdated", best[0] if best else None


def decide(claim: Claim, cands: list[tuple[FactSpec, float, Optional[float]]], ctx: CheckContext,
           jev_ok: bool, error: Optional[str] = None) -> ClaimResult:
    """The decision table in the module docstring. `cands` = [(fact, ratio, p_support|None)]."""
    codes = claim_rule_codes(ctx.enabled_rules())
    today = ctx.today
    info = [{"code": f.code, "score": round(s, 1), "p": None if p is None else round(p, 3),
             "expired": is_expired(f.valid_until, today)} for f, s, p in cands]
    base = dict(claim=claim, candidates=info, error=error)

    def outdated(f: FactSpec, p: Optional[float], via: str) -> ClaimResult:
        b = to_band(p, "claims") if p is not None else "human"
        return ClaimResult(status="outdated", rule_code=codes["outdated"] or claim.rule_code, fact_code=f.code,
                           fact_valid_until=f.valid_until, probability=p, band=b, via=via, **base)

    if not cands:
        return ClaimResult(status="unsupported", rule_code=claim.rule_code, probability=1.0, band="flag", via="no_facts", **base)

    claim_nums = numbers(claim.text) if claim.group == "factual" else set()
    if jev_ok:
        cur = [(f, p) for f, _, p in cands if p is not None and not is_expired(f.valid_until, today)]
        exp = [(f, p) for f, _, p in cands if p is not None and is_expired(f.valid_until, today)]
        best_cur = max(cur, key=lambda x: x[1], default=None)
        best_exp = max(exp, key=lambda x: x[1], default=None)
        if best_cur and best_cur[1] >= Q.SUPPORT_AT:
            return ClaimResult(status="supported", rule_code=None, fact_code=best_cur[0].code, probability=1 - best_cur[1],
                               band="pass", via="jev", **base)
        if best_exp and best_exp[1] >= Q.SUPPORT_AT:
            return outdated(best_exp[0], best_exp[1], "jev")
        cov = _coverage(claim_nums, cands, today, Q.HUMAN_AT)
        if cov and cov[0] == "supported":
            return ClaimResult(status="supported", rule_code=None, probability=None, band="pass", via="numbers", **base)
        if cov and cov[0] == "outdated" and cov[1] is not None:
            p = next((p for f, _, p in cands if f is cov[1]), None)
            return outdated(cov[1], p, "numbers")
        p_problem = 1 - (best_cur[1] if best_cur else 0.0)
        b = to_band(p_problem, "claims")
        if claim.from_jev:
            # No trigger word: partial support (>= HUMAN_AT) counts, and it can only ask a human.
            if best_cur and best_cur[1] >= Q.HUMAN_AT:
                return ClaimResult(status="supported", rule_code=None, probability=round(p_problem, 4), band="pass", via="jev", **base)
            b = "human" if b == "flag" else b
        return ClaimResult(status="unsupported", rule_code=claim.rule_code, probability=round(p_problem, 4),
                           band=b, via="jev", **base)

    # no Jev: numbers only, everything else needs a human
    strong = [(f, s, p) for f, s, p in cands if s >= NO_JEV_FACT_SCORE]
    cov = _coverage(claim_nums, strong, today, None)
    if cov and cov[0] == "supported":
        return ClaimResult(status="supported", rule_code=None, probability=None, band="pass", via="numbers", **base)
    if cov and cov[0] == "outdated" and cov[1] is not None:
        return outdated(cov[1], None, "numbers")
    return ClaimResult(status="unsupported", rule_code=claim.rule_code, probability=None, band="human", via="no_jev", **base)


# ------------------------------------------------------------------ Jev support check
async def check_claim(claim: Claim, ctx: CheckContext, *, use_jev: bool, calls: Optional[jev.CallLog] = None,
                      unit_id: Optional[int] = None, masked_text: Optional[str] = None, purpose: str = "claims") -> ClaimResult:
    """One Jev call per claim: state {"text": claim}, one support Noul per candidate fact."""
    facts = ctx.facts
    if claim.group == "eco":  # only facts about the same term can support "organic", "sustainable", ...
        terms = [t.text.lower() for t in claim.triggers]
        facts = [f for f in facts if any(t in _fact_text(f).lower() for t in terms)]
    found = top_facts(claim.text, facts)
    if not found:
        return decide(claim, [], ctx, jev_ok=use_jev)
    if not use_jev:
        return decide(claim, [(f, s, None) for f, s in found], ctx, jev_ok=False)
    qs = {Q.support_key(f.code): Q.support_question(f) for f, _ in found}
    res = await jev.ask(Q.claim_state(masked_text if masked_text is not None else claim.text), qs, purpose=purpose,
                        calls=calls, unit_id=unit_id)
    if not res.ok:
        return decide(claim, [(f, s, None) for f, s in found], ctx, jev_ok=False, error=res.error)
    return decide(claim, [(f, s, res.nouls.get(Q.support_key(f.code))) for f, s in found], ctx, jev_ok=True)
