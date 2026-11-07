"""Precedent memory: human decisions teach the next check. OWNER: AI/ML (Jiteesh).

When a person dismisses a finding (with a reason) or a reviewer overrides a blocking one, a Precedent is
stored (models.Precedent, written by app/precedents.py). On later checks, every new finding is compared
with the workspace's active precedents for the same rule. Everything here is plain code: no model call.

Match (all three must hold):
1. Same rule code (any rule version; the card says when the rule has changed since).
2. The flagged words are the same phrase: the normalised precedent words appear as a phrase in the new
   sentence, or rapidfuzz token_set_ratio >= 90 between the two flagged texts.
3. The surrounding sentence (the "context window") is the same wording:
   - short texts (fewer than SHORT_TOKENS tokens on either side): exact phrase containment;
   - otherwise token_set_ratio >= 90 AND the new sentence keeps at least COVERAGE of the precedent's
     distinct tokens. token_set_ratio alone scores 100 whenever one token set is a subset of the other,
     so without the coverage guard "Free shipping" would inherit a precedent written for
     "Free shipping on orders above Rs 599".

Exclusions: inactive (revoked) precedents; precedents older than max_age_days; precedents whose reason is
"rule outdated" when the rule has been rewritten since (version bump); precedents from the same version
(a re-check of the item the decision was made on doesn't cite itself).

Band (code decides; never auto-dismisses, never touches workflow state, severity or blocking):
    flag  -> human
    human -> pass, listed as "Previously accepted"
    blocking rules never go below "human": a person must still look.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from rapidfuzz import fuzz

FUZZ_MIN = 90.0
SHORT_TOKENS = 4
COVERAGE = 0.67
MAX_QUOTE_CHARS = 300
MAX_CONTEXT_CHARS = 400
DEFAULT_MAX_AGE_DAYS = 180

DECISIONS = ("dismissed", "overridden")
BAND_LABEL = {"flag": "Flag", "human": "Needs a human look", "pass": "Pass"}
_SENTENCE_END = ".!?\n।॥"


# ------------------------------------------------------------------ text
def normalise(text: str) -> str:
    """NFKC, lower case, punctuation -> space (keeps letters, digits, Indic vowel signs, % and ₹)."""
    t = unicodedata.normalize("NFKC", text or "").lower()
    out = []
    for ch in t:
        cat = unicodedata.category(ch)
        if ch in "%₹":
            out.append(ch)
        elif cat.startswith("P") or (cat.startswith("S") and ch not in "%₹") or ch.isspace():
            out.append(" ")
        else:
            out.append(ch)
    norm = re.sub(r"\s+", " ", "".join(out)).strip()
    if not norm and t.strip():  # punctuation-only quotes ("!!!") compare as written
        return re.sub(r"\s+", " ", t).strip()
    return norm


def _tokens(norm: str) -> list[str]:
    return norm.split() if norm else []


def _contains_phrase(hay: str, needle: str) -> bool:
    if not needle or not hay:
        return False
    return f" {needle} " in f" {hay} "


def context_window(text: str, start: Optional[int], end: Optional[int], limit: int = MAX_CONTEXT_CHARS) -> str:
    """The sentence(s) around text[start:end], clipped to `limit` characters. "" when there is no span."""
    if not text or start is None or end is None or not (0 <= start <= end <= len(text)):
        return ""
    a = start
    while a > 0 and text[a - 1] not in _SENTENCE_END:
        a -= 1
    b = end
    while b < len(text) and text[b] not in _SENTENCE_END:
        b += 1
    if b < len(text):
        b += 1  # keep the sentence's own end mark
    s = text[a:b].strip()
    if len(s) > limit:  # centre the clip on the flagged words
        mid = (start + end) // 2 - a
        lo = max(0, min(len(s) - limit, mid - limit // 2))
        s = s[lo:lo + limit]
    return s


def flagged_words(text: str, quote: str, hit_start: Optional[int], hit_end: Optional[int]) -> str:
    """The words the rule flagged: the hit span for edit regions, else the finding's quote."""
    if text and hit_start is not None and hit_end is not None and 0 <= hit_start < hit_end <= len(text):
        return text[hit_start:hit_end]
    return quote or ""


