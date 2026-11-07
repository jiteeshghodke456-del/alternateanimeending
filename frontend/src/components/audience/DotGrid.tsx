import { Check, CircleHelp, X } from "lucide-react";
import type { AudiencePersona, AudienceSignal } from "@/lib/api";
import { cn } from "@/lib/utils";

export const TOTAL_DOTS = 240;

/** Colour + glyph + words for each dot kind. The glyph is dark so it passes 3:1 on all three fills. */
export const SIGNAL = {
  distrust: { label: "Distrusts it", bg: "#FF453A", glyph: X },
  unsure: { label: "Confused or unsure", bg: "#FF9F0A", glyph: CircleHelp },
  ok: { label: "Seems fine", bg: "#30D158", glyph: Check },
} as const;
type DotKind = keyof typeof SIGNAL | "waiting" | "none";

function kindOf(p: AudiencePersona | undefined, finished: boolean): DotKind {
  const s: AudienceSignal | null | undefined = p?.signal;
  if (!s) return finished && p ? "none" : "waiting";
  return s === "distrust" ? "distrust" : s === "ok" ? "ok" : "unsure";
}

export function DotLegend({ counts, className }: { counts: Record<string, number>; className?: string }) {
  return (
    <ul className={cn("flex flex-wrap gap-x-4 gap-y-1.5 text-caption", className)} aria-label="Legend">
      {(Object.keys(SIGNAL) as (keyof typeof SIGNAL)[]).map((k) => {
        const G = SIGNAL[k].glyph;
        return (
          <li key={k} className="inline-flex items-center gap-1.5">
            <span className="grid size-[18px] place-items-center rounded-full" style={{ background: SIGNAL[k].bg }}>
              <G className="size-3 text-black/75" strokeWidth={3} aria-hidden />
            </span>
            <span className="font-medium">{SIGNAL[k].label}</span>
            <span className="tabular-nums text-muted-foreground">{counts[k] ?? 0}</span>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * 240 small glass dots, one per simulated reader, grouped by segment. Each dot lights up when its answer arrives.
 * Colour is always paired with a glyph (x, ?, tick) and a legend.
 */
export function DotGrid({ personas, segmentOrder, finished, selected, onSelect }: {
  personas: AudiencePersona[]; segmentOrder: string[]; finished: boolean;
  selected: string | null; onSelect: (segment: string) => void;
}) {
  const rank = new Map(segmentOrder.map((k, i) => [k, i]));
  const sorted = [...personas].sort((a, b) => (rank.get(a.segment) ?? 99) - (rank.get(b.segment) ?? 99) || a.idx - b.idx);
  const slots: (AudiencePersona | undefined)[] = Array.from({ length: Math.max(TOTAL_DOTS, sorted.length) }, (_, i) => sorted[i]);

  return (
    <div className="grid grid-cols-[repeat(15,minmax(0,1fr))] gap-[5px] sm:grid-cols-[repeat(20,minmax(0,1fr))] sm:gap-[4px]" role="group" aria-label="240 simulated readers, one dot each">
      {slots.map((p, i) => {
        const k = kindOf(p, finished);
        const sig = k === "distrust" || k === "unsure" || k === "ok" ? SIGNAL[k] : null;
        const G = sig?.glyph;
        const dim = selected != null && p != null && p.segment !== selected;
        return (
          <button
            key={p?.id ?? `slot-${i}`}
            type="button" tabIndex={-1} disabled={!p}
            onClick={() => p && onSelect(p.segment)}
            aria-label={p ? `${p.bio}: ${sig ? sig.label : "no answer"}` : "Waiting for an answer"}
            title={p ? `${p.bio}\n${sig ? sig.label : "No answer"}` : undefined}
            style={{
              background: sig ? `radial-gradient(circle at 30% 25%, rgba(255,255,255,.55), transparent 55%), ${sig.bg}` : undefined,
              transitionDelay: sig ? `${Math.min(i * 3, 500)}ms` : undefined,
            }}
            className={cn(
              "relative grid aspect-square place-items-center rounded-full transition-[transform,opacity,background-color,box-shadow] duration-300 ease-out motion-reduce:transition-none",
              sig ? "scale-100 shadow-[0_1px_2px_rgba(0,0,0,0.25),inset_0_0_0_1px_rgba(255,255,255,0.35)]"
                : k === "none" ? "scale-90 border border-dashed border-foreground/30"
                : "scale-[0.62] border border-foreground/25 bg-foreground/10",
              dim && "opacity-25",
              !dim && selected != null && "ring-2 ring-[#0A84FF]/70 ring-offset-1 ring-offset-transparent",
              p && "cursor-pointer hover:scale-110",
            )}
          >
            {G && <G className="size-[62%] text-black/75" strokeWidth={3.2} aria-hidden />}
          </button>
        );
      })}
    </div>
  );
}

export function countKinds(personas: AudiencePersona[]): Record<string, number> {
  const out: Record<string, number> = { distrust: 0, unsure: 0, ok: 0 };
  for (const p of personas) {
    if (p.signal === "distrust") out.distrust++;
    else if (p.signal === "ok") out.ok++;
    else if (p.signal) out.unsure++;
  }
  return out;
}
