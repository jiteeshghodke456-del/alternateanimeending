import { Info } from "lucide-react";
import { useSession } from "@/lib/session";

/** Subtle glass notice when live AI is off on this server. Honest about what actually runs. */
export function HealthBanner() {
  const { health } = useSession();
  if (!health) return null;
  const notes: string[] = [];
  if (!health.jev_configured) notes.push("Live AI judging is off on this server; only rule-based checks run.");
  if (!health.llm_configured) notes.push("The writing assistant is off, so explanations use templates and rewrites may be missing.");
  if (notes.length === 0) return null;
  return (
    <div role="status" className="glass mx-auto mb-5 flex max-w-[1200px] items-start gap-2.5 !rounded-2xl px-4 py-3 text-footnote text-foreground/85">
      <Info className="mt-0.5 size-4 shrink-0 text-[var(--accent-text)]" strokeWidth={1.75} aria-hidden />
      <div className="space-y-0.5">{notes.map((n) => <p key={n}>{n}</p>)}</div>
    </div>
  );
}
