import { BadgeCheck, ScanSearch, TriangleAlert } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { FindingOut, ImageBox, LogoCheck, VersionOut } from "@/lib/api";
import { withToken } from "@/lib/api";
import { cn } from "@/lib/utils";

type Verdict = "genuine" | "altered" | "not_found";
type Pt = [number, number];

export interface LogoShape {
  key: string;
  verdict: Verdict;
  quad: Pt[] | null; // fractions of the image size
  findingId?: number;
  reason?: string;
}

const STYLE: Record<Verdict, { label: string; icon: LucideIcon; stroke: string; text: string; chip: string }> = {
  genuine: { label: "Genuine logo", icon: BadgeCheck, stroke: "#30D158", text: "text-pass-text", chip: "bg-pass/14 text-pass-text" },
  altered: { label: "Altered logo", icon: TriangleAlert, stroke: "#FF453A", text: "text-flag-text", chip: "bg-flag/12 text-flag-text" },
  not_found: { label: "Logo not found", icon: ScanSearch, stroke: "#FF9F0A", text: "text-human-text", chip: "bg-human/15 text-human-text" },
};

function boxToQuad(b: ImageBox): Pt[] {
  return [[b.left, b.top], [b.right, b.top], [b.right, b.bottom], [b.left, b.bottom]];
}
function validQuad(q: unknown): q is Pt[] {
  return Array.isArray(q) && q.length === 4 && q.every((p) => Array.isArray(p) && p.length === 2 && p.every((n) => typeof n === "number" && Number.isFinite(n)));
}

/** Combine the version's logo checks (local OpenCV) and any finding that carries a box/quad into drawable shapes. */
export function logoShapes(version: VersionOut, findings: FindingOut[]): LogoShape[] {
  const checks = (Array.isArray(version.stats.logo) ? version.stats.logo : []) as LogoCheck[];
  const unitIdx = (id: number | null) => version.units.find((u) => u.id === id)?.idx;
  const shapes: LogoShape[] = [];
  const covered = new Set<number>();

  checks.forEach((c, i) => {
    if (!c.verdict) return;
    const f = findings.find((x) => x.tier === "logo" && unitIdx(x.unit_id) === c.unit_idx) ?? findings.find((x) => x.tier === "logo");
    const quad = validQuad(c.quad) ? c.quad : validQuad(f?.quad) ? f!.quad! : c.box ? boxToQuad(c.box) : f?.box ? boxToQuad(f.box) : null;
    if (f) covered.add(f.id);
    shapes.push({ key: `check-${i}`, verdict: c.verdict, quad, findingId: f?.id, reason: c.reason });
  });
  for (const f of findings) {
    if (covered.has(f.id) || !(validQuad(f.quad) || f.box)) continue;
    shapes.push({ key: `finding-${f.id}`, verdict: f.band === "pass" ? "genuine" : "altered", quad: validQuad(f.quad) ? f.quad : boxToQuad(f.box!), findingId: f.id, reason: f.explanation });
  }
  return shapes;
}

/** The image, with the projected approved-logo outline drawn on top. The SVG shares the image box, so it scales with it. */
export function ImageWithLogo({ src, alt, shapes, activeFindingId, onSelectFinding }: {
  src: string; alt: string; shapes: LogoShape[]; activeFindingId?: number; onSelectFinding?: (id: number) => void;
}) {
  const drawn = shapes.filter((s) => s.quad);
  return (
    <div className="space-y-2.5">
      <div className="relative mx-auto w-fit max-w-full overflow-hidden rounded-2xl border border-border bg-foreground/5">
        <img src={withToken(src)} alt={alt} className="block h-auto max-h-[420px] w-auto max-w-full" />
        {drawn.length > 0 && (
          <svg className="absolute inset-0 size-full" viewBox="0 0 1 1" preserveAspectRatio="none" role="img" aria-label={drawn.map((s) => STYLE[s.verdict].label).join(", ") + " outlined on the image"}>
            {drawn.map((s) => {
              const st = STYLE[s.verdict];
              const active = s.findingId != null && s.findingId === activeFindingId;
              return (
                <g key={s.key} onClick={() => s.findingId != null && onSelectFinding?.(s.findingId)} className={s.findingId != null ? "cursor-pointer" : undefined}>
                  <polygon
                    points={s.quad!.map((p) => `${p[0]},${p[1]}`).join(" ")}
                    fill={st.stroke} fillOpacity={active ? 0.28 : 0.14}
                    stroke={st.stroke} strokeWidth={active ? 4.5 : 2.5} strokeLinejoin="round"
                    vectorEffect="non-scaling-stroke"
                    style={{ transition: "fill-opacity .2s, stroke-width .2s", filter: active ? `drop-shadow(0 0 6px ${st.stroke})` : undefined }}
                  />
                </g>
              );
            })}
          </svg>
        )}
        {drawn.map((s) => {
          const st = STYLE[s.verdict];
          const Icon = st.icon;
          const xs = s.quad!.map((p) => p[0]), ys = s.quad!.map((p) => p[1]);
          const top = Math.min(...ys), left = Math.min(...xs);
          const below = top < 0.1;
          return (
            <span
              key={`lbl-${s.key}`}
              className={cn("pointer-events-none absolute inline-flex items-center gap-1 whitespace-nowrap rounded-full bg-white/92 px-2 py-0.5 text-caption font-semibold shadow-sm dark:bg-neutral-900/90", st.text)}
              style={{ left: `${Math.min(left, 0.6) * 100}%`, top: `${(below ? Math.max(...ys) : top) * 100}%`, transform: below ? "translateY(4px)" : "translateY(calc(-100% - 4px))" }}
            >
              <Icon className="size-3.5" strokeWidth={2} aria-hidden /> {st.label}
            </span>
          );
        })}
      </div>
      {shapes.length > 0 && (
        <ul className="space-y-1.5" aria-label="Logo check">
          {shapes.map((s) => {
            const st = STYLE[s.verdict];
            const Icon = st.icon;
            const active = s.findingId != null && s.findingId === activeFindingId;
            return (
              <li key={`n-${s.key}`}>
                <button
                  type="button" disabled={s.findingId == null} onClick={() => s.findingId != null && onSelectFinding?.(s.findingId)}
                  className={cn("flex w-full items-start gap-2 rounded-xl px-3 py-2 text-left text-footnote", st.chip, active && "ring-2 ring-[#0A84FF]/60", s.findingId != null && "cursor-pointer")}
                >
                  <Icon className="mt-0.5 size-4 shrink-0" strokeWidth={1.75} aria-hidden />
                  <span><span className="font-semibold">{st.label}.</span>{" "}
                    <span className="text-foreground/85">{s.reason ?? ""}{!s.quad && s.verdict === "altered" ? " The outline couldn't be placed on the image." : ""}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
