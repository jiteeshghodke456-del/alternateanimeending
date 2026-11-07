/**
 * Round 3: unclear-rule detection (backend/app/routers/clarity.py). Kept in its own module so the
 * shared api.ts stays untouched; it uses api.ts's exported `request` (same auth + workspace headers).
 */
import { request, type IsoDateTime } from "./api";

export type ClarityBand = "clear" | "could_be_clearer" | "unclear";

export interface ClarityReasons {
  vague_terms: string[];
  disagreement: number | null; // 0-1, Jev self-disagreement across rewordings (null = not measured)
  dismiss_rate: number | null;
  override_rate: number | null;
  human_share: number | null;
  findings: number;
  notes: string[]; // plain-language reasons, most important first
}

export interface ProbeAnswer {
  text: string;
  expect: "follows" | "breaks" | "borderline";
  p: number[]; // Jev's probability per wording (original first)
  spread: number;
  flip: boolean;
  mismatch: boolean | null;
}

export interface DisagreementSignal {
  score: number;
  mean_spread: number;
  flip_rate: number;
  unsettled_rate: number;
  mismatch_rate: number | null;
  probes: number;
  wordings: number;
  failed_calls: number;
  per_probe: ProbeAnswer[];
}

export interface RuleRewrite {
  statement: string;
  example_follows: string;
  example_breaks: string;
  what_changed: string;
  for_version: number;
  score_before: number;
  score_after: number;
  band_after: ClarityBand;
  disagreement_before: number | null;
  disagreement_after: number | null;
  spread_before: number | null;
  spread_after: number | null;
  mismatch_before: number | null;
  mismatch_after: number | null;
  vague_terms_after: string[];
  intent_changed: number | null; // Jev: high = the rewrite may ask for something else
  intent_warning: boolean;
  created_at: IsoDateTime;
}

export interface RuleClarity {
  rule_id: number;
  code: string;
  statement: string;
  category: string;
  version: number;
  enabled: boolean;
  score: number; // 0-100, higher = clearer
  band: ClarityBand;
  band_label: string;
  reasons: ClarityReasons;
  signals: {
    vague: { score: number; hits: { term: string; kind: string }[]; measurable: boolean; has_example: boolean; short: boolean };
    disagreement: DisagreementSignal | null;
    people: { score: number | null; findings: number; dismissed: number; overridden: number; human: number } | null;
    weights: Record<string, number>;
    bands: { clear_at: number; unclear_below: number };
  };
  measured: boolean;
  partial: boolean;
  stale: boolean;
  computed_at: IsoDateTime | null;
  paraphrases: string[];
  paraphrase_source: "" | "llm" | "template";
  probe_source: "" | "llm" | "examples";
  rewrite: RuleRewrite | null;
  history: { version: number; statement: string; score: number; band: ClarityBand; replaced_at: IsoDateTime; what_changed?: string }[];
  error: string | null;
}

export interface ClarityRun {
  status: "idle" | "running" | "done" | "error";
  done: number;
  total: number;
  failed: number;
  cost_usd: number;
  elapsed_ms: number;
  error: string | null;
}

export const clarityApi = {
  list: () => request<RuleClarity[]>("GET", "/rules/clarity"),
  runStatus: () => request<ClarityRun>("GET", "/rules/clarity/run"),
  runAll: () => request<ClarityRun>("POST", "/rules/clarity/run", { json: {} }),
  scoreOne: (ruleId: number) => request<RuleClarity>("POST", `/rules/${ruleId}/clarity`, { json: {} }),
  suggest: (ruleId: number) => request<RuleClarity>("POST", `/rules/${ruleId}/rewrite`, { json: {} }),
  apply: (ruleId: number) => request<RuleClarity>("POST", `/rules/${ruleId}/rewrite/apply`, { json: {} }),
  dismiss: (ruleId: number) => request<RuleClarity>("POST", `/rules/${ruleId}/rewrite/dismiss`, { json: {} }),
};

export const CLARITY_BAND_LABEL: Record<ClarityBand, string> = {
  clear: "Clear", could_be_clearer: "Could be clearer", unclear: "Unclear",
};
