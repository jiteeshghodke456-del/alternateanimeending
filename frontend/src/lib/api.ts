/**
 * Brand Gate API contract + client. Source of truth for shapes: backend/app/schemas.py.
 * Change a field there => change it here in the same commit (CLAUDE.md "Code conventions").
 *
 * - All calls go to same-origin `/api` (Vite dev server proxies to :7860).
 * - `credentials: "include"` sends the session cookie. As a fallback for iframes / blocked
 *   third-party cookies, the login token is kept in sessionStorage and sent as a Bearer header
 *   (and as `?token=` on the SSE URL, because EventSource can't set headers).
 * - Every non-2xx response becomes an `ApiError` with a human `message` (server `detail`).
 */

// ------------------------------------------------------------------ enums
export type Role = "admin" | "creator" | "reviewer" | "approver";
export type ItemState =
  | "draft" | "checking" | "findings_ready" | "in_review" | "changes_requested" | "approved" | "rejected";
export type Channel = "instagram" | "email" | "website" | "linkedin";
export type Severity = "blocking" | "major" | "minor";
export type CheckMethod = "tier0" | "jev" | "claim" | "visual";
export type TermKind = "banned" | "preferred" | "capitalise";
export type Band = "flag" | "human" | "pass";
export type FindingStatus = "open" | "accepted" | "edited" | "dismissed" | "overridden";
export type FindingTier = "tier0" | "jev" | "claim" | "visual" | "logo";
export type ClaimStatus = "supported" | "unsupported" | "outdated";
export type CheckStatus = "not_run" | "queued" | "running" | "done" | "error";
export type UnitKind = "text" | "image" | "caption" | "alt_text";
export type VersionKind = "text" | "image" | "pdf";
export type DismissReason = "false_alarm" | "approved_exception" | "rule_outdated" | "other";
export type CheckStage =
  | "queued" | "units" | "tier0" | "jev" | "claims" | "explain" | "selfcheck" | "done" | "error";

/** ISO-8601 UTC datetime string, e.g. "2026-11-07T09:28:22.367381Z". */
export type IsoDateTime = string;
/** "YYYY-MM-DD" */
export type IsoDate = string;

export const ROLE_LABELS: Record<Role, string> = {
  admin: "Brand Admin", creator: "Creator", reviewer: "Reviewer", approver: "Approver",
};
/** "Meera or Kabir" for the users holding these roles (names come from the server, never hard-coded). */
export function namesWithRole(users: UserOut[], ...roles: Role[]): string {
  const names = users.filter((u) => roles.includes(u.role)).map((u) => u.name);
  if (names.length === 0) return roles.map((r) => `a ${ROLE_LABELS[r].toLowerCase()}`).join(" or ");
  return names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;
}
export const CHANNELS: Channel[] = ["instagram", "email", "website", "linkedin"];
export const DISMISS_REASONS: { value: DismissReason; label: string }[] = [
  { value: "false_alarm", label: "False alarm" },
  { value: "approved_exception", label: "Approved exception" },
  { value: "rule_outdated", label: "Rule outdated" },
  { value: "other", label: "Other" },
];
/** Key of the seeded "Try the demo" post: `api.items({ key: DEMO_ITEM_KEY })`. */
export const DEMO_ITEM_KEY = "demo_flawed_post";

// ------------------------------------------------------------------ health / auth
export interface HealthOut {
  ok: boolean;
  version: string;
  jev_configured: boolean;
  llm_configured: boolean;
  demo_mode: boolean;
  /** false while the server builds the demo after a deploy or wake (other calls answer 503). Older servers omit it. */
  ready?: boolean;
  stage?: string | null;
}

export interface UserOut { id: string; name: string; role: Role }
export interface LoginIn { user_id: string }
export interface LoginOut { user: UserOut; token: string }
export interface MeOut {
  user: UserOut | null; // null when not signed in
  demo_mode: boolean;
  users: UserOut[]; // demo users for the role picker; ids come from the seed (don't hard-code them)
}
export interface OkOut { ok: boolean; message: string }

// ------------------------------------------------------------------ brand kit
export interface PaletteColor { name: string; hex: string }
export interface LogoOut { id: number; variant: "approved" | "alt_wrong" | string; url: string }
export interface BrandOut {
  id: number;
  name: string;
  tone_labels: string[];
  channel_targets: Record<string, { max_grade?: number; [k: string]: unknown }>;
  palette: PaletteColor[];
  fonts: string[];
  logos: LogoOut[];
  guideline_url: string | null;
  rule_count: number;
  term_count: number;
  fact_count: number;
  demo: boolean; // seeded fictional brand: label it as demo data
  // Workspace / setup fields (newer servers; all optional so older servers still type-check).
  tagline?: string | null;
  description?: string | null;
  logo_url?: string | null;
  counts?: { rules: number; facts: number; terms: number; items: number };
  /** true while a blank workspace still needs its brand set up: show the setup card. */
  is_blank_setup?: boolean;
}

