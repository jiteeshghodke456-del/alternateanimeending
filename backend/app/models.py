"""SQLModel tables (BUILD.md §4).

JSON fields use `sa_column=Column(JSON)`. SQLAlchemy does NOT track in-place mutation of
JSON values: always reassign (`row.stats = {**row.stats, "k": v}`), never `row.stats["k"] = v`.

Additions beyond §4 (all optional/defaulted; see DECISIONS.md):
- User.id is a string slug ("asha"), so seed files and cookies stay readable.
- ContentItem.key: stable handle from seed items.json (e.g. "demo_flawed_post" for Try the demo).
- Rule: created_at/updated_at.
- Version: kind, caption, alt_text, mime.
- Unit: char_start/char_end (offsets into Version.text for highlighting).
- Finding: rule_version, tier, claim_status, dismiss_note, edited_text, created_at.
- AuditEvent.item_id is nullable (brand-kit edits have no item); version_id added.
- AICall: unit_id (CLAUDE.md asks for it), ok.
- Finding: box, quad (P1 logo overlay). AudienceRun: status, error, updated_at (P1 audience).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

# ---- enums as plain strings (kept in sync with schemas.py Literals) ----
ROLES = ("admin", "creator", "reviewer", "approver")
ITEM_STATES = ("draft", "checking", "findings_ready", "in_review", "changes_requested", "approved", "rejected")
CHANNELS = ("instagram", "email", "website", "linkedin")
SEVERITIES = ("blocking", "major", "minor")
CHECK_METHODS = ("tier0", "jev", "claim", "visual")
TERM_KINDS = ("banned", "preferred", "capitalise")
BANDS = ("flag", "human", "pass")
FINDING_STATUSES = ("open", "accepted", "edited", "dismissed", "overridden")
CHECK_STATUSES = ("not_run", "queued", "running", "done", "error")
UNIT_KINDS = ("text", "image", "caption", "alt_text")
VERSION_KINDS = ("text", "image", "pdf")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    role: str = Field(index=True)  # admin|creator|reviewer|approver


class Brand(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    tagline: str = ""
    description: str = ""
    tone_labels: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # {"instagram": {"max_grade": 8}, ...}
    channel_targets: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class Rule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    brand_id: int = Field(foreign_key="brand.id", index=True)
    code: str = Field(index=True)  # e.g. R-CLAIM-02
    version: int = 1
    category: str = Field(index=True)
    severity: str = "minor"  # blocking|major|minor
    statement: str
    source_quote: str = ""
    page: Optional[int] = None
    check_method: str = "jev"  # tier0|jev|claim|visual
    params: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    examples_violating: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    examples_ok: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    enabled: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Term(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    brand_id: int = Field(foreign_key="brand.id", index=True)
    kind: str  # banned|preferred|capitalise
    value: str
    replacement: Optional[str] = None


class Fact(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    brand_id: int = Field(foreign_key="brand.id", index=True)
    code: str = Field(index=True)
    statement: str
    value: str = ""
    source: str = ""
    valid_until: Optional[date] = None


class Asset(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    brand_id: int = Field(foreign_key="brand.id", index=True)
    kind: str  # logo|palette|font|guideline
    path: str = ""  # absolute file path (logo/guideline); "" for palette/font
    # palette: {"name","hex"}; font: {"name"}; logo: {"variant": "approved"|"alt_wrong"}
    meta: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class ContentItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    brand_id: int = Field(foreign_key="brand.id", index=True)
    title: str
    key: Optional[str] = Field(default=None, index=True)  # stable seed handle, e.g. "demo_flawed_post"
    channel: str
    creator_id: str = Field(foreign_key="user.id", index=True)
    state: str = Field(default="draft", index=True)
    confidential: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Version(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="contentitem.id", index=True)
    n: int = 1
    kind: str = "text"  # text|image|pdf
    text: str = ""  # full text; for PDFs the paragraphs joined by "\n\n"; for images the caption
    caption: Optional[str] = None
    alt_text: Optional[str] = None
    file_path: Optional[str] = None
    mime: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    check_status: str = "queued"  # not_run|queued|running|done|error
    # {"units_total","units_done","judgments","elapsed_ms","cost_usd","pii_masked",...}
    stats: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class Unit(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    version_id: int = Field(foreign_key="version.id", index=True)
    kind: str = "text"  # text|image|caption|alt_text
    page: Optional[int] = None  # 1-based, PDFs only
    idx: int = 0
    text: str = ""
    image_path: Optional[str] = None
    char_start: Optional[int] = None  # offsets into Version.text
    char_end: Optional[int] = None


class Finding(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    version_id: int = Field(foreign_key="version.id", index=True)
    unit_id: Optional[int] = Field(default=None, foreign_key="unit.id")
    rule_code: Optional[str] = Field(default=None, index=True)
    rule_version: Optional[int] = None
    fact_code: Optional[str] = None
    tier: str = "tier0"  # tier0|jev|claim|visual|logo
    category: str = ""
    severity: str = "minor"
    band: str = "flag"  # flag|human|pass
    probability: Optional[float] = None
    claim_status: Optional[str] = None  # supported|unsupported|outdated
    span_start: Optional[int] = None  # offsets into Version.text
    span_end: Optional[int] = None
    quote: str = ""  # the offending excerpt from the content (the guideline quote lives on Rule)
    explanation: str = ""
    fix: Optional[str] = None
    fix_verified: bool = False
    status: str = Field(default="open", index=True)  # open|accepted|edited|dismissed|overridden
    dismiss_reason: Optional[str] = None
    dismiss_note: Optional[str] = None
    edited_text: Optional[str] = None
    # P1 logo fidelity: image region as fractions {top, left, bottom, right} and the projected logo
    # corners [[x, y], ...] (TL, TR, BR, BL), so the UI can draw an overlay. Null for text findings.
    box: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    quad: Optional[list[Any]] = Field(default=None, sa_column=Column(JSON))
    # Edit regions (several findings in one sentence share one fix): span = the region, hit_* = the flagged
    # words (Version.text offsets), fix_group = shared by every finding of the region.
    hit_start: Optional[int] = None
    hit_end: Optional[int] = None
    fix_group: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class Comment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="contentitem.id", index=True)
    user_id: str = Field(foreign_key="user.id")
    body: str
    created_at: datetime = Field(default_factory=utcnow)


class Override(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    finding_id: int = Field(foreign_key="finding.id", index=True)
    user_id: str = Field(foreign_key="user.id")
    reason: str
    created_at: datetime = Field(default_factory=utcnow)


class AuditEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    item_id: Optional[int] = Field(default=None, foreign_key="contentitem.id", index=True)
    version_id: Optional[int] = None
    user_id: Optional[str] = Field(default=None, foreign_key="user.id")
    action: str
    detail: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)


class AICall(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    version_id: Optional[int] = Field(default=None, foreign_key="version.id", index=True)
    unit_id: Optional[int] = None
    provider: str  # "jev" | "llm"
    model: str
    purpose: str  # rules|claims|selfcheck|explain|audience|import_pdf|eval
    input_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    ok: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class EvalRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    split: str
    metrics: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)


class AudienceRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    version_id: int = Field(foreign_key="version.id", index=True)
    status: str = "running"  # running|done|error
    error: Optional[str] = None  # human message when status == "error"
    results: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# ---- Round 3: unclear-rule detection (pipeline/clarity.py, routers/clarity.py) ----
class RuleClarity(SQLModel, table=True):
    """One row per rule: the latest clarity result, the cached paraphrases (per rule version) and probe
    texts (kept across versions so before/after compare on the same probes), the last suggested rewrite
    and the statements that rewrites replaced (history)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    rule_id: int = Field(foreign_key="rule.id", index=True)
    rule_code: str = ""
    rule_version: int = 1  # the version the score was computed for (stale when the rule moved on)
    score: Optional[float] = None  # 0-100, higher = clearer
    band: str = ""  # clear|could_be_clearer|unclear
    signals: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))  # {vague, disagreement, people}
    reasons: list[str] = Field(default_factory=list, sa_column=Column(JSON))  # plain-language reasons
    paraphrases: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    paraphrase_version: int = 0  # rule version the paraphrases were written for
    paraphrase_source: str = ""  # llm|template
    probes: list[Any] = Field(default_factory=list, sa_column=Column(JSON))  # [{text, expect}]
    probe_source: str = ""  # llm|examples
    rewrite: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    history: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    cost_usd: float = 0.0
    latency_ms: int = 0
    error: Optional[str] = None
    computed_at: Optional[datetime] = None
