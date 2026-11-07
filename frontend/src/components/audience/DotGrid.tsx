import type { AudiencePersona } from "@/lib/api";
import { cn } from "@/lib/utils";

export const TOTAL_DOTS = 240;

/**
 * The five reaction levels (1 trusts it and would act ... 5 distrusts it or is put off).
 * Colour is always paired with the level number inside the dot and the words in the legend.
 * The number is dark (black/75), which passes 4.5:1 on every fill.
 */
export const LEVELS = [
  { n: 1, label: "Trusts it, would act", bg: "#30D158" },
  { n: 2, label: "Mostly trusts it", bg: "#A8DD5A" },
  { n: 3, label: "Unsure", bg: "#FFD60A" },
  { n: 4, label: "Doubts it", bg: "#FF9F0A" },
  { n: 5, label: "Distrusts it, put off", bg: "#FF6B5E" },
] as const;
export const levelInfo = (n: number) => LEVELS[Math.min(5, Math.max(1, Math.round(n))) - 1];
/** Colour for a 1..5 mean (nearest level). */
export const scoreColor = (s: number) => levelInfo(s).bg;

export function DotLegend({ counts, confused, className }: { counts: number[]; confused: number; className?: string }) {
  return (
    <ul className={cn("flex flex-wrap gap-x-4 gap-y-1.5 text-caption", className)} aria-label="Legend">
      {LEVELS.map((l, i) => (
        <li key={l.n} className="inline-flex items-center gap-1.5">
          <span className="grid size-[18px] place-items-center rounded-full text-[11px] font-bold text-black/75" style={{ background: l.bg }} aria-hidden>{l.n}</span>
          <span className="font-medium">{l.label}</span>
          <span className="tabular-nums text-muted-foreground">{counts[i] ?? 0}</span>
        </li>
      ))}
      <li className="inline-flex items-center gap-1.5">
        <span className="grid size-[18px] place-items-center rounded-full bg-foreground/10 text-[11px] font-bold ring-2 ring-[#0A84FF]" aria-hidden>?</span>
        <span className="font-medium">May misread the main message</span>
        <span className="tabular-nums text-muted-foreground">{confused}</span>
      </li>
    </ul>
  );
}

/**
 * One small glass dot per simulated reader, grouped by reader group. Each dot lights up when its answer arrives,
 * coloured by its reaction level (1-5) with the number inside; a blue ring marks readers who may misread it.
 */
export function DotGrid({ personas, segmentOrder, finished, selected, onSelect, total }: {
  personas: AudiencePersona[]; segmentOrder: string[]; finished: boolean;
  selected: string | null; onSelect: (segment: string) => void; total?: number;
}) {
  const rank = new Map(segmentOrder.map((k, i) => [k, i]));
  const sorted = [...personas].sort((a, b) => (rank.get(a.segment) ?? 99) - (rank.get(b.segment) ?? 99) || a.idx - b.idx);
  const slots: (AudiencePersona | undefined)[] = Array.from({ length: Math.max(total ?? TOTAL_DOTS, sorted.length) }, (_, i) => sorted[i]);

  return (
    <div className="grid grid-cols-[repeat(15,minmax(0,1fr))] gap-[5px] sm:grid-cols-[repeat(20,minmax(0,1fr))] sm:gap-[4px]" role="group" aria-label={`${slots.length} simulated readers, one dot each`}>
      {slots.map((p, i) => {
        const lv = p?.level != null ? LEVELS[p.level - 1] : null;
        const dim = selected != null && p != null && p.segment !== selected;
        const conf = !!p?.confused_flag;
        const words = p ? `${p.bio}: ${lv ? `${lv.n}, ${lv.label}` : "no answer"}${conf ? ", may misread the main message" : ""}` : "Waiting for an answer";
        return (
          <button
            key={p?.id ?? `slot-${i}`}
            type="button" tabIndex={-1} disabled={!p}
            onClick={() => p && onSelect(p.segment)}
            aria-label={words}
            title={p ? words : undefined}
            style={{
              background: lv ? `radial-gradient(circle at 30% 25%, rgba(255,255,255,.5), transparent 55%), ${lv.bg}` : undefined,
              transitionDelay: lv ? `${Math.min(i * 3, 500)}ms` : undefined,
            }}
            className={cn(
              "relative grid aspect-square place-items-center rounded-full text-[9px] font-bold leading-none text-black/75 transition-[transform,opacity,background-color,box-shadow] duration-300 ease-out motion-reduce:transition-none sm:text-[10px]",
              lv ? "scale-100 shadow-[0_1px_2px_rgba(0,0,0,0.25),inset_0_0_0_1px_rgba(255,255,255,0.35)]"
                : finished && p ? "scale-90 border border-dashed border-foreground/30"
                : "scale-[0.62] border border-foreground/25 bg-foreground/10",
              conf && "ring-2 ring-[#0A84FF]",
              dim && "opacity-25",
              p && "cursor-pointer hover:scale-110",
            )}
          >
            {lv && <span aria-hidden>{lv.n}</span>}
          </button>
        );
      })}
    </div>
  );
}

export function countLevels(personas: AudiencePersona[]): { levels: number[]; confused: number } {
  const levels = [0, 0, 0, 0, 0];
  let confused = 0;
  for (const p of personas) {
    if (p.level != null) levels[p.level - 1]++;
    if (p.confused_flag) confused++;
  }
  return { levels, confused };
}