// ------------------------------------------------------------------ workspaces
export type WorkspaceKind = "demo" | "blank";
export interface WorkspaceCreateIn { kind: WorkspaceKind }
export interface WorkspaceCreated {
  id: string; kind: WorkspaceKind; brand: { name: string }; created_at: IsoDateTime;
  /** Signed workspace token (= the bg_ws cookie). Kept for browsers that block cookies; see setWorkspaceToken. */
  token?: string;
}
export interface WorkspaceOut extends WorkspaceCreated {
  token?: string;
  expires_at: IsoDateTime | null;
  limits: { max_workspaces: number; ttl_hours: number };
}
export interface BrandPatchIn { name?: string; tagline?: string; description?: string }
export interface PalettePutIn { colours: PaletteColor[] }
export interface FactImportOut { imported: number; skipped: { row: number; reason: string }[] }

export interface RuleOut {
  id: number;
  code: string;
  version: number;
  category: string;
  severity: Severity;
  statement: string;
  source_quote: string;
  page: number | null;
  check_method: CheckMethod;
  params: Record<string, unknown>; // e.g. { vague: true } on the 5 planted vague rules
  examples_violating: string[];
  examples_ok: string[];
  enabled: boolean;
  updated_at: IsoDateTime;
}
export interface RuleCreate {
  code: string;
  category: string;
  severity?: Severity;
  statement: string;
  source_quote?: string;
  page?: number | null;
  check_method?: CheckMethod;
  params?: Record<string, unknown>;
  examples_violating?: string[];
  examples_ok?: string[];
  enabled?: boolean;
}
/** PATCH: send only changed fields. Any change bumps `version`. */
export type RuleUpdate = Partial<Omit<RuleCreate, "code">>;

export interface TermOut { id: number; kind: TermKind; value: string; replacement: string | null }
export interface TermCreate { kind: TermKind; value: string; replacement?: string | null }
export type TermUpdate = Partial<TermCreate>;

export interface FactOut {
  id: number;
  code: string;
  statement: string;
  value: string;
  source: string;
  valid_until: IsoDate | null;
  expired: boolean;
}
export interface FactCreate {
  code: string;
  statement: string;
  value?: string;
  source?: string;
  valid_until?: IsoDate | null;
}
export type FactUpdate = Partial<Omit<FactCreate, "code">>;

// P1 guideline import
/** @deprecated superseded by ImportProposal (the server sends ImportJobOut). */
export interface ProposedRule { rule: RuleCreate; quote_verified: boolean; quote_score: number }
/** @deprecated superseded by ImportJobOut. */
export interface ImportPdfOut { proposals: ProposedRule[]; pages: number }
/** @deprecated superseded by ImportConfirmIn. */
export interface ImportPdfConfirmIn { rules: RuleCreate[] }

/** A proposed rule in the rules.json shape, without a code (R-IMP-NN is given on confirm). */
export interface ImportRuleDraft {
  statement: string;
  category: string; // tone | claims | visual | messaging | terminology | legal | accessibility
  severity: Severity;
  check_method: CheckMethod;
  source_quote: string;
  page: number | null; // 1-based PDF page
  examples_violating: string[];
  examples_ok: string[];
}
export interface ImportProposal {
  id: string; // "P-01"; use it in ImportConfirmIn
  rule: ImportRuleDraft;
  quote_verified: boolean; // false: show a "quote not found" badge
  quote_score: number; // rapidfuzz partial ratio 0..100 (verified at >= 90)
  selected: boolean; // default tick: true only when the quote is verified
}
export interface ImportJobOut {
  import_id: string;
  status: "running" | "done" | "error"; // poll GET /brand/import-pdf/{id} every ~1 s while running
  filename: string;
  pages: number;
  proposals: ImportProposal[]; // up to 40
  verified: number; // proposals with a verified quote
  warnings: string[];
  message: string | null; // human message when status is "error"
  elapsed_ms: number | null;
  cost_usd: number | null;
  confirmed: boolean;
  created_codes: string[];
}
export interface ImportConfirmIn {
  selected: string[]; // proposal ids, at least one
  edits?: Record<string, Partial<Pick<ImportRuleDraft, "statement" | "category" | "severity" | "check_method">>>;
  enable?: boolean; // default false: imported rules start disabled (turn on in the Brand Kit)
}
export interface ImportConfirmOut { import_id: string; created: RuleOut[] }

