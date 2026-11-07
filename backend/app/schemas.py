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


class ImageBox(_Out):
    """A region of an image as fractions (0..1) of its height (top, bottom) and width (left, right)."""

    top: float
    left: float
    bottom: float
    right: float


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
    # P1 logo fidelity overlay (image findings only): fractions of the image width/height.
    box: Optional[ImageBox] = None
    quad: Optional[list[list[float]]] = None  # [[x, y], ...] TL, TR, BR, BL of the projected approved logo


class FindingEditIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class FindingDismissIn(BaseModel):
    reason: DismissReason
    note: Optional[str] = Field(default=None, max_length=1000)


class OverrideIn(BaseModel):
    reason: str = Field(max_length=1000)  # >= 10 chars, checked in the handler for a human message


class NoteIn(BaseModel):
    note: str = Field(default="", max_length=2000)


class DiffSegment(_Out):
    op: Literal["equal", "insert", "delete"]
    text: str


class SkippedFix(_Out):
    finding_id: int
    reason: str


class ResubmitOut(_Out):
    item_id: int
    version_id: int
    n: int
    diff: list[DiffSegment]  # word-level, previous text -> new text
    applied: list[int]  # finding ids whose fix/edit went into the new text
    skipped: list[SkippedFix]  # e.g. overlapping spans


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
    summary: str  # plain-language line, e.g. "No external services used ..."
    groups: list[AIUsageGroup]  # grouped by provider + purpose (model = distinct models joined by ", ")


# ---------------------------------------------------------------- audience (P1)
AUDIENCE_LABEL = "Simulated audience, directional signal, not a survey"
AUDIENCE_ATTRIBUTION = "Personas: NVIDIA Nemotron-Personas-India (CC-BY-4.0)"
AudienceSignal = Literal["distrust", "confused", "unsure", "ok"]


class AudiencePersona(_Out):
    id: str
    idx: int
    segment: str
    bio: str  # short: "34, Shop owner, Nagpur, Maharashtra"
    detail: str  # the dataset's one-line persona
    distrust: Optional[float]  # Jev p, 0..1 (null = not answered: its request failed)
    confused: Optional[float]
    signal: Optional[AudienceSignal]  # dot colour, decided in code: distrust > 0.7 red; confused > 0.7 or 0.3-0.7 amber


class AudienceTotals(_Out):
    n: int
    answered: int
    distrust_pct: float  # 0..1, share with distrust p > 0.70
    confused_pct: float  # share with confused p > 0.70
    unsure_pct: float  # share with distrust p in 0.30..0.70
    mean_distrust: float  # mean p (smoother than the thresholded shares; use it for small differences)
    mean_confused: float


class AudienceSegment(AudienceTotals):
    key: str
    label: str
    worst: list[AudiencePersona]  # the 3 most negative personas
    why: Optional[str] = None  # one LLM-written sentence for the worst segments (an AI guess)
    why_source: Optional[Literal["llm"]] = None


class AudienceParagraphP(_Out):
    index: int
    p: float


class AudienceCulprit(_Out):
    index: int  # paragraph index in the version text
    text: str
    char_start: Optional[int]  # offsets into VersionOut.text, for highlighting
    char_end: Optional[int]
    probability: float  # Jev Choice probability of this paragraph
    any_distrust: float  # companion Noul: does any part reduce trust?
    certain: bool  # any_distrust >= 0.70; otherwise show as "possibly"
    probabilities: list[AudienceParagraphP]


class AudienceDelta(_Out):
    distrust_pct: float  # this version minus the compared one (negative = trusted more)
    confused_pct: float
    unsure_pct: float
    mean_distrust: float
    mean_confused: float


class AudienceSegmentDelta(_Out):
    key: str
    label: str
    previous: AudienceTotals
    delta: AudienceDelta


class AudienceCompare(_Out):
    version_id: int
    version_n: Optional[int]
    run_id: int
    created_at: Optional[str]
    overall: AudienceTotals
    delta: AudienceDelta
    segments: list[AudienceSegmentDelta]
    culprit_paragraph: Optional[AudienceCulprit] = None


class AudienceProgress(_Out):
    done: int
    total: int


class AudienceOut(_Out):
    """GET/POST /versions/{id}/audience. Poll GET (or GET .../audience/events, SSE) while `running`;
    `personas` fill in as each batch of 20 finishes."""

    version_id: int
    run_id: Optional[int] = None
    status: Literal["none", "running", "done", "error", "unavailable"]
    label: str = AUDIENCE_LABEL
    attribution: str = AUDIENCE_ATTRIBUTION
    message: Optional[str] = None  # human message for error / unavailable
    progress: Optional[AudienceProgress] = None
    overall: Optional[AudienceTotals] = None
    segments: list[AudienceSegment] = Field(default_factory=list)
    personas: list[AudiencePersona] = Field(default_factory=list)
    culprit_paragraph: Optional[AudienceCulprit] = None
    compare_to_previous_version: Optional[AudienceCompare] = None
    warnings: list[str] = Field(default_factory=list)
    elapsed_ms: Optional[int] = None
    judgments: Optional[int] = None
    cost_usd: Optional[float] = None
    created_at: Optional[UtcDatetime] = None
    updated_at: Optional[UtcDatetime] = None


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
    label: Optional[str] = None  # top_rules: the rule statement


class DismissalRate(_Out):
    rule_code: str
    findings: int
    dismissed: int
    rate: float
    possibly_unclear: bool  # rate > 0.40 and findings >= 3
    statement: Optional[str] = None


class InsightsOut(_Out):
    kpis: InsightsKpis
    by_category: list[CountBucket]
    top_rules: list[CountBucket]  # key = rule_code, max 10
    dismissal_rate: list[DismissalRate]
    by_channel: list[CountBucket]


# ---------------------------------------------------------------- eval
class EvalLatestOut(_Out):
    available: bool
    # "not_run": no results file yet; "running": an admin re-run is in progress (older results, if any, are shown)
    status: Literal["ok", "not_run", "running"] = "ok"
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
