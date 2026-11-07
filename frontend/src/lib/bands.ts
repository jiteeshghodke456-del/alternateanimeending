import { CheckCircle2, Eye, OctagonAlert, type LucideIcon } from "lucide-react";
import type { Band, Severity } from "./mock";

/** Band thresholds from CLAUDE.md: p >= 0.70 flag, 0.30 to 0.70 human look, < 0.30 pass. */
export function bandOf(p: number): Band {
  return p >= 0.7 ? "flag" : p >= 0.3 ? "human" : "pass";
}

export const BAND: Record<Band, { label: string; short: string; icon: LucideIcon; text: string; bg: string; bar: string; ring: string }> = {
  flag: {
    label: "Flagged", short: "Flag", icon: OctagonAlert,
    text: "text-flag-text", bg: "bg-flag/12", bar: "bg-flag", ring: "ring-flag/35",
  },
  human: {
    label: "Needs a human look", short: "Review", icon: Eye,
    text: "text-human-text", bg: "bg-human/15", bar: "bg-human", ring: "ring-human/40",
  },
  pass: {
    label: "Pass", short: "Pass", icon: CheckCircle2,
    text: "text-pass-text", bg: "bg-pass/14", bar: "bg-pass", ring: "ring-pass/35",
  },
};

export const SEVERITY_LABEL: Record<Severity, string> = { blocking: "Blocking", major: "Major", minor: "Minor" };
export const SEVERITY_STYLE: Record<Severity, string> = {
  blocking: "bg-flag/12 text-flag-text",
  major: "bg-human/15 text-human-text",
  minor: "bg-foreground/8 text-muted-foreground",
};

export const STATE_LABEL: Record<string, string> = {
  draft: "Draft", checking: "Checking", findings_ready: "Findings ready", in_review: "In review",
  changes_requested: "Changes requested", approved: "Approved", rejected: "Rejected",
};
