"""Tier 0: deterministic, local checks. OWNER: pipeline-agent. Runs for confidential items too.

Every check is driven by `Rule.params` (seed/rules.json), so an admin can add a rule without code:

| params keys                                  | check                                                   |
|----------------------------------------------|---------------------------------------------------------|
| banned [+ replacement] [+ whole_word]        | exact words/phrases, case-insensitive, word boundaries   |
| pattern [+ canonical]                        | one regex (e.g. brand-name spelling), canonical = fix    |
| patterns                                     | list of regexes                                          |
| max_exclamations / all_caps_min_len          | post-level "!" budget and ALL CAPS words                 |
| promo_triggers + end_date_pattern            | promotion without an end date anywhere in the post       |
| triggers + required_text                     | e.g. giveaway without "T&C apply"                        |
| price_pattern + required_text_any            | price without "incl. taxes" (thresholds like "above Rs 499" skipped) |
| requires_alt_text [+ min_chars, reject_generic] | image without useful alt text                         |
| max_grade_from_channel                       | Flesch-Kincaid grade vs brand.channel_targets            |
| palette_delta_e                              | 5 dominant colours vs palette, CIEDE2000                 |
| (none of the above, tier0 rule)              | quoted words in the statement, e.g. Never say 'cheap'    |

Plus the brand word list (Term rows): banned / preferred (old -> new) / capitalise.
Plus claim triggers (numbers, %, best, #1, guaranteed, cheapest, comparatives, prices, awards,
eco terms) that claims.py turns into fact checks.

All offsets are local to the unit text. Every hit is a finding with probability 1.0, band "flag".
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable, Optional

from app.pipeline import lang as L
from app.pipeline.context import CheckContext, RuleSpec, TermSpec, UnitIn

log = logging.getLogger("brandgate.tier0")


@dataclass
class Tier0Hit:
    rule_code: Optional[str]
    category: str
    severity: str
    span_start: Optional[int]  # local to the unit text; None = whole unit / image
    span_end: Optional[int]
    quote: str
    detail: str = ""  # extra sentence for the template explanation (grade, colours, ...)
    fix: Optional[str] = None  # deterministic replacement for text[span_start:span_end]
    kind: str = "rule"  # rule|term|readability|palette|alt_text|caps|exclaim|...
    probability: float = 1.0
    band: str = "flag"


@dataclass
class ClaimTrigger:
    group: str  # superlative|eco|price|number|award
    start: int
    end: int
    text: str


@dataclass
class PostState:
    """Running state for post-level rules across the units of one version."""

    exclamations_seen: int = 0
    flagged_post_rules: set[str] = field(default_factory=set)


# ------------------------------------------------------------------ regex helpers
_APOS = "['’]"


def phrase_regex(phrase: str, case_insensitive: bool = True) -> re.Pattern[str]:
    """Exact phrase with word boundaries that also work for phrases like "S&S" or "No. 1", and for Indic
    words: a vowel sign or virama counts as part of the word ("मुफ्त" doesn't match inside "मुफ्ती")."""
    esc = re.escape(L.nfc(phrase.strip())).replace("\\'", _APOS).replace("'", _APOS)
    esc = re.sub(r"(\\ )+", r"\\s+", esc)
    return re.compile(rf"{L.BEFORE}{esc}{L.AFTER}", re.IGNORECASE if case_insensitive else 0)


@lru_cache(maxsize=512)
def _compile(pattern: str, flags: int) -> Optional[re.Pattern[str]]:
    try:
        return re.compile(L.unicode_boundaries(pattern), flags)  # \b that works inside Indic words
    except re.error:
        log.warning("tier0: invalid regex in rule params: %r", pattern)
        return None


def _flags(params: dict, default_ci: bool = True) -> int:
    return re.IGNORECASE if params.get("case_insensitive", default_ci) else 0


def _hit(rule: RuleSpec, text: str, s: int, e: int, **kw) -> Tier0Hit:  # noqa: ANN003
    return Tier0Hit(rule_code=rule.code, category=rule.category, severity=rule.severity,
                    span_start=s, span_end=e, quote=text[s:e], **kw)


_ABBR = {"incl", "rs", "no", "co", "st", "dr", "mr", "mrs", "ms", "approx", "vs", "etc", "e.g", "i.e", "inr", "min", "max"}
# "." "!" "?" and the Devanagari danda "।" / double danda "॥" end a sentence.
_SENT_END = re.compile(r"[.!?।॥]+[\"'’”)\]]*(?=\s|$)|\n+")


def sentences(text: str) -> list[tuple[int, int]]:
    """Sentence spans (start, end) with trailing punctuation included; skips common abbreviations."""
    out: list[tuple[int, int]] = []
    start = 0
    for m in _SENT_END.finditer(text):
        if m.group(0).startswith("."):
            prev = re.search(r"([\w.]+)$", text[start:m.start()])
            word = (prev.group(1) if prev else "").lower().rstrip(".")
            if word in _ABBR or (prev and len(prev.group(1)) == 1 and prev.group(1).isupper()):
                continue
        end = m.end() if not m.group(0).startswith("\n") else m.start()
        if text[start:end].strip():
            s = start + (len(text[start:end]) - len(text[start:end].lstrip()))
            out.append((s, end))
        start = m.end()
    if text[start:].strip():
        s = start + (len(text[start:]) - len(text[start:].lstrip()))
        out.append((s, len(text.rstrip())))
    return out


def sentence_at(text: str, pos: int) -> tuple[int, int]:
    for s, e in sentences(text):
        if s <= pos < e:
            return s, e
    return 0, len(text)


def merge_hits(hits: list[Tier0Hit]) -> list[Tier0Hit]:
    """Merge overlapping hits of the same rule (e.g. "Flat 20% off" matched by two promo triggers)."""
    hits = sorted(hits, key=lambda h: (h.rule_code or "", h.kind, h.span_start if h.span_start is not None else -1))
    out: list[Tier0Hit] = []
    for h in hits:
        prev = out[-1] if out else None
        if (prev and prev.rule_code == h.rule_code and prev.kind == h.kind and h.span_start is not None
                and prev.span_end is not None and h.span_start <= prev.span_end):
            if h.span_end and h.span_end > prev.span_end:
                prev.span_end = h.span_end
                prev.quote = ""  # recomputed by caller
                prev.fix = None if prev.fix != h.fix else prev.fix
            continue
        out.append(h)
    return out


# ------------------------------------------------------------------ words, terms, patterns
def find_phrases(text: str, phrases: Iterable[str], case_insensitive: bool = True) -> list[tuple[int, int, str]]:
    out = []
    for p in phrases:
        if not str(p).strip():
            continue
        for m in phrase_regex(str(p), case_insensitive).finditer(text):
            out.append((m.start(), m.end(), str(p)))
    return sorted(out)


_QUOTED = re.compile(r"[\"'‘“]([^\"'’”]{2,40})[\"'’”]")


NEVER_SAY = re.compile(r"^\s*(never|don'?t|do not|avoid|no)\s+(say|use|write|saying|using|writing)\b", re.IGNORECASE)


def is_never_say(rule: RuleSpec) -> bool:
    """An admin rule like "Never say 'cheap'" with no params is checked exactly, whatever its method."""
    return not _has_known_params(rule.params or {}) and bool(NEVER_SAY.search(rule.statement)) and bool(statement_words(rule.statement))


def statement_words(statement: str) -> list[str]:
    """Quoted words in a rule statement ("Never say 'cheap'") for admin rules without params."""
    return [m.group(1).strip() for m in _QUOTED.finditer(statement) if m.group(1).strip()]


def check_banned(rule: RuleSpec, text: str) -> list[Tier0Hit]:
    p = rule.params or {}
    words = p.get("banned")
    if not isinstance(words, list):
        words = statement_words(rule.statement) if not _has_known_params(p) else []
    replacement = p.get("replacement")
    hits = []
    for s, e, w in find_phrases(text, [str(w) for w in words], bool(p.get("case_insensitive", True))):
        detail = f"“{text[s:e]}” is on the brand's do-not-use list." if not replacement else f"Write “{replacement}” instead of “{text[s:e]}”."
        hits.append(_hit(rule, text, s, e, fix=replacement, detail=detail, kind="banned"))
    return hits


def check_pattern(rule: RuleSpec, text: str) -> list[Tier0Hit]:
    p = rule.params or {}
    rx = _compile(str(p["pattern"]), _flags(p, default_ci=False))
    if rx is None:
        return []
    canonical = p.get("canonical")
    return [_hit(rule, text, m.start(), m.end(), fix=canonical, kind="pattern",
                 detail=f"Write “{canonical}”." if canonical else "")
            for m in rx.finditer(text) if m.end() > m.start()]


def check_patterns(rule: RuleSpec, text: str) -> list[Tier0Hit]:
    p = rule.params or {}
    hits = []
    for pat in p.get("patterns") or []:
        rx = _compile(str(pat), _flags(p))
        if rx is None:
            continue
        hits += [_hit(rule, text, m.start(), m.end(), kind="patterns") for m in rx.finditer(text) if m.end() > m.start()]
    return hits


def check_terms(text: str, terms: list[TermSpec], rules: list[RuleSpec]) -> list[Tier0Hit]:
    """Brand word list. Cites the enabled rule whose statement mentions the term (else no rule)."""
    hits: list[Tier0Hit] = []
    for t in terms:
        value = (t.value or "").strip()
        if not value:
            continue
        rule = _rule_mentioning(value, rules, t.kind)
        cat = rule.category if rule else "terminology"
        sev = rule.severity if rule else "minor"
        code = rule.code if rule else None
        if t.kind == "banned":
            for s, e, _ in find_phrases(text, [value]):
                hits.append(Tier0Hit(code, cat, sev, s, e, text[s:e], kind="term",
                                     detail=f"“{text[s:e]}” is on the brand's do-not-use word list."))
        elif t.kind == "preferred":
            for s, e, _ in find_phrases(text, [value]):
                hits.append(Tier0Hit(code, cat, sev, s, e, text[s:e], fix=t.replacement, kind="term",
                                     detail=f"Write “{t.replacement}” instead of “{text[s:e]}”." if t.replacement else ""))
        elif t.kind == "capitalise":
            for s, e, _ in find_phrases(text, [value], case_insensitive=True):
                if text[s:e] != value:
                    hits.append(Tier0Hit(code, cat, sev, s, e, text[s:e], fix=value, kind="term",
                                         detail=f"Write it exactly as “{value}”."))
    return hits


def _rule_mentioning(value: str, rules: list[RuleSpec], kind: str) -> Optional[RuleSpec]:
    v = value.lower()
    cands = [r for r in rules if r.enabled and v in r.statement.lower()]
    cands.sort(key=lambda r: (r.category != "terminology", r.check_method != "tier0"))
    return cands[0] if cands else None


# ------------------------------------------------------------------ tone: exclamations + caps
_CAPS_RUN = re.compile(r"(?<![\w&])[A-Z][A-Z'&]*(?:\s+[A-Z][A-Z'&]*)*(?![\w&])")


def check_caps(rule: RuleSpec, text: str) -> list[Tier0Hit]:
    p = rule.params or {}
    min_len = int(p.get("all_caps_min_len", 4))
    allow = {str(a).upper() for a in p.get("all_caps_allowlist", [])}
    hits = []
    for m in _CAPS_RUN.finditer(text):
        words = m.group(0).split()
        if any(len(w.strip("'&")) >= min_len and w.upper() not in allow for w in words):
            hits.append(_hit(rule, text, m.start(), m.end(), kind="caps", detail="Avoid words in ALL CAPS.",
                             fix=_sentence_case(m.group(0))))
    return hits


def _sentence_case(s: str) -> str:
    low = s.lower()
    return low[:1].upper() + low[1:]


_EXCL = re.compile(r"!+")


def check_exclamations(rule: RuleSpec, text: str, state: PostState) -> list[Tier0Hit]:
    limit = int((rule.params or {}).get("max_exclamations", 1))
    hits = []
    for m in _EXCL.finditer(text):
        state.exclamations_seen += len(m.group(0))
        if state.exclamations_seen > limit:
            keep = max(0, limit - (state.exclamations_seen - len(m.group(0))))
            hits.append(_hit(rule, text, m.start(), m.end(), kind="exclaim", fix="!" * keep if keep else ".",
                             detail=f"Use at most {limit} exclamation mark{'s' if limit != 1 else ''} per post."))
    return hits


# ------------------------------------------------------------------ post-level requirements
def check_promo_end_date(rule: RuleSpec, text: str, post_text: str) -> list[Tier0Hit]:
    p = rule.params or {}
    end_rx = _compile(str(p.get("end_date_pattern", "")), _flags(p)) if p.get("end_date_pattern") else None
    if end_rx is not None and end_rx.search(post_text):
        return []
    hits = []
    for pat in p.get("promo_triggers") or []:
        rx = _compile(str(pat), _flags(p))
        if rx is None:
            continue
        for m in rx.finditer(text):
            hits.append(_hit(rule, text, m.start(), m.end(), kind="promo",
                             detail="The post runs a promotion but never says when it ends."))
    return hits


def check_required_text(rule: RuleSpec, text: str, post_text: str) -> list[Tier0Hit]:
    p = rule.params or {}
    required = str(p.get("required_text", ""))
    if not required or phrase_regex(required).search(post_text):
        return []
    hits = []
    for pat in p.get("triggers") or []:
        rx = _compile(str(pat), _flags(p))
        if rx is None:
            continue
        hits += [_hit(rule, text, m.start(), m.end(), kind="required", detail=f"Add the line “{required}”.")
                 for m in rx.finditer(text)]
    return hits


_THRESHOLD_BEFORE = re.compile(r"\b(above|over|orders? of|minimum|min\.?|upto|up to|below|under)\s*$", re.IGNORECASE)


def price_matches(rule: RuleSpec, text: str) -> list[re.Match[str]]:
    """Matches of params.price_pattern that contain a digit and don't start inside a word ("customers,")."""
    p = rule.params or {}
    rx = _compile(str(p.get("price_pattern", "")), _flags(p, default_ci=False))
    if rx is None:
        return []
    return [m for m in rx.finditer(text)
            if re.search(r"\d", m.group(0)) and not (m.start() > 0 and text[m.start() - 1].isalnum())]


def check_price_taxes(rule: RuleSpec, text: str, post_text: str) -> list[Tier0Hit]:
    p = rule.params or {}
    need = [str(x) for x in p.get("required_text_any") or []]
    if not need:
        return []
    if any(phrase_regex(n).search(post_text) for n in need) or re.search(r"\b(incl(\.|uding|usive of)?)\s+(all\s+)?(taxes|gst)\b", post_text, re.I):
        return []
    hits = []
    for m in price_matches(rule, text):
        if _THRESHOLD_BEFORE.search(text[max(0, m.start() - 25):m.start()]):
            continue
        hits.append(_hit(rule, text, m.start(), m.end(), kind="price_tax",
                         detail="Prices must say that taxes are included (for example “incl. taxes”)."))
    return hits


# ------------------------------------------------------------------ readability
_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")


@lru_cache(maxsize=1)
def _pyphen():  # noqa: ANN202
    import pyphen

    return pyphen.Pyphen(lang="en_US")


def _syllables(word: str) -> int:
    w = word.lower()
    try:
        return max(1, len(_pyphen().positions(w)) + 1)
    except Exception:  # noqa: BLE001
        return max(1, len(re.findall(r"[aeiouy]+", w)))


def fk_grade(text: str) -> Optional[float]:
    """Flesch-Kincaid grade. Same formula as textstat; syllables via pyphen (textstat's own
    syllable counter downloads the NLTK cmudict at runtime, which is unreliable in the container)."""
    words = _WORD.findall(text)
    if not words:
        return None
    n_sent = max(1, len(sentences(text)))
    syl = sum(_syllables(w) for w in words)
    return round(0.39 * (len(words) / n_sent) + 11.8 * (syl / len(words)) - 15.59, 1)


MIN_WORDS_FOR_READABILITY = 12


# Non-English text: Flesch-Kincaid counts English syllables, so its grade is meaningless for Hindi, Marathi,
# Tamil or Hinglish (romanised Hindi). Those units get a language-neutral check instead: sentence length in
# words. Plain-language guidance in any language keeps sentences short; 30 words is a generous ceiling, so only
# really long sentences are flagged. No grade is ever shown for them.
LONG_SENTENCE_WORDS = 30
_ANY_WORD = re.compile(rf"[{L.WORD}]+(?:['’][{L.WORD}]+)*")


def check_sentence_length(rule: RuleSpec, text: str, info: "L.LangInfo") -> list[Tier0Hit]:
    hits = []
    for s, e in sentences(text):
        n = len(_ANY_WORD.findall(text[s:e]))
        if n > LONG_SENTENCE_WORDS:
            hits.append(_hit(rule, text, s, e, kind="readability",
                             detail=(f"This sentence has {n} words. Reading-grade formulas only work for English, so for "
                                     f"{info.name} text Brand Gate checks sentence length instead: at most "
                                     f"{LONG_SENTENCE_WORDS} words.")))
    return hits


def check_readability(rule: RuleSpec, text: str, channel: str, channel_targets: dict) -> list[Tier0Hit]:
    target = (channel_targets.get(channel) or {}).get("max_grade")
    if target is None:
        return []
    info = L.detect(text)
    if not info.is_english:
        return check_sentence_length(rule, text, info) if info.lang != "none" else []
    if len(_WORD.findall(text)) < MIN_WORDS_FOR_READABILITY:
        return []
    grade = fk_grade(text)
    if grade is None or grade <= float(target):
        return []
    return [_hit(rule, text, 0, len(text), kind="readability",
                 detail=f"Reading grade is {grade:g}; the {channel.title()} target is {target}.")]


# ------------------------------------------------------------------ images: alt text
def check_alt_text(rule: RuleSpec, alt_text: Optional[str]) -> list[Tier0Hit]:
    p = rule.params or {}
    alt = (alt_text or "").strip()
    min_chars = int(p.get("min_chars", 10))
    generic = {str(g).lower() for g in p.get("reject_generic", [])}
    if not alt:
        detail = "This image has no alt text."
    elif len(alt) < min_chars or alt.lower().strip(" .") in generic:
        detail = f"The alt text “{alt}” doesn't describe what the image shows."
    else:
        return []
    return [Tier0Hit(rule.code, rule.category, rule.severity, None, None, alt, detail=detail, kind="alt_text")]


# ------------------------------------------------------------------ images: colours
def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(rgb: Iterable[float]) -> str:
    r, g, b = (max(0, min(255, int(round(float(c))))) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def rgb_to_lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """sRGB (0-255) -> CIE L*a*b* (D65)."""
    def lin(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = (0.2126729 * r + 0.7151522 * g + 0.0721750 * b) / 1.00000
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def ciede2000(lab1: tuple[float, float, float], lab2: tuple[float, float, float]) -> float:
    """CIEDE2000 colour difference (Sharma, Wu & Dalal 2005), kL = kC = kH = 1."""
    from math import atan2, cos, degrees, exp, radians, sin, sqrt

    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1, C2 = sqrt(a1 * a1 + b1 * b1), sqrt(a2 * a2 + b2 * b2)
    Cbar = (C1 + C2) / 2
    G = 0.5 * (1 - sqrt(Cbar ** 7 / (Cbar ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = sqrt(a1p * a1p + b1 * b1), sqrt(a2p * a2p + b2 * b2)

    def hp(a: float, b: float) -> float:
        if a == 0 and b == 0:
            return 0.0
        h = degrees(atan2(b, a))
        return h + 360 if h < 0 else h

    h1p, h2p = hp(a1p, b1), hp(a2p, b2)
    dLp = L2 - L1
    dCp = C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    else:
        dhp = h2p - h1p
        if dhp > 180:
            dhp -= 360
        elif dhp < -180:
            dhp += 360
    dHp = 2 * sqrt(C1p * C2p) * sin(radians(dhp / 2))

    Lbarp = (L1 + L2) / 2
    Cbarp = (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbarp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbarp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hbarp = (h1p + h2p + 360) / 2
    else:
        hbarp = (h1p + h2p - 360) / 2

    T = (1 - 0.17 * cos(radians(hbarp - 30)) + 0.24 * cos(radians(2 * hbarp))
         + 0.32 * cos(radians(3 * hbarp + 6)) - 0.20 * cos(radians(4 * hbarp - 63)))
    dtheta = 30 * exp(-(((hbarp - 275) / 25) ** 2))
    Rc = 2 * sqrt(Cbarp ** 7 / (Cbarp ** 7 + 25 ** 7))
    Sl = 1 + (0.015 * (Lbarp - 50) ** 2) / sqrt(20 + (Lbarp - 50) ** 2)
    Sc = 1 + 0.045 * Cbarp
    Sh = 1 + 0.015 * Cbarp * T
    Rt = -sin(radians(2 * dtheta)) * Rc
    return sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh))


def dominant_colours(image_path: str, k: int = 5, max_side: int = 128, iters: int = 12) -> list[tuple[str, float]]:
    """k-means (numpy) on a <=128px downsample. Returns [(hex, share)] sorted by share."""
    import numpy as np
    from PIL import Image

    with Image.open(image_path) as im:
        if im.format == "JPEG":
            im.draft(im.mode, (max_side, max_side))  # reduced DCT decode; never a full-size RGBA copy
        im.thumbnail((max_side, max_side))  # downscale first, then convert (memory)
        im = im.convert("RGBA")
        arr = np.asarray(im, dtype=np.float64).reshape(-1, 4)
    px = arr[arr[:, 3] > 127][:, :3]  # ignore transparent pixels
    if len(px) == 0:
        return []
    k = max(1, min(k, len(np.unique(px, axis=0))))
    # deterministic init: k-means++-style farthest point from a fixed start
    centres = [px[len(px) // 2]]
    for _ in range(1, k):
        d = np.min(((px[:, None, :] - np.array(centres)[None, :, :]) ** 2).sum(-1), axis=1)
        centres.append(px[int(np.argmax(d))])
    c = np.array(centres)
    labels = np.zeros(len(px), dtype=int)
    for _ in range(iters):
        labels = np.argmin(((px[:, None, :] - c[None, :, :]) ** 2).sum(-1), axis=1)
        new = np.array([px[labels == i].mean(axis=0) if np.any(labels == i) else c[i] for i in range(k)])
        if np.allclose(new, c, atol=0.5):
            c = new
            break
        c = new
    shares = np.bincount(labels, minlength=k) / len(px)
    order = np.argsort(-shares)
    return [(rgb_to_hex(c[i]), float(shares[i])) for i in order if shares[i] > 0]


MIN_COLOUR_SHARE = 0.05


def palette_distances(colours: list[tuple[str, float]], palette: list[tuple[str, str]]) -> list[dict]:
    pal = [(name, hx, rgb_to_lab(hex_to_rgb(hx))) for name, hx in palette]
    out = []
    for hx, share in colours:
        lab = rgb_to_lab(hex_to_rgb(hx))
        best = min(((ciede2000(lab, plab), name, phx) for name, phx, plab in pal), default=(float("inf"), "", ""))
        out.append({"hex": hx, "share": round(share, 3), "delta_e": round(best[0], 1), "nearest": best[1], "nearest_hex": best[2]})
    return out


def check_palette(rule: RuleSpec, image_path: str, palette: list[tuple[str, str]], default_threshold: float = 12.0) -> list[Tier0Hit]:
    if not palette or not image_path:
        return []
    thr = float((rule.params or {}).get("palette_delta_e", default_threshold))
    try:
        dist = palette_distances(dominant_colours(image_path), palette)
    except Exception:  # noqa: BLE001
        log.exception("tier0: could not read image colours")
        return []
    off = [d for d in dist if d["share"] >= MIN_COLOUR_SHARE and d["delta_e"] > thr]
    if not off:
        return []
    desc = "; ".join(f"{d['hex']} ({d['share']:.0%} of the image, ΔE {d['delta_e']:g} from {d['nearest']})" for d in off)
    return [Tier0Hit(rule.code, rule.category, rule.severity, None, None, desc, kind="palette",
                     detail=f"Off-palette colours: {desc}. Allowed ΔE is {thr:g}.")]


# ------------------------------------------------------------------ claim triggers
_MONTHS = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE = re.compile(
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS}\b(?:,?\s+\d{{4}})?|\b{_MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?:,?\s+\d{{4}})?"
    r"|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b(?:19|20)\d{2}\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"(?<![A-Za-z\d])\d[\d,]*(?:\.\d+)?\s?%?")
_AWARD = re.compile(r"\b(won|wins|award(?:s|ed)?|gold|silver|bronze|medal|certified|rated)\b", re.IGNORECASE)
_DEFAULT_SUPERLATIVES = [r"#\s?1\b", r"\bnumber (one|1)\b", r"\bbest\b", r"\bfinest\b", r"\bcheapest\b", r"\bguaranteed\b"]
# Hindi / Marathi / Hinglish superlatives and guarantees (code defaults, like the English ones above).
# "\b" is rewritten by _compile so it works inside Devanagari words.
_INDIC_SUPERLATIVES = [
    r"\bनंबर\s?(?:1|१|वन)\b(?![\d०-९])", r"\bनं\.?\s?(?:1|१)\b", r"\bसर्वश्रेष्ठ\b", r"\bसर्वोत्तम\b", r"\bसर्वोत्कृष्ट\b",
    r"\bबेस्ट\b", r"\bबेहतरीन\b", r"\bगारंटी\b", r"\bगॅरंटी\b",
    r"\bसबसे\s+(?:अच्छ[ाीे]|बढ़िया|बढ़िया|बड़[ाीे]|बड़[ाीे]|सस्त[ाीे]|लोकप्रिय|पसंदीदा|ताज़[ाीे]|ताज[ाीे])",
    r"\bसर्वात\s+(?:चांगल[ाीे]|उत्तम|मोठ[ाीे]|स्वस्त|लोकप्रिय|आवडत[ाीे]|ताज[ाीे])",
    r"\b(?:भारत|देश)\S*\s+(?:\S+\s+)?(?:पहल[ाीे]|पहिल[ाीे])\b",  # "India's first"
    r"\bsabse\s+(?:best|accha|achha|acha|badhiya|sasta|bada)\b",
]
_COMPARATIVE = re.compile(r"\b(?:(\w+er)|more \w+|less \w+)\s+than\b", re.IGNORECASE)
_NOT_COMPARATIVE = {"rather", "other", "later", "earlier", "never", "ever", "under", "over", "after", "either", "whether", "faster"}


def _rule_with(rules: list[RuleSpec], key: str) -> Optional[RuleSpec]:
    return next((r for r in rules if r.enabled and key in (r.params or {})), None)


def claim_triggers(text: str, rules: list[RuleSpec]) -> list[ClaimTrigger]:
    """Spans that make a sentence a checkable claim. Dates and product codes don't count as numbers."""
    out: list[ClaimTrigger] = []
    sup_rule = _rule_with(rules, "superlative_patterns")
    sup = list((sup_rule.params or {}).get("superlative_patterns", [])) if sup_rule else []
    for pat in sup + _DEFAULT_SUPERLATIVES + _INDIC_SUPERLATIVES:
        rx = _compile(str(pat), re.IGNORECASE)
        if rx:
            out += [ClaimTrigger("superlative", m.start(), m.end(), m.group(0)) for m in rx.finditer(text) if m.end() > m.start()]
    for m in _COMPARATIVE.finditer(text):
        if m.group(1) and m.group(1).lower() in _NOT_COMPARATIVE:
            continue
        out.append(ClaimTrigger("superlative", m.start(), m.end(), m.group(0)))
    eco_rule = _rule_with(rules, "claim_terms")
    if eco_rule:
        for s, e, _ in find_phrases(text, [str(t) for t in eco_rule.params.get("claim_terms", [])]):
            out.append(ClaimTrigger("eco", s, e, text[s:e]))
    price_rule = _rule_with(rules, "price_pattern")
    price_spans: list[tuple[int, int]] = []
    if price_rule:
        for m in price_matches(price_rule, text):
            threshold = bool(_THRESHOLD_BEFORE.search(text[max(0, m.start() - 25):m.start()]))
            out.append(ClaimTrigger("number" if threshold else "price", m.start(), m.end(), m.group(0)))
            price_spans.append((m.start(), m.end()))
    dates = [(m.start(), m.end()) for m in _DATE.finditer(text)]
    taken = price_spans + dates + [(t.start, t.end) for t in out if t.group == "superlative"]
    for m in _NUMBER.finditer(text):
        s, e = m.start(), m.end()
        if any(a <= s < b or a < e <= b for a, b in taken):
            continue
        out.append(ClaimTrigger("number", s, len(m.group(0).rstrip()) + s, m.group(0).rstrip()))
    for m in _AWARD.finditer(text):
        out.append(ClaimTrigger("award", m.start(), m.end(), m.group(0)))
    # de-duplicate identical spans
    seen: set[tuple[str, int, int]] = set()
    uniq = []
    for t in sorted(out, key=lambda t: (t.start, t.end)):
        key = (t.group, t.start, t.end)
        if key not in seen:
            seen.add(key)
            uniq.append(t)
    return uniq


# ------------------------------------------------------------------ dispatch
_KNOWN = ("banned", "pattern", "patterns", "max_exclamations", "promo_triggers", "triggers", "price_pattern",
          "requires_alt_text", "max_grade_from_channel", "palette_delta_e", "superlative_patterns", "claim_terms")


def _has_known_params(p: dict) -> bool:
    return any(k in p for k in _KNOWN)


def check_rule_on_text(rule: RuleSpec, text: str, ctx: CheckContext, state: PostState) -> list[Tier0Hit]:
    """All text checks one rule asks for (a rule may combine several param groups)."""
    p = rule.params or {}
    hits: list[Tier0Hit] = []
    if "banned" in p or (rule.check_method == "tier0" and not _has_known_params(p)):
        hits += check_banned(rule, text)
    if "pattern" in p:
        hits += check_pattern(rule, text)
    if "patterns" in p:
        hits += check_patterns(rule, text)
    if "max_exclamations" in p:
        hits += check_exclamations(rule, text, state)
    if "all_caps_min_len" in p or "all_caps_allowlist" in p:
        hits += check_caps(rule, text)
    if "promo_triggers" in p:
        hits += check_promo_end_date(rule, text, ctx.post_text or text)
    if "triggers" in p and "required_text" in p:
        hits += check_required_text(rule, text, ctx.post_text or text)
    if "price_pattern" in p and "required_text_any" in p:
        hits += check_price_taxes(rule, text, ctx.post_text or text)
    if p.get("max_grade_from_channel"):
        hits += check_readability(rule, text, ctx.channel, ctx.channel_targets)
    return hits


# Links, email addresses and phone numbers are contact details, not copy: a brand-name rule must not
# "fix" the lowercase name inside a URL (that breaks the link), and digits in them are not claims.
_PROTECTED = re.compile(
    r"https?://\S+|www\.\S+"
    r"|\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
    r"|\b[\w-]+(?:\.[\w-]+)*\.(?:com|app|in|org|net|io|ai|co|be|gl|ly)(?:/\S*)?"
    r"|\+?\d[\d\s().-]{7,}\d",
    re.IGNORECASE,
)


def protected_spans(text: str) -> list[tuple[int, int]]:
    """Spans of URLs, domains, emails and phone numbers (start, end)."""
    return [(m.start(), m.end()) for m in _PROTECTED.finditer(text)]


def in_protected(start: Optional[int], end: Optional[int], spans: list[tuple[int, int]]) -> bool:
    if start is None or end is None:
        return False
    return any(a <= start and end <= b for a, b in spans)


def run_tier0_text(text: str, ctx: CheckContext, state: Optional[PostState] = None) -> list[Tier0Hit]:
    """Tier 0 for one text unit. Pass the same PostState for every unit of a version, in order."""
    state = state or PostState()
    rules = ctx.enabled_rules()
    hits: list[Tier0Hit] = []
    for rule in rules:
        if rule.check_method in ("tier0", "claim"):
            hits += check_rule_on_text(rule, text, ctx, state)
        elif is_never_say(rule):
            hits += check_banned(rule, text)
    hits = drop_linked_pack_hits(text, hits, rules)
    hits = merge_hits(hits)
    term_hits = check_terms(text, ctx.terms, rules)
    hits += [h for h in term_hits if not any(_overlaps(h, o) for o in hits)]
    spans = protected_spans(text)
    hits = [h for h in hits if not in_protected(h.span_start, h.span_end, spans)]
    for h in hits:
        if h.span_start is not None and h.span_end is not None:
            h.quote = text[h.span_start:h.span_end]
    return sorted(hits, key=lambda h: (h.span_start if h.span_start is not None else -1, h.rule_code or ""))


def drop_linked_pack_hits(text: str, hits: list[Tier0Hit], rules: list[RuleSpec]) -> list[Tier0Hit]:
    """Rule packs (app/packs.py): a pack rule linked to a brand rule (params.linked_rules) stays quiet in a
    sentence where that brand rule already fired; the brand rule's finding shows the regulation instead."""
    linked = {r.code: set((r.params or {}).get("linked_rules") or []) for r in rules
              if (r.params or {}).get("pack") and (r.params or {}).get("linked_rules")}
    if not linked:
        return hits
    fired = {(h.rule_code, sentence_at(text, h.span_start)[0]) for h in hits
             if h.rule_code and h.rule_code not in linked and h.span_start is not None}
    out = []
    for h in hits:
        if h.rule_code in linked and h.span_start is not None:
            sent = sentence_at(text, h.span_start)[0]
            if any((code, sent) in fired for code in linked[h.rule_code]):
                continue
        out.append(h)
    return out


def run_tier0_image(unit: UnitIn, ctx: CheckContext) -> list[Tier0Hit]:
    hits: list[Tier0Hit] = []
    for rule in ctx.enabled_rules():
        p = rule.params or {}
        if "palette_delta_e" in p and unit.image_path:
            hits += check_palette(rule, unit.image_path, ctx.palette)
        if p.get("requires_alt_text"):
            hits += check_alt_text(rule, ctx.alt_text)
    return hits


def run_tier0_units(units: list[UnitIn], ctx: CheckContext) -> dict[int, list[Tier0Hit]]:
    """Tier 0 for every unit of a version (keyed by unit.idx). Order matters for the "!" budget."""
    state = PostState()
    out: dict[int, list[Tier0Hit]] = {}
    for u in sorted(units, key=lambda u: u.idx):
        if u.kind == "image":
            out[u.idx] = run_tier0_image(u, ctx)
        elif u.is_text:
            out[u.idx] = run_tier0_text(u.text, ctx, state)
        else:
            out[u.idx] = []
    return out


def _overlaps(a: Tier0Hit, b: Tier0Hit) -> bool:
    if None in (a.span_start, a.span_end, b.span_start, b.span_end):
        return False
    return a.span_start < b.span_end and b.span_start < a.span_end  # type: ignore[operator]
