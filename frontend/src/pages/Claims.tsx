import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertTriangle, ListChecks, Radar, X } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { StateBadge } from "@/components/StateBadge";
import { CLAIM_STATUS, ClaimStatusBadge, ExpiryChip, FactImpactDialog, STATUS_ORDER, evidenceText } from "@/components/claims";
import { claimsApi, type Channel, type ItemState, type LedgerGroup, type LedgerStatus } from "@/lib/api";
import { STATE_LABEL } from "@/lib/bands";
import { formatDate, useAsync } from "@/lib/hooks";
import { cn } from "@/lib/utils";

const CHANNELS: Channel[] = ["instagram", "email", "website", "linkedin"];
const CH_LABEL: Record<Channel, string> = { instagram: "Instagram", email: "Email", website: "Website", linkedin: "LinkedIn" };

function Group({ g, onImpact }: { g: LedgerGroup; onImpact: (id: number) => void }) {
  const f = g.fact;
  const items = g.items_approved + g.items_in_progress;
  return (
    <section className="glass-strong overflow-hidden" aria-label={f ? `Fact ${f.code}` : "Unsupported claims"} data-testid={f ? `group-${f.code}` : "group-unsupported"}>
      <header className="flex flex-wrap items-start gap-x-4 gap-y-2 border-b border-border px-5 py-4">
        <div className="min-w-0 flex-1 basis-64">
          {f ? (
            <>
              <div className="flex flex-wrap items-center gap-2"><span className="font-mono text-footnote font-semibold">{f.code}</span><ExpiryChip fact={f} /></div>
              <h2 className="mt-1 text-headline">{f.statement}</h2>
              <p className="mt-0.5 text-caption text-muted-foreground">Source: {f.source || "none given"}{f.value ? ` · ${f.value}` : ""}</p>
            </>
          ) : (
            <>
              <div className="flex items-center gap-2"><AlertTriangle className="size-4 text-human-text" strokeWidth={1.75} aria-hidden /><h2 className="text-headline">Unsupported claims</h2></div>
              <p className="mt-0.5 text-caption text-muted-foreground">No approved fact backs these claims. Add a fact, or change the wording.</p>
            </>
          )}
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="min-w-0 text-footnote text-muted-foreground"><span className="font-semibold text-foreground">{items}</span> item{items === 1 ? "" : "s"} {f ? (items === 1 ? "relies on it" : "rely on it") : (items === 1 ? "makes these claims" : "make these claims")} · {g.items_approved} approved · {g.items_in_progress} in progress</span>
          {f && <Button size="sm" variant="secondary" className="rounded-full" onClick={() => onImpact(f.id)} aria-label={`Impact of ${f.code}`}><Radar strokeWidth={1.75} /> Impact</Button>}
        </div>
      </header>
      <ul className="divide-y divide-border">
        {g.claims.map((c) => (
          <li key={`${c.id}-${f?.code ?? "none"}`} className="px-5 py-3">
            <div className="flex flex-wrap items-start gap-2">
              <ClaimStatusBadge status={c.status} />
              <p className="min-w-0 flex-1 basis-60 text-body">“{c.text}”</p>
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-muted-foreground">
              <Link to={`/items/${c.item_id}`} className="font-medium text-[var(--accent-text)] underline-offset-2 hover:underline">{c.item_title}</Link>
              <StateBadge state={c.item_state} />
              <span>{CH_LABEL[c.channel]} · v{c.version_n}{c.latest ? "" : " (older version)"}</span>
              {evidenceText(c) && <span>· {evidenceText(c)}</span>}
              {c.fact_codes.length > 1 && <span>· with {c.fact_codes.filter((x) => x !== f?.code).join(", ")}</span>}
              {c.stale && <span className="inline-flex items-center rounded-full bg-human/15 px-2 py-0.5 font-medium text-human-text">Fact changed since the check{c.fact_valid_until ? ` (was valid until ${formatDate(c.fact_valid_until)})` : ""}</span>}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function Claims() {
  const [params, setParams] = useSearchParams();
  const fact = params.get("fact") ?? undefined;
  const [status, setStatus] = useState<LedgerStatus | "all">("all");
  const [channel, setChannel] = useState<Channel | "all">("all");
  const [state, setState] = useState<ItemState | "all">("all");
  const [history, setHistory] = useState(false);
  const [impact, setImpact] = useState<number | null>(null);
  const q = useAsync(() => claimsApi.ledger({
    status: status === "all" ? undefined : status, channel: channel === "all" ? undefined : channel,
    state: state === "all" ? undefined : state, fact, all_versions: history || undefined,
  }), [status, channel, state, fact, history]);
  const filtered = status !== "all" || channel !== "all" || state !== "all" || !!fact;
  const clear = () => { setStatus("all"); setChannel("all"); setState("all"); if (fact) setParams({}); };
  const d = q.data;

  return (
    <>
      <PageHeader title="Claims ledger" subtitle="Every claim in your content, the approved fact behind it, and when that fact expires. Built from the checks run in this workspace." />
      {d && (
        <ul className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4" aria-label="Claims by status">
          {STATUS_ORDER.map((s) => {
            const m = CLAIM_STATUS[s];
            const Icon = m.icon;
            const active = status === s;
            return (
              <li key={s}>
                <button type="button" aria-pressed={active} onClick={() => setStatus(active ? "all" : s)}
                  className={cn("glass-strong flex w-full items-center gap-3 px-4 py-3 text-left transition-shadow focus-visible:outline-2", active && "ring-2 ring-[var(--accent-fill)]")}>
                  <span className={cn("grid size-9 shrink-0 place-items-center rounded-xl", m.cls)}><Icon className="size-4.5" strokeWidth={1.75} aria-hidden /></span>
                  <span className="min-w-0"><span className="block text-title3 tabular-nums">{d.totals[s] ?? 0}</span><span className="block truncate text-caption text-muted-foreground">{m.label}</span></span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Select value={status} onValueChange={(v) => setStatus(v as LedgerStatus | "all")}>
          <SelectTrigger aria-label="Filter by claim status" className="w-[170px] rounded-full"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            {STATUS_ORDER.map((s) => <SelectItem key={s} value={s}>{CLAIM_STATUS[s].label}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={channel} onValueChange={(v) => setChannel(v as Channel | "all")}>
          <SelectTrigger aria-label="Filter by channel" className="w-[150px] rounded-full"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All channels</SelectItem>
            {CHANNELS.map((c) => <SelectItem key={c} value={c}>{CH_LABEL[c]}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={state} onValueChange={(v) => setState(v as ItemState | "all")}>
          <SelectTrigger aria-label="Filter by item state" className="w-[180px] rounded-full"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All item states</SelectItem>
            {Object.entries(STATE_LABEL).map(([k, v]) => <SelectItem key={k} value={k}>{v}</SelectItem>)}
          </SelectContent>
        </Select>
        <label className="flex items-center gap-2 text-footnote"><Switch checked={history} onCheckedChange={setHistory} aria-label="Include older versions" /> Older versions</label>
        {fact && (
          <span className="inline-flex items-center gap-1 rounded-full bg-[#0A84FF]/12 py-1 pl-3 pr-1 text-caption font-medium text-[var(--accent-text)]">
            Fact {fact}
            <button type="button" onClick={() => setParams({})} aria-label="Show all facts" className="grid size-6 place-items-center rounded-full hover:bg-foreground/8"><X className="size-3.5" strokeWidth={2} /></button>
          </span>
        )}
      </div>
      {q.error ? <ErrorState message={q.error.message} onRetry={q.reload} />
        : q.loading && !d ? <div className="space-y-4">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-40" />)}</div>
        : d && d.groups.length === 0 ? (
          <EmptyState icon={ListChecks}
            title={filtered ? "No claims match these filters" : d.checked_items === 0 ? "No checks yet" : "No claims found yet"}
            description={filtered ? "Try clearing a filter." : d.checked_items === 0
              ? "The ledger fills in as content is checked: every claim, supported or not, is recorded with the fact behind it."
              : "None of the checked content makes a factual claim yet."}
            action={filtered ? <Button variant="secondary" className="rounded-full" onClick={clear}>Clear filters</Button>
              : <Button asChild className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]"><Link to="/content/new">Check some content</Link></Button>} />
        ) : d ? (
          <>
            <p className="mb-3 text-footnote text-muted-foreground" aria-live="polite">{d.items} item{d.items === 1 ? "" : "s"} · grouped by the fact each claim relies on, expired and soon-to-expire facts first.</p>
            <div className="space-y-4">{d.groups.map((g) => <Group key={g.fact?.code ?? "none"} g={g} onImpact={setImpact} />)}</div>
          </>
        ) : null}
      <FactImpactDialog factId={impact} open={impact != null} onOpenChange={(o) => { if (!o) setImpact(null); }} onDone={q.reload} />
    </>
  );
}
