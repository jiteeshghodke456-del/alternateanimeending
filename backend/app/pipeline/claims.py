"""Claims: find claims, match approved facts, ask Jev whether each fact supports the claim,
and decide the status in code. OWNER: pipeline-agent.

Claims come from Tier 0 triggers (grouped per clause: superlative, eco, factual) or from Jev's
`has_claim` + `claim_type` (objective/comparative) on a unit with no trigger.

Decision table (code, not the model; p = Jev support probability, numbers = digits in the claim
excluding dates):
1. A current fact with p >= SUPPORT_AT                                  -> supported (no finding), unless
   1b. a number in the claim is held only by an expired candidate fact    -> outdated (human band, code)
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
Atomic claims (`atomic`, after the table): a clause with 2+ figures is also judged figure by figure, and when
only some figures are covered by facts, only the uncovered ones are reported (ClaimResult.spans).

Check-worthiness gate (`gate`, applied after the table above when the engine asks for it, i.e. a live Jev
check; selfcheck does not). The claim's Jev call also asks `claim_kind` (Choice: comparative_or_superlative,
service_promise, health_or_safety, first_party_statistic, opinion_or_puffery) and its companion Noul
`claim_checkable` (high = a factual claim), plus the Noul `claim_record` (high = the brand reporting what already
happened in its own orders, customers or sales). "first-party" = kind first_party_statistic, or claim_record >= 0.70
with kind comparative_or_superlative / service_promise (a record can read like a ranking or a promise: "the fastest
delivery happened in 3 minutes"; health claims never count). "named" = the words came from a rule's own superlative_patterns
("best" under R-CLAIM-01 / IN-ASCI-02): the brand or regulation named them, so the rule decides. Eco claims
(a rule's claim_terms list) skip the gate. Code, first matching row wins:

  status supported                                          -> unchanged (no finding)
  status outdated, or a current fact contradicts it         -> unchanged (evidence says otherwise)
  not named and claim_checkable < HUMAN_AT                  -> not_a_claim (no finding)
  not named and kind = opinion_or_puffery                   -> not_a_claim (no finding; tone rules cover it)
  first-party (see above)                                   -> reminder: rule_code None, band "human" at most
                                                               (the brand reporting its own data is unverified,
                                                               not false; "Add as fact" clears it)
  kind = comparative / service_promise / health_or_safety   -> unchanged: flag when a rule asks for evidence,
                                                               a reminder when none does (engine.from_claim)
  ... comparative / service with HUMAN_AT <= claim_record < 0.70 -> band capped at "human", rule kept (may be a
                                                               record: the Noul is unsettled, so a person decides)
  ... and not named with HUMAN_AT <= claim_checkable < 0.70 -> band capped at "human" (unsure it is a claim)
No Jev, a confidential item or a failed call: no gate (the code-only table above).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from rapidfuzz import fuzz

from app.pipeline import jev
from app.pipeline import lang as L
from app.pipeline import questions as Q
from app.pipeline.bands import band as to_band
from app.pipeline.context import CheckContext, FactSpec, RuleSpec
from app.pipeline.tier0 import ClaimTrigger, Tier0Hit, _DATE, claim_triggers, in_protected, protected_spans, sentences
from app.clock import local_today

MIN_FACT_SCORE = 40
NO_JEV_FACT_SCORE = 50
TOP_K = 3

FACTUAL_GROUPS = ("number", "price", "award")


def is_expired(valid_until: date | None, today: date | None = None) -> bool:
    """True if `valid_until` is set and strictly before today."""
    if valid_until is None:
        return False
    return valid_until < (today or local_today())


@dataclass(frozen=True)
class Figure:
    """One number in a claim (atomic claims): unit-local span, the words shown ("10x", "₹4.3 lakh") and its
    normalised values (numbers())."""
    start: int
    end: int
    label: str
    values: frozenset[str]


@dataclass
class Claim:
    group: str  # superlative|eco|factual
    rule_code: Optional[str]
    start: int  # local span (first..last trigger)
    end: int
    text: str  # the clause sent to Jev and used for fact matching
    triggers: list[ClaimTrigger] = field(default_factory=list)
    from_jev: bool = False  # found by has_claim/claim_type, not by a trigger
    figures: list[Figure] = field(default_factory=list)  # numbers in the claim, for atomic coverage


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
    kind: Optional[str] = None  # claim_kind (Jev), when the gate ran
    p_checkable: Optional[float] = None  # claim_checkable (Jev), when the gate ran
    p_record: Optional[float] = None  # claim_record (Jev), when the gate ran
    gated: Optional[str] = None  # the gate row that changed the outcome (None = unchanged)
    spans: list[tuple[int, int]] = field(default_factory=list)  # unit-local spans to report (atomic); [] = the claim span

    @property
    def is_finding(self) -> bool:
        return self.status not in ("supported", NOT_A_CLAIM) and self.band != "pass"


NOT_A_CLAIM = "not_a_claim"


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
    spans = protected_spans(text)
    trig = [t for t in trig if not in_protected(t.start, t.end, spans)]  # digits in links/phones aren't claims
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
                             text=text[cs:ce].strip(), triggers=ts,
                             figures=figures(text, ts) if group == "factual" else []))
    return out


_FIGURE_TAIL = re.compile(r"[^\s,;:.!?()\[\]\"“”]*")


def figures(text: str, triggers: list[ClaimTrigger]) -> list[Figure]:
    """The numbers among a claim's triggers, with Indian scale words ("₹4.3 lakh" -> 430000) and unit tails
    ("10x", "491%") kept in the label. Code only."""
    scaled = L.scaled_numbers(text) if len(L.ascii_digits(L.nfc(text))) == len(text) else []
    out: list[Figure] = []
    for t in sorted(triggers, key=lambda t: t.start):
        if t.group not in ("number", "price"):
            continue
        sc = next(((a, b, v) for a, b, v in scaled if a < t.end and t.start < b), None)
        if sc is not None:
            s0, e0, vals = min(sc[0], t.start), sc[1], {sc[2]}
        else:
            s0, e0 = t.start, t.end + len(_FIGURE_TAIL.match(text, t.end).group(0))  # type: ignore[union-attr]
            vals = numbers(t.text)
        if vals and not (out and out[-1].start <= s0 < out[-1].end):
            out.append(Figure(s0, e0, text[s0:e0], frozenset(vals)))
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
    """Facts that may be about the same thing as the claim (rapidfuzz token-set ratio).

    Facts are written in English. A claim in another language or script (Hindi, Marathi, Tamil, Hinglish)
    shares almost no words with them, so there a fact that contains every number of the claim is also a
    candidate for Jev (score MIN_FACT_SCORE). A shared number alone is weak evidence ("40 कैफ़े" vs "40 hours"),
    so without Jev these candidates are not strong enough for rule 3 and the claim goes to a human."""
    scored = [(f, float(fuzz.token_set_ratio(claim_text.lower(), _fact_text(f).lower()))) for f in facts]
    if not L.detect(claim_text).is_english:
        nums = numbers(claim_text)
        if nums:
            scored = [(f, max(sc, float(MIN_FACT_SCORE)) if nums <= numbers(_fact_text(f)) else sc) for f, sc in scored]
    scored = [x for x in scored if x[1] >= min_score]
    return sorted(scored, key=lambda x: -x[1])[:k]


def numbers(text: str) -> set[str]:
    """Digits in a text, normalised ("1,299" and "1,00,000" -> plain digits; Devanagari ०-९ -> 0-9;
    "1 लाख" / "2 crore" / "५० हजार" -> their value), ignoring dates and codes like MONSOON20."""
    t = L.ascii_digits(L.nfc(text))
    out: set[str] = set()
    for s, e, v in reversed(L.scaled_numbers(t)):
        out.add(v)
        t = t[:s] + " " * (e - s) + t[e:]
    no_dates = _DATE.sub(" ", t)
    return out | {m.group(0).replace(",", "").rstrip(".") for m in _NUM_TOKEN.finditer(no_dates)}


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


def _number_only_in_expired(claim_nums: set[str], cands: list[tuple[FactSpec, float, Optional[float]]],
                            today: date) -> Optional[FactSpec]:
    """Rule 1b (code): a number in the claim that no current candidate fact holds, but an expired one does."""
    if not claim_nums:
        return None
    current = set().union(*[numbers(_fact_text(f)) for f, _, _ in cands if not is_expired(f.valid_until, today)] or [set()])
    missing = claim_nums - current
    return next((f for f, _, _ in cands if is_expired(f.valid_until, today) and numbers(_fact_text(f)) & missing), None)


def figure_key(fact_code: str, i: int) -> str:
    return f"fig_{fact_code}_{i}"


def figure_questions(claim: Claim, found: list[tuple[FactSpec, float]], today: date) -> dict[str, Any]:
    """Atomic claims: for a claim with 2+ figures, one Noul per (current candidate fact, figure) where the fact
    holds the figure's digits (code pre-filters), so Jev only confirms it is the same thing."""
    if len(claim.figures) < 2:
        return {}
    qs: dict[str, Any] = {}
    for f, _ in found:
        if is_expired(f.valid_until, today):
            continue
        fnums = numbers(_fact_text(f))
        for i, fig in enumerate(claim.figures):
            if fig.values <= fnums:
                qs[figure_key(f.code, i)] = Q.figure_support_question(f, fig.label)
    return qs


