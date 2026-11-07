import { useState } from "react";
import { Palette } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Switch } from "@/components/ui/switch";
import { SEVERITY_LABEL, SEVERITY_STYLE } from "@/lib/bands";
import { MOCK_RULES } from "@/lib/mock";
import { cn } from "@/lib/utils";

const SWATCHES = [["Saffron", "#F4A300"], ["Steam", "#F5F1EA"], ["Roast", "#3B2418"], ["Leaf", "#3E7C59"], ["Ember", "#C8452B"]];

export default function BrandKit() {
  const [rules, setRules] = useState(MOCK_RULES);
  return (
    <>
      <PageHeader title="Brand Kit" subtitle="Saffron & Steam Coffee Co. (fictional). Rules, words, palette and approved facts." />
      <Tabs defaultValue="rules">
        <TabsList className="h-11 rounded-full bg-[var(--glass-bg)] p-1 backdrop-blur-xl">
          {["rules", "words", "palette", "facts"].map((t) => (
            <TabsTrigger key={t} value={t} className="rounded-full px-4 capitalize data-[state=active]:bg-[#0A84FF] data-[state=active]:text-white">{t === "palette" ? "Palette & Logo" : t}</TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="rules" className="mt-5">
          <ul className="glass-strong divide-y divide-border overflow-hidden">
            {rules.map((r) => (
              <li key={r.id} className="flex items-center gap-4 px-5 py-4">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-caption text-muted-foreground">{r.id}</span>
                    <span className={cn("rounded-full px-2 py-0.5 text-caption font-medium", SEVERITY_STYLE[r.severity])}>{SEVERITY_LABEL[r.severity]}</span>
                    <span className="text-caption text-muted-foreground">{r.category} · p.{r.page} · v{r.version}</span>
                  </div>
                  <p className="mt-1 text-body">{r.statement}</p>
                </div>
                <Switch checked={r.enabled} aria-label={`Rule ${r.id} enabled`} onCheckedChange={(v) => setRules((rs) => rs.map((x) => (x.id === r.id ? { ...x, enabled: v } : x)))} />
              </li>
            ))}
          </ul>
        </TabsContent>
        <TabsContent value="words" className="mt-5"><EmptyState title="Words list" description="Banned words, old product names and capitalisation rules will appear here." /></TabsContent>
        <TabsContent value="palette" className="mt-5">
          <div className="glass-strong grid grid-cols-2 gap-4 p-6 sm:grid-cols-5">
            {SWATCHES.map(([n, c]) => (
              <div key={n}>
                <div className="h-20 rounded-2xl border border-border" style={{ background: c }} />
                <p className="mt-2 text-footnote font-medium">{n}</p>
                <p className="font-mono text-caption text-muted-foreground">{c}</p>
              </div>
            ))}
          </div>
        </TabsContent>
        <TabsContent value="facts" className="mt-5"><EmptyState icon={Palette} title="Approved facts" description="Facts with source and expiry date will appear here, with an expired badge." /></TabsContent>
      </Tabs>
    </>
  );
}
