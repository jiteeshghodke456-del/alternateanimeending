import { useState } from "react";
import { useParams } from "react-router-dom";
import { ShieldCheck } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { LiveMeter } from "@/components/LiveMeter";
import { RuleScanPanel } from "@/components/RuleScanPanel";
import { FindingCard } from "@/components/FindingCard";
import { HighlightedText } from "@/components/HighlightedText";
import { AiUsageDrawer } from "@/components/AiUsageDrawer";
import { Button } from "@/components/ui/button";
import { DEMO_TEXT, MOCK_AI_USAGE, MOCK_FINDINGS, MOCK_RULE_SCAN } from "@/lib/mock";

export default function Item() {
  const { id } = useParams();
  const [active, setActive] = useState<string | undefined>();
  const findings = MOCK_FINDINGS;
  return (
    <>
      <PageHeader
        title="Saffron Brew Blend launch post"
        subtitle={`Item ${id}. Sample findings shown until the live check is connected.`}
        actions={<AiUsageDrawer usage={MOCK_AI_USAGE} trigger={<Button variant="secondary" className="rounded-full"><ShieldCheck strokeWidth={1.5} /> AI used</Button>} />}
      />
      <LiveMeter paragraphs={1} paragraphsTotal={1} judgments={9} judgmentsPerSec={7.4} seconds={1.2} costUsd={0.0011} />
      <div className="mt-6 grid gap-6 lg:grid-cols-[1fr_minmax(0,460px)]">
        <div className="space-y-6">
          <section className="glass-strong p-6">
            <h2 className="mb-3 text-headline">Content</h2>
            <HighlightedText text={DEMO_TEXT} activeId={active} onSelect={setActive} spans={findings.map((f) => ({ start: f.start, end: f.end, band: f.band, id: f.id, label: f.rule_id }))} />
          </section>
          <RuleScanPanel rows={MOCK_RULE_SCAN} />
        </div>
        <div className="space-y-4">
          {findings.map((f) => <FindingCard key={f.id} finding={f} active={active === f.id} />)}
        </div>
      </div>
    </>
  );
}
