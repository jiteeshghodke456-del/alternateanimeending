/* Claims ledger + fact impact UI pieces (Round 3, improvement 1). Used by pages/Claims, BrandKit (Facts)
 * and Dashboard. Colour is never the only signal: every status has an icon and a label. */
import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, CalendarClock, CalendarX2, CheckCircle2, CircleHelp, Loader2, RefreshCw, Send, XOctagon, type LucideIcon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ErrorState } from "@/components/ErrorState";
import { StateBadge } from "@/components/StateBadge";
import { claimsApi, type FactImpactOut, type ImpactReason, type LedgerClaim, type LedgerFact, type LedgerStatus, type SendBackOut } from "@/lib/api";
import { STATE_LABEL } from "@/lib/bands";
import { formatDate, reportError, useAsync } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

export const CLAIM_STATUS: Record<LedgerStatus, { label: string; icon: LucideIcon; cls: string; help: string }> = {
  supported: { label: "Supported", icon: CheckCircle2, cls: "bg-pass/14 text-pass-text", help: "A current approved fact backs this claim." },
  outdated: { label: "Outdated", icon: CalendarX2, cls: "bg-flag/12 text-flag-text", help: "It relies on a fact that has expired." },
  contradicted: { label: "Contradicted", icon: XOctagon, cls: "bg-flag/12 text-flag-text", help: "A current fact about the same thing gives a different figure." },
  unsupported: { label: "Unsupported", icon: CircleHelp, cls: "bg-human/15 text-human-text", help: "No approved fact backs this claim." },
};
export const STATUS_ORDER: LedgerStatus[] = ["outdated", "contradicted", "unsupported", "supported"];

export function ClaimStatusBadge({ status }: { status: LedgerStatus }) {
  const m = CLAIM_STATUS[status];
  const Icon = m.icon;
  return (
    <span title={m.help} className={cn("inline-flex shrink-0 items-center gap-1 rounded-full px-2.5 py-0.5 text-caption font-medium", m.cls)}>
      <Icon className="size-3.5" strokeWidth={1.75} aria-hidden />{m.label}
    </span>
  );
}

/** "Expired", "Expires in 3 days", "Valid until 31 Mar 2027" or "No expiry". */
export function ExpiryChip({ fact, soonDays = 14 }: { fact: Pick<LedgerFact, "valid_until" | "days_left" | "expired">; soonDays?: number }) {
  const d = fact.days_left;
  if (fact.valid_until == null || d == null) return <span className="rounded-full bg-foreground/8 px-2.5 py-0.5 text-caption font-medium text-muted-foreground">No expiry</span>;
  if (fact.expired || d < 0) {
    return <span className="inline-flex items-center gap-1 rounded-full bg-flag/12 px-2.5 py-0.5 text-caption font-medium text-flag-text"><CalendarX2 className="size-3.5" strokeWidth={1.75} aria-hidden />Expired {formatDate(fact.valid_until)}</span>;
  }
  if (d <= soonDays) {
    const when = d === 0 ? "today" : d === 1 ? "in 1 day" : `in ${d} days`;
    return <span className="inline-flex items-center gap-1 rounded-full bg-human/15 px-2.5 py-0.5 text-caption font-medium text-human-text"><CalendarClock className="size-3.5" strokeWidth={1.75} aria-hidden />Expires {when}</span>;
  }
  return <span className="rounded-full bg-foreground/8 px-2.5 py-0.5 text-caption font-medium text-muted-foreground">Valid until {formatDate(fact.valid_until)}</span>;
}

export function evidenceText(c: LedgerClaim): string {
  if (c.support_p != null) return `Jev support ${Math.round(c.support_p * 100)}%`;
  if (c.via === "numbers") return "Matched by its numbers";
  if (c.via === "no_facts") return "No similar fact";
  if (c.via === "no_jev") return "Not checked by Jev";
  return "";
}

