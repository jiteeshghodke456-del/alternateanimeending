import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Coins, Crosshair, FlaskConical, Loader2, Play, Target, Timer, Trophy } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/PageHeader";
import { KpiTile } from "@/components/KpiTile";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { chartTheme, GlassChartCard } from "@/components/GlassChartCard";
import { api, namesWithRole } from "@/lib/api";
import { formatDate, reportError, useAsync } from "@/lib/hooks";
import { useSession } from "@/lib/session";

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => !!v && typeof v === "object" && !Array.isArray(v);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
/** Look for a number under several possible key names, also inside `overall`. */
function pick(m: Obj, ...keys: string[]): number | null {
  for (const scope of [m, isObj(m.overall) ? m.overall : {}]) for (const k of keys) { const n = num((scope as Obj)[k]); if (n != null) return n; }
  return null;
}
const pct = (n: number | null) => (n == null ? null : n <= 1 ? n * 100 : n);

function categoryRows(m: Obj): { name: string; precision: number | null; recall: number | null; f1: number | null; n: number | null }[] {
  const src = m.per_category ?? m.by_category;
  const entries: [string, Obj][] = Array.isArray(src)
    ? src.filter(isObj).map((r) => [String(r.category ?? r.name ?? r.key ?? ""), r])
    : isObj(src) ? Object.entries(src).filter((e): e is [string, Obj] => isObj(e[1])) : [];
  return entries.map(([name, r]) => ({ name, precision: pct(num(r.precision)), recall: pct(num(r.recall)), f1: pct(num(r.f1)), n: num(r.n) ?? num(r.support) ?? num(r.items) }));
}
function calibration(m: Obj): { name: string; actual: number; n: number | null }[] {
  const src = m.calibration ?? m.calibration_bins;
  if (!Array.isArray(src)) return [];
  return src.filter(isObj).map((b, i) => {
    const lo = num(b.lo) ?? num(b.low), hi = num(b.hi) ?? num(b.high);
    const label = typeof b.bin === "string" ? b.bin : lo != null && hi != null ? `${lo.toFixed(1)}–${hi.toFixed(1)}` : String(i + 1);
    const actual = num(b.actual) ?? num(b.actual_rate) ?? num(b.observed) ?? num(b.accuracy) ?? 0;
    return { name: label, actual: actual <= 1 ? actual * 100 : actual, n: num(b.n) ?? num(b.count) };
  });
}
const cell = (v: unknown): string => (v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v));

