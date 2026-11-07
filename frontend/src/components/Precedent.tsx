import { useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, History } from "lucide-react";
import { PRECEDENT_REASON_LABEL, type FindingOut, type PrecedentRef } from "@/lib/api";
import { BAND } from "@/lib/bands";
import { cn } from "@/lib/utils";

/** "7 Nov" (adds the year when it isn't this year). */
export function shortDate(iso: string): string {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return iso;
  const same = t.getFullYear() === new Date().getFullYear();
  return t.toLocaleDateString(undefined, { day: "numeric", month: "short", ...(same ? {} : { year: "numeric" }) });
}

export function precedentSentence(p: PrecedentRef): string {
  const verb = p.decision === "overridden" ? "overridden" : "dismissed";
  return `Seen before: ${verb} by ${p.by || "a reviewer"} on ${shortDate(p.at)}`;
}

/** Precedent memory: a quiet "Seen before" line on a finding card. Code decided the band; this says why. */
export function PrecedentLine({ finding: f, className }: { finding: FindingOut; className?: string }) {
  const p = f.precedent;
  if (!p) return null;
  const why = p.note || PRECEDENT_REASON_LABEL[p.reason] || p.reason;
  return (
    <div data-testid="precedent-line" className={cn("mt-3 flex items-start gap-2 rounded-xl bg-foreground/5 px-3 py-2 text-footnote", className)}>
      <History className="mt-0.5 size-4 shrink-0 text-muted-foreground" strokeWidth={1.5} aria-hidden />
      <div className="min-w-0 space-y-0.5">
        <p className="break-words">
          <span className="font-medium">{precedentSentence(p)}</span>
          {why && <span className="text-muted-foreground"> — &ldquo;{why}&rdquo;</span>}
          {p.item_id != null && (
            <>
              {" · "}
              <Link to={`/items/${p.item_id}`} onClick={(e) => e.stopPropagation()}
                className="font-medium text-[var(--accent-text)] underline-offset-2 hover:underline">
                {p.item_title ? `Open “${p.item_title}”` : "Open the earlier item"}
              </Link>
            </>
          )}
        </p>
        {f.band_note && <p data-testid="precedent-band-note" className="text-caption text-muted-foreground">{f.band_note}</p>}
        {p.rule_changed && <p className="text-caption text-human-text">The rule has been rewritten since that decision, so check it still holds.</p>}
      </div>
    </div>
  );
}

/** Findings a precedent lowered to "pass": listed for reference, not counted as open issues. */
export function PreviouslyAccepted({ findings, onSelect }: { findings: FindingOut[]; onSelect?: (id: number) => void }) {
  const [open, setOpen] = useState(false);
  const list = findings.filter((f) => f.band === "pass" && f.precedent);
  if (!list.length) return null;
  const Pass = BAND.pass.icon;
  return (
    <section data-testid="previously-accepted" className="glass-strong p-4">
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)}
        className="flex w-full min-h-11 items-center justify-between gap-3 text-left">
        <span className="flex items-center gap-2 text-callout font-semibold">
          <Pass className={cn("size-4", BAND.pass.text)} strokeWidth={1.75} aria-hidden />
          Previously accepted ({list.length})
        </span>
        <ChevronDown className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} strokeWidth={1.75} aria-hidden />
      </button>
      <p className="mt-1 text-caption text-muted-foreground">
        Someone accepted the same wording for the same rule before, so these aren&rsquo;t counted as open issues. They&rsquo;re still listed so nothing is hidden.
      </p>
      {open && (
        <ul className="mt-3 space-y-3">
          {list.map((f) => (
            <li key={f.id} id={`finding-${f.id}`} className="rounded-2xl bg-foreground/[0.04] p-3" onClick={() => onSelect?.(f.id)}>
              <p className="flex flex-wrap items-center gap-x-2 text-footnote">
                <span className="font-mono text-caption">{f.rule_code ?? f.fact_code ?? f.category}</span>
                <span className="break-words">&ldquo;{f.quote}&rdquo;</span>
              </p>
              {f.rule && <p className="mt-0.5 text-caption text-muted-foreground">{f.rule.statement}</p>}
              <PrecedentLine finding={f} className="mt-2" />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