// ------------------------------------------------------------------ content
export interface ItemCreateIn {
  title: string;
  channel: Channel;
  confidential?: boolean;
  text?: string; // send text OR file
  file?: File | Blob; // PNG/JPG/WEBP <= 10 MB, or PDF <= 20 pages
  caption?: string; // images only
  alt_text?: string; // images only
}
export interface ItemCreated { item_id: number; version_id: number }

export interface ItemSummary {
  id: number;
  key: string | null; // stable seed handle, e.g. DEMO_ITEM_KEY
  title: string;
  channel: Channel;
  creator_id: string;
  creator_name: string;
  state: ItemState;
  confidential: boolean;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
  latest_version_id: number | null;
  latest_version_n: number | null;
  open_findings: number;
  blocking_open: number;
}

export interface UnitOut {
  id: number;
  kind: UnitKind;
  page: number | null; // 1-based, PDFs only
  idx: number;
  text: string;
  image_url: string | null;
  char_start: number | null; // offsets into VersionOut.text
  char_end: number | null;
}

export interface VersionSummary {
  id: number;
  n: number;
  kind: VersionKind;
  check_status: CheckStatus;
  created_at: IsoDateTime;
}

export interface VersionStats {
  units_total?: number;
  units_done?: number;
  judgments?: number;
  elapsed_ms?: number;
  cost_usd?: number;
  pii_masked?: number;
  /** Human notes about the check, e.g. "Add rules first: ..." in a brand with no rules. */
  warnings?: string[];
  [k: string]: unknown;
}

export interface VersionOut {
  id: number;
  item_id: number;
  n: number;
  kind: VersionKind;
  text: string; // PDFs: paragraphs joined by "\n\n"; images: the caption
  caption: string | null;
  alt_text: string | null;
  file_url: string | null;
  check_status: CheckStatus;
  stats: VersionStats;
  created_at: IsoDateTime;
  units: UnitOut[];
}

export interface ActionPermission { allowed: boolean; reason: string | null }
export type ItemActionKey =
  | "submit" | "approve" | "request_changes" | "reject" | "resubmit" | "override" | "comment";

export interface ItemDetail {
  item: ItemSummary;
  versions: VersionSummary[]; // ascending by n
  actions: Record<ItemActionKey, ActionPermission>;
}

export interface RuleRef {
  code: string;
  version: number;
  statement: string;
  source_quote: string;
  page: number | null;
}
export interface FactRef {
  code: string;
  statement: string;
  source: string;
  valid_until: IsoDate | null;
  expired: boolean;
}

export interface FindingOut {
  id: number;
  version_id: number;
  unit_id: number | null;
  rule_code: string | null;
  rule_version: number | null;
  fact_code: string | null;
  tier: FindingTier;
  category: string;
  severity: Severity;
  band: Band;
  probability: number | null;
  claim_status: ClaimStatus | null;
  span_start: number | null; // offsets into VersionOut.text
  span_end: number | null;
  quote: string; // offending excerpt of the content
  explanation: string;
  fix: string | null;
  fix_verified: boolean;
  status: FindingStatus;
  dismiss_reason: DismissReason | null;
  dismiss_note: string | null;
  edited_text: string | null;
  rule: RuleRef | null; // guideline quote + page
  fact: FactRef | null;
  // P1 logo fidelity overlay (image findings only), fractions of the image size
  box?: ImageBox | null;
  quad?: [number, number][] | null; // [x, y] corners TL, TR, BR, BL of the projected approved logo
  // Edit regions: findings in the same sentence share ONE fix. For them span/quote cover the whole
  // region (fix still replaces exactly span) and hit_start/hit_end are the flagged words (offsets into
  // VersionOut.text), for highlighting. Same fix_group = same fix: accepting one accepts the group.
  hit_start?: number | null;
  hit_end?: number | null;
  fix_group?: string | null;
}

/** A region of an image as fractions (0..1): top/bottom of the height, left/right of the width. */
export interface ImageBox { top: number; left: number; bottom: number; right: number }

/** VersionOut.stats.logo[] (P1 logo fidelity, one per image unit; local OpenCV, also for confidential items). */
export interface LogoCheck {
  unit_idx: number;
  verdict: "genuine" | "altered" | "not_found" | null; // null = the check could not run (see `error`)
  band?: Band;
  reason?: string;
  inliers?: number; // SIFT+RANSAC inliers with a valid projected outline (0 if degenerate)
  raw_inliers?: number;
  good_matches?: number;
  has_logo?: number | null; // Jev "does the image show a logo?" (null when not asked, e.g. confidential)
  stretched?: boolean;
  box?: ImageBox | null; // draw this (or `quad`) on the image; present for genuine logos too
  quad?: [number, number][] | null;
  aspect_deviation?: number | null;
  colours?: { hex: string; share: number; delta_e: number; nearest: string }[];
  off_palette?: { hex: string; share: number; delta_e: number; nearest: string }[];
  elapsed_ms?: number;
  error?: string;
}

