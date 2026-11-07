import { useState } from "react";
import { BadgeCheck, Check, Gavel, Link2, Loader2, Pencil, TriangleAlert, X } from "lucide-react";
import { motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { BAND, SEVERITY_LABEL, SEVERITY_STYLE } from "@/lib/bands";
import type { FindingOut } from "@/lib/api";
import { TipButton } from "@/components/TipButton";
import { Textarea } from "@/components/ui/textarea";
import { formatDate } from "@/lib/hooks";
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

const TIER_LABEL: Record<string, string> = {
  tier0: "Rule-based check", jev: "Jev judgment", claim: "Claim check", visual: "Visual check", logo: "Logo check",
};
const STATUS_LABEL: Record<string, string> = {
  accepted: "Accepted", edited: "Edited", dismissed: "Dismissed", overridden: "Overridden",
};

/** Findings that share a `fix_group` share one rewrite of one region. The lead card carries the fix; the others link to it. */
export interface FixGroupInfo { lead: FindingOut; others: FindingOut[]; isLead: boolean }

export interface FindingCardProps {
  finding: FindingOut;
  group?: FixGroupInfo | null;
  onJump?: (id: number) => void;
  active?: boolean;
  /** false once the item is locked (approved / rejected) */
  canAct?: boolean;
  /** show the Override button (reviewer / approver on a blocking finding) */
  canOverride?: boolean;
  overrideDisabledReason?: string | null;
  onSelect?: (id: number) => void;
  onAccept?: (f: FindingOut) => Promise<void> | void;
  onEdit?: (f: FindingOut, text: string) => Promise<void> | void;
  onDismiss?: (f: FindingOut) => void;
  onOverride?: (f: FindingOut) => void;
}

export function FindingCard({ finding: f, group, onJump, active, canAct = true, canOverride, overrideDisabledReason, onSelect, onAccept, onEdit, onDismiss, onOverride }: FindingCardProps) {
  const band = BAND[f.band];
  const BandIcon = band.icon;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(f.edited_text ?? f.fix ?? f.quote);
  const [busy, setBusy] = useState<"accept" | "edit" | null>(null);
  const shownFix = f.status === "edited" && f.edited_text ? f.edited_text : f.fix;
  const diff = shownFix && f.quote ? wordDiff(f.quote, shownFix) : null;
  const closed = f.status !== "open";
  const linked = !!group && !group.isLead; // fix lives on the lead card of its group
  const codeOf = (x: FindingOut) => x.rule_code ?? x.fact_code ?? "finding";
  const locked = !canAct;
  const code = f.rule_code ?? f.fact_code ?? "Finding";

  async function accept() {
    setBusy("accept");
    try { await onAccept?.(f); } finally { setBusy(null); }
  }
  async function saveEdit() {
    setBusy("edit");
    try { await onEdit?.(f, draft.trim()); setEditing(false); } catch { /* parent toasts; keep editor open */ } finally { setBusy(null); }
  }

  return (
    <article
      aria-label={`${band.label}: ${code}`}
      onClick={() => onSelect?.(f.id)}
      className={cn("glass-strong p-5", active && "ring-2 ring-[#0A84FF]/60", closed && "opacity-80")}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span className={cn("inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-caption font-medium", SEVERITY_STYLE[f.severity])}>
          {f.severity === "blocking" && <TriangleAlert className="size-3.5" strokeWidth={1.75} aria-hidden />}
          {SEVERITY_LABEL[f.severity]}
        </span>
        <span className="rounded-full bg-foreground/8 px-2.5 py-1 text-caption font-medium capitalize">{f.category}</span>
        <span className="font-mono text-caption text-muted-foreground">{code}</span>
        {f.claim_status && (
          <span className={cn("rounded-full px-2.5 py-1 text-caption font-medium capitalize", f.claim_status === "supported" ? "bg-pass/14 text-pass-text" : "bg-human/15 text-human-text")}>
            Claim {f.claim_status}
          </span>
        )}
        {closed && (
          <span className="ml-auto rounded-full bg-foreground/8 px-2.5 py-1 text-caption font-medium">{STATUS_LABEL[f.status] ?? f.status}</span>
        )}
      </div>

      {f.quote && (
        <p className="mt-3 break-words rounded-xl bg-foreground/5 px-3 py-2 text-footnote">
          <span className="text-muted-foreground">In your content: </span>
          <span className="font-medium">&ldquo;{f.quote}&rdquo;</span>
        </p>
      )}

      {f.rule && (
        <blockquote className="mt-3 border-l-2 border-[#0A84FF]/50 pl-3">
          <p className="text-footnote italic text-foreground/85">&ldquo;{f.rule.source_quote || f.rule.statement}&rdquo;</p>
          <p className="mt-0.5 text-caption text-muted-foreground">
            {f.rule.page != null ? `Guideline p.${f.rule.page}` : "Guideline"} · {f.rule.code} v{f.rule.version}
          </p>
        </blockquote>
      )}
      {f.fact && (
        <blockquote className="mt-3 border-l-2 border-[#0A84FF]/50 pl-3">
          <p className="text-footnote italic text-foreground/85">&ldquo;{f.fact.statement}&rdquo;</p>
          <p className="mt-0.5 text-caption text-muted-foreground">
            Approved fact {f.fact.code} · {f.fact.source}
            {f.fact.valid_until && <> · valid until {formatDate(f.fact.valid_until)}</>}
            {f.fact.expired && <span className="ml-1.5 rounded-full bg-flag/12 px-2 py-0.5 font-medium text-flag-text">expired</span>}
          </p>
        </blockquote>
      )}

      <div className="mt-4">
        <div className="mb-1.5 flex items-center justify-between text-caption">
          <span className={cn("inline-flex items-center gap-1 font-medium", band.text)}><BandIcon className="size-3.5" strokeWidth={1.75} aria-hidden /> {band.label}</span>
          <span className="tabular-nums text-muted-foreground">
            {f.probability != null ? `p = ${f.probability.toFixed(2)}` : TIER_LABEL[f.tier] ?? f.tier}
          </span>
        </div>
        {f.probability != null && (
          <div className="h-1.5 overflow-hidden rounded-full bg-foreground/8" role="meter" aria-valuemin={0} aria-valuemax={1} aria-valuenow={f.probability} aria-label="Probability of a problem">
            <motion.div className={cn("h-full rounded-full", band.bar)} initial={{ width: 0 }} animate={{ width: `${f.probability * 100}%` }} transition={{ type: "spring", stiffness: 140, damping: 22 }} />
          </div>
        )}
      </div>

      <p className="mt-4 text-body">{f.explanation}</p>

      {group && group.isLead && group.others.length > 0 && (
        <p data-testid="fix-group-lead" className="mt-3 flex items-start gap-2 rounded-xl bg-[#0A84FF]/10 px-3 py-2 text-footnote">
          <Link2 className="mt-0.5 size-4 shrink-0 text-[#0A84FF]" strokeWidth={1.75} aria-hidden />
          <span>This fix also resolves {group.others.length} other finding{group.others.length === 1 ? "" : "s"}:{" "}
            {group.others.map((o, i) => (
              <span key={o.id}>{i > 0 && ", "}<button type="button" onClick={(e) => { e.stopPropagation(); onJump?.(o.id); }} className="font-mono text-caption underline underline-offset-2">{codeOf(o)}</button></span>
            ))}.</span>
        </p>
      )}
      {linked && (
        <p data-testid="fix-group-linked" className="mt-3 flex items-start gap-2 rounded-xl bg-[#0A84FF]/10 px-3 py-2 text-footnote">
          <Link2 className="mt-0.5 size-4 shrink-0 text-[#0A84FF]" strokeWidth={1.75} aria-hidden />
          <span>Same sentence as <button type="button" onClick={(e) => { e.stopPropagation(); onJump?.(group!.lead.id); }} className="font-mono text-caption underline underline-offset-2">{codeOf(group!.lead)}</button>.{" "}
            {f.status === "open" ? "One rewrite covers both, so accepting the fix there resolves this finding too." : "Resolved together with it."}</span>
        </p>
      )}

      {f.status === "dismissed" && (
        <p className="mt-3 rounded-xl bg-foreground/5 p-3 text-footnote text-muted-foreground">
          Dismissed as <span className="font-medium text-foreground">{(f.dismiss_reason ?? "other").replace(/_/g, " ")}</span>
          {f.dismiss_note ? <>: {f.dismiss_note}</> : null}
        </p>
      )}

      {editing ? (
        <div className="mt-4 space-y-2">
          <label htmlFor={`edit-${f.id}`} className="text-caption font-medium text-muted-foreground">Your replacement text</label>
          <Textarea id={`edit-${f.id}`} value={draft} onChange={(e) => setDraft(e.target.value)} rows={3} className="rounded-2xl bg-background/60 text-footnote" />
          <div className="flex gap-2">
            <Button size="sm" disabled={!draft.trim() || busy === "edit"} onClick={saveEdit} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">
              {busy === "edit" ? <Loader2 className="animate-spin" /> : <Check strokeWidth={2} />} Save edit
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)} className="rounded-full">Cancel</Button>
          </div>
        </div>
      ) : linked ? null : diff ? (
        <div className="mt-4 overflow-hidden rounded-2xl bg-foreground/5">
          <div className="grid gap-px bg-border/60 sm:grid-cols-2">
            <div className="bg-[var(--glass-strong-bg)] p-3.5">
              <p className="mb-1 text-caption font-medium text-muted-foreground">Before</p>
              <p className="text-footnote leading-6">{diff.before.map((w, i) => w.d ? <del key={i} className="rounded bg-flag/15 px-0.5 text-foreground decoration-flag-text/70">{w.t}</del> : <span key={i}>{w.t}</span>)}</p>
            </div>
            <div className="bg-[var(--glass-strong-bg)] p-3.5">
              <p className="mb-1 flex items-center justify-between text-caption font-medium text-muted-foreground">
                After
                {f.status === "edited" ? (
                  <span className="rounded-full bg-foreground/8 px-2 py-0.5 text-foreground">Your edit</span>
                ) : f.fix_verified ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-pass/14 px-2 py-0.5 text-pass-text"><BadgeCheck className="size-3.5" strokeWidth={1.75} aria-hidden /> Verified</span>
                ) : (
                  <span className="inline-flex items-center gap-1 rounded-full bg-human/15 px-2 py-0.5 text-human-text">Not verified</span>
                )}
              </p>
              <p className="text-footnote leading-6">{diff.after.map((w, i) => w.d ? <ins key={i} className="rounded bg-pass/18 px-0.5 text-foreground no-underline">{w.t}</ins> : <span key={i}>{w.t}</span>)}</p>
            </div>
          </div>
        </div>
      ) : f.status === "open" || f.status === "dismissed" ? (
        f.status === "open" && <p role="status" data-testid="manual-fix" className="mt-4 flex items-start gap-2 rounded-2xl bg-human/12 p-3.5 text-footnote text-human-text"><TriangleAlert className="mt-0.5 size-4 shrink-0" strokeWidth={1.75} aria-hidden /><span><span className="font-semibold">Manual fix needed.</span> We could not produce a safe rewrite for this, so use Edit to write your own.</span></p>
      ) : null}

      {!editing && f.status === "open" && (
        <div className="mt-4 flex flex-wrap gap-2">
          {!linked && (
            <Button size="sm" disabled={!f.fix || locked || busy !== null} onClick={(e) => { e.stopPropagation(); void accept(); }} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">
              {busy === "accept" ? <Loader2 className="animate-spin" /> : <Check strokeWidth={2} />} {group && group.others.length > 0 ? `Accept fix for ${group.others.length + 1} findings` : "Accept fix"}
            </Button>
          )}
          {!linked && <Button size="sm" variant="secondary" disabled={locked} onClick={(e) => { e.stopPropagation(); setDraft(f.fix ?? f.quote); setEditing(true); }} className="rounded-full"><Pencil strokeWidth={1.75} /> Edit</Button>}
          <Button size="sm" variant="ghost" disabled={locked} onClick={(e) => { e.stopPropagation(); onDismiss?.(f); }} className="rounded-full text-muted-foreground"><X strokeWidth={1.75} /> Dismiss</Button>
          {canOverride && f.severity === "blocking" && (
            <TipButton
              size="sm" variant="outline" reason={overrideDisabledReason}
              onClick={(e) => { e.stopPropagation(); onOverride?.(f); }}
              className="rounded-full"
            >
              <Gavel strokeWidth={1.75} /> Override
            </TipButton>
          )}
        </div>
      )}
    </article>
  );
}