def atomic(r: ClaimResult, cands: list[tuple[FactSpec, float, Optional[float]]], today: date, jev_ok: bool,
           fig_p: Optional[dict[str, float]] = None) -> ClaimResult:
    """Atomic claims (code): a clause with 2+ figures ("Rajkot 10x, Ludhiana 7x") is judged figure by figure.
    A figure is covered when a current candidate fact holds its digits and Jev confirms that fact for it
    (figure Noul >= HUMAN_AT; without Jev: token-set ratio >= NO_JEV_FACT_SCORE). In a clause Jev already called
    supported, holding the digits is enough (as in relied_facts). Then:
      none covered                    -> unchanged (the whole claim)
      all covered, status unsupported -> supported (via "figures")
      some covered                    -> only the uncovered figures are reported (`spans`, merged when adjacent);
                                         a clause Jev called supported becomes unsupported for those figures
                                         (p = 1 - best figure support, 1.0 when no fact holds the digits)
    Outdated claims are left alone."""
    figs = r.claim.figures
    if r.claim.group != "factual" or len(figs) < 2 or r.status == "outdated":
        return r
    fig_p = fig_p or {}
    covered, worst = [], 0.0
    for i, fig in enumerate(figs):
        best = 0.0
        for f, ratio, _ in cands:
            if is_expired(f.valid_until, today) or not fig.values <= numbers(_fact_text(f)):
                continue
            if r.status == "supported":
                best = 1.0  # the clause is supported: a figure a current candidate fact holds counts (as relied_facts)
            elif jev_ok:
                best = max(best, fig_p.get(figure_key(f.code, i), 0.0))
            elif ratio >= NO_JEV_FACT_SCORE:
                best = 1.0
        ok = best >= Q.HUMAN_AT
        covered.append(ok)
        if not ok:
            worst = max(worst, 1.0 - best)
    if not any(covered):
        return r
    if all(covered):
        if r.status == "unsupported":
            r.status, r.rule_code, r.band, r.via, r.probability = "supported", None, "pass", "figures", None
        return r
    runs: list[tuple[int, int]] = []
    prev_uncovered = False
    for fig, ok in zip(figs, covered):
        if ok:
            prev_uncovered = False
            continue
        if prev_uncovered:
            runs[-1] = (runs[-1][0], fig.end)
        else:
            runs.append((fig.start, fig.end))
        prev_uncovered = True
    r.spans = runs
    if r.status == "supported":
        p = round(worst, 4) if jev_ok else None
        r.status, r.rule_code, r.via, r.probability = "unsupported", r.claim.rule_code, "figures", p
        r.fact_code = None
        r.band = to_band(p, "claims") if p is not None else "human"
    return r