export interface FindingEditIn { text: string }
export interface FindingDismissIn { reason: DismissReason; note?: string | null }
export interface OverrideIn { reason: string }
export interface NoteIn { note: string }
export interface DiffSegment { op: "equal" | "insert" | "delete"; text: string }
export interface SkippedFix { finding_id: number; reason: string }
export interface ResubmitOut {
  item_id: number;
  version_id: number;
  n: number;
  diff: DiffSegment[]; // word-level, previous text -> new text
  applied: number[]; // finding ids applied
  skipped: SkippedFix[]; // e.g. overlapping spans
}

/** One SSE message on /api/versions/{id}/events. */
export interface CheckEvent {
  stage: CheckStage;
  units_done: number;
  units_total: number;
  judgments: number;
  elapsed_ms: number;
  cost_usd: number;
  finding: FindingOut | null;
  message: string | null;
}

// ------------------------------------------------------------------ review
export interface CommentOut {
  id: number;
  item_id: number;
  user_id: string;
  user_name: string;
  role: Role;
  body: string;
  created_at: IsoDateTime;
}
export interface CommentIn { body: string }

export interface AuditEventOut {
  id: number;
  item_id: number | null;
  version_id: number | null;
  user_id: string | null;
  user_name: string | null;
  action: string;
  detail: Record<string, unknown>;
  created_at: IsoDateTime;
}

export interface AIUsageGroup {
  provider: string;
  model: string;
  purpose: string;
  count: number;
  input_tokens: number;
  cost_usd: number;
}
export interface AIUsageOut {
  item_id: number;
  confidential: boolean;
  no_external_services: boolean; // show "No external services used"
  external_calls: number;
  pii_masked: number;
  total_cost_usd: number;
  summary: string; // plain-language line
  groups: AIUsageGroup[]; // grouped by provider + purpose
}

// ------------------------------------------------------------------ audience (P1)
// Shapes mirror backend/app/schemas.py (Audience*). Shares are 0..1; "p" values are Jev probabilities.
export type AudienceLean = "trust" | "unsure" | "doubt";
export interface AudiencePersona {
  id: string;
  idx: number;
  segment: string; // reader-group key
  bio: string; // short: "34, Shop owner, Nagpur, Maharashtra"
  detail: string; // the group's description and trust / doubt triggers
  score: number | null; // expected reaction 1 (trusts it, would act) .. 5 (distrusts it, put off); null = not answered
  level: 1 | 2 | 3 | 4 | 5 | null; // nearest level, decided in code
  lean: AudienceLean | null; // <= 2.5 trust, >= 3.5 doubt, else unsure (decided in code)
  confused: number | null; // Jev p that this reader would misunderstand the main message
  confused_flag: boolean | null; // confused > 0.70
}
export interface AudienceTotals {
  n: number;
  answered: number;
  mean_score: number; // 1..5, lower = more trusting
  sd: number; // spread of the readers' scores
  trust_pct: number; // 0..1, share with score <= 2.5
  unsure_pct: number;
  doubt_pct: number; // share with score >= 3.5
  confused_pct: number; // share with confused p > 0.70
  mean_confused: number;
  levels: number[]; // readers at each level 1..5
}
export interface AudienceSegment extends AudienceTotals {
  key: string;
  label: string; // e.g. "First-time college students"
  description: string;
  vs_overall: number; // group mean minus the post's mean (positive = more negative than average)
  worst: AudiencePersona[]; // the 3 most negative readers
  why: string | null; // optional LLM sentence (an AI guess); null when not asked or the call failed
  why_source: "llm" | null;
}
export interface AudienceCulprit {
  index: number; // paragraph index in the version text
  text: string;
  char_start: number | null; // offsets into VersionOut.text, for highlighting
  char_end: number | null;
  probability: number; // Jev Choice probability of this paragraph
  any_distrust: number; // companion Noul: does any part reduce trust?
  certain: boolean; // false: show as "possibly"
  probabilities: { index: number; p: number }[];
}
export interface AudienceDelta {
  mean_score: number; // this version minus the compared one (negative = reacts better)
  trust_pct: number;
  unsure_pct: number;
  doubt_pct: number;
  confused_pct: number;
  mean_confused: number;
}
export interface AudienceCompare {
  version_id: number;
  version_n: number | null;
  run_id: number;
  created_at: string | null;
  overall: AudienceTotals; // the compared (previous) version's totals
  delta: AudienceDelta;
  segments: { key: string; label: string; previous: AudienceTotals; delta: AudienceDelta }[];
  culprit_paragraph: AudienceCulprit | null;
}
export interface AudienceOut {
  version_id: number;
  run_id: number | null;
  status: "none" | "running" | "done" | "error" | "unavailable";
  label: string; // "Simulated audience, directional signal, not a survey" (always show it)
  attribution: string; // "Personas: NVIDIA Nemotron-Personas-India (CC-BY-4.0)" (licence-required; always show it)
  message: string | null; // human message for error / unavailable
  audience_source: "brand" | "default" | null; // brand-defined reader groups (audiences.json) or the 6 default groups
  progress: { done: number; total: number } | null;
  overall: AudienceTotals | null;
  segments: AudienceSegment[];
  personas: AudiencePersona[]; // fills in while running (all readers when done)
  culprit_paragraph: AudienceCulprit | null;
  compare_to_previous_version: AudienceCompare | null;
  warnings: string[];
  elapsed_ms: number | null;
  judgments: number | null;
  cost_usd: number | null;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
  /** @deprecated never sent by the server; the results are the top-level fields above. */
  results?: Record<string, unknown>;
}