export default function Eval() {
  const { user, users } = useSession();
  const q = useAsync(() => api.evalLatest(), []);
  const [running, setRunning] = useState(false);
  const admin = user?.role === "admin";

  async function run() {
    setRunning(true);
    try { const r = await api.evalRun(); toast.success(r.message || "Evaluation started."); setTimeout(q.reload, 1500); }
    catch (e) { reportError(e); } finally { setRunning(false); }
  }

  if (q.error) return <><PageHeader title="Evaluation" /><ErrorState message={q.error.message} onRetry={q.reload} /></>;
  if (q.loading && !q.data) return <><PageHeader title="Evaluation" /><div className="grid grid-cols-2 gap-4 lg:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-28" />)}</div></>;
  const d = q.data!;

  if (!d.available) {
    return (
      <>
        <PageHeader title="Evaluation" subtitle="Precision and recall on a labelled test split, from a real run." />
        {d.brand_note && <p role="note" className="mb-5 rounded-2xl bg-[var(--accent-vivid)]/10 p-3 text-footnote text-[var(--accent-text)]">{d.brand_note}</p>}
        <EmptyState
          icon={FlaskConical} title="Evaluation hasn't been run on this server yet"
          description="We only show accuracy numbers that come from a real run, so there is nothing to show until one finishes."
          action={admin ? (
            <Button onClick={run} disabled={running} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">{running ? <Loader2 className="animate-spin" /> : <Play strokeWidth={1.75} />} Run on test split</Button>
          ) : <p className="text-footnote text-muted-foreground">A brand admin can start a run. Switch to {namesWithRole(users, "admin")} in the top bar.</p>}
        />
      </>
    );
  }

  const m = d.metrics as Obj;
  const precision = pct(pick(m, "precision")), recall = pct(pick(m, "recall")), f1 = pct(pick(m, "f1", "f1_score"));
  const med = pick(m, "median_latency_ms", "latency_median_ms", "median_ms"), p95 = pick(m, "p95_latency_ms", "latency_p95_ms", "p95_ms");
  const medS = med != null ? med / 1000 : pick(m, "median_latency_s", "median_latency_seconds");
  const cost = pick(m, "cost_per_item_usd", "cost_per_item", "avg_cost_usd");
  const claimAcc = pct(pick(m, "claim_status_accuracy", "claim_accuracy"));
  const cats = categoryRows(m);
  const cal = calibration(m);
  const missCols = d.misses.length ? Object.keys(d.misses[0]).slice(0, 6) : [];
  const note = d.brand_note ? <p role="note" className="mb-5 rounded-2xl bg-[var(--accent-vivid)]/10 p-3 text-footnote text-[var(--accent-text)]">{d.brand_note}</p> : null;

  return (
    <>
      <PageHeader
        title="Evaluation"
        subtitle={`Run ${formatDate(d.run_at)} on the ${d.split ?? "test"} split.${Object.keys(d.models).length ? ` Models: ${Object.values(d.models).join(", ")}.` : ""}`}
        actions={admin ? <Button onClick={run} disabled={running} variant="secondary" className="rounded-full">{running ? <Loader2 className="animate-spin" /> : <Play strokeWidth={1.75} />} Re-run on test split</Button> : undefined}
      />
      {note}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
        {precision != null && <KpiTile label="Precision" value={precision} decimals={1} suffix="%" icon={Target} />}
        {recall != null && <KpiTile label="Recall" value={recall} decimals={1} suffix="%" icon={Crosshair} />}
        {f1 != null && <KpiTile label="F1" value={f1} decimals={1} suffix="%" icon={Trophy} />}
        {medS != null && <KpiTile label="Median latency" value={medS} decimals={1} suffix="s" icon={Timer} hint={p95 != null ? `p95 ${(p95 / 1000).toFixed(1)} s` : undefined} />}
        {cost != null && <KpiTile label="Cost per item" value={cost} decimals={4} prefix="$" icon={Coins} />}
        {claimAcc != null && <KpiTile label="Claim status accuracy" value={claimAcc} decimals={1} suffix="%" icon={Target} />}
      </div>

      {cats.length > 0 && (
        <section className="glass-strong mt-6 overflow-x-auto p-5 md:p-6">
          <h3 className="text-headline">By category</h3>
          <table className="mt-3 w-full min-w-[420px] text-left text-footnote">
            <thead className="text-caption text-muted-foreground"><tr><th className="py-2 pr-3 font-medium">Category</th><th className="px-3 font-medium">Precision</th><th className="px-3 font-medium">Recall</th><th className="px-3 font-medium">F1</th></tr></thead>
            <tbody className="divide-y divide-border">
              {cats.map((r) => (
                <tr key={r.name}><td className="py-2.5 pr-3 font-medium capitalize">{r.name}</td>{[r.precision, r.recall, r.f1].map((v, i) => <td key={i} className="px-3 tabular-nums">{v == null ? "n/a" : `${v.toFixed(0)}%`}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {cal.length > 0 && (
        <GlassChartCard className="mt-6" title="Calibration" subtitle="Share of flagged items that were truly problems, per Jev probability bin">
          <ResponsiveContainer width="100%" height="100%" className="text-muted-foreground">
            <BarChart data={cal} margin={{ left: -16 }}>
              <CartesianGrid stroke={chartTheme.grid} vertical={false} />
              <XAxis dataKey="name" {...chartTheme.axis} /><YAxis {...chartTheme.axis} unit="%" domain={[0, 100]} />
              <Tooltip cursor={{ fill: "rgba(128,128,128,0.1)" }} contentStyle={chartTheme.tooltipStyle} formatter={(v) => [`${Number(v).toFixed(0)}%`, "Actual rate"]} />
              <Bar isAnimationActive={false} dataKey="actual" name="Actual rate" fill={chartTheme.accent} radius={8} maxBarSize={36} />
            </BarChart>
          </ResponsiveContainer>
        </GlassChartCard>
      )}

      <section className="glass-strong mt-6 overflow-x-auto p-5 md:p-6">
        <h3 className="text-headline">Misses</h3>
        <p className="mt-0.5 text-footnote text-muted-foreground">Where the checker was wrong. We show these on purpose.</p>
        {d.misses.length === 0 ? <p className="mt-3 text-footnote text-muted-foreground">No misses recorded in this run.</p> : (
          <table className="mt-3 w-full min-w-[520px] text-left text-footnote">
            <thead className="text-caption text-muted-foreground"><tr>{missCols.map((c) => <th key={c} className="py-2 pr-3 font-medium capitalize">{c.replace(/_/g, " ")}</th>)}</tr></thead>
            <tbody className="divide-y divide-border">
              {d.misses.map((r, i) => <tr key={i}>{missCols.map((c) => <td key={c} className="max-w-[320px] break-words py-2.5 pr-3 align-top">{cell(r[c])}</td>)}</tr>)}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
