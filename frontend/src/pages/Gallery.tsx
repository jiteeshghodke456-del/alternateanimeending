import { useState } from "react";
import { Bar, BarChart, ResponsiveContainer, XAxis, YAxis } from "recharts";
import { Activity, Coins, Flag } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/PageHeader";
import { LiveMeter } from "@/components/LiveMeter";
import { RuleScanPanel } from "@/components/RuleScanPanel";
import { FindingCard } from "@/components/FindingCard";
import { HighlightedText } from "@/components/HighlightedText";
import { KpiTile } from "@/components/KpiTile";
import { chartTheme, GlassChartCard } from "@/components/GlassChartCard";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { AiUsageDrawer } from "@/components/AiUsageDrawer";
import { ConfidentialBadge } from "@/components/ConfidentialBadge";
import { DEMO_TEXT, MOCK_AI_USAGE, MOCK_BY_CATEGORY, MOCK_FINDINGS, MOCK_RULE_SCAN } from "@/lib/mock";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="mb-10"><h2 className="mb-3 text-title3">{title}</h2>{children}</section>;
}

/** Dev-only component gallery (route is registered only when import.meta.env.DEV). */
export default function Gallery() {
  const [running, setRunning] = useState(true);
  return (
    <>
      <PageHeader title="Component gallery" subtitle="Every reusable component with mock data. Dev only." actions={<Button variant="secondary" className="rounded-full" onClick={() => setRunning((r) => !r)}>{running ? "Stop" : "Start"} meter</Button>} />
      <Section title="LiveMeter"><LiveMeter paragraphs={3} paragraphsTotal={4} judgments={27} judgmentsPerSec={9.3} seconds={2.9} costUsd={0.0032} running={running} /></Section>
      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="RuleScanPanel"><RuleScanPanel rows={MOCK_RULE_SCAN} /></Section>
        <Section title="HighlightedText">
          <div className="glass-strong p-6"><HighlightedText text={DEMO_TEXT} spans={MOCK_FINDINGS.map((f) => ({ start: f.start, end: f.end, band: f.band, id: f.id }))} /></div>
        </Section>
      </div>
      <Section title="FindingCard">
        <div className="grid gap-4 lg:grid-cols-2">
          <FindingCard finding={MOCK_FINDINGS[0]} />
          <FindingCard finding={{ ...MOCK_FINDINGS[2], fix: MOCK_FINDINGS[2].fix }} />
        </div>
      </Section>
      <Section title="KpiTile">
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
          <KpiTile label="Findings" value={342} icon={Flag} hint="Last 30 days" />
          <KpiTile label="Fixes accepted" value={74} suffix="%" icon={Activity} />
          <KpiTile label="Total cost" value={0.22} decimals={2} prefix="$" icon={Coins} />
        </div>
      </Section>
      <Section title="GlassChartCard">
        <GlassChartCard title="Findings by category" subtitle="Last 30 days" height={200}>
          <ResponsiveContainer width="100%" height="100%" className="text-muted-foreground">
            <BarChart data={MOCK_BY_CATEGORY} margin={{ left: -16 }}>
              <XAxis dataKey="name" {...chartTheme.axis} /><YAxis {...chartTheme.axis} />
              <Bar isAnimationActive={false} dataKey="count" fill={chartTheme.accent} radius={8} maxBarSize={32} />
            </BarChart>
          </ResponsiveContainer>
        </GlassChartCard>
      </Section>
      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="EmptyState"><EmptyState title="Nothing here yet" description="Paste a caption or upload an image to see your first check." action={<Button className="rounded-full">Check something</Button>} /></Section>
        <Section title="ErrorState"><ErrorState onRetry={() => undefined} /></Section>
      </div>
      <Section title="AiUsageDrawer and ConfidentialBadge">
        <div className="flex flex-wrap items-center gap-3">
          <AiUsageDrawer usage={MOCK_AI_USAGE} trigger={<Button variant="secondary" className="rounded-full">Open AI used</Button>} />
          <AiUsageDrawer usage={{ ...MOCK_AI_USAGE, confidential: true }} trigger={<Button variant="secondary" className="rounded-full">Open (confidential)</Button>} />
          <ConfidentialBadge />
        </div>
      </Section>
    </>
  );
}
