import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Activity, BarChart3, Clock, Coins, FileCheck2, Flag, HelpCircle, Scale } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { KpiTile } from "@/components/KpiTile";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { chartTheme, GlassChartCard } from "@/components/GlassChartCard";
import { api, type CountBucket } from "@/lib/api";
import { useAsync } from "@/lib/hooks";

type Row = { name: string; count: number; label?: string | null };

export function SimpleBar({ data, horizontal }: { data: Row[]; horizontal?: boolean }) {
  return (
    <ResponsiveContainer width="100%" height="100%" className="text-muted-foreground">
      <BarChart data={data} layout={horizontal ? "vertical" : "horizontal"} margin={{ left: horizontal ? 24 : -16, right: 8, top: 4 }}>
        <CartesianGrid stroke={chartTheme.grid} vertical={false} horizontal={!horizontal} />
        {horizontal ? (
          <>
            <XAxis type="number" allowDecimals={false} {...chartTheme.axis} />
            <YAxis type="category" dataKey="name" width={96} interval={0} {...chartTheme.axis} />
          </>
        ) : (
          <>
            <XAxis dataKey="name" interval={0} tickFormatter={(v: string) => (v.length > 9 ? `${v.slice(0, 8)}…` : v)} {...chartTheme.axis} />
            <YAxis allowDecimals={false} {...chartTheme.axis} />
          </>
        )}
        <Tooltip
          cursor={{ fill: "rgba(128,128,128,0.1)" }} contentStyle={chartTheme.tooltipStyle}
          formatter={(v) => [String(v), "Findings"]}
          labelFormatter={(l, p) => { const lab = (p?.[0]?.payload as Row | undefined)?.label; return lab ? `${l}: ${lab}` : String(l); }}
        />
        <Bar isAnimationActive={false} dataKey="count" name="Findings" fill={chartTheme.accent} radius={[8, 8, 8, 8]} maxBarSize={28} />
      </BarChart>
    </ResponsiveContainer>
  );
}

const rows = (b: CountBucket[]): Row[] => b.map((x) => ({ name: x.key?.trim() || "uncategorised", count: x.count, label: x.label }));

export default function Dashboard() {
  const q = useAsync(() => api.insights(), []);
  if (q.error) return <><PageHeader title="Dashboard" /><ErrorState message={q.error.message} onRetry={q.reload} /></>;
  if (q.loading && !q.data) {
    return <><PageHeader title="Dashboard" subtitle="Loading the latest numbers…" /><div className="grid grid-cols-2 gap-4 lg:grid-cols-3">{[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-28" />)}</div></>;
  }
  const d = q.data!;
  const k = d.kpis;
  const empty = k.items_checked === 0 && k.findings === 0;
  return (
    <>
      <PageHeader title="Dashboard" subtitle="How content is doing against the guidelines. Numbers come from checks run on this server." />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
        <KpiTile label="Items checked" value={k.items_checked} icon={FileCheck2} />
        <KpiTile label="Findings" value={k.findings} icon={Flag} />
        <KpiTile label="Fixes accepted" value={Math.round(k.fix_accept_rate * 100)} suffix="%" icon={Activity} />
        <KpiTile label="Average check time" value={k.avg_check_ms / 1000} decimals={1} suffix="s" icon={Clock} />
        <KpiTile label="Jev judgments" value={k.jev_judgments} icon={Scale} />
        <KpiTile label="Total cost" value={k.total_cost_usd} decimals={4} prefix="$" icon={Coins} />
      </div>
      {empty ? (
        <EmptyState className="mt-6" icon={BarChart3} title="No checks yet" description="Run a check on some content and the charts fill in here." />
      ) : (
        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          <GlassChartCard title="Findings by category">{d.by_category.length ? <SimpleBar data={rows(d.by_category)} /> : <p className="text-footnote text-muted-foreground">No data yet.</p>}</GlassChartCard>
          <GlassChartCard title="Top rules broken" subtitle="Hover a bar for the rule text" height={Math.max(240, d.top_rules.length * 30)}>{d.top_rules.length ? <SimpleBar data={rows(d.top_rules)} horizontal /> : <p className="text-footnote text-muted-foreground">No data yet.</p>}</GlassChartCard>
          <GlassChartCard title="Findings by channel">{d.by_channel.length ? <SimpleBar data={rows(d.by_channel)} /> : <p className="text-footnote text-muted-foreground">No data yet.</p>}</GlassChartCard>
          <section className="glass-strong p-5 md:p-6">
            <h3 className="text-headline">Dismissal rate per rule</h3>
            <p className="mt-0.5 text-footnote text-muted-foreground">Rules people often dismiss may be unclear. Above 40% (with at least 3 findings) they get a tag.</p>
            {d.dismissal_rate.length === 0 ? <p className="mt-4 text-footnote text-muted-foreground">Nothing dismissed yet.</p> : (
              <ul className="mt-4 max-h-[280px] space-y-3 overflow-y-auto pr-1">
                {[...d.dismissal_rate].sort((a, b) => b.rate - a.rate).map((r) => (
                  <li key={r.rule_code}>
                    <div className="flex items-center justify-between gap-2 text-footnote">
                      <span className="min-w-0 truncate"><span className="font-mono text-caption">{r.rule_code}</span>{r.statement ? <span className="text-muted-foreground"> · {r.statement}</span> : null}</span>
                      <span className="flex shrink-0 items-center gap-2">
                        {r.possibly_unclear && <span className="inline-flex items-center gap-1 rounded-full bg-human/15 px-2 py-0.5 text-caption font-medium text-human-text"><HelpCircle className="size-3.5" strokeWidth={1.75} aria-hidden /> Possibly unclear</span>}
                        <span className="tabular-nums">{Math.round(r.rate * 100)}%</span>
                      </span>
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-foreground/8"><div className="h-full rounded-full bg-[#0A84FF]" style={{ width: `${Math.max(2, r.rate * 100)}%` }} /></div>
                    <p className="mt-0.5 text-caption text-muted-foreground">{r.dismissed} of {r.findings} dismissed</p>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </>
  );
}
