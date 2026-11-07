import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Activity, Clock, Coins, FileCheck2, Flag, Scale } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { KpiTile } from "@/components/KpiTile";
import { chartTheme, GlassChartCard } from "@/components/GlassChartCard";
import { MOCK_BY_CATEGORY, MOCK_BY_CHANNEL, MOCK_KPIS, MOCK_TOP_RULES } from "@/lib/mock";

export function SimpleBar({ data, horizontal }: { data: { name: string; count: number }[]; horizontal?: boolean }) {
  return (
    <ResponsiveContainer width="100%" height="100%" className="text-muted-foreground">
      <BarChart data={data} layout={horizontal ? "vertical" : "horizontal"} margin={{ left: horizontal ? 24 : -16, right: 8, top: 4 }}>
        <CartesianGrid stroke={chartTheme.grid} vertical={!horizontal} horizontal={!!horizontal ? false : true} />
        {horizontal ? (
          <>
            <XAxis type="number" {...chartTheme.axis} />
            <YAxis type="category" dataKey="name" width={96} {...chartTheme.axis} />
          </>
        ) : (
          <>
            <XAxis dataKey="name" {...chartTheme.axis} />
            <YAxis {...chartTheme.axis} />
          </>
        )}
        <Tooltip cursor={{ fill: "rgba(128,128,128,0.1)" }} contentStyle={chartTheme.tooltipStyle} />
        <Bar isAnimationActive={false} dataKey="count" name="Findings" fill={chartTheme.accent} radius={[8, 8, 8, 8]} maxBarSize={28} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export default function Dashboard() {
  const k = MOCK_KPIS;
  return (
    <>
      <PageHeader title="Dashboard" subtitle="How content is doing against the guidelines. Sample data until the live insights are connected." />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
        <KpiTile label="Items checked" value={k.itemsChecked} icon={FileCheck2} />
        <KpiTile label="Findings" value={k.findings} icon={Flag} />
        <KpiTile label="Fixes accepted" value={k.fixesAcceptedPct} suffix="%" icon={Activity} />
        <KpiTile label="Average check time" value={k.avgCheckSeconds} decimals={1} suffix="s" icon={Clock} />
        <KpiTile label="Jev judgments" value={k.judgments} icon={Scale} />
        <KpiTile label="Total cost" value={k.costUsd} decimals={2} prefix="$" icon={Coins} />
      </div>
      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <GlassChartCard title="Findings by category"><SimpleBar data={MOCK_BY_CATEGORY} /></GlassChartCard>
        <GlassChartCard title="Top rules broken"><SimpleBar data={MOCK_TOP_RULES} horizontal /></GlassChartCard>
        <GlassChartCard title="Findings by channel"><SimpleBar data={MOCK_BY_CHANNEL} /></GlassChartCard>
      </div>
    </>
  );
}
