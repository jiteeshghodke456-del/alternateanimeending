"""Plain, DB-free inputs for the pipeline. OWNER: pipeline-agent.

run.py copies DB rows into these dataclasses once per check, so the pipeline functions stay pure
and can be unit-tested (and run by the eval runner) without a database session.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional


@dataclass
class RuleSpec:
    code: str
    category: str
    severity: str
    statement: str
    check_method: str
    params: dict[str, Any] = field(default_factory=dict)
    source_quote: str = ""
    page: Optional[int] = None
    version: int = 1
    examples_violating: list[str] = field(default_factory=list)
    examples_ok: list[str] = field(default_factory=list)
    enabled: bool = True


@dataclass
class TermSpec:
    kind: str  # banned|preferred|capitalise
    value: str
    replacement: Optional[str] = None


@dataclass
class FactSpec:
    code: str
    statement: str
    value: str = ""
    source: str = ""
    valid_until: Optional[date] = None


@dataclass
class UnitIn:
    """One checkable unit. `char_start` maps local offsets to Version.text (None = no mapping)."""

    id: Optional[int]
    kind: str  # text|caption|image|alt_text
    idx: int
    text: str = ""
    char_start: Optional[int] = None
    image_path: Optional[str] = None
    page: Optional[int] = None

    @property
    def is_text(self) -> bool:
        return self.kind in ("text", "caption")


@dataclass
class CheckContext:
    channel: str
    rules: list[RuleSpec]
    terms: list[TermSpec] = field(default_factory=list)
    facts: list[FactSpec] = field(default_factory=list)
    palette: list[tuple[str, str]] = field(default_factory=list)  # (name, hex)
    tone_labels: list[str] = field(default_factory=list)
    channel_targets: dict[str, Any] = field(default_factory=dict)
    version_kind: str = "text"  # text|image|pdf|web
    alt_text: Optional[str] = None
    post_text: str = ""  # Version.text (all text units), for post-level checks
    confidential: bool = False
    version_id: Optional[int] = None
    today: date = field(default_factory=date.today)
    logo_path: Optional[str] = None  # the approved logo file (Asset kind=logo, meta.variant=approved), for P1 logo fidelity
    web_issues: list[dict[str, Any]] = field(default_factory=list)  # web pages: accessibility issues found at fetch time

    def enabled_rules(self) -> list[RuleSpec]:
        return [r for r in self.rules if r.enabled and rule_applies_to_channel(r, self.channel, self.version_kind)]

    def rule_by_code(self, code: Optional[str]) -> Optional[RuleSpec]:
        if not code:
            return None
        return next((r for r in self.rules if r.code == code), None)


def rule_applies_to_channel(rule: RuleSpec, channel: str, version_kind: str) -> bool:
    """`params.channels` limits a rule to channels; `params.applies_to` to version kinds (text|image|pdf)."""
    p = rule.params or {}
    chans = p.get("channels")
    if isinstance(chans, list) and chans and channel not in [str(c).lower() for c in chans]:
        return False
    kinds = p.get("applies_to")
    if isinstance(kinds, list) and kinds and version_kind not in [str(k).lower() for k in kinds]:
        return False
    return True
