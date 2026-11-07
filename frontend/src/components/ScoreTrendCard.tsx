import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Gauge } from "lucide-react";
import { ScoreBadge } from "@/components/BrandScore";
import { chartTheme } from "@/components/GlassChartCard";
import type { InsightsOut } from "@/lib/api";
import { SCORE_LABEL, scoreBand } from "@/lib/score";
import { cn } from "@/lib/utils";

const fmtDay = (d: string) => new Date(`${d}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" });

/** Dashboard: average Brand Score of each item's latest version, and the daily average when there is more than one day. */
export function ScoreTrendCard({ data, className }: { data: InsightsOut; className?: string }) {
  const avg = data.avg_score;
  if (avg == null) return null;
  const trend = data.score_trend ?? [];
  const rounded = Math.round(avg);
  return (
    <section aria-labelledby="score-h" data-testid="score-trend" className={cn("glass-strong p-5 md:p-6", className)}>
      <div className="flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1 basis-60">
          <h3 id="score-h" className="inline-flex items-center gap-1.5 text-headline"><Gauge className="size-4" strokeWidth={1.5} aria-hidden /> Brand Score</h3>
          <p className="mt-0.5 text-footnote text-muted-foreground">
            Average of the latest version of {data.scored_items ?? 0} checked item{data.scored_items === 1 ? "" : "s"}. Each score is 100 minus weights for open findings.
          </p>
        </div>
        <p className="flex items-center gap-2">
          <span className="text-title1 tabular-nums tracking-tight">{avg.toFixed(1)}</span>
          <ScoreBadge score={rounded} band={scoreBand(rounded)} showLabel />
          <span className="sr-only">{SCORE_LABEL[scoreBand(rounded)]}</span>
        </p>
      </div>
      {trend.length >= 2 ? (
        <div className="mt-4 h-[200px] text-muted-foreground">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={trend.map((t) => ({ ...t, label: fmtDay(t.day) }))} margin={{ left: -16, right: 8, top: 4 }}>
              <CartesianGrid stroke={chartTheme.grid} vertical={false} />
              <XAxis dataKey="label" {...chartTheme.axis} />
              <YAxis domain={[0, 100]} ticks={[0, 60, 80, 100]} {...chartTheme.axis} />
              <Tooltip contentStyle={chartTheme.tooltipStyle} formatter={(v, _n, p) => [`${v} (${(p?.payload as { versions: number }).versions} versions)`, "Average score"]} />
              <Line isAnimationActive={false} type="monotone" dataKey="avg_score" name="Average score" stroke={chartTheme.accent} strokeWidth={2} dot={{ r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <p className="mt-3 text-caption text-muted-foreground">The trend line appears once versions have been checked on two or more days.</p>
      )}
    </section>
  );
}
