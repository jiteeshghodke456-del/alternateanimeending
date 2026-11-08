"""Neutral wording for anything a user sees: the UI never names the AI models, vendors or APIs behind a check.

Docs (README, NOTICE, BUILD) disclose them; the app itself only says that AI is used ("the AI check",
"the writing assistant", "external AI services"). Code writes neutral text directly; `scrub()` is the safety
net for model-written text (explanations, audience "why" sentences, rule rewrites) and for error strings.

`BANNED` is also what tests/test_neutral_ui.py and frontend/scripts/check-neutral.mjs look for.
"""
from __future__ import annotations

import re

# Judgment model / vendor names -> "the AI check"; writer model / vendor names -> "the writing assistant".
_JUDGE = r"(?:jev(?:[-\s]?(?:latest|preview|\d+(?:\.\d+)*))?(?:\s*\(typesafe(?:\s+ai)?\))?|typesafe(?:[-\s]?(?:ai|sdk))?" \
         r"|system\s+one|decision\s+models?)"
_WRITER = r"(?:deepseek(?:[-\s]?(?:chat|reasoner|v\d+(?:\.\d+)*))?|openai|chatgpt|gpt-?\d[\w.-]*|llms?|large language models?)"

BANNED = re.compile(r"(?i)\b(?:jev|typesafe|deepseek|openai|chatgpt|llms?|system one|decision models?)\b|jev-[\w.]+")

_JUDGE_RE = re.compile(rf"(?i)(?:\bthe\s+)?\b{_JUDGE}(?:'s)?(?=\b|\W|$)")
_WRITER_RE = re.compile(rf"(?i)(?:\bthe\s+)?\b{_WRITER}(?:'s)?(?=\b|\W|$)")

JUDGE_LABEL = "AI judgment model"
WRITER_LABEL = "AI writing model"


def _sub(pattern: re.Pattern[str], repl: str, text: str, keep: str) -> str:
    def one(m: re.Match[str]) -> str:
        s = m.group(0)
        core = BANNED.search(s)
        if core is not None and core.group(0).lower() in keep:
            return s  # the content itself names it (e.g. a post about a chatbot): a quote, not the app naming itself
        out = repl + ("'s" if s.lower().endswith("'s") else "")
        at_start = m.start() == 0 or text[: m.start()].rstrip().endswith((".", "!", "?", ":", "\n"))
        return out[0].upper() + out[1:] if at_start else out

    return pattern.sub(one, text)


def scrub(text: str | None, content: str = "") -> str | None:
    """Replace any model/vendor name in model-written, user-visible text with a neutral name. A name that also
    appears in `content` (the text being checked) is kept, so quotes of the user's own words survive.
    None and "" pass through unchanged."""
    if not text:
        return text
    if not BANNED.search(text):
        return text
    keep = (content or "").lower()
    out = _sub(_JUDGE_RE, "the AI check", text, keep)
    out = _sub(_WRITER_RE, "the writing assistant", out, keep)
    # "OpenAI GPT-4o" -> one name, not two
    return re.sub(r"(?i)\b(the (?:AI check|writing assistant))(?:\s+the (?:AI check|writing assistant))+", r"\1", out)


def has_banned(text: str | None) -> bool:
    return bool(text) and bool(BANNED.search(text or ""))


def provider_label(provider: str) -> str:
    """ai_calls.provider ("typesafe" | "llm") -> the neutral name the AI-used panel shows."""
    return {"typesafe": JUDGE_LABEL, "jev": JUDGE_LABEL, "llm": WRITER_LABEL}.get(provider, "External AI service")