// ------------------------------------------------------------------ insights
export interface InsightsKpis {
  items_checked: number;
  findings: number;
  fix_accept_rate: number; // 0..1
  avg_check_ms: number;
  jev_judgments: number;
  total_cost_usd: number;
}
export interface CountBucket { key: string; count: number; label?: string | null } // top_rules: label = rule statement
export interface DismissalRate {
  rule_code: string;
  findings: number;
  dismissed: number;
  rate: number;
  possibly_unclear: boolean; // rate > 0.4 and findings >= 3
  statement?: string | null;
}
export interface InsightsOut {
  kpis: InsightsKpis;
  by_category: CountBucket[];
  top_rules: CountBucket[];
  dismissal_rate: DismissalRate[];
  by_channel: CountBucket[];
}

// ------------------------------------------------------------------ eval
export interface EvalLatestOut {
  available: boolean;
  status: "ok" | "not_run" | "running"; // not_run: no results file yet; running: an admin re-run is in progress
  run_at: IsoDateTime | null;
  split: string | null;
  models: Record<string, string>;
  metrics: Record<string, unknown>;
  misses: Record<string, unknown>[];
  /** Newer servers: which brand the eval set was built for (it measures the demo brand). Show when present. */
  brand_note?: string | null;
}
export interface EvalRunOut { started: boolean; message: string }

// ================================================================== client
export class ApiError extends Error {
  readonly status: number; // 0 = network error
  readonly detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
  get isAuth(): boolean { return this.status === 401; }
  get isForbidden(): boolean { return this.status === 403; }
  get notImplemented(): boolean { return this.status === 501; }
  /** 404/405 on an endpoint this server doesn't have yet (older backend). */
  get missing(): boolean { return this.status === 404 || this.status === 405; }
  /** The server is starting or waking up (our startup 503, a host's 502/503/504 page, or no connection). */
  get waking(): boolean {
    if (this.status === 0 || this.status === 502 || this.status === 504) return true;
    if (this.status !== 503) return false;
    const d = this.detail;
    return !(d && typeof d === "object" && "detail" in d) || (d as { starting?: boolean }).starting === true;
  }
}

/** Window event fired when a request finds the server starting or waking (SessionProvider waits for it). */
export const SERVER_WAKING_EVENT = "bg:server-waking";

const BASE = "/api";
const TOKEN_KEY = "bg-token";
const WS_KEY = "bg-ws";
const WS_HEADER = "X-BG-Workspace";
const WS_RESET_HEADER = "X-BG-Workspace-Reset";
/** Window event fired when a response says this browser's workspace expired (SessionProvider shows a toast). */
export const WORKSPACE_RESET_EVENT = "bg:workspace-reset";
let lastResetNotice = 0;
function notifyWorkspaceReset(): void {
  const now = Date.now();
  if (now - lastResetNotice < 10_000) return; // parallel requests: one notice
  lastResetNotice = now;
  try { window.dispatchEvent(new Event(WORKSPACE_RESET_EVENT)); } catch { /* no window (tests) */ }
}

let memWs: string | null = null;
/** Workspace token: sent as X-BG-Workspace (and ?ws= on URLs) so workspaces work where cookies are blocked. */
export function getWorkspaceToken(): string | null {
  if (memWs) return memWs;
  try { memWs = sessionStorage.getItem(WS_KEY); } catch { /* storage blocked */ }
  return memWs;
}
export function setWorkspaceToken(token: string | null): void {
  memWs = token;
  try {
    if (token) sessionStorage.setItem(WS_KEY, token);
    else sessionStorage.removeItem(WS_KEY);
  } catch { /* storage blocked */ }
}

let memToken: string | null = null;

