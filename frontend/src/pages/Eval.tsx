import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Crosshair, Target, Timer } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { KpiTile } from "@/components/KpiTile";
import { chartTheme, GlassChartCard } from "@/components/GlassChartCard";

const BINS = [0.1, 0.3, 0.5, 0.7, 0.9].map((x, i) => ({ name: x.toFixed(1), count: [0.06, 0.28, 0.51, 0.74, 0.93][i] * 100 }));

export default function Eval() {
  return (
    <>
      <PageHeader title="Evaluation" subtitle="Layout preview only. Numbers here are placeholders and are replaced by the latest real run on the test split." />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
        <KpiTile label="Precision" value={0} suffix="%" icon={Target} hint="Placeholder" />
        <KpiTile label="Recall" value={0} suffix="%" icon={Crosshair} hint="Placeholder" />
        <KpiTile label="Median latency" value={0} decimals={1} suffix="s" icon={Timer} hint="Placeholder" />
      </div>
      <GlassChartCard className="mt-6" title="Calibration" subtitle="Share of items that were truly problems, per probability bin (placeholder shape)">
        <ResponsiveContainer width="100%" height="100%" className="text-muted-foreground">
          <BarChart data={BINS} margin={{ left: -16 }}>
            <CartesianGrid stroke={chartTheme.grid} vertical={false} />
            <XAxis dataKey="name" {...chartTheme.axis} /><YAxis {...chartTheme.axis} unit="%" />
            <Tooltip cursor={{ fill: "rgba(128,128,128,0.1)" }} contentStyle={chartTheme.tooltipStyle} />
            <Bar isAnimationActive={false} dataKey="count" name="Actual rate" fill={chartTheme.accent} radius={8} maxBarSize={36} />
          </BarChart>
        </ResponsiveContainer>
      </GlassChartCard>
    </>
  );
}
