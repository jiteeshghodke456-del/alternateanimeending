import { useState } from "react";
import { BadgeCheck, BookPlus, Check, Gavel, Info, Link2, Loader2, Pencil, TriangleAlert, X } from "lucide-react";
import { motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { BAND, SEVERITY_LABEL, SEVERITY_STYLE } from "@/lib/bands";
import type { FindingOut, ItemState } from "@/lib/api";
import { TipButton } from "@/components/TipButton";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { formatDate } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { RegulationChip, RegulationCitations } from "@/components/RegulationCitation"; // rule packs
import { PrecedentLine } from "@/components/Precedent"; // precedent memory

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
  tier0: "Rule-based check", jev: "Jev judgment", claim: "Claim check", visual: "Visual check", logo: "Logo check", provenance: "Content Credentials (local)",
};
const STATUS_LABEL: Record<string, string> = {
  accepted: "Accepted", edited: "Edited", dismissed: "Dismissed", overridden: "Overridden",
};

/** Read-only view: the item is approved / rejected, or the viewer isn't its creator, or it's an older version. */
export interface ReadOnlyInfo { itemState: ItemState; nextVersionN: number | null }

/** A quiet, neutral sentence saying where a finding ended up (shown instead of the action buttons). */
export function findingOutcome(f: FindingOut, ro: ReadOnlyInfo): string {
  const reason = (f.dismiss_reason ?? "other").replace(/_/g, " ");
  switch (f.status) {
    case "dismissed": return `Dismissed: ${reason}${f.dismiss_note ? ` (${f.dismiss_note})` : ""}`;
    case "overridden": return "Overridden by a reviewer";
    case "accepted": return ro.nextVersionN ? `Fixed in v${ro.nextVersionN}` : "Fix accepted";
    case "edited": return ro.nextVersionN ? `Fixed in v${ro.nextVersionN} (creator's edit)` : "Edited by the creator";
    default:
      if (ro.itemState === "approved") return "Left open at approval";
      if (ro.itemState === "rejected") return "Left open when rejected";
      if (ro.nextVersionN) return `Left as it was in v${ro.nextVersionN}`;
      return "Open: the creator decides how to handle it";
  }
}

/** Findings that share a `fix_group` share one rewrite of one region. The lead card carries the fix; the others link to it. */
export interface FixGroupInfo { lead: FindingOut; others: FindingOut[]; isLead: boolean }

export interface FindingCardProps {
  finding: FindingOut;
  group?: FixGroupInfo | null;
  onJump?: (id: number) => void;
  active?: boolean;
  /** false while actions are briefly unavailable (a check is running) */
  canAct?: boolean;
  /** set when the viewer can only read this finding: no Accept / Edit / Dismiss, no fix banner, a neutral outcome line */
  readOnly?: ReadOnlyInfo | null;
  /** show the Override button (reviewer / approver on a blocking finding) */
  canOverride?: boolean;
  overrideDisabledReason?: string | null;
  onSelect?: (id: number) => void;
  onAccept?: (f: FindingOut) => Promise<void> | void;
  onEdit?: (f: FindingOut, text: string) => Promise<void> | void;
  onDismiss?: (f: FindingOut) => void;
  onOverride?: (f: FindingOut) => void;
  /** "Add as fact" on an unverified figure: saves an approved fact with its source, then closes the finding */
  onAddFact?: (f: FindingOut, fact: { statement: string; source: string; valid_until: string | null }) => Promise<void> | void;
}

/** A figure no brand or regulation rule asks evidence for: a reminder to verify, never a rewrite (engine.py). */
export const isUnverifiedFigure = (f: FindingOut) => f.claim_status === "unsupported" && !f.rule_code && f.tier === "claim";

