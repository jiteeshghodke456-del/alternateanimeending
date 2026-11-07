import { Coins, Gauge, Layers, Scale, Timer } from "lucide-react";
import type { ReactNode } from "react";
import { NumberTicker } from "@/components/ui/number-ticker";
import { BorderBeam } from "@/components/ui/border-beam";
import { cn } from "@/lib/utils";

export interface LiveMeterProps {
  paragraphs: number;
  paragraphsTotal?: number;
  judgments: number;
  judgmentsPerSec: number;
  seconds: number;
  costUsd: number;
  running?: boolean;
  className?: string;
}

function Stat({ icon, label, children }: { icon: ReactNode; label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-1.5 text-caption text-muted-foreground">{icon}{label}</div>
      <div className="mt-1 text-title2 tabular-nums">{children}</div>
    </div>
  );
}

const tick = "!tracking-tight !text-foreground";

/** Live check meter. Values come from the SSE stream; a border beam runs while the check is running. */
export function LiveMeter({ paragraphs, paragraphsTotal, judgments, judgmentsPerSec, seconds, costUsd, running, className }: LiveMeterProps) {
  const ic = "size-3.5";
  return (
    <section aria-label="Live check meter" className={cn("glass-strong relative overflow-hidden p-5", className)}>
      <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-5">
        <Stat icon={<Layers className={ic} strokeWidth={1.5} />} label="Paragraphs">
          <NumberTicker value={paragraphs} className={tick} />
          {paragraphsTotal != null && <span className="text-callout text-muted-foreground"> / {paragraphsTotal}</span>}
        </Stat>
        <Stat icon={<Scale className={ic} strokeWidth={1.5} />} label="Jev judgments"><NumberTicker value={judgments} className={tick} /></Stat>
        <Stat icon={<Gauge className={ic} strokeWidth={1.5} />} label="Judgments / s"><NumberTicker value={judgmentsPerSec} decimalPlaces={1} className={tick} /></Stat>
        <Stat icon={<Timer className={ic} strokeWidth={1.5} />} label="Seconds"><NumberTicker value={seconds} decimalPlaces={1} className={tick} /></Stat>
        <Stat icon={<Coins className={ic} strokeWidth={1.5} />} label="Cost">
          <span className="text-callout text-muted-foreground">$</span><NumberTicker value={costUsd} decimalPlaces={4} className={tick} />
        </Stat>
      </div>
      <div className="mt-3 flex items-center gap-2 text-caption text-muted-foreground" role="status">
        <span className={cn("size-1.5 rounded-full", running ? "bg-[var(--accent-fill)]" : "bg-pass")} />
        {running ? "Checking now" : "Check complete"}
      </div>
      {running && <BorderBeam size={120} duration={5} colorFrom="#0A84FF" colorTo="#64D2FF" borderWidth={2} />}
    </section>
  );
}