def decide(claim: Claim, cands: list[tuple[FactSpec, float, Optional[float]]], ctx: CheckContext,
           jev_ok: bool, error: Optional[str] = None, fig_p: Optional[dict[str, float]] = None) -> ClaimResult:
    """The decision table in the module docstring, then `atomic`. `cands` = [(fact, ratio, p_support|None)]."""
    r = _decide(claim, cands, ctx, jev_ok, error)
    return atomic(r, cands, ctx.today, jev_ok, fig_p) if cands else r


def _decide(claim: Claim, cands: list[tuple[FactSpec, float, Optional[float]]], ctx: CheckContext,
            jev_ok: bool, error: Optional[str] = None) -> ClaimResult:
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
            stale = _number_only_in_expired(claim_nums, cands, today)
            if stale is not None:  # rule 1b: "12 cafes, open 8 am to 10 pm" after the "12 cafes" fact expired
                return outdated(stale, None, "numbers")
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


# ------------------------------------------------------------------ check-worthiness gate
def named_by_rule(claim: Claim, rules: list[RuleSpec]) -> bool:
    """True if a superlative trigger matches a rule's own superlative_patterns (the rule named the words)."""
    pats = [str(p) for r in rules if r.enabled for p in (r.params or {}).get("superlative_patterns", [])]
    for t in claim.triggers:
        if t.group != "superlative":
            continue
        for p in pats:
            try:
                if re.search(p, t.text, re.IGNORECASE):
                    return True
            except re.error:
                continue
    return False


