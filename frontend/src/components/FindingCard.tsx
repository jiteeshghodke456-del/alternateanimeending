import { BadgeCheck, Check, Pencil, TriangleAlert, X } from "lucide-react";
import { motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { BAND, SEVERITY_LABEL, SEVERITY_STYLE } from "@/lib/bands";
import type { MockFinding } from "@/lib/mock";
import { cn } from "@/lib/utils";

/** Word-level diff (LCS). Returns tokens tagged same / del / add. */
export function wordDiff(a: string, b: string) {
  const x = a.split(/(\s+)/).filter(Boolean), y = b.split(/(\s+)/).filter(Boolean);
  const dp = Array.from({ length: x.length + 1 }, () => new Array<number>(y.length + 1).fill(0));
  for (let i = x.length - 1; i >= 0; i--) for (let j = y.length - 1; j >= 0; j--)
    dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const before: { t: string; d: boolean }[] = [], after: { t: string; d: boolean }[] = [];
  let i = 0, j = 0;
  while (i < x.length && j < y.length) {
    if (x[i] === y[j]) { before.push({ t: x[i], d: false }); after.push({ t: y[j], d: false }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) before.push({ t: x[i++], d: true });
    else after.push({ t: y[j++], d: true });
  }
  while (i < x.length) before.push({ t: x[i++], d: true });
  while (j < y.length) after.push({ t: y[j++], d: true });
  // a single space between two changed words belongs to the change, so highlights read as one phrase
  const merge = (arr: { t: string; d: boolean }[]) =>
    arr.map((w, k) => (!w.d && /^\s+$/.test(w.t) && arr[k - 1]?.d && arr[k + 1]?.d ? { ...w, d: true } : w));
  return { before: merge(before), after: merge(after) };
}

export interface FindingCardProps {
  finding: MockFinding;
  active?: boolean;
  onAccept?: (id: string) => void;
  onEdit?: (id: string) => void;
  onDismiss?: (id: string) => void;
}

export function FindingCard({ finding: f, active, onAccept, onEdit, onDismiss }: FindingCardProps) {
  const band = BAND[f.band];
  const BandIcon = band.icon;
  const diff = f.fix ? wordDiff(f.original, f.fix) : null;
  const closed = f.status !== "open";
  return (
    <motion.article
      layout
      aria-label={`${band.label}: ${f.rule_id}`}
      className={cn("glass-strong p-5", active && "ring-2 ring-[#0A84FF]/60", closed && "opacity-70")}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span className={cn("inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-caption font-medium", SEVERITY_STYLE[f.severity])}>
          {f.severity === "blocking" && <TriangleAlert className="size-3.5" strokeWidth={1.75} aria-hidden />}
          {SEVERITY_LABEL[f.severity]}
        </span>
        <span className="rounded-full bg-foreground/8 px-2.5 py-1 text-caption font-medium">{f.category}</span>
        <span className="font-mono text-caption text-muted-foreground">{f.rule_id}</span>
        {closed && <span className="ml-auto rounded-full bg-foreground/8 px-2.5 py-1 text-caption font-medium capitalize">{f.status}</span>}
      </div>

      <blockquote className="mt-3 border-l-2 border-[#0A84FF]/50 pl-3">
        <p className="text-footnote italic text-foreground/85">&ldquo;{f.quote}&rdquo;</p>
        <p className="mt-0.5 text-caption text-muted-foreground">Guideline p.{f.page}</p>
      </blockquote>

      <div className="mt-4">
        <div className="mb-1.5 flex items-center justify-between text-caption">
          <span className={cn("inline-flex items-center gap-1 font-medium", band.text)}><BandIcon className="size-3.5" strokeWidth={1.75} aria-hidden /> {band.label}</span>
          <span className="tabular-nums text-muted-foreground">p = {f.probability.toFixed(2)}</span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-foreground/8" role="meter" aria-valuemin={0} aria-valuemax={1} aria-valuenow={f.probability} aria-label="Probability of a problem">
          <motion.div className={cn("h-full rounded-full", band.bar)} initial={{ width: 0 }} animate={{ width: `${f.probability * 100}%` }} transition={{ type: "spring", stiffness: 140, damping: 22 }} />
        </div>
      </div>

      <p className="mt-4 text-body">{f.explanation}</p>

      {diff ? (
        <div className="mt-4 overflow-hidden rounded-2xl bg-foreground/5">
          <div className="grid gap-px bg-border/60 sm:grid-cols-2">
            <div className="bg-[var(--glass-strong-bg)] p-3.5">
              <p className="mb-1 text-caption font-medium text-muted-foreground">Before</p>
              <p className="text-footnote leading-6">{diff.before.map((w, i) => w.d ? <del key={i} className="rounded bg-flag/15 px-0.5 text-foreground decoration-flag-text/70">{w.t}</del> : <span key={i}>{w.t}</span>)}</p>
            </div>
            <div className="bg-[var(--glass-strong-bg)] p-3.5">
              <p className="mb-1 flex items-center justify-between text-caption font-medium text-muted-foreground">
                After
                {f.fix_verified ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-pass/14 px-2 py-0.5 text-pass-text"><BadgeCheck className="size-3.5" strokeWidth={1.75} aria-hidden /> Verified</span>
                ) : (
                  <span className="inline-flex items-center gap-1 rounded-full bg-human/15 px-2 py-0.5 text-human-text">Not verified</span>
                )}
              </p>
              <p className="text-footnote leading-6">{diff.after.map((w, i) => w.d ? <ins key={i} className="rounded bg-pass/18 px-0.5 text-foreground no-underline">{w.t}</ins> : <span key={i}>{w.t}</span>)}</p>
            </div>
          </div>
        </div>
      ) : (
        <p className="mt-4 rounded-2xl bg-human/12 p-3.5 text-footnote text-human-text">Manual fix needed. We could not produce a safe rewrite.</p>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        <Button size="sm" disabled={!f.fix || closed} onClick={() => onAccept?.(f.id)} className="rounded-full bg-[#0A84FF] text-white hover:bg-[#0A84FF]/90"><Check strokeWidth={2} /> Accept</Button>
        <Button size="sm" variant="secondary" disabled={closed} onClick={() => onEdit?.(f.id)} className="rounded-full"><Pencil strokeWidth={1.75} /> Edit</Button>
        <Button size="sm" variant="ghost" disabled={closed} onClick={() => onDismiss?.(f.id)} className="rounded-full text-muted-foreground"><X strokeWidth={1.75} /> Dismiss</Button>
      </div>
    </motion.article>
  );
}
