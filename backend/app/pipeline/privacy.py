"""PII masking + confidential routing. OWNER: pipeline-agent.

- `mask_pii(text) -> (masked, count)` replaces emails, phone numbers (Indian and international)
  and digit runs of 9+ with numbered placeholders ("[email_1]", "[phone_1]", "[number_1]").
  Run on every string before any Jev/LLM call. The count goes into Version.stats["pii_masked"].
- `unmask(text, mapping)` puts the originals back into an LLM fix, so accepting a fix never
  destroys a phone number the writer typed.
- `external_allowed(item)` is False for confidential items: no Jev, LLM or audience calls, and
  nothing is written to `aicall`.
"""
from __future__ import annotations

import re
from typing import Any

EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Indian mobiles (+91 / 0 prefix, 10 digits starting 6-9, optional space/dash after 5 digits)
PHONE_IN = re.compile(r"(?<![\w+])(?:\+91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
# International: + country code then 7-14 more digits with optional separators
PHONE_INTL = re.compile(r"(?<![\w+])\+\d{1,3}(?:[\s.-]?\(?\d{1,4}\)?){2,5}(?!\d)")
# Indian landlines like 020-2345 6789 / (020) 23456789
PHONE_LANDLINE = re.compile(r"(?<!\d)\(?0\d{2,4}\)?[\s-]\d{3,4}[\s-]?\d{4}(?!\d)")
LONG_DIGITS = re.compile(r"(?<!\d)\d(?:[ -]?\d){8,}(?!\d)")

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", EMAIL), ("phone", PHONE_INTL), ("phone", PHONE_IN), ("phone", PHONE_LANDLINE), ("number", LONG_DIGITS),
]


def mask_pii_map(text: str) -> tuple[str, dict[str, str]]:
    """Masked text and {placeholder: original}."""
    mapping: dict[str, str] = {}
    counters: dict[str, int] = {}
    out = text or ""
    for kind, rx in _PATTERNS:
        def repl(m: re.Match[str], kind: str = kind) -> str:
            if kind == "phone" and sum(c.isdigit() for c in m.group(0)) < 8:
                return m.group(0)
            counters[kind] = counters.get(kind, 0) + 1
            ph = f"[{kind}_{counters[kind]}]"
            mapping[ph] = m.group(0)
            return ph

        out = rx.sub(repl, out)
    return out, mapping


def mask_pii(text: str) -> tuple[str, int]:
    masked, mapping = mask_pii_map(text)
    return masked, len(mapping)


def unmask(text: Any, mapping: dict[str, str]) -> Any:
    if not isinstance(text, str) or not mapping:
        return text
    for ph, original in mapping.items():
        text = text.replace(ph, original)
    return text


def external_allowed(item: Any) -> bool:
    return not bool(getattr(item, "confidential", False))
