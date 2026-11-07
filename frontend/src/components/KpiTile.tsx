import type { LucideIcon } from "lucide-react";
import { NumberTicker } from "@/components/ui/number-ticker";
import { cn } from "@/lib/utils";

export function KpiTile({ label, value, decimals = 0, prefix, suffix, icon: Icon, hint, className }: {
  label: string; value: number; decimals?: number; prefix?: string; suffix?: string; icon?: LucideIcon; hint?: string; className?: string;
}) {
  return (
    <div className={cn("glass p-5", className)}>
      <div className="flex items-center justify-between text-footnote text-muted-foreground">
        <span>{label}</span>
        {Icon && <Icon className="size-4" strokeWidth={1.5} aria-hidden />}
      </div>
      <p className="mt-2 flex items-baseline gap-0.5 text-title1 tabular-nums">
        {prefix && <span className="text-title3 text-muted-foreground">{prefix}</span>}
        <NumberTicker value={value} decimalPlaces={decimals} className="!tracking-tight !text-foreground" />
        {suffix && <span className="text-title3 text-muted-foreground">{suffix}</span>}
      </p>
      {hint && <p className="mt-1 text-caption text-muted-foreground">{hint}</p>}
    </div>
  );
}
