import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Frame for a recharts chart. Pass a ResponsiveContainer as children; height is fixed so layouts do not jump. */
export function GlassChartCard({ title, subtitle, children, height = 240, className, footer }: {
  title: string; subtitle?: string; children: ReactNode; height?: number; className?: string; footer?: ReactNode;
}) {
  return (
    <section className={cn("glass-strong p-5 md:p-6", className)}>
      <h3 className="text-headline">{title}</h3>
      {subtitle && <p className="mt-0.5 text-footnote text-muted-foreground">{subtitle}</p>}
      <div className="mt-4" style={{ height }}>{children}</div>
      {footer && <div className="mt-3 text-caption text-muted-foreground">{footer}</div>}
    </section>
  );
}

/** Shared recharts styling so every chart looks the same in both themes. */
export const chartTheme = {
  axis: { stroke: "currentColor", fontSize: 12, tickLine: false, axisLine: false } as const,
  grid: "rgba(128,128,128,0.18)",
  accent: "#951EFC",
  tooltipStyle: {
    background: "var(--glass-strong-bg)", border: "1px solid var(--glass-border)", borderRadius: 14,
    backdropFilter: "blur(20px)", color: "hsl(var(--foreground))", fontSize: 13, boxShadow: "var(--glass-shadow)",
  } as React.CSSProperties,
};
