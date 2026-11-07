import { useState } from "react";
import { Link } from "react-router-dom";
import { History, Loader2, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { SEVERITY_LABEL, SEVERITY_STYLE } from "@/lib/bands";
import { PRECEDENT_REASON_LABEL, precedentsApi, type PrecedentOut } from "@/lib/api";
import { reportError, useAsync } from "@/lib/hooks";
import { shortDate } from "@/components/Precedent";
import { cn } from "@/lib/utils";

/** Brand Kit → Precedents: every human decision later checks reuse ("Seen before"), with how often each
 *  was used. Admins can revoke one; future checks then ignore it. */
export function PrecedentsPanel({ admin }: { admin: boolean }) {
  const q = useAsync(() => precedentsApi.list(), []);
  const [busy, setBusy] = useState<number | null>(null);

  async function revoke(p: PrecedentOut) {
    setBusy(p.id);
    try {
      await precedentsApi.revoke(p.id);
      toast.success(`Revoked. Future checks won't use this ${p.decision === "overridden" ? "override" : "dismissal"} of ${p.rule_code}.`);
      q.reload();
    } catch (e) { reportError(e); } finally { setBusy(null); }
  }

  if (q.error) return <ErrorState title="Can't load precedents" message={q.error.message} onRetry={q.reload} />;
  if (!q.data) return <div className="space-y-3"><Skeleton className="h-20" /><Skeleton className="h-20" /></div>;
  const rows = q.data.precedents;
  return (
    <section aria-labelledby="precedents-title" data-testid="precedents-panel" className="space-y-4">
      <div className="glass-strong p-5">
        <h2 id="precedents-title" className="flex items-center gap-2 text-headline font-semibold tracking-tight">
          <History className="size-5" strokeWidth={1.5} aria-hidden /> Precedents
        </h2>
        <p className="mt-1 text-footnote text-muted-foreground">
          When someone dismisses a finding with a reason, or overrides a blocking one, the decision is kept here. A later finding of
          the same rule on the same wording shows &ldquo;Seen before&rdquo; and drops one band. Blocking rules never drop below
          &ldquo;Needs a human look&rdquo;, nothing is dismissed automatically, and decisions older than {q.data.max_age_days} days are ignored.
        </p>
      </div>
      {rows.length === 0 ? (
        <EmptyState icon={History} title="No precedents yet" description="Dismiss a finding with a reason on any item, and similar findings will show it next time." />
      ) : (
        <ul className="space-y-3">
          {rows.map((p) => (
            <li key={p.id} data-testid="precedent-row" className={cn("glass-strong p-4", !p.active && "opacity-75")}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1 space-y-1">
                  <p className="flex flex-wrap items-center gap-2 text-footnote">
                    <span className="font-mono text-caption">{p.rule_code}</span>
                    <span className={cn("rounded-full px-2 py-0.5 text-caption font-medium", SEVERITY_STYLE[p.severity])}>{SEVERITY_LABEL[p.severity]}</span>
                    <span className="rounded-full bg-foreground/8 px-2 py-0.5 text-caption">{p.decision === "overridden" ? "Overridden" : "Dismissed"}</span>
                    {!p.active ? <span className="rounded-full bg-foreground/8 px-2 py-0.5 text-caption">Revoked</span>
                      : !p.applies ? <span className="rounded-full bg-human/15 px-2 py-0.5 text-caption text-human-text">Not applied: {p.excluded_why}</span>
                      : null}
                    {p.seeded && <span className="rounded-full bg-foreground/8 px-2 py-0.5 text-caption">Demo data</span>}
                  </p>
                  <p className="break-words text-body">&ldquo;{p.quote}&rdquo;</p>
                  {p.context && p.context !== p.quote && <p className="break-words text-caption text-muted-foreground">in &ldquo;{p.context}&rdquo;</p>}
                  <p className="text-footnote">
                    <span className="font-medium">{PRECEDENT_REASON_LABEL[p.reason] ?? p.reason}</span>
                    {p.note && <span className="text-muted-foreground"> — &ldquo;{p.note}&rdquo;</span>}
                  </p>
                  <p className="text-caption text-muted-foreground">
                    {p.by || "Someone"} on {shortDate(p.at)}
                    {p.item_id != null && <> · <Link to={`/items/${p.item_id}`} className="font-medium text-[var(--accent-text)] underline-offset-2 hover:underline">{p.item_title || "Open item"}</Link></>}
                    {" · "}Used {p.uses} time{p.uses === 1 ? "" : "s"} since
                    {p.rule_current_version != null && p.rule_current_version !== p.rule_version && <> · rule rewritten since (v{p.rule_version} → v{p.rule_current_version})</>}
                  </p>
                </div>
                {admin && p.active && (
                  <Button variant="secondary" className="min-h-11 rounded-full" disabled={busy === p.id} onClick={() => revoke(p)}
                    aria-label={`Revoke the precedent for ${p.rule_code} on “${p.quote}”`}>
                    {busy === p.id ? <Loader2 className="animate-spin" strokeWidth={1.75} /> : <Undo2 strokeWidth={1.75} />} Revoke
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