def is_first_party(kind: Optional[str], p_record: Optional[float]) -> bool:
    if kind == Q.KIND_FIRST_PARTY:
        return True
    return p_record is not None and p_record >= Q.CLAIM_RECORD_AT and kind in (Q.KIND_COMPARATIVE, Q.KIND_SERVICE)


def gate(cr: ClaimResult, kind: Optional[str], p_checkable: Optional[float], ctx: CheckContext,
         p_record: Optional[float] = None) -> ClaimResult:
    """The check-worthiness truth table in the module docstring. Pure; the answers come from Jev."""
    cr.kind, cr.p_checkable, cr.p_record = kind, p_checkable, p_record
    if kind is None or p_checkable is None or cr.claim.group == "eco":
        return cr
    if cr.status != "unsupported" or contradicting_fact(cr, ctx) is not None:
        return cr
    named = named_by_rule(cr.claim, ctx.enabled_rules())
    if not named and (p_checkable < Q.HUMAN_AT or kind == Q.KIND_PUFFERY):
        cr.status, cr.band, cr.rule_code = NOT_A_CLAIM, "pass", None
        cr.gated = "not_checkable" if p_checkable < Q.HUMAN_AT else "puffery"
        return cr
    if is_first_party(kind, p_record):
        if cr.rule_code is not None or cr.band == "flag":
            cr.gated = "first_party"
        cr.rule_code = None  # engine.from_claim turns a rule-less unsupported claim into a minor reminder
        cr.band = "pass" if cr.band == "pass" else "human"
        return cr
    if cr.band == "flag" and kind in (Q.KIND_COMPARATIVE, Q.KIND_SERVICE) and p_record is not None and p_record >= Q.HUMAN_AT:
        cr.band, cr.gated = "human", "maybe_record"  # may be the brand's own past data: a person decides
        return cr
    if not named and p_checkable < Q.CLAIM_IS_CLAIM_AT and cr.band == "flag":
        cr.band, cr.gated = "human", "unsure"
    return cr


# ------------------------------------------------------------------ Jev support check
async def check_claim(claim: Claim, ctx: CheckContext, *, use_jev: bool, calls: Optional[jev.CallLog] = None,
                      unit_id: Optional[int] = None, masked_text: Optional[str] = None, purpose: str = "claims",
                      gate_with: Optional[str] = None) -> ClaimResult:
    """One Jev call per claim: state {"text": claim}, one support Noul per candidate fact.
    `gate_with` (the masked paragraph, engine only): also ask claim_kind + claim_checkable in the same call and
    apply `gate`. A claim with no candidate fact then gets a call too (for the gate alone)."""
    facts = ctx.facts
    if claim.group == "eco":  # only facts about the same term can support "organic", "sustainable", ...
        terms = [t.text.lower() for t in claim.triggers]
        facts = [f for f in facts if any(t in _fact_text(f).lower() for t in terms)]
    found = top_facts(claim.text, facts)
    gating = use_jev and gate_with is not None and claim.group != "eco"
    if not found and not gating:
        return decide(claim, [], ctx, jev_ok=use_jev)
    if not use_jev:
        return decide(claim, [(f, s, None) for f, s in found], ctx, jev_ok=False)
    qs: dict = {Q.support_key(f.code): Q.support_question(f) for f, _ in found}
    fig_qs = figure_questions(claim, found, ctx.today)
    qs.update(fig_qs)
    if gating:
        qs["claim_kind"] = Q.claim_kind_question(gate_with)
        qs["claim_checkable"] = Q.claim_checkable_question(gate_with)
        qs["claim_record"] = Q.claim_record_question(gate_with)
    res = await jev.ask(Q.claim_state(masked_text if masked_text is not None else claim.text), qs, purpose=purpose,
                        calls=calls, unit_id=unit_id)
    if not res.ok:
        if not found:  # the call was only for the gate: decide as without it
            return decide(claim, [], ctx, jev_ok=True, error=res.error)
        return decide(claim, [(f, s, None) for f, s in found], ctx, jev_ok=False, error=res.error)
    cr = decide(claim, [(f, s, res.nouls.get(Q.support_key(f.code))) for f, s in found], ctx, jev_ok=True,
                fig_p={k: res.nouls[k] for k in fig_qs if k in res.nouls})
    if gating:
        kind = res.choices.get("claim_kind")
        cr = gate(cr, kind.choice if kind else None, res.nouls.get("claim_checkable"), ctx, res.nouls.get("claim_record"))
    return cr


