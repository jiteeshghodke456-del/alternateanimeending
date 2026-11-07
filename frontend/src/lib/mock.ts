/**
 * TEMPORARY mock data for the component gallery and placeholder pages.
 * Local types here mirror BUILD.md section 5; once `lib/api.ts` is the source of truth,
 * the integration agent should replace these with imports from "./api" and delete this file
 * (or keep only the seed arrays for the gallery).
 */
export type Band = "flag" | "human" | "pass";
export type Severity = "blocking" | "major" | "minor";
export type Role = "Brand Admin" | "Creator" | "Reviewer" | "Approver";

export interface DemoUser {
  id: string;
  name: string;
  role: Role;
  blurb: string;
}

export interface MockFinding {
  id: string;
  rule_id: string;
  category: string;
  severity: Severity;
  band: Band;
  probability: number;
  page: number;
  quote: string;
  explanation: string;
  original: string;
  fix: string | null;
  fix_verified: boolean;
  start: number;
  end: number;
  status: "open" | "accepted" | "edited" | "dismissed";
}

export const DEMO_USERS: DemoUser[] = [
  { id: "asha", name: "Asha", role: "Brand Admin", blurb: "Owns the guidelines, facts and palette." },
  { id: "rohan", name: "Rohan", role: "Creator", blurb: "Writes the posts and sends them for review." },
  { id: "meera", name: "Meera", role: "Reviewer", blurb: "Reads every flagged line before it moves on." },
  { id: "kabir", name: "Kabir", role: "Approver", blurb: "Gives the final yes. Never on his own work." },
];

export const DEMO_TEXT =
  "Meet the new Saffron Brew Blend, the #1 coffee in India! Our beans are the cheapest you will find, and we guarantee you will feel more awake in 5 minutes. Grab yours today at 40% off. Don't miss out, losers who skip this deal will regret it.";

const t = DEMO_TEXT;
const span = (s: string): [number, number] => [t.indexOf(s), t.indexOf(s) + s.length];

export const MOCK_FINDINGS: MockFinding[] = [
  {
    id: "f1", rule_id: "R-CLAIM-02", category: "Claims", severity: "blocking", band: "flag", probability: 0.93, page: 4,
    quote: "Never state a ranking such as #1 or best unless an approved fact supports it.",
    explanation: "“#1 coffee in India” is a ranking claim, and no approved fact backs it up. Use a claim we can prove, or describe the taste instead.",
    original: "the #1 coffee in India", fix: "a coffee people in India love", fix_verified: true,
    start: span("#1 coffee in India")[0], end: span("#1 coffee in India")[1], status: "open",
  },
  {
    id: "f2", rule_id: "R-WORD-01", category: "Language", severity: "major", band: "flag", probability: 0.88, page: 6,
    quote: "We say “affordable” or “great value”. We never say “cheap” or “cheapest”.",
    explanation: "“The cheapest” is on the banned word list and reads as low quality, which is the opposite of our premium position.",
    original: "the cheapest you will find", fix: "great value for what you get", fix_verified: true,
    start: span("the cheapest you will find")[0], end: span("the cheapest you will find")[1], status: "open",
  },
  {
    id: "f3", rule_id: "R-FACT-07", category: "Claims", severity: "major", band: "human", probability: 0.52, page: 5,
    quote: "Promotions must match the current offer sheet.",
    explanation: "The best-matching offer (30% off) expired in October. A person should confirm the 40% figure before this goes live.",
    original: "Grab yours today at 40% off.", fix: "Grab yours today and enjoy our current offer.", fix_verified: false,
    start: span("Grab yours today at 40% off.")[0], end: span("Grab yours today at 40% off.")[1], status: "open",
  },
  {
    id: "f4", rule_id: "R-TONE-03", category: "Tone", severity: "minor", band: "flag", probability: 0.81, page: 8,
    quote: "Warm, never mocking. We do not insult people who choose not to buy.",
    explanation: "Calling people “losers” is mocking. Our voice is warm and inviting, even when we create urgency.",
    original: "Don't miss out, losers who skip this deal will regret it.", fix: "Don't miss out, this deal is worth a cup.", fix_verified: true,
    start: span("Don't miss out, losers who skip this deal will regret it.")[0], end: span("Don't miss out, losers who skip this deal will regret it.")[1], status: "open",
  },
];

