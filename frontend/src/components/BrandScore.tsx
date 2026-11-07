import { useId, useState } from "react";
import { AlertTriangle, ChevronDown, CircleCheck, Target, TrendingDown, TrendingUp, XCircle } from "lucide-react";
import { SCORE_LABEL, scoreBand, type ScoreBand, type ScoreOut } from "@/lib/score";
import { cn } from "@/lib/utils";

const STYLE: Record<ScoreBand, { pill: string; text: string; stroke: string; Icon: typeof CircleCheck }> = {
  good: { pill: "bg-pass/14 text-pass-text", text: "text-pass-text", stroke: "#30D158", Icon: CircleCheck },
  fair: { pill: "bg-human/15 text-human-text", text: "text-human-text", stroke: "#FF9F0A", Icon: AlertTriangle },
  poor: { pill: "bg-flag/12 text-flag-text", text: "text-flag-text", stroke: "#FF453A", Icon: XCircle },
};

/** Small pill: icon + number (+ label). Never colour alone. */
export function ScoreBadge({ score, band, showLabel = false, className }: {
  score: number; band?: ScoreBand | null; showLabel?: boolean; className?: string;
}) {
  const b = band ?? scoreBand(score);
  const s = STYLE[b];
  return (
    <span
      data-testid="score-badge"
      title={`Brand Score ${score} of 100: ${SCORE_LABEL[b]}`}
      className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-caption font-semibold tabular-nums", s.pill, className)}
    >
      <s.Icon className="size-3.5" strokeWidth={1.75} aria-hidden />
      {score}
      {showLabel ? <span className="font-medium"> · {SCORE_LABEL[b]}</span> : <span className="sr-only"> of 100, {SCORE_LABEL[b]}</span>}
    </span>
  );
}

function Ring({ score, band }: { score: number; band: ScoreBand }) {
  const r = 30;
  const c = 2 * Math.PI * r;
  return (
    <svg viewBox="0 0 72 72" className="size-[72px] shrink-0 -rotate-90" aria-hidden>
      <circle cx="36" cy="36" r={r} fill="none" strokeWidth="7" className="stroke-foreground/10" />
      <circle cx="36" cy="36" r={r} fill="none" strokeWidth="7" strokeLinecap="round" stroke={STYLE[band].stroke}
        strokeDasharray={c} strokeDashoffset={c * (1 - score / 100)} style={{ transition: "stroke-dashoffset 300ms ease-out" }} />
    </svg>
  );
}

/** The item header's score: big number, band label + icon, target for the channel, change from the previous
 *  version, and the formula behind it (expand). */
export function ScoreCard({ score, target, channel, previous }: {
  score: ScoreOut; target: number; channel: string; previous?: { n: number; score: number } | null;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const s = STYLE[score.band];
  const below = score.score < target;
  const delta = previous ? score.score - previous.score : 0;
  return (
    <section aria-label="Brand Score" data-testid="brand-score" className="glass-strong mt-4 !rounded-2xl px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="relative" title={score.formula}>
          <Ring score={score.score} band={score.band} />
          <span className="absolute inset-0 flex items-center justify-center text-title2 font-semibold tabular-nums tracking-tight" data-testid="brand-score-value">
            {score.score}
          </span>
        </div>
        <div className="min-w-0 flex-1 basis-48">
          <p className="text-footnote text-muted-foreground">Brand Score <span className="sr-only">{score.score} of 100</span></p>
          <p className={cn("inline-flex items-center gap-1.5 text-headline font-semibold", s.text)}>
            <s.Icon className="size-4" strokeWidth={1.75} aria-hidden /> {score.label}
          </p>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-caption text-muted-foreground">
            <span className="inline-flex items-center gap-1" data-testid="brand-score-target">
              <Target className="size-3.5" strokeWidth={1.5} aria-hidden />
              {below ? `Below the ${channel} target of ${target}` : `Meets the ${channel} target of ${target}`}
            </span>
            {previous && (
              <span className="inline-flex items-center gap-1 tabular-nums" data-testid="brand-score-delta">
                {delta >= 0 ? <TrendingUp className="size-3.5" strokeWidth={1.5} aria-hidden /> : <TrendingDown className="size-3.5" strokeWidth={1.5} aria-hidden />}
                v{previous.n} {previous.score} → {score.score} ({delta >= 0 ? "+" : ""}{delta})
              </span>
            )}
          </p>
        </div>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={id}
          onClick={() => setOpen((o) => !o)}
          className="inline-flex min-h-9 items-center gap-1 rounded-full bg-foreground/8 px-3 py-1.5 text-caption font-medium hover:bg-foreground/12"
        >
          How it's worked out <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} strokeWidth={1.75} aria-hidden />
        </button>
      </div>
      {open && (
        <div id={id} className="mt-3 border-t border-foreground/10 pt-3 text-footnote">
          <p className="break-words font-medium tabular-nums" data-testid="brand-score-formula">{score.formula}</p>
          <p className="mt-1 text-caption text-muted-foreground">
            Start at 100 and subtract for every finding still open (accepted fixes count until the next version):
            blocking 25 (12 if it needs a human look), major 10 (5), minor 3 (1). Dismissed and overridden findings
            don't count. It summarises the findings; it is not an accuracy number. Below the target, submitting asks
            for a short reason; only blocking findings stop a submit.
          </p>
        </div>
      )}
    </section>
  );
}