# ------------------------------------------------------------------ claims ledger (Round 3, improvement 1)
CONTRADICT_SCORE = 60  # token-set ratio: the fact is about the same thing as the claim


def _fact_by_code(ctx: CheckContext, code: Optional[str]) -> Optional[FactSpec]:
    return next((f for f in ctx.facts if f.code == code), None) if code else None


def contradicting_fact(cr: ClaimResult, ctx: CheckContext) -> Optional[FactSpec]:
    """Ledger refinement of an *unsupported* factual claim (code, no model): a current fact about the
    same thing (ratio >= CONTRADICT_SCORE, Jev did not find support) states numbers, and none of the
    claim's numbers appear in it ("40 cafes" vs F-04 "12 cafes"). The finding itself stays "unsupported"."""
    if cr.status != "unsupported" or cr.claim.group != "factual" or cr.claim.from_jev:
        return None
    claim_nums = numbers(cr.claim.text)
    if not claim_nums:
        return None
    for c in sorted(cr.candidates, key=lambda c: -c["score"]):
        if c["expired"] or c["score"] < CONTRADICT_SCORE or (c["p"] is not None and c["p"] >= Q.HUMAN_AT):
            continue
        f = _fact_by_code(ctx, c["code"])
        fnums = numbers(_fact_text(f)) if f else set()
        if f and fnums and not (claim_nums & fnums):
            return f
    return None


def relied_facts(cr: ClaimResult, ctx: CheckContext) -> list[str]:
    """Codes of the approved facts this claim relies on (the ledger's "evidence"), primary first.
    supported/outdated -> the deciding fact; supported by numbers -> every candidate fact that holds one
    of the claim's numbers; contradicted -> the fact it contradicts; unsupported -> none."""
    out: list[str] = [cr.fact_code] if cr.fact_code else []
    if cr.status == "supported" and not out:
        # a claim Jev found without a trigger word, partly supported (p >= HUMAN_AT): the best current fact
        cur = [c for c in cr.candidates if not c["expired"] and c["p"] is not None and c["p"] >= Q.HUMAN_AT]
        if cur:
            out = [max(cur, key=lambda c: c["p"])["code"]]
    elif cr.status == "unsupported":
        f = contradicting_fact(cr, ctx)
        return [f.code] if f else []
    if cr.status in ("supported", "outdated") and cr.claim.group == "factual":
        # Numbers the primary fact doesn't hold come from other candidate facts ("12 cafes, open 8 am to
        # 10 pm" relies on F-04 and F-18). Code, not the model: a candidate whose text holds such a number.
        claim_nums = numbers(cr.claim.text)
        covered = set().union(*[numbers(_fact_text(f)) for f in (_fact_by_code(ctx, c) for c in out) if f] or [set()])
        for c in sorted(cr.candidates, key=lambda c: -(c["p"] or 0)):
            f = _fact_by_code(ctx, c["code"])
            if f is None or f.code in out or (c["expired"] and cr.status == "supported"):
                continue
            new = numbers(_fact_text(f)) & (claim_nums - covered)
            if new:
                out.append(f.code)
                covered |= new
    return out


def ledger_status(cr: ClaimResult, ctx: CheckContext) -> str:
    return "contradicted" if contradicting_fact(cr, ctx) is not None else cr.status


def support_probability(cr: ClaimResult, fact_code: Optional[str]) -> Optional[float]:
    """Jev's probability that `fact_code` supports the claim (None when decided without Jev)."""
    return next((c["p"] for c in cr.candidates if c["code"] == fact_code), None) if fact_code else None