export const MOCK_RULE_SCAN = [
  { id: "R-CLAIM-02", label: "No unsupported rankings", p: 0.93 },
  { id: "R-WORD-01", label: "Banned words", p: 0.88 },
  { id: "R-TONE-03", label: "Never mocking", p: 0.81 },
  { id: "R-FACT-07", label: "Offers match the sheet", p: 0.52 },
  { id: "R-NAME-01", label: "Current product names", p: 0.41 },
  { id: "R-CLAIM-05", label: "Health claims need a source", p: 0.22 },
  { id: "R-TONE-01", label: "Warm and plain", p: 0.14 },
  { id: "R-CAP-02", label: "Product name capitalisation", p: 0.09 },
  { id: "R-LEGAL-01", label: "Disclaimer when needed", p: 0.04 },
];

export const MOCK_KPIS = {
  itemsChecked: 128,
  findings: 342,
  fixesAcceptedPct: 74,
  avgCheckSeconds: 6.4,
  judgments: 5210,
  costUsd: 0.2184,
};

export const MOCK_BY_CATEGORY = [
  { name: "Claims", count: 118 }, { name: "Language", count: 84 }, { name: "Tone", count: 67 },
  { name: "Visual", count: 41 }, { name: "Legal", count: 32 },
];
export const MOCK_TOP_RULES = [
  { name: "R-CLAIM-02", count: 46 }, { name: "R-WORD-01", count: 39 }, { name: "R-TONE-03", count: 31 },
  { name: "R-NAME-01", count: 24 }, { name: "R-FACT-07", count: 19 }, { name: "R-VIS-02", count: 14 },
];
export const MOCK_BY_CHANNEL = [
  { name: "Instagram", count: 140 }, { name: "Email", count: 92 }, { name: "Website", count: 68 }, { name: "LinkedIn", count: 42 },
];

export interface MockItem {
  id: string; title: string; channel: string; state: string; creator: string; findings: number; confidential?: boolean; updated: string;
}
export const MOCK_ITEMS: MockItem[] = [
  { id: "demo-1", title: "Saffron Brew Blend launch post", channel: "Instagram", state: "findings_ready", creator: "Rohan", findings: 4, updated: "2 min ago" },
  { id: "demo-2", title: "Winter menu newsletter", channel: "Email", state: "in_review", creator: "Rohan", findings: 1, updated: "1 h ago" },
  { id: "demo-3", title: "Wholesale price update (confidential)", channel: "Email", state: "draft", creator: "Rohan", findings: 0, confidential: true, updated: "Yesterday" },
  { id: "demo-4", title: "Founder story, LinkedIn", channel: "LinkedIn", state: "approved", creator: "Kabir", findings: 0, updated: "2 days ago" },
  { id: "demo-5", title: "Cold brew banner ad", channel: "Website", state: "changes_requested", creator: "Rohan", findings: 3, updated: "3 days ago" },
];

export const MOCK_AI_USAGE = {
  confidential: false,
  piiMasked: 2,
  groups: [
    { provider: "Jev (TypeSafe AI)", calls: [
      { purpose: "Rule judgments", count: 6, tokens: 4210, costUsd: 0.0002 },
      { purpose: "Claim support checks", count: 3, tokens: 1180, costUsd: 0.00005 },
    ] },
    { provider: "Writer model (Groq, open-weight)", calls: [
      { purpose: "Explanations and fixes", count: 4, tokens: 2890, costUsd: 0.0009 },
    ] },
  ],
};

export const MOCK_RULES = [
  { id: "R-CLAIM-02", category: "Claims", severity: "blocking" as Severity, statement: "Never claim a ranking (#1, best) unless an approved fact supports it.", page: 4, version: 2, enabled: true },
  { id: "R-WORD-01", category: "Language", severity: "major" as Severity, statement: "Say “affordable” or “great value”, never “cheap”.", page: 6, version: 1, enabled: true },
  { id: "R-TONE-03", category: "Tone", severity: "minor" as Severity, statement: "Warm, never mocking. Do not insult people who skip an offer.", page: 8, version: 1, enabled: true },
  { id: "R-FACT-07", category: "Claims", severity: "major" as Severity, statement: "Promotions must match the current offer sheet.", page: 5, version: 3, enabled: true },
  { id: "R-NAME-01", category: "Language", severity: "major" as Severity, statement: "Use “Saffron Brew Blend”. The old name “Morning Gold” is retired.", page: 3, version: 1, enabled: false },
];
