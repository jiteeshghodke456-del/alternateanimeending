import { STATE_LABEL } from "@/lib/bands";
import { cn } from "@/lib/utils";

const STYLE: Record<string, string> = {
  approved: "bg-pass/14 text-pass-text",
  rejected: "bg-flag/12 text-flag-text",
  changes_requested: "bg-human/15 text-human-text",
  in_review: "bg-[#0A84FF]/12 text-[var(--accent-text)]",
  findings_ready: "bg-[#0A84FF]/12 text-[var(--accent-text)]",
};
export function StateBadge({ state }: { state: string }) {
  return <span className={cn("inline-flex rounded-full bg-foreground/8 px-2.5 py-1 text-caption font-medium", STYLE[state])}>{STATE_LABEL[state] ?? state}</span>;
}
