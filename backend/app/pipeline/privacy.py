"""PII masking + confidential routing. OWNER: pipeline-agent.

Intended API:

    def mask_pii(text: str) -> tuple[str, int]
        Replace emails -> "[email]", phone numbers -> "[phone]", digit runs of 8+ -> "[number]".
        Returns (masked_text, count). Run on every string before any Jev/LLM call. The count
        goes into Version.stats["pii_masked"] for the AI-used drawer.
    def external_allowed(item: ContentItem) -> bool
        False for confidential items: no Jev, LLM or audience calls (CLAUDE.md).

Masking changes offsets: map spans back to the unmasked text before storing findings.
"""
from __future__ import annotations

from app.models import ContentItem


def external_allowed(item: ContentItem) -> bool:
    return not item.confidential


def mask_pii(text: str) -> tuple[str, int]:
    raise NotImplementedError("pipeline-agent: implement privacy.mask_pii")
