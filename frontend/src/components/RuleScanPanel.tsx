import { motion } from "framer-motion";
import { BAND, bandOf } from "@/lib/bands";
import { cn } from "@/lib/utils";

export interface RuleScanRow { id: string; label: string; p: number }

/** One thin bar per rule checked, coloured by Jev probability. The label and icon repeat the band so colour is never the only signal. */
export function RuleScanPanel({ rows, className }: { rows: RuleScanRow[]; className?: string }) {
  return (
    <section aria-label="Rule scan" className={cn("glass-strong p-5", className)}>
      <div className="mb-3 flex items-baseline justify-between">
        <h3 className="text-headline">Rule scan</h3>
        <span className="text-caption text-muted-foreground">{rows.length} rules checked</span>
      </div>
      <ul className="space-y-2.5">
        {rows.map((r, i) => {
          const b = BAND[bandOf(r.p)];
          const Icon = b.icon;
          return (
            <li key={r.id} className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1">
              <div className="flex min-w-0 items-baseline gap-2">
                <span className="shrink-0 font-mono text-caption text-muted-foreground">{r.id}</span>
                <span className="truncate text-footnote">{r.label}</span>
              </div>
              <span className={cn("flex items-center gap-1 text-caption font-medium tabular-nums", b.text)}>
                <Icon className="size-3.5" strokeWidth={1.75} aria-hidden /> {Math.round(r.p * 100)}% <span className="sr-only">{b.label}</span>
              </span>
              <div className="col-span-2 h-1.5 overflow-hidden rounded-full bg-foreground/8" role="presentation">
                <motion.div
                  className={cn("h-full rounded-full", b.bar)}
                  initial={{ width: 0 }}
                  animate={{ width: `${Math.max(2, r.p * 100)}%` }}
                  transition={{ type: "spring", stiffness: 140, damping: 22, delay: i * 0.04 }}
                />
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
