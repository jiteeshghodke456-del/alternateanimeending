"""Script and language helpers for Indian-language content. OWNER: pipeline-agent. Pure code, no models.

- `detect(text)`: the dominant script of a unit (Unicode ranges) and a cheap language guess:
  Devanagari -> Hindi or Marathi by common function words; Latin -> English, or Hinglish when
  enough Hindi words written in Latin letters appear. Other Indic scripts are named by script
  (Tamil, Bengali, ...). Used to skip English-only readability, to tell the LLM which language
  a fix must stay in, and to reject a fix that switches script or language.
- Word characters: Python's `\\w` does not include combining marks, so a vowel sign (matra) or a
  virama ends a "word" in the middle of a Devanagari word ("मुफ्त" matched inside "मुफ्ती").
  `WORD` is `\\w` plus the combining marks of the Indic blocks (and ZWJ/ZWNJ); `unicode_boundaries`
  rewrites `\\b` in rule regexes to lookarounds over that class (identical on ASCII text).
- Numbers: Indian digits (०-९, ০-৯, ௦-௯, ...) to ASCII, and lakh / crore / thousand words
  (लाख, करोड़, कोटी, हज़ार, lakh, crore) to their value, so a Hindi claim matches an English fact.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

# ------------------------------------------------------------------ word characters
_MARK_RANGES = [(0x0300, 0x036F), (0x0900, 0x0DFF), (0x1CD0, 0x1CFF), (0xA8E0, 0xA8FF)]


@lru_cache(maxsize=1)
def _extra_word_chars() -> str:
    chars = [chr(c) for a, b in _MARK_RANGES for c in range(a, b + 1)
             if unicodedata.category(chr(c)) in ("Mn", "Mc", "Me")]
    return "".join(re.escape(c) for c in chars) + "‌‍"


WORD = r"\w" + _extra_word_chars()  # use inside [...]
BEFORE = rf"(?<![{WORD}])"  # no word character just before
AFTER = rf"(?![{WORD}])"  # no word character just after
BOUNDARY = rf"(?:(?<=[{WORD}])(?![{WORD}])|(?<![{WORD}])(?=[{WORD}]))"

_B = re.compile(r"\\b|\\.|\[|\]")


def unicode_boundaries(pattern: str) -> str:
    r"""Replace `\b` (outside character classes) with BOUNDARY, so word boundaries don't fall inside
    Indic words at a vowel sign or virama. ASCII matching is unchanged."""
    out, pos, in_class = [], 0, False
    for m in _B.finditer(pattern):
        tok = m.group(0)
        if tok == "[" and not in_class:
            in_class = True
        elif tok == "]" and in_class:
            in_class = False
        elif tok == r"\b" and not in_class:
            out.append(pattern[pos:m.start()] + BOUNDARY)
            pos = m.end()
    out.append(pattern[pos:])
    return "".join(out)


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


# ------------------------------------------------------------------ digits and Indian number words
def ascii_digits(text: str) -> str:
    """Any Unicode decimal digit (Devanagari, Bengali, Tamil, ...) -> ASCII. Same length, so offsets hold."""
    if text.isascii():
        return text
    return "".join(str(unicodedata.decimal(ch)) if not ch.isascii() and unicodedata.decimal(ch, None) is not None else ch
                   for ch in text)


SCALES = {
    "thousand": 1_000, "हज़ार": 1_000, "हजार": 1_000, "hazaar": 1_000, "hazar": 1_000,
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000, "लाख": 100_000,
    "crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000, "करोड़": 10_000_000, "करोड": 10_000_000, "कोटी": 10_000_000,
}
_SCALED = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(" + "|".join(sorted(map(re.escape, SCALES), key=len, reverse=True))
                     + rf"){AFTER}", re.IGNORECASE)


def scaled_numbers(text: str) -> list[tuple[int, int, str]]:
    """[(start, end, value)] for "1 लाख", "2.5 crore", "५० हजार" (value as plain digits)."""
    t = ascii_digits(nfc(text)) if not text.isascii() else text
    out = []
    for m in _SCALED.finditer(t):
        try:
            v = float(m.group(1).replace(",", "")) * SCALES[m.group(2).lower() if m.group(2).isascii() else m.group(2)]
        except (KeyError, ValueError):
            continue
        out.append((m.start(), m.end(), str(int(round(v)))))
    return out


# ------------------------------------------------------------------ script + language
INDIC_SCRIPTS = {
    "Devanagari": (0x0900, 0x097F), "Bengali": (0x0980, 0x09FF), "Gurmukhi": (0x0A00, 0x0A7F),
    "Gujarati": (0x0A80, 0x0AFF), "Odia": (0x0B00, 0x0B7F), "Tamil": (0x0B80, 0x0BFF), "Telugu": (0x0C00, 0x0C7F),
    "Kannada": (0x0C80, 0x0CFF), "Malayalam": (0x0D00, 0x0D7F),
}
SCRIPT_LANGUAGE = {"Bengali": "Bengali", "Gurmukhi": "Punjabi", "Gujarati": "Gujarati", "Odia": "Odia", "Tamil": "Tamil",
                   "Telugu": "Telugu", "Kannada": "Kannada", "Malayalam": "Malayalam"}

# Common function words (code, not a model). Enough to tell Hindi from Marathi in a short post.
MARATHI_WORDS = frozenset(
    "आहे आहेत आणि आमच्या आमचे आमची आमचा आम्ही फक्त मोफत नाही करा द्या घ्या तुमच्या तुमची तुमचे तुमचा साठी मध्ये पण "
    "झाले झाली होते आता कोणत्याही भेट येते येतो जवळच्या नक्की म्हणजे तिथूनच".split())
HINDI_WORDS = frozenset(
    "है हैं और के की का में सिर्फ सिर्फ़ मुफ्त मुफ़्त नहीं हमारे हमारी हमारा आपके आपकी आपका लिए से पर को यह अब भी "
    "करें आइए जाइए गई गया हुआ ज़्यादा ज्यादा".split())
HINGLISH_WORDS = frozenset(
    "hai hain aur ke ki ka mein nahi nahin sirf aaj bhi kya apne apna aapka aapke aapki hum hamara hamare hamari yeh "
    "toh karo karein kijiye abhi jaldi mauka milega raha rahe rahi saath dosto bahut accha achha acha sabse wala wale "
    "wali lo maza garam khatam phir kal yahan wahan kyun kaise".split())
_TOKEN = re.compile(rf"[{WORD}]+")


@dataclass(frozen=True)
class LangInfo:
    script: str  # Latin|Devanagari|Tamil|...|Other|none
    lang: str  # en|hinglish|hi|mr|devanagari|<script language>|none
    letters: int
    latin_share: float  # share of letters that are Latin

    @property
    def is_english(self) -> bool:
        return self.lang == "en"

    @property
    def label(self) -> str:
        return {"en": "English", "hinglish": "Hinglish (Hindi in Latin letters)", "hi": "Hindi (Devanagari script)",
                "mr": "Marathi (Devanagari script)", "devanagari": "Hindi or Marathi (Devanagari script)",
                "none": "no words"}.get(self.lang, f"{self.lang} ({self.script} script)")

    @property
    def name(self) -> str:
        """Short language name for prompts and notes."""
        return {"en": "English", "hinglish": "Hinglish", "hi": "Hindi", "mr": "Marathi",
                "devanagari": "Hindi or Marathi", "none": ""}.get(self.lang, self.lang)


def _script_of(ch: str) -> Optional[str]:
    o = ord(ch)
    if o < 0x250 and ch.isalpha():
        return "Latin"
    for name, (a, b) in INDIC_SCRIPTS.items():
        if a <= o <= b:
            return name if (ch.isalpha() or unicodedata.category(ch).startswith("M")) else None
    return "Other" if ch.isalpha() else None


def script_counts(text: str) -> Counter[str]:
    c: Counter[str] = Counter()
    for ch in text or "":
        s = _script_of(ch)
        if s:
            c[s] += 1
    return c


MIXED_SHARE = 0.2  # a unit whose words are >= 20% Indic is treated as that language (code-mixed posts)


def _word_script(word: str) -> Optional[str]:
    c = script_counts(word)
    return c.most_common(1)[0][0] if c else None


def detect(text: str) -> LangInfo:
    c = script_counts(text)
    n = sum(c.values())
    if not n:
        return LangInfo("none", "none", 0, 0.0)
    latin = c.get("Latin", 0) / n
    words = [w.lower() for w in _TOKEN.findall(nfc(text))]
    by_script = Counter(s for s in (_word_script(w) for w in words) if s)
    indic = [(k, v) for k, v in by_script.most_common() if k in INDIC_SCRIPTS]
    n_words = sum(by_script.values()) or 1
    script = c.most_common(1)[0][0]
    if script == "Latin" and indic and indic[0][1] >= MIXED_SHARE * n_words:
        script = indic[0][0]  # e.g. Hindi sentences around English product names
    if script == "Latin":
        latin_words = [w for w in words if w.isascii() and w.isalpha()]
        hits = sum(1 for w in latin_words if w in HINGLISH_WORDS)
        hinglish = hits >= 2 and hits >= 0.15 * max(1, len(latin_words))
        return LangInfo(script, "hinglish" if hinglish else "en", n, latin)
    if script == "Devanagari":
        mr = sum(1 for w in words if w in MARATHI_WORDS)
        hi = sum(1 for w in words if w in HINDI_WORDS)
        lang = "mr" if mr > hi else "hi" if hi > mr else "devanagari"
        return LangInfo(script, lang, n, latin)
    return LangInfo(script, SCRIPT_LANGUAGE.get(script, script), n, latin)


def fix_language_problem(original: str, fix: str) -> Optional[str]:
    """Why a rewrite of `original` is in the wrong language or script, or None. Code check, no model.

    Devanagari text must stay mostly Devanagari (English product names inside are fine), Latin text
    must stay Latin, and a Hinglish excerpt must not come back as plain English."""
    a, b = detect(original), detect(fix)
    if a.lang == "none" or b.lang == "none" or b.letters < 4:
        return None
    if a.script != b.script:
        return f"the fix is written in {b.script} script, but the text is {a.label}"
    if a.lang == "hinglish" and b.lang == "en" and len(fix.split()) >= 4:
        return "the fix is in English, but the text is Hinglish (Hindi in Latin letters)"
    return None


def prompt_language_line(text: str) -> Optional[str]:
    """One line for the LLM prompt when the content isn't English: explanations in English, fix in the content's
    language and script."""
    info = detect(text)
    if info.lang in ("en", "none"):
        return None
    if info.lang == "hinglish":
        how = "Hinglish: Hindi written in Latin letters, mixed with English, like the excerpt"
    elif info.script == "Devanagari":
        how = f"{info.name}, in Devanagari script, like the excerpt"
    else:
        how = f"{info.name}, in {info.script} script, like the excerpt"
    return (f"The content is in {info.label}. Write the explanations in English. Write the fix in {how}; "
            "keep brand and product names exactly as they are written in the post.")


# ------------------------------------------------------------------ transliteration (for Choice option names)
_DEV = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ii", "उ": "u", "ऊ": "uu", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऋ": "ri", "ऑ": "o", "ॲ": "e",
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "ळ": "l", "व": "v",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h",
}
_DEV_SIGNS = {"ा": "aa", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ृ": "ri",
              "ॉ": "o", "ॅ": "e", "ं": "n", "ँ": "n", "ः": "h"}


def romanize(word: str) -> str:
    """Rough Devanagari -> Latin (inherent 'a' dropped before a vowel sign or virama, and word-finally).
    Only for readable identifiers; other scripts return ''."""
    out = []
    w = nfc(word)
    for i, ch in enumerate(w):
        if ch in _DEV:
            base = _DEV[ch]
            nxt = w[i + 1] if i + 1 < len(w) else ""
            consonant = "क" <= ch <= "ह"
            if consonant and nxt and nxt not in _DEV_SIGNS and nxt != "्" and nxt != "़" and i + 1 < len(w) - 1:
                base += "a"
            out.append(base)
        elif ch in _DEV_SIGNS:
            out.append(_DEV_SIGNS[ch])
        elif "0" <= ch <= "9" or ("a" <= ch.lower() <= "z"):
            out.append(ch.lower())
    return "".join(out)