# ------------------------------------------------------------------ matching
def text_match(prec: str, new: str) -> bool:
    """Same wording? Both already normalised."""
    if not prec or not new:
        return False
    pt, nt = _tokens(prec), _tokens(new)
    if len(pt) < SHORT_TOKENS or len(nt) < SHORT_TOKENS:
        return _contains_phrase(new, prec)
    if fuzz.token_set_ratio(prec, new) < FUZZ_MIN:
        return False
    ps = set(pt)
    return len(ps & set(nt)) / len(ps) >= COVERAGE


def quote_match(prec_quote: str, new_quote: str, new_context: str) -> bool:
    """Same flagged phrase? All normalised."""
    if not prec_quote or not new_quote:
        return False
    if prec_quote == new_quote or _contains_phrase(new_context or new_quote, prec_quote):
        return True
    return fuzz.token_set_ratio(prec_quote, new_quote) >= FUZZ_MIN and text_match(prec_quote, new_quote)


@dataclass
class PrecedentSpec:
    """A stored precedent as plain data (built from models.Precedent by app/precedents.py)."""

    id: int
    rule_code: str
    rule_version: int
    quote: str  # original flagged words (normalised on use)
    context: str  # the sentence around them
    decision: str  # dismissed|overridden
    reason: str  # false_alarm|approved_exception|rule_outdated|other|override
    note: Optional[str]
    by: str  # display name
    at: datetime
    item_id: Optional[int]
    version_id: Optional[int]
    item_title: str = ""
    active: bool = True


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def eligible(p: PrecedentSpec, rule_version: Optional[int], *, now: Optional[datetime] = None,
             max_age_days: int = DEFAULT_MAX_AGE_DAYS, version_id: Optional[int] = None) -> bool:
    """May this precedent be applied at all (before looking at the text)?"""
    if not p.active:
        return False
    if version_id is not None and p.version_id == version_id:
        return False
    now = now or datetime.now(timezone.utc)
    if max_age_days > 0 and (now - _aware(p.at)).total_seconds() > max_age_days * 86400:
        return False
    if p.reason == "rule_outdated" and rule_version is not None and rule_version > p.rule_version:
        return False  # the rule was rewritten since someone said it was out of date
    return True


def find_match(rule_code: Optional[str], rule_version: Optional[int], quote: str, context: str,
               precedents: Iterable[PrecedentSpec], *, now: Optional[datetime] = None,
               max_age_days: int = DEFAULT_MAX_AGE_DAYS, version_id: Optional[int] = None) -> Optional[PrecedentSpec]:
    """The most recent eligible precedent for this rule whose flagged words and sentence match, or None."""
    if not rule_code:
        return None
    nq = normalise(quote)
    nc = normalise(context) or nq
    best: Optional[PrecedentSpec] = None
    for p in precedents:
        if p.rule_code != rule_code or not eligible(p, rule_version, now=now, max_age_days=max_age_days, version_id=version_id):
            continue
        pq = normalise(p.quote)
        pc = normalise(p.context) or pq
        if quote_match(pq, nq, nc) and text_match(pc, nc):
            if best is None or _aware(p.at) > _aware(best.at):
                best = p
    return best


# ------------------------------------------------------------------ band (truth table)
def lower_band(band: str, severity: str) -> str:
    """One step down; blocking rules never below "human"."""
    if band == "flag":
        return "human"
    if band == "human":
        return "human" if severity == "blocking" else "pass"
    return band


def band_note(before: str, after: str, severity: str, decision: str) -> str:
    what = "dismissed" if decision == "dismissed" else "overridden"
    if before == after:
        if severity == "blocking":
            return (f"Seen before ({what}), but this is a blocking rule, so it stays at "
                    f"“{BAND_LABEL[after]}”: a person must still look.")
        return f"Seen before ({what})."
    if after == "pass":
        return (f"Previously accepted: a similar finding was {what} before, so this one is listed for reference "
                "and doesn't count as an open issue.")
    return f"Lowered from “{BAND_LABEL[before]}” to “{BAND_LABEL[after]}” because a similar finding was {what} before."
