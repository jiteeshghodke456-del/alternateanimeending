import type { RuleCreate } from "./api";

/**
 * A generic starter set for a brand-new workspace. Brand-neutral on purpose: no product names,
 * no market, no language beyond plain English. Params reuse the generic Tier 0 / claim keys that
 * backend/app/pipeline/tier0.py documents, so they work without any code change.
 */
export const STARTER_RULES: RuleCreate[] = [
  {
    code: "R-START-01", category: "claims", severity: "blocking", check_method: "claim",
    statement: "No superlatives or '#1' claims (best, number one, leading, most trusted) unless an approved fact backs them.",
    params: {
      superlative_patterns: ["#\\s?1\\b", "\\bnumber (one|1)\\b", "\\bno\\.? ?1\\b", "\\bbest\\b", "\\bfinest\\b", "\\b(top|leading)[- ]rated\\b", "\\bmost (loved|trusted|awarded|popular)\\b", "\\bworld'?s (best|finest|top|leading)\\b"],
      needs_supporting_fact: true,
    },
    examples_violating: ["The #1 choice for families."], examples_ok: ["Chosen by 2,000 teams (see our 2026 customer report)."],
  },
  {
    code: "R-START-02", category: "claims", severity: "major", check_method: "claim",
    statement: "Every number, rating, award or price must match a current approved fact.",
    params: { needs_supporting_fact: true, reject_expired_facts: true, fact_match: "keyword+numbers" },
    examples_violating: ["Rated 4.9 by 10,000 customers."], examples_ok: ["Rated 4.6 on the App Store (approved fact F-03)."],
  },
  {
    code: "R-START-03", category: "accessibility", severity: "minor", check_method: "jev",
    statement: "Use plain language a general reader understands on first read; avoid jargon and long, winding sentences.",
    params: {},
    examples_violating: ["Leverage our synergistic, best-of-breed paradigm."], examples_ok: ["Plan your week in five minutes."],
  },
  {
    code: "R-START-04", category: "accessibility", severity: "major", check_method: "tier0",
    statement: "Every image must have alt text that describes what it shows.",
    params: { requires_alt_text: true, min_chars: 10, reject_generic: ["image", "photo", "picture", "banner"] },
    examples_violating: ["alt=\"image\""], examples_ok: ["alt=\"Two people reviewing a printed calendar at a desk\""],
  },
  {
    code: "R-START-05", category: "messaging", severity: "minor", check_method: "jev",
    statement: "Each post carries one clear call to action.",
    params: { max_ctas: 1, min_ctas: 1 },
    examples_violating: ["Sign up, follow us, share this and visit our store today."], examples_ok: ["Start your free trial today."],
  },
  {
    code: "R-START-06", category: "tone", severity: "minor", check_method: "tier0",
    statement: "No ALL CAPS words, and at most one exclamation mark per post.",
    params: { max_exclamations: 1, all_caps_min_len: 4, all_caps_allowlist: ["FAQ", "PDF", "HTML", "NASA", "USA", "GST", "UPI"] },
    examples_violating: ["HUGE news!!!"], examples_ok: ["Big news: our spring update is here!"],
  },
  {
    code: "R-START-07", category: "tone", severity: "major", check_method: "tier0",
    statement: "No pressure tactics or fake urgency such as 'only today', 'last chance', 'hurry' or 'act now'.",
    params: {
      patterns: ["\\bonly today\\b", "\\blast chance\\b", "\\bhurry\\b", "\\bact now\\b", "\\bdon'?t miss out\\b", "\\bnow or never\\b", "\\bwhile (stocks|stock|supplies) last\\b", "!{2,}"],
      case_insensitive: true,
    },
    examples_violating: ["Last chance! Hurry, act now!"], examples_ok: ["The offer runs until 30 June."],
  },
];

/** Downloadable template for POST /api/facts/import. */
export const FACTS_TEMPLATE_CSV = [
  "statement,value,source,valid_until",
  "\"Customers rate the app 4.6 out of 5\",4.6,\"App Store rating page, June 2026\",2026-12-31",
  "\"Used by more than 2,000 teams\",\"2,000\",\"Internal customer count, Q2 2026\",",
  "\"Starter plan price per month\",\"USD 19\",\"Pricing page\",2027-03-31",
].join("\n") + "\n";