export function getToken(): string | null {
  if (memToken) return memToken;
  try { memToken = sessionStorage.getItem(TOKEN_KEY); } catch { /* storage blocked */ }
  return memToken;
}
export function setToken(token: string | null): void {
  memToken = token;
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch { /* storage blocked */ }
}

const STATUS_MESSAGES: Record<number, string> = {
  401: "Please sign in first.",
  403: "You don't have permission to do that.",
  404: "We couldn't find that. It may have been removed or the demo was reset.",
  413: "That file is too large.",
  415: "That file type isn't supported.",
  422: "Some of the information isn't valid. Please check and try again.",
  429: "Too many requests. Please wait a moment and try again.",
  501: "This feature isn't available yet.",
  503: "The service is temporarily unavailable. Please try again shortly.",
};

function humanMessage(status: number, body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string" && d.trim()) return d;
  }
  if (status >= 500) return "Something went wrong on our side. Please try again.";
  return STATUS_MESSAGES[status] ?? `Request failed (${status}).`;
}

type Query = Record<string, string | number | boolean | null | undefined>;

function qs(query?: Query): string {
  if (!query) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export async function request<T>(
  method: string,
  path: string,
  opts: { json?: unknown; form?: FormData; query?: Query; signal?: AbortSignal } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const ws = getWorkspaceToken();
  if (ws) headers[WS_HEADER] = ws;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}${qs(opts.query)}`, {
      method, headers, body, credentials: "include", signal: opts.signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    try { window.dispatchEvent(new Event(SERVER_WAKING_EVENT)); } catch { /* no window */ }
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.", e);
  }
  // A response that had to create a workspace hands back its token; keep it for cookie-less browsers.
  const newWs = res.headers.get(WS_HEADER);
  if (newWs) setWorkspaceToken(newWs);
  // The workspace this browser had expired (or was deleted) and the server opened a fresh demo.
  if (res.headers.get(WS_RESET_HEADER) === "1") notifyWorkspaceReset();
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = text; }
  }
  if (!res.ok) {
    const err = new ApiError(res.status, humanMessage(res.status, data), data);
    if (err.waking) {
      try { window.dispatchEvent(new Event(SERVER_WAKING_EVENT)); } catch { /* no window */ }
    }
    throw err;
  }
  return data as T;
}

const get = <T>(path: string, query?: Query) => request<T>("GET", path, { query });
const post = <T>(path: string, json?: unknown) => request<T>("POST", path, { json: json ?? {} });
const patch = <T>(path: string, json: unknown) => request<T>("PATCH", path, { json });
const put = <T>(path: string, json: unknown) => request<T>("PUT", path, { json });
const upload = <T>(path: string, file: File | Blob) => {
  const form = new FormData();
  form.append("file", file);
  return request<T>("POST", path, { form });
};

export const api = {
  // health / auth
  health: () => get<HealthOut>("/health"),
  me: () => get<MeOut>("/me"),
  async login(user_id: string): Promise<LoginOut> {
    const out = await post<LoginOut>("/demo/login", { user_id } satisfies LoginIn);
    setToken(out.token);
    return out;
  },
  async logout(): Promise<OkOut> {
    setToken(null);
    return post<OkOut>("/demo/logout");
  },
  reset: () => post<OkOut>("/demo/reset"),

  // workspaces (newer servers: 404 on older ones; callers degrade gracefully)
  /** 201; sets the workspace cookie and clears the login. Follow with me() and login(). */
  async createWorkspace(kind: WorkspaceKind): Promise<WorkspaceCreated> {
    const out = await post<WorkspaceCreated>("/workspaces", { kind } satisfies WorkspaceCreateIn);
    setToken(null); // the server cleared the login: the old user doesn't exist in the new workspace
    if (out.token) setWorkspaceToken(out.token);
    return out;
  },
  async workspace(): Promise<WorkspaceOut> {
    const out = await get<WorkspaceOut>("/workspace");
    if (out.token) setWorkspaceToken(out.token);
    return out;
  },

  // brand kit
  brand: () => get<BrandOut>("/brand"),
  updateBrand: (body: BrandPatchIn) => patch<BrandOut>("/brand", body),
  setPalette: (colours: PaletteColor[]) => put<BrandOut>("/brand/palette", { colours } satisfies PalettePutIn),
  /** PNG or JPEG, <= 5 MB. */
  uploadLogo: (file: File | Blob) => upload<BrandOut>("/brand/logo", file),
  /** CSV with header statement,value,source,valid_until. */
  importFacts: (file: File | Blob) => upload<FactImportOut>("/facts/import", file),
  rules: (q?: { category?: string; severity?: Severity; enabled?: boolean }) => get<RuleOut[]>("/rules", q),
  createRule: (body: RuleCreate) => post<RuleOut>("/rules", body),
  updateRule: (id: number, body: RuleUpdate) => patch<RuleOut>(`/rules/${id}`, body),
  terms: () => get<TermOut[]>("/terms"),
  createTerm: (body: TermCreate) => post<TermOut>("/terms", body),
  updateTerm: (id: number, body: TermUpdate) => patch<TermOut>(`/terms/${id}`, body),
  facts: () => get<FactOut[]>("/facts"),
  createFact: (body: FactCreate) => post<FactOut>("/facts", body),
  updateFact: (id: number, body: FactUpdate) => patch<FactOut>(`/facts/${id}`, body),
  /** @deprecated the server now answers with ImportJobOut; use startImport. */
  importPdf(file: File | Blob): Promise<ImportPdfOut> {
    const form = new FormData();
    form.append("file", file);
    return request<ImportPdfOut>("POST", "/brand/import-pdf", { form });
  },
  /** @deprecated the server has no /brand/import-pdf/confirm; use confirmImport. */
  importPdfConfirm: (body: ImportPdfConfirmIn) => post<RuleOut[]>("/brand/import-pdf/confirm", body),
  /** Admin. 202 + status "running"; 501 when FEATURE_PDF_IMPORT=0, 503 without the LLM, 413/415/422 bad file. */
  startImport(file: File | Blob): Promise<ImportJobOut> {
    const form = new FormData();
    form.append("file", file);
    return request<ImportJobOut>("POST", "/brand/import-pdf", { form });
  },
  importJob: (importId: string) => get<ImportJobOut>(`/brand/import-pdf/${importId}`),
  confirmImport: (importId: string, body: ImportConfirmIn) =>
    post<ImportConfirmOut>(`/brand/import-pdf/${importId}/confirm`, body),

  // content
  createItem(input: ItemCreateIn): Promise<ItemCreated> {
    const form = new FormData();
    form.append("title", input.title);
    form.append("channel", input.channel);
    form.append("confidential", String(Boolean(input.confidential)));
    if (input.text !== undefined) form.append("text", input.text);
    if (input.file) form.append("file", input.file);
    if (input.caption !== undefined) form.append("caption", input.caption);
    if (input.alt_text !== undefined) form.append("alt_text", input.alt_text);
    return request<ItemCreated>("POST", "/items", { form });
  },
  items: (q?: { state?: ItemState; mine?: boolean; key?: string }) => get<ItemSummary[]>("/items", q),
  item: (id: number) => get<ItemDetail>(`/items/${id}`),
  version: (id: number) => get<VersionOut>(`/versions/${id}`),
  findings: (versionId: number, q?: { include_pass?: boolean }) =>
    get<FindingOut[]>(`/versions/${versionId}/findings`, q),
  acceptFinding: (id: number) => post<FindingOut>(`/findings/${id}/accept`),
  editFinding: (id: number, body: FindingEditIn) => post<FindingOut>(`/findings/${id}/edit`, body),
  dismissFinding: (id: number, body: FindingDismissIn) => post<FindingOut>(`/findings/${id}/dismiss`, body),
  resubmit: (itemId: number) => post<ResubmitOut>(`/items/${itemId}/resubmit`),

  // workflow
  submit: (itemId: number) => post<ItemDetail>(`/items/${itemId}/submit`),
  approve: (itemId: number) => post<ItemDetail>(`/items/${itemId}/approve`),
  requestChanges: (itemId: number, body: NoteIn) => post<ItemDetail>(`/items/${itemId}/request-changes`, body),
  reject: (itemId: number, body: NoteIn) => post<ItemDetail>(`/items/${itemId}/reject`, body),
  override: (findingId: number, body: OverrideIn) => post<FindingOut>(`/findings/${findingId}/override`, body),
  comments: (itemId: number) => get<CommentOut[]>(`/items/${itemId}/comments`),
  addComment: (itemId: number, body: CommentIn) => post<CommentOut>(`/items/${itemId}/comments`, body),
  history: (itemId: number) => get<AuditEventOut[]>(`/items/${itemId}/history`),
  aiUsage: (itemId: number) => get<AIUsageOut>(`/items/${itemId}/ai-usage`),

  // audience (P1)
  startAudience: (versionId: number) => post<AudienceOut>(`/versions/${versionId}/audience`),
  audience: (versionId: number, q?: { compare_to?: number }) => get<AudienceOut>(`/versions/${versionId}/audience`, q),
  /** SSE URL: events `progress`, then `done` | `error`; each carries AudienceOut JSON. */
  audienceEventsUrl: (versionId: number) => withToken(`${BASE}/versions/${versionId}/audience/events`),

  // insights / eval
  insights: () => get<InsightsOut>("/insights"),
  evalLatest: () => get<EvalLatestOut>("/eval/latest"),
  evalRun: () => post<EvalRunOut>("/eval/run"),
};

// ================================================================== feature detection
/** Server endpoints added for workspaces / brand setup. Older servers lack them. */
export interface ServerFeatures {
  workspaces: boolean; // POST /workspaces + GET /workspace
  brandEdit: boolean; // PATCH /brand
  palette: boolean; // PUT /brand/palette
  logo: boolean; // POST /brand/logo
  factsImport: boolean; // POST /facts/import
}
const ALL_ON: ServerFeatures = { workspaces: true, brandEdit: true, palette: true, logo: true, factsImport: true };
let featuresP: Promise<ServerFeatures> | null = null;

/**
 * Which newer endpoints this server has. Read from the tiny GET /api/features (never blocks first
 * render: callers await it only when they need it); older servers without it fall back to their
 * OpenAPI document. If neither can be read we assume everything is there and each call still
 * handles 404 itself.
 */
export function serverFeatures(): Promise<ServerFeatures> {
  featuresP ??= (async () => {
    try {
      const res = await fetch(`${BASE}/features`, { credentials: "include" });
      if (res.ok) {
        const f = (await res.json()) as { workspaces: boolean; brand_edit: boolean; palette: boolean; logo: boolean; facts_import: boolean };
        return { workspaces: f.workspaces, brandEdit: f.brand_edit, palette: f.palette, logo: f.logo, factsImport: f.facts_import };
      }
      if (res.status !== 404) { featuresP = null; return ALL_ON; } // starting up: ask again next time
      const doc = await fetch(`${BASE}/openapi.json`, { credentials: "include" });
      if (!doc.ok) return ALL_ON;
      const paths = ((await doc.json()) as { paths?: Record<string, Record<string, unknown>> }).paths ?? {};
      const has = (path: string, method: string) => !!paths[`${BASE}${path}`]?.[method];
      return {
        workspaces: has("/workspaces", "post") && has("/workspace", "get"),
        brandEdit: has("/brand", "patch"),
        palette: has("/brand/palette", "put"),
        logo: has("/brand/logo", "post"),
        factsImport: has("/facts/import", "post"),
      };
    } catch {
      featuresP = null;
      return ALL_ON;
    }
  })();
  return featuresP;
}

/** Append the session token to a same-origin URL (for <img src>, EventSource, downloads) when cookies are blocked. */
export function withToken(url: string): string {
  const params: string[] = [];
  const token = getToken();
  if (token) params.push(`token=${encodeURIComponent(token)}`);
  const ws = getWorkspaceToken();
  if (ws) params.push(`ws=${encodeURIComponent(ws)}`);
  if (!params.length) return url;
  return `${url}${url.includes("?") ? "&" : "?"}${params.join("&")}`;
}

// ================================================================== SSE
export interface CheckStreamHandlers {
  onEvent?: (ev: CheckEvent) => void; // every event, including those carrying a finding
  onFinding?: (finding: FindingOut, ev: CheckEvent) => void;
  onDone?: (ev: CheckEvent) => void;
  onError?: (err: ApiError) => void; // stage "error" or a broken connection
}

const STAGES: CheckStage[] = ["queued", "units", "tier0", "jev", "claims", "explain", "selfcheck", "done", "error"];

/**
 * Subscribe to live check progress for a version. Returns an unsubscribe function.
 * The server replays stored findings first, then streams live events, and closes after done/error.
 */
export function streamCheck(versionId: number, h: CheckStreamHandlers): () => void {
  const es = new EventSource(withToken(`${BASE}/versions/${versionId}/events`), { withCredentials: true });
  let finished = false;
  const close = () => { finished = true; es.close(); };

  const handle = (msg: MessageEvent<string>) => {
    let ev: CheckEvent;
    try { ev = JSON.parse(msg.data) as CheckEvent; } catch { return; }
    h.onEvent?.(ev);
    if (ev.finding) h.onFinding?.(ev.finding, ev);
    if (ev.stage === "done") { close(); h.onDone?.(ev); }
    else if (ev.stage === "error") {
      close();
      h.onError?.(new ApiError(500, ev.message || "The check failed. Please try again.", ev));
    }
  };
  for (const s of STAGES) es.addEventListener(s, handle as EventListener);
  es.onmessage = handle; // events without a name

  es.onerror = () => {
    if (finished) return;
    // EventSource auto-reconnects on transient errors; give up only if the browser closed it.
    if (es.readyState === EventSource.CLOSED) {
      finished = true;
      h.onError?.(new ApiError(0, "Lost the live connection. Refresh to see the latest results."));
    }
  };
  return close;
}