export function FindingCard({ finding: f, group, onJump, active, canAct = true, readOnly = null, canOverride, overrideDisabledReason, onSelect, onAccept, onEdit, onDismiss, onOverride, onAddFact }: FindingCardProps) {
  const band = BAND[f.band];
  const BandIcon = band.icon;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(f.edited_text ?? f.fix ?? f.quote);
  const [busy, setBusy] = useState<"accept" | "edit" | "fact" | null>(null);
  const unverified = isUnverifiedFigure(f);
  const [addingFact, setAddingFact] = useState(false);
  const [factText, setFactText] = useState(f.quote ?? "");
  const [factSource, setFactSource] = useState("");
  const [factUntil, setFactUntil] = useState("");
  const shownFix = f.status === "edited" && f.edited_text ? f.edited_text : f.fix;
  const diff = shownFix && f.quote ? wordDiff(f.quote, shownFix) : null;
  const closed = f.status !== "open";
  const linked = !!group && !group.isLead; // fix lives on the lead card of its group
  const codeOf = (x: FindingOut) => x.rule_code ?? x.fact_code ?? x.category;
  const locked = !canAct;
  const code = f.rule_code ?? f.fact_code ?? null; // claim findings in a brand with no rules have neither
  const textual = f.span_start != null && f.span_end != null; // image / whole-document findings have no passage to rewrite
  const overrideBtn = canOverride && f.severity === "blocking" && (
    <TipButton
      size="sm" variant="outline" reason={overrideDisabledReason}
      onClick={(e) => { e.stopPropagation(); onOverride?.(f); }}
      className="rounded-full"
    >
      <Gavel strokeWidth={1.75} /> Override
    </TipButton>
  );

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
      aria-label={`${band.label}: ${code ?? f.category}`}
      onClick={() => onSelect?.(f.id)}
      className={cn("glass-strong p-5", active && "ring-2 ring-[var(--accent-vivid)]/60", closed && "opacity-80")}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span className={cn("inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-caption font-medium", SEVERITY_STYLE[f.severity])}>
          {f.severity === "blocking" && <TriangleAlert className="size-3.5" strokeWidth={1.75} aria-hidden />}
          {SEVERITY_LABEL[f.severity]}
        </span>
        <span className="rounded-full bg-foreground/8 px-2.5 py-1 text-caption font-medium capitalize">{f.category}</span>
        {code && <span className="font-mono text-caption text-muted-foreground">{code}</span>}
        <RegulationChip f={f} />
        {f.claim_status && (
          <span className={cn("rounded-full px-2.5 py-1 text-caption font-medium capitalize", f.claim_status === "supported" ? "bg-pass/14 text-pass-text" : "bg-human/15 text-human-text")}>
            {unverified ? "Not in your facts yet" : `Claim ${f.claim_status}`}
          </span>
        )}
        {closed && !readOnly && (
          <span className="ml-auto rounded-full bg-foreground/8 px-2.5 py-1 text-caption font-medium">{STATUS_LABEL[f.status] ?? f.status}</span>
        )}
      </div>

      {f.quote && (
        <p className="mt-3 break-words rounded-xl bg-foreground/5 px-3 py-2 text-footnote">
          <span className="text-muted-foreground">In your content: </span>
          <span className="font-medium">&ldquo;{f.quote}&rdquo;</span>
        </p>
      )}

      {f.rule && !f.rule.citation && (
        <blockquote className="mt-3 border-l-2 border-[var(--accent-vivid)]/50 pl-3">
          <p className="text-footnote italic text-foreground/85">&ldquo;{f.rule.source_quote || f.rule.statement}&rdquo;</p>
          <p className="mt-0.5 text-caption text-muted-foreground">
            {f.rule.page != null ? `Guideline p.${f.rule.page}` : "Guideline"} · {f.rule.code} v{f.rule.version}
          </p>
        </blockquote>
      )}
      <RegulationCitations f={f} />
      {f.fact && (
        <blockquote className="mt-3 border-l-2 border-[var(--accent-vivid)]/50 pl-3">
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
      <PrecedentLine finding={f} />

      {group && group.isLead && group.others.length > 0 && (
        <p data-testid="fix-group-lead" className="mt-3 flex items-start gap-2 rounded-xl bg-[var(--accent-vivid)]/10 px-3 py-2 text-footnote">
          <Link2 className="mt-0.5 size-4 shrink-0 text-[var(--accent-vivid)]" strokeWidth={1.75} aria-hidden />
          <span>This fix also resolves {group.others.length} other finding{group.others.length === 1 ? "" : "s"}:{" "}
            {group.others.map((o, i) => (
              <span key={o.id}>{i > 0 && ", "}<button type="button" onClick={(e) => { e.stopPropagation(); onJump?.(o.id); }} className="font-mono text-caption underline underline-offset-2">{codeOf(o)}</button></span>
            ))}.</span>
        </p>
      )}
      {linked && (
        <p data-testid="fix-group-linked" className="mt-3 flex items-start gap-2 rounded-xl bg-[var(--accent-vivid)]/10 px-3 py-2 text-footnote">
          <Link2 className="mt-0.5 size-4 shrink-0 text-[var(--accent-vivid)]" strokeWidth={1.75} aria-hidden />
          <span>Same sentence as <button type="button" onClick={(e) => { e.stopPropagation(); onJump?.(group!.lead.id); }} className="font-mono text-caption underline underline-offset-2">{codeOf(group!.lead)}</button>.{" "}
            {f.status === "open" ? "One rewrite covers both, so accepting the fix there resolves this finding too." : "Resolved together with it."}</span>
        </p>
      )}

      {f.status === "dismissed" && !readOnly && (
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
      ) : !readOnly && f.status === "open" && unverified ? (
        addingFact ? (
          <form data-testid="add-fact-form" className="mt-4 space-y-2.5 rounded-2xl bg-foreground/5 p-3.5" onClick={(e) => e.stopPropagation()}
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy("fact");
              try { await onAddFact?.(f, { statement: factText.trim(), source: factSource.trim(), valid_until: factUntil || null }); setAddingFact(false); }
              finally { setBusy(null); }
            }}>
            <label className="block text-caption font-medium">Approved fact
              <Textarea value={factText} onChange={(e) => setFactText(e.target.value)} className="mt-1 min-h-[64px] text-footnote" required />
            </label>
            <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
              <label className="block text-caption font-medium">Source (where this number comes from)
                <Input value={factSource} onChange={(e) => setFactSource(e.target.value)} placeholder="e.g. Order data report, Dec 2025" className="mt-1" required />
              </label>
              <label className="block text-caption font-medium">Valid until
                <Input type="date" value={factUntil} onChange={(e) => setFactUntil(e.target.value)} className="mt-1" />
              </label>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" type="submit" disabled={busy !== null || !factText.trim() || !factSource.trim()} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">
                {busy === "fact" ? <Loader2 className="animate-spin" /> : <BookPlus strokeWidth={1.75} />} Save fact
              </Button>
              <Button size="sm" type="button" variant="ghost" onClick={() => setAddingFact(false)} className="rounded-full">Cancel</Button>
            </div>
          </form>
        ) : (
          <p data-testid="unverified-note" className="mt-4 flex items-start gap-2 rounded-2xl bg-foreground/5 p-3.5 text-footnote text-muted-foreground">
            <Info className="mt-0.5 size-4 shrink-0" strokeWidth={1.5} aria-hidden />
            <span>Nothing is rewritten for an unverified figure. If it's correct, add it as a fact; if not, edit it.</span>
          </p>
        )
      ) : !readOnly && f.status === "open" ? (
        <p data-testid="manual-fix" className="mt-4 flex items-start gap-2 rounded-2xl bg-foreground/5 p-3.5 text-footnote text-muted-foreground">
          <Info className="mt-0.5 size-4 shrink-0" strokeWidth={1.5} aria-hidden />
          <span>{textual
            ? "No automatic rewrite passed our checks for this one. Use Edit to write your own."
            : "There's no single passage to rewrite here. Change the content and resubmit, or dismiss this with a reason."}</span>
        </p>
      ) : null}

      {readOnly && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <p data-testid="finding-outcome" className="flex items-center gap-1.5 text-footnote text-muted-foreground">
            <Info className="size-4 shrink-0" strokeWidth={1.5} aria-hidden /> {findingOutcome(f, readOnly)}
          </p>
          {f.status === "open" && overrideBtn && <span className="ml-auto">{overrideBtn}</span>}
        </div>
      )}

      {!readOnly && !editing && f.status === "open" && (
        <div className="mt-4 flex flex-wrap gap-2">
          {!linked && f.fix && (
            <Button size="sm" disabled={locked || busy !== null} onClick={(e) => { e.stopPropagation(); void accept(); }} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">
              {busy === "accept" ? <Loader2 className="animate-spin" /> : <Check strokeWidth={2} />} {group && group.others.length > 0 ? `Accept fix for ${group.others.length + 1} findings` : "Accept fix"}
            </Button>
          )}
          {unverified && onAddFact && !addingFact && (
            <Button size="sm" disabled={locked} onClick={(e) => { e.stopPropagation(); setAddingFact(true); }} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">
              <BookPlus strokeWidth={1.75} /> Add as fact
            </Button>
          )}
          {!linked && textual && <Button size="sm" variant="secondary" disabled={locked} onClick={(e) => { e.stopPropagation(); setDraft(f.fix ?? f.quote); setEditing(true); }} className="rounded-full"><Pencil strokeWidth={1.75} /> Edit</Button>}
          <Button size="sm" variant="ghost" disabled={locked} onClick={(e) => { e.stopPropagation(); onDismiss?.(f); }} className="rounded-full text-muted-foreground"><X strokeWidth={1.75} /> Dismiss</Button>
          {overrideBtn}
        </div>
      )}
    </article>
  );
}
