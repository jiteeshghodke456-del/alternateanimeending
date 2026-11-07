import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Link } from "react-router-dom";
import { Activity, ArrowRight, BarChart3, Check, Clock, Coins, FileCheck2, Flag, HelpCircle, Scale } from "lucide-react";
import { BrandMark } from "@/components/BrandMark";
import { Button } from "@/components/ui/button";
import { useSession } from "@/lib/session";
import { PageHeader } from "@/components/PageHeader";
import { KpiTile } from "@/components/KpiTile";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { chartTheme, GlassChartCard } from "@/components/GlassChartCard";
import { api, namesWithRole, type CountBucket } from "@/lib/api";
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

/** Shown while a fresh (blank) workspace still needs its brand set up. */
function SetupCard() {
  const { brand, user, users } = useSession();
  if (!brand?.is_blank_setup) return null;
  const c = brand.counts;
  const parts = [
    { label: "Name", done: !!(brand.tagline || brand.description) },
    { label: "Colours", done: brand.palette.length > 0 || !!brand.logo_url },
    { label: "Rules", done: (c?.rules ?? brand.rule_count) > 0 },
    { label: "Facts", done: (c?.facts ?? brand.fact_count) > 0 },
    { label: "Words", done: (c?.terms ?? brand.term_count) > 0 },
  ];
  const n = parts.filter((p) => p.done).length;
  const admin = user?.role === "admin";
  return (
    <section aria-labelledby="setup-h" className="glass-strong relative mb-6 overflow-hidden p-5 md:p-6">
      <div aria-hidden className="pointer-events-none absolute -right-16 -top-20 size-56 rounded-full bg-[#0A84FF]/15 blur-3xl" />
      <div className="relative flex flex-wrap items-center gap-4">
        <BrandMark brand={brand} className="size-12 rounded-2xl text-headline" />
        <div className="min-w-0 flex-1 basis-60">
          <h2 id="setup-h" className="text-title3">Finish setting up your brand</h2>
          <p className="mt-0.5 text-footnote text-muted-foreground">{n} of {parts.length} steps done. Checks get sharper with your own rules, facts and words.</p>
          <ul className="mt-3 flex flex-wrap gap-1.5">
            {parts.map((p) => (
              <li key={p.label} className={p.done ? "inline-flex items-center gap-1 rounded-full bg-pass/14 px-2.5 py-0.5 text-caption font-medium text-pass-text" : "rounded-full bg-foreground/8 px-2.5 py-0.5 text-caption font-medium text-muted-foreground"}>
                {p.done && <Check className="size-3" strokeWidth={2.5} aria-hidden />}{p.label}<span className="sr-only">{p.done ? " done" : " to do"}</span>
              </li>
            ))}
          </ul>
        </div>
        {admin ? (
          <Button asChild className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">
            <Link to="/setup">Continue setup <ArrowRight strokeWidth={2} /></Link>
          </Button>
        ) : <p className="text-footnote text-muted-foreground">Ask {namesWithRole(users, "admin")} to finish setup.</p>}
      </div>
    </section>
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
      <SetupCard />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
        <KpiTile label="Items checked" value={k.items_checked} icon={FileCheck2} />
        <KpiTile label="Findings" value={k.findings} icon={Flag} />
        <KpiTile label="Fixes accepted" value={Math.round(k.fix_accept_rate * 100)} suffix="%" icon={Activity} />
        <KpiTile label="Average check time" value={k.avg_check_ms / 1000} decimals={1} suffix="s" icon={Clock} />
        <KpiTile label="Jev judgments" value={k.jev_judgments} icon={Scale} />
        <KpiTile label="Total cost" value={k.total_cost_usd} decimals={4} prefix="$" icon={Coins} />
      </div>
      {empty ? (
        <EmptyState
          className="mt-6" icon={BarChart3} title="No checks yet" description="Run a check on some content and the charts fill in here."
          action={<Button asChild className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]"><Link to="/content/new">Check your first post</Link></Button>}
        />
      ) : (
        <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2 [&>*]:min-w-0">
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
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-foreground/8"><div className="h-full rounded-full bg-[var(--accent-fill)]" style={{ width: `${Math.max(2, r.rate * 100)}%` }} /></div>
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
