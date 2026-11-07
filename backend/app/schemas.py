"""API request/response models (BUILD.md §5). Mirrored 1:1 in frontend/src/lib/api.ts.

Rules for everyone:
- Don't rename or remove a field without updating api.ts in the same commit.
- Errors are always `{"detail": "<human message>"}` (422 also carries `errors: [...]`).
- Datetimes are ISO-8601 UTC strings; dates are "YYYY-MM-DD".
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

Role = Literal["admin", "creator", "reviewer", "approver"]
ItemState = Literal["draft", "checking", "findings_ready", "in_review", "changes_requested", "approved", "rejected"]
Channel = Literal["instagram", "email", "website", "linkedin"]
Severity = Literal["blocking", "major", "minor"]
CheckMethod = Literal["tier0", "jev", "claim", "visual"]
TermKind = Literal["banned", "preferred", "capitalise"]
Band = Literal["flag", "human", "pass"]
FindingStatus = Literal["open", "accepted", "edited", "dismissed", "overridden"]
FindingTier = Literal["tier0", "jev", "claim", "visual", "logo"]
ClaimStatus = Literal["supported", "unsupported", "outdated"]
CheckStatus = Literal["not_run", "queued", "running", "done", "error"]
UnitKind = Literal["text", "image", "caption", "alt_text"]
VersionKind = Literal["text", "image", "pdf"]
DismissReason = Literal["false_alarm", "approved_exception", "rule_outdated", "other"]
CheckStage = Literal["queued", "units", "tier0", "jev", "claims", "explain", "selfcheck", "done", "error"]


def _as_utc(d: UtcDatetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# SQLite returns naive datetimes; all stored times are UTC, so tag them before serialising.
UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- health
class HealthOut(_Out):
    ok: bool
    version: str
    jev_configured: bool
    llm_configured: bool
    demo_mode: bool


# ---------------------------------------------------------------- auth
class UserOut(_Out):
    id: str
    name: str
    role: Role


class LoginIn(BaseModel):
    user_id: str


class LoginOut(_Out):
    user: UserOut
    # Same value as the session cookie. The frontend keeps it in sessionStorage and sends
    # `Authorization: Bearer <token>` (and `?token=` for SSE) so login still works when the
    # Space is embedded in an iframe and third-party cookies are blocked.
    token: str


class MeOut(_Out):
    user: Optional[UserOut]  # null when not signed in
    demo_mode: bool
    users: list[UserOut]  # demo users for the role picker (empty when demo_mode is off)


class OkOut(_Out):
    ok: bool = True
    message: str = ""


# ---------------------------------------------------------------- brand kit
class PaletteColor(_Out):
    name: str
    hex: str


class LogoOut(_Out):
    id: int
    variant: str  # "approved" | "alt_wrong"
    url: str  # GET /api/assets/{id}


class BrandOut(_Out):
    id: int
    name: str
    tone_labels: list[str]
    channel_targets: dict[str, dict[str, Any]]  # {"instagram": {"max_grade": 8}}
    palette: list[PaletteColor]
    fonts: list[str]
    logos: list[LogoOut]
    guideline_url: Optional[str] = None
    rule_count: int
    term_count: int
    fact_count: int
    demo: bool  # true = seeded fictional demo brand; UI must label it


class RuleOut(_Out):
    id: int
    code: str
    version: int
    category: str
    severity: Severity
    statement: str
    source_quote: str
    page: Optional[int]
    check_method: CheckMethod
    params: dict[str, Any]
    examples_violating: list[str]
    examples_ok: list[str]
    enabled: bool
    updated_at: UtcDatetime


class RuleCreate(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    category: str = Field(min_length=1, max_length=40)
    severity: Severity = "minor"
    statement: str = Field(min_length=1, max_length=1000)
    source_quote: str = ""
    page: Optional[int] = None
    check_method: CheckMethod = "jev"
    params: dict[str, Any] = Field(default_factory=dict)
    examples_violating: list[str] = Field(default_factory=list)
    examples_ok: list[str] = Field(default_factory=list)
    enabled: bool = True


class RuleUpdate(BaseModel):
    """PATCH: only fields that are sent are changed. Any change bumps `version`."""

    category: Optional[str] = None
    severity: Optional[Severity] = None
    statement: Optional[str] = None
    source_quote: Optional[str] = None
    page: Optional[int] = None
    check_method: Optional[CheckMethod] = None
    params: Optional[dict[str, Any]] = None
    examples_violating: Optional[list[str]] = None
    examples_ok: Optional[list[str]] = None
    enabled: Optional[bool] = None


class TermOut(_Out):
    id: int
    kind: TermKind
    value: str
    replacement: Optional[str]


class TermCreate(BaseModel):
    kind: TermKind
    value: str = Field(min_length=1, max_length=200)
    replacement: Optional[str] = None


class TermUpdate(BaseModel):
    kind: Optional[TermKind] = None
    value: Optional[str] = None
    replacement: Optional[str] = None


class FactOut(_Out):
    id: int
    code: str
    statement: str
    value: str
    source: str
    valid_until: Optional[date]
    expired: bool


class FactCreate(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    statement: str = Field(min_length=1, max_length=1000)
    value: str = ""
    source: str = ""
    valid_until: Optional[date] = None


class FactUpdate(BaseModel):
    statement: Optional[str] = None
    value: Optional[str] = None
    source: Optional[str] = None
    valid_until: Optional[date] = None


# P1: guideline PDF import
class ProposedRule(_Out):
    rule: RuleCreate
    quote_verified: bool
    quote_score: float  # rapidfuzz partial ratio vs page text, 0-100


class ImportPdfOut(_Out):
    proposals: list[ProposedRule]
    pages: int


class ImportPdfConfirmIn(BaseModel):
    rules: list[RuleCreate]


# ---------------------------------------------------------------- content
class ItemCreated(_Out):
    """POST /items is multipart/form-data: title, channel, confidential, text?, file?, caption?, alt_text?"""

    item_id: int
    version_id: int


class ItemSummary(_Out):
    """GET /items?state=&mine=&key=  (key=demo_flawed_post finds the "Try the demo" post)."""

    id: int
    key: Optional[str]  # stable seed handle; null for user-created items
    title: str
    channel: Channel
    creator_id: str
    creator_name: str
    state: ItemState
    confidential: bool
    created_at: UtcDatetime
    updated_at: UtcDatetime
    latest_version_id: Optional[int]
    latest_version_n: Optional[int]
    open_findings: int
    blocking_open: int


class UnitOut(_Out):
    id: int
    kind: UnitKind
    page: Optional[int]
    idx: int
    text: str
    image_url: Optional[str]  # GET /api/units/{id}/image
    char_start: Optional[int]
    char_end: Optional[int]


class VersionSummary(_Out):
    id: int
    n: int
    kind: VersionKind
    check_status: CheckStatus
    created_at: UtcDatetime


class VersionOut(_Out):
    id: int
    item_id: int
    n: int
    kind: VersionKind
    text: str
    caption: Optional[str]
    alt_text: Optional[str]
    file_url: Optional[str]  # GET /api/versions/{id}/file
    check_status: CheckStatus
    stats: dict[str, Any]  # units_total, units_done, judgments, elapsed_ms, cost_usd, pii_masked
    created_at: UtcDatetime
    units: list[UnitOut]


class ActionPermission(_Out):
    allowed: bool
    reason: Optional[str] = None  # human message shown on disabled buttons


class ItemDetail(_Out):
    item: ItemSummary
    versions: list[VersionSummary]  # ascending by n
    # keys: submit, approve, request_changes, reject, resubmit, override, comment
    actions: dict[str, ActionPermission]


class RuleRef(_Out):
    code: str
    version: int
    statement: str
    source_quote: str
    page: Optional[int]


class FactRef(_Out):
    code: str
    statement: str
    source: str
    valid_until: Optional[date]
    expired: bool


class FindingOut(_Out):
    id: int
    version_id: int
    unit_id: Optional[int]
    rule_code: Optional[str]
    rule_version: Optional[int]
    fact_code: Optional[str]
    tier: FindingTier
    category: str
    severity: Severity
    band: Band
    probability: Optional[float]
    claim_status: Optional[ClaimStatus]
    span_start: Optional[int]
    span_end: Optional[int]
    quote: str  # offending excerpt of the content
    explanation: str
    fix: Optional[str]
    fix_verified: bool
    status: FindingStatus
    dismiss_reason: Optional[DismissReason]
    dismiss_note: Optional[str]
    edited_text: Optional[str]
    rule: Optional[RuleRef]  # guideline quote + page for the card
    fact: Optional[FactRef]


class FindingEditIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class FindingDismissIn(BaseModel):
    reason: DismissReason
    note: Optional[str] = Field(default=None, max_length=1000)


class OverrideIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class NoteIn(BaseModel):
    note: str = Field(default="", max_length=2000)


class ResubmitOut(_Out):
    item_id: int
    version_id: int
    n: int


class CheckEvent(_Out):
    """One SSE `data:` payload on GET /versions/{id}/events. Event name = `stage`."""

    stage: CheckStage
    units_done: int = 0
    units_total: int = 0
    judgments: int = 0
    elapsed_ms: int = 0
    cost_usd: float = 0.0
    finding: Optional[FindingOut] = None
    message: Optional[str] = None


# ---------------------------------------------------------------- review
class CommentOut(_Out):
    id: int
    item_id: int
    user_id: str
    user_name: str
    role: Role
    body: str
    created_at: UtcDatetime


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class AuditEventOut(_Out):
    id: int
    item_id: Optional[int]
    version_id: Optional[int]
    user_id: Optional[str]
    user_name: Optional[str]
    action: str
    detail: dict[str, Any]
    created_at: UtcDatetime


class AIUsageGroup(_Out):
    provider: str
    model: str
    purpose: str
    count: int
    input_tokens: int
    cost_usd: float


class AIUsageOut(_Out):
    item_id: int
    confidential: bool
    no_external_services: bool  # true => UI shows "No external services used"
    external_calls: int
    pii_masked: int
    total_cost_usd: float
    groups: list[AIUsageGroup]


# ---------------------------------------------------------------- audience (P1)
class AudienceOut(_Out):
    version_id: int
    status: Literal["none", "running", "done", "error", "unavailable"]
    label: str = "Simulated audience, directional signal, not a survey"
    results: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[UtcDatetime] = None


# ---------------------------------------------------------------- insights
class InsightsKpis(_Out):
    items_checked: int
    findings: int
    fix_accept_rate: float  # 0..1
    avg_check_ms: int
    jev_judgments: int
    total_cost_usd: float


class CountBucket(_Out):
    key: str
    count: int


class DismissalRate(_Out):
    rule_code: str
    findings: int
    dismissed: int
    rate: float
    possibly_unclear: bool  # rate > 0.40


class InsightsOut(_Out):
    kpis: InsightsKpis
    by_category: list[CountBucket]
    top_rules: list[CountBucket]  # key = rule_code, max 10
    dismissal_rate: list[DismissalRate]
    by_channel: list[CountBucket]


# ---------------------------------------------------------------- eval
class EvalLatestOut(_Out):
    available: bool
    run_at: Optional[UtcDatetime] = None
    split: Optional[str] = None
    models: dict[str, str] = Field(default_factory=dict)
    # precision/recall/f1 (overall + per_category), claim_accuracy, latency_ms {median,p95},
    # cost_per_item_usd, calibration [{bin, predicted, observed, n}]
    metrics: dict[str, Any] = Field(default_factory=dict)
    misses: list[dict[str, Any]] = Field(default_factory=list)


class EvalRunOut(_Out):
    started: bool
    message: str