const REASON: Record<ImpactReason, (f: LedgerFact) => string> = {
  expired: (f) => `${f.code} expired on ${formatDate(f.valid_until)}. Content that relies on it is now making an outdated claim.`,
  changed: (f) => `${f.code} was edited after this content was checked. Its claims may no longer match the fact.`,
  expiring: (f) => `${f.code} expires ${f.days_left === 0 ? "today" : `in ${f.days_left} day${f.days_left === 1 ? "" : "s"}`}. Plan updates before it does.`,
  current: (f) => `${f.code} is current. You can still send the content that relies on it back for a fresh review.`,
};

const ACTION_LABEL = { changes_requested: "Back to the creator", recheck: "Re-check", none: "No change" } as const;

/** Lists every item relying on a fact; a Brand Admin can send them back for review (+ re-check). */
export function FactImpactDialog({ factId, open, onOpenChange, onDone }: {
  factId: number | null; open: boolean; onOpenChange: (o: boolean) => void; onDone?: () => void;
}) {
  const { user } = useSession();
  const admin = user?.role === "admin";
  const q = useAsync<FactImpactOut | null>(() => (open && factId != null ? claimsApi.impact(factId) : Promise.resolve(null)), [open, factId]);
  const [recheck, setRecheck] = useState(true);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SendBackOut | null>(null);
  const [lastKey, setLastKey] = useState("");
  const key = `${open}:${factId}`;
  if (key !== lastKey) { setLastKey(key); setResult(null); setRecheck(true); }

  const d = q.data;
  const actionable = d ? d.items.filter((i) => i.action !== "none").length : 0;

  async function sendBack() {
    if (factId == null) return;
    setBusy(true);
    try {
      const out = await claimsApi.sendBack(factId, { recheck });
      setResult(out);
      toast.success(out.moved ? `${out.moved} item${out.moved === 1 ? "" : "s"} sent back for review` : "Affected items are being re-checked");
      q.reload();
      onDone?.();
    } catch (e) { reportError(e); } finally { setBusy(false); }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto rounded-3xl border-[var(--glass-border)] bg-[var(--glass-strong-bg)] backdrop-blur-2xl sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="text-title3">Fact impact{d ? `: ${d.fact.code}` : ""}</DialogTitle>
          <DialogDescription>Every draft and approved item whose latest version relies on this fact.</DialogDescription>
        </DialogHeader>
        {q.error ? <ErrorState title="Can't load the impact" message={q.error.message} onRetry={q.reload} className="!py-8" />
          : !d ? <div className="space-y-3"><Skeleton className="h-16" /><Skeleton className="h-24" /></div> : (
          <div className="space-y-4">
            <div className="rounded-2xl border border-border bg-background/60 p-4">
              <div className="flex flex-wrap items-center gap-2"><span className="font-mono text-footnote font-semibold">{d.fact.code}</span><ExpiryChip fact={d.fact} /></div>
              <p className="mt-1 text-body">{d.fact.statement}</p>
              <p className="mt-0.5 text-caption text-muted-foreground">{d.fact.source || "No source"}{d.fact.value ? ` · ${d.fact.value}` : ""}</p>
              <p className={cn("mt-3 text-footnote", d.reason === "expired" || d.reason === "changed" ? "text-flag-text" : "text-muted-foreground")} role="status">{REASON[d.reason](d.fact)}</p>
            </div>
            <p className="text-footnote font-medium" data-testid="impact-counts">{d.items.length === 0 ? "No content relies on this fact." : `${d.approved} approved · ${d.in_progress} in progress`}</p>
            {d.items.length > 0 && (
              <ul className="divide-y divide-border overflow-hidden rounded-2xl border border-border bg-background/60" data-testid="impact-items">
                {d.items.map((it) => {
                  const res = result?.results.find((r) => r.item_id === it.item_id);
                  return (
                    <li key={it.item_id} className="px-4 py-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Link to={`/items/${it.item_id}`} onClick={() => onOpenChange(false)} className="min-w-0 truncate font-medium text-[var(--accent-text)] underline-offset-2 hover:underline">{it.title}</Link>
                        <StateBadge state={it.state} />
                        <span className="text-caption text-muted-foreground">v{it.version_n} · {it.creator_name}</span>
                      </div>
                      <ul className="mt-1.5 space-y-1">
                        {it.claims.map((c) => (
                          <li key={c.id} className="flex flex-wrap items-start gap-2 text-footnote">
                            <ClaimStatusBadge status={c.status} />
                            <span className="min-w-0 flex-1 basis-48">“{c.text}”</span>
                          </li>
                        ))}
                      </ul>
                      <p className="mt-1.5 text-caption text-muted-foreground">
                        {res ? (res.skipped_reason ? res.skipped_reason : `${STATE_LABEL[res.from_state]} → ${STATE_LABEL[res.to_state]}${res.rechecked ? " · re-check started" : ""}`)
                          : <><span className="font-medium text-foreground">{ACTION_LABEL[it.action]}:</span> {it.action_reason}</>}
                      </p>
                    </li>
                  );
                })}
              </ul>
            )}
            {!admin && d.items.length > 0 && <p className="text-footnote text-muted-foreground">Only a Brand Admin can send these items back for review.</p>}
          </div>
        )}
        <DialogFooter className="flex-col items-stretch gap-3 sm:flex-row sm:items-center sm:justify-between">
          {admin && d && actionable > 0 ? (
            <label className="flex items-center gap-2 text-footnote"><Switch checked={recheck} onCheckedChange={setRecheck} aria-label="Re-check affected items" /> Re-check against current facts</label>
          ) : <span />}
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" className="rounded-full" onClick={() => onOpenChange(false)}>Close</Button>
            {admin && d && actionable > 0 && (
              <Button disabled={busy} onClick={sendBack} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">
                {busy ? <Loader2 className="animate-spin" /> : result ? <RefreshCw strokeWidth={1.75} /> : <Send strokeWidth={1.75} />} {result ? "Send back again" : "Send affected items back for review"}
              </Button>
            )}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Dashboard card: facts expiring within 14 days (and expired ones) that content still relies on. */
export function ExpiringFactsCard() {
  const q = useAsync(() => claimsApi.expiring(), []);
  const [impact, setImpact] = useState<number | null>(null);
  if (q.error || !q.data) return null; // the dashboard has its own error state; this card is extra
  const rows = [...q.data.expired, ...q.data.expiring];
  return (
    <section aria-labelledby="expiring-h" className="glass-strong mb-6 p-5 md:p-6" data-testid="expiring-card">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 id="expiring-h" className="text-headline">Facts expiring soon</h2>
          <p className="mt-0.5 text-footnote text-muted-foreground">Approved facts that expire within {q.data.days} days, or already have, and the content that relies on them.</p>
        </div>
        <Button asChild variant="ghost" className="rounded-full"><Link to="/claims">Claims ledger <ArrowRight strokeWidth={2} /></Link></Button>
      </div>
      {rows.length === 0 ? <p className="mt-3 text-footnote text-muted-foreground">Nothing expiring in the next {q.data.days} days.</p> : (
        <ul className="mt-3 divide-y divide-border">
          {rows.map((r) => (
            <li key={r.fact.id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 py-2.5">
              <span className="font-mono text-footnote font-semibold">{r.fact.code}</span>
              <span className="min-w-0 flex-1 basis-40 truncate text-footnote" title={r.fact.statement}>{r.fact.statement}</span>
              <ExpiryChip fact={r.fact} />
              <button type="button" onClick={() => setImpact(r.fact.id)} className="rounded-full px-2.5 py-1 text-caption font-medium text-[var(--accent-text)] underline-offset-2 hover:underline focus-visible:outline-2">
                {r.items_approved} approved · {r.items_in_progress} in progress
              </button>
            </li>
          ))}
        </ul>
      )}
      <FactImpactDialog factId={impact} open={impact != null} onOpenChange={(o) => { if (!o) setImpact(null); }} onDone={q.reload} />
    </section>
  );
}
