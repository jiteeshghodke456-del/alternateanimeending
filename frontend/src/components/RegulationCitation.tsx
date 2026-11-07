import { ExternalLink, Landmark } from "lucide-react";
import type { FindingOut, RuleRef } from "@/lib/api";

/** Rule packs: the regulations a finding rests on — its own pack rule, or regulations linked to its brand rule. */
export function regulationsOf(f: FindingOut): RuleRef[] {
  const out: RuleRef[] = [];
  if (f.rule?.citation) out.push(f.rule);
  for (const r of f.regulations ?? []) if (r.citation && !out.some((x) => x.code === r.code)) out.push(r);
  return out;
}

/** Small "Regulation" chip for the finding header (icon + label, never colour alone). */
export function RegulationChip({ f }: { f: FindingOut }) {
  if (!regulationsOf(f).length) return null;
  return (
    <span data-testid="regulation-chip" className="inline-flex items-center gap-1 rounded-full bg-[#0A84FF]/12 px-2.5 py-1 text-caption font-medium text-[var(--accent-text)]">
      <Landmark className="size-3.5" strokeWidth={1.5} aria-hidden /> {regulationsOf(f)[0]?.pack_id === "platform" ? "Platform spec" : "Regulation"}
    </span>
  );
}

/** The citation(s): document, clause, a short quoted phrase, a link to the official source and the date it was read. */
export function RegulationCitations({ f }: { f: FindingOut }) {
  const regs = regulationsOf(f);
  if (!regs.length) return null;
  const linked = !f.rule?.citation; // a brand rule backed by a regulation
  return (
    <div className="mt-3 space-y-2" data-testid="regulation-citations">
      {regs.map((r) => {
        const c = r.citation!;
        return (
          <blockquote key={r.code} className="border-l-2 border-[#0A84FF]/50 pl-3">
            <p className="text-caption font-medium text-[var(--accent-text)]">
              {linked ? "Also breaks: " : ""}{c.document}, {c.clause}
            </p>
            {c.quote && <p className="text-footnote italic text-foreground/85">&ldquo;{c.quote}&rdquo;</p>}
            <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-caption text-muted-foreground">
              <a
                href={c.url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="inline-flex items-center gap-0.5 font-medium text-[var(--accent-text)] underline underline-offset-2"
              >
                Official source<ExternalLink className="size-3" strokeWidth={1.5} aria-hidden />
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
              <span>· read {c.retrieved} · {r.code} v{r.version}</span>
            </p>
            {c.also.length > 0 && (
              <p className="mt-0.5 text-caption text-muted-foreground">
                See also:{" "}
                {c.also.map((a, i) => (
                  <span key={a.clause}>
                    {i > 0 && "; "}
                    <a href={a.url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()} className="underline underline-offset-2">{a.clause}</a>
                  </span>
                ))}
              </p>
            )}
          </blockquote>
        );
      })}
    </div>
  );
}
