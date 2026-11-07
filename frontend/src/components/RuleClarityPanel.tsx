import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, ArrowRight, Check, CheckCircle2, ChevronDown, HelpCircle, History, Loader2, RefreshCw, Sparkles, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { DiffView } from "@/components/DiffView";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { diffSegments } from "@/lib/diff";
import { clarityApi, CLARITY_BAND_LABEL, type ClarityBand, type ClarityRun, type RuleClarity } from "@/lib/clarity";
import { reportError, timeAgo, useAsync } from "@/lib/hooks";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ small pieces (shared with the dashboard card) */

const BAND_STYLE: Record<ClarityBand, { chip: string; bar: string; Icon: typeof CheckCircle2 }> = {
  clear: { chip: "bg-pass/14 text-pass-text", bar: "bg-pass", Icon: CheckCircle2 },
  could_be_clearer: { chip: "bg-human/15 text-human-text", bar: "bg-human", Icon: HelpCircle },
  unclear: { chip: "bg-flag/12 text-flag-text", bar: "bg-flag", Icon: AlertTriangle },
};

export function ClarityChip({ band, className }: { band: ClarityBand; className?: string }) {
  const s = BAND_STYLE[band];
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-caption font-medium", s.chip, className)}>
      <s.Icon className="size-3.5" strokeWidth={1.75} aria-hidden /> {CLARITY_BAND_LABEL[band]}
    </span>
  );
}

export function ClarityBar({ score, band, label = "Clarity" }: { score: number; band: ClarityBand; label?: string }) {
  const v = Math.round(score);
  return (
    <div className="flex items-center gap-3">
      <span className="w-[4.5rem] shrink-0 text-caption text-muted-foreground">{label}</span>
      <div role="meter" aria-label={`${label} score`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={v}
        className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-foreground/8">
        <div className={cn("h-full rounded-full transition-[width] duration-300", BAND_STYLE[band].bar)} style={{ width: `${Math.max(3, v)}%` }} />
      </div>
      <span className="w-14 shrink-0 text-right text-footnote font-semibold tabular-nums">{v}<span className="font-normal text-muted-foreground">/100</span></span>
    </div>
  );
}

/** The statement with the vague words marked (underline + sr-only label, not colour alone). */
function MarkedStatement({ text, terms }: { text: string; terms: string[] }) {
  if (!terms.length) return <>{text}</>;
  const esc = terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).sort((a, b) => b.length - a.length);
  const parts = text.split(new RegExp(`(${esc.join("|")})`, "gi"));
  const set = new Set(terms.map((t) => t.toLowerCase()));
  return (
    <>
      {parts.map((p, i) => set.has(p.toLowerCase())
        ? <mark key={i} className="rounded bg-human/18 px-0.5 text-inherit underline decoration-human-text decoration-dotted decoration-2 underline-offset-4"><span className="sr-only">vague word: </span>{p}</mark>
        : <span key={i}>{p}</span>)}
    </>
  );
}

const pct = (x: number | null | undefined) => (x == null ? "–" : `${Math.round(x * 100)}`);

/* ------------------------------------------------------------------ rewrite (diff + effect) */

function Metric({ label, before, after, lowerIsBetter, unit = "" }: { label: string; before: string; after: string; lowerIsBetter?: boolean; unit?: string }) {
  const b = Number(before), a = Number(after);
  const better = Number.isFinite(a) && Number.isFinite(b) && (lowerIsBetter ? a < b : a > b);
  const worse = Number.isFinite(a) && Number.isFinite(b) && (lowerIsBetter ? a > b : a < b);
  return (
    <div className="rounded-2xl bg-foreground/[0.04] px-3 py-2">
      <p className="text-caption text-muted-foreground">{label}</p>
      <p className="mt-0.5 flex items-center gap-1.5 text-callout font-semibold tabular-nums">
        <span>{before}{unit}</span><ArrowRight className="size-3.5 text-muted-foreground" strokeWidth={2} aria-label="to" />
        <span className={cn(better && "text-pass-text", worse && "text-flag-text")}>{after}{unit}</span>
        {better && <span className="sr-only">(better)</span>}{worse && <span className="sr-only">(worse)</span>}
      </p>
    </div>
  );
}

/** A word diff when most words survive; otherwise the old and new wording one above the other (easier to read). */
function WordingChange({ before, after }: { before: string; after: string }) {
  const segs = diffSegments(before, after);
  const kept = segs.filter((x) => x.op === "equal").reduce((n, x) => n + x.text.trim().split(/\s+/).filter(Boolean).length, 0);
  const total = Math.max(1, after.trim().split(/\s+/).length);
  if (kept / total >= 0.5) return <div className="mt-2"><DiffView segments={segs} /></div>;
  return (
    <dl className="mt-2 space-y-2 text-callout">
      <div className="rounded-xl bg-flag/8 px-3 py-2"><dt className="text-caption font-medium text-flag-text">Now</dt><dd className="text-muted-foreground line-through decoration-flag-text/60">{before}</dd></div>
      <div className="rounded-xl bg-pass/10 px-3 py-2"><dt className="text-caption font-medium text-pass-text">Suggested</dt><dd>{after}</dd></div>
    </dl>
  );
}

function RewriteBox({ r, admin, onChange }: { r: RuleClarity; admin: boolean; onChange: (x: RuleClarity) => void }) {
  const rw = r.rewrite!;
  const [busy, setBusy] = useState<"" | "apply" | "dismiss">("");
  async function act(kind: "apply" | "dismiss") {
    setBusy(kind);
    try {
      const out = kind === "apply" ? await clarityApi.apply(r.rule_id) : await clarityApi.dismiss(r.rule_id);
      onChange(out);
      if (kind === "apply") toast.success(`${r.code} is now version ${out.version}. The old wording is kept in its history.`);
    } catch (e) { reportError(e); } finally { setBusy(""); }
  }
  return (
    <div className="mt-4 rounded-2xl border border-[var(--glass-border)] bg-background/60 p-4" aria-label={`Suggested rewrite for ${r.code}`}>
      <p className="flex items-center gap-1.5 text-footnote font-semibold"><Sparkles className="size-4 text-[var(--accent-text)]" strokeWidth={1.75} aria-hidden /> Suggested wording</p>
      <WordingChange before={r.statement} after={rw.statement} />
      {rw.what_changed && <p className="mt-2 text-footnote text-muted-foreground">{rw.what_changed}</p>}
      <ul className="mt-3 grid gap-2 text-footnote sm:grid-cols-2">
        <li className="flex gap-2 rounded-xl bg-pass/10 px-3 py-2"><Check className="mt-0.5 size-4 shrink-0 text-pass-text" strokeWidth={2} aria-hidden /><span><span className="font-medium">Follows it:</span> “{rw.example_follows}”</span></li>
        <li className="flex gap-2 rounded-xl bg-flag/10 px-3 py-2"><X className="mt-0.5 size-4 shrink-0 text-flag-text" strokeWidth={2} aria-hidden /><span><span className="font-medium">Breaks it:</span> “{rw.example_breaks}”</span></li>
      </ul>
      <p className="mt-4 text-caption font-medium uppercase tracking-wide text-muted-foreground">Measured effect (same test texts)</p>
      <div className="mt-2 grid grid-cols-1 gap-2 min-[420px]:grid-cols-3">
        <Metric label="Clarity score" before={`${Math.round(rw.score_before)}`} after={`${Math.round(rw.score_after)}`} />
        <Metric label="Jev disagreement" before={pct(rw.disagreement_before)} after={pct(rw.disagreement_after)} lowerIsBetter unit={rw.disagreement_after == null ? "" : "%"} />
        <Metric label="Off-target answers" before={pct(rw.mismatch_before)} after={pct(rw.mismatch_after)} lowerIsBetter unit={rw.mismatch_after == null ? "" : "%"} />
      </div>
      {rw.intent_warning && (
        <p role="note" className="mt-3 flex gap-2 rounded-xl bg-human/12 px-3 py-2 text-footnote text-human-text">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" strokeWidth={1.75} aria-hidden />
          Jev thinks this wording may ask for something the original rule doesn't. Check the intent before applying.
        </p>
      )}
      {rw.vague_terms_after.length > 0 && <p className="mt-2 text-caption text-muted-foreground">Still vague: {rw.vague_terms_after.join(", ")}.</p>}
      {admin && (
        <div className="mt-4 flex flex-wrap gap-2">
          <Button disabled={!!busy} onClick={() => act("apply")} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">
            {busy === "apply" ? <Loader2 className="animate-spin" /> : <Check strokeWidth={2} />} Apply as version {r.version + 1}
          </Button>
          <Button variant="ghost" disabled={!!busy} onClick={() => act("dismiss")} className="rounded-full">{busy === "dismiss" && <Loader2 className="animate-spin" />} Dismiss</Button>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ one rule */

function Details({ summary, children }: { summary: string; children: ReactNode }) {
  return (
    <details className="group mt-3 rounded-2xl bg-foreground/[0.03] px-3 py-2 text-footnote">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 font-medium text-[var(--accent-text)] [&::-webkit-details-marker]:hidden">
        <ChevronDown className="size-4 transition-transform group-open:rotate-180" strokeWidth={1.75} aria-hidden /> {summary}
      </summary>
      <div className="mt-2">{children}</div>
    </details>
  );
}

function JevDetails({ r }: { r: RuleClarity }) {
  const d = r.signals.disagreement;
  if (!d) return null;
  return (
    <Details summary={`How Jev read ${d.wordings} wordings of this rule`}>
      <ol className="space-y-1 text-caption text-muted-foreground">
        <li><span className="font-semibold text-foreground">A</span> (as written): {r.statement}</li>
        {r.paraphrases.map((p, i) => <li key={i}><span className="font-semibold text-foreground">{String.fromCharCode(66 + i)}</span> ({r.paraphrase_source === "llm" ? "another reader's wording" : "template"}): {p}</li>)}
      </ol>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[320px] text-left text-caption">
          <caption className="sr-only">Jev's probability that each test text breaks the rule, per wording</caption>
          <thead><tr className="text-muted-foreground"><th className="py-1 pr-2 font-medium">Test text</th><th className="px-1 font-medium">Meant to</th>{d.per_probe[0]?.p.map((_, i) => <th key={i} className="px-1 text-right font-medium">{String.fromCharCode(65 + i)}</th>)}</tr></thead>
          <tbody>
            {d.per_probe.map((p, i) => (
              <tr key={i} className="border-t border-foreground/8 align-top">
                <td className="py-1 pr-2">{p.text}</td>
                <td className="px-1 whitespace-nowrap">{p.expect === "breaks" ? "break" : p.expect === "follows" ? "follow" : "borderline"}</td>
                {p.p.map((x, j) => <td key={j} className={cn("px-1 text-right tabular-nums", p.flip && "font-semibold text-human-text")}>{Math.round(x * 100)}%</td>)}
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-1 text-caption text-muted-foreground">Bold rows land in different bands depending on the wording. Average spread {pct(d.mean_spread)} points.</p>
      </div>
    </Details>
  );
}

function RuleCard({ r, admin, onChange }: { r: RuleClarity; admin: boolean; onChange: (x: RuleClarity) => void }) {
  const [busy, setBusy] = useState<"" | "score" | "suggest">("");
  async function score() {
    setBusy("score");
    try { onChange(await clarityApi.scoreOne(r.rule_id)); } catch (e) { reportError(e); } finally { setBusy(""); }
  }
  async function suggest() {
    setBusy("suggest");
    try { onChange(await clarityApi.suggest(r.rule_id)); } catch (e) { reportError(e); } finally { setBusy(""); }
  }
  const last = r.history[r.history.length - 1];
  return (
    <li id={`clarity-${r.code}`} className={cn("glass-strong p-5", !r.enabled && "opacity-75")}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-footnote font-semibold">{r.code}</span>
        <ClarityChip band={r.band} />
        <span className="text-caption capitalize text-muted-foreground">{r.category} · v{r.version}{r.enabled ? "" : " · off"}</span>
        {!r.measured && <span className="rounded-full bg-foreground/8 px-2 py-0.5 text-caption text-muted-foreground">{r.computed_at ? "Jev signal out of date" : "Wording and reviews only"}</span>}
      </div>
      <p className="mt-2 text-body"><MarkedStatement text={r.statement} terms={r.reasons.vague_terms} /></p>
      <div className="mt-3"><ClarityBar score={r.score} band={r.band} /></div>
      {r.reasons.notes.length > 0 && (
        <ul className="mt-3 space-y-1 text-footnote text-muted-foreground">
          {r.reasons.notes.map((n, i) => <li key={i} className="flex gap-2"><span aria-hidden className="mt-2 size-1 shrink-0 rounded-full bg-muted-foreground" />{n}</li>)}
        </ul>
      )}
      {r.error && <p className="mt-2 text-caption text-human-text">{r.error}</p>}
      <JevDetails r={r} />
      {last && (
        <Details summary={`Rewritten from version ${last.version}`}>
          <p className="text-caption text-muted-foreground">Version {last.version} said: “{last.statement}” (clarity {Math.round(last.score)}). Replaced {timeAgo(last.replaced_at)}.</p>
        </Details>
      )}
      {r.rewrite && <RewriteBox r={r} admin={admin} onChange={onChange} />}
      {admin && !r.rewrite && (
        <div className="mt-4 flex flex-wrap gap-2">
          {r.band !== "clear" && (
            <Button size="sm" disabled={!!busy} onClick={suggest} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">
              {busy === "suggest" ? <Loader2 className="animate-spin" /> : <Sparkles strokeWidth={1.75} />} {busy === "suggest" ? "Writing and testing…" : "Suggest clearer wording"}
            </Button>
          )}
          <Button size="sm" variant="ghost" disabled={!!busy} onClick={score} className="rounded-full">
            {busy === "score" ? <Loader2 className="animate-spin" /> : <RefreshCw strokeWidth={1.75} />} {r.measured ? "Re-check" : "Check with Jev"}
          </Button>
        </div>
      )}
    </li>
  );
}

/* ------------------------------------------------------------------ the tab */

export function RuleClarityPanel({ admin }: { admin: boolean }) {
  const q = useAsync(() => clarityApi.list(), []);
  const [run, setRun] = useState<ClarityRun | null>(null);
  const [showClear, setShowClear] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  // a run started earlier (or in another tab) is still going: keep polling it
  useEffect(() => { clarityApi.runStatus().then((s) => { if (s.status === "running") { setRun(s); poll(); } }).catch(() => {}); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  function poll() {
    timer.current = window.setTimeout(async () => {
      try {
        const s = await clarityApi.runStatus();
        setRun(s);
        if (s.status === "running") { poll(); q.reload(); return; }
        q.reload();
        if (s.status === "done") toast.success(`Checked ${s.total} rules in ${(s.elapsed_ms / 1000).toFixed(0)} s for $${s.cost_usd.toFixed(4)}`);
        else if (s.error) toast.error(s.error);
      } catch (e) { reportError(e); }
    }, 1200);
  }
  async function runAll() {
    try { const s = await clarityApi.runAll(); setRun(s); if (s.status === "running") poll(); else q.reload(); }
    catch (e) { reportError(e); }
  }
  const loaded = !!q.data;
  useEffect(() => {
    const h = window.location.hash;
    if (!loaded || !h.startsWith("#clarity-")) return;
    const code = decodeURIComponent(h.slice("#clarity-".length));
    if (!(q.data ?? []).some((r) => r.code === code && r.band !== "clear")) setShowClear(true);
    window.setTimeout(() => document.getElementById(`clarity-${code}`)?.scrollIntoView({ behavior: "smooth", block: "start" }), 60);
  }, [loaded]); // eslint-disable-line react-hooks/exhaustive-deps
  const replace = (x: RuleClarity) => q.setData((cur) => (cur ?? []).map((r) => (r.rule_id === x.rule_id ? x : r)).sort((a, b) => a.score - b.score || a.code.localeCompare(b.code)));

  if (q.error) return <ErrorState message={q.error.message} onRetry={q.reload} />;
  if (q.loading && !q.data) return <div className="space-y-3"><Skeleton className="h-28" /><Skeleton className="h-28" /><Skeleton className="h-28" /></div>;
  const rows = q.data ?? [];
  if (rows.length === 0) return <EmptyState icon={HelpCircle} title="No rules to check yet" description="Add rules in the Rules tab, then come back to see which ones are unclear." />;
  const unclear = rows.filter((r) => r.band === "unclear").length;
  const maybe = rows.filter((r) => r.band === "could_be_clearer").length;
  const measured = rows.filter((r) => r.measured).length;
  const shown = showClear ? rows : rows.filter((r) => r.band !== "clear");
  const running = run?.status === "running";
  return (
    <>
      <section className="glass-strong mb-4 p-5" aria-labelledby="clarity-h">
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1 basis-64">
            <h2 id="clarity-h" className="text-headline">Where the guidelines are unclear</h2>
            <p className="mt-1 text-footnote text-muted-foreground">
              Each rule gets a clarity score from 0 to 100: vague words in its wording (40%), Jev giving different answers when
              the rule is reworded (40%), and reviewers dismissing or overriding its findings (20%).{" "}
              <span className="font-medium text-foreground">{unclear} unclear, {maybe} could be clearer</span> of {rows.length} rules
              {measured < rows.length ? ` · ${measured} checked with Jev` : ""}.
            </p>
          </div>
          {admin && (
            <Button onClick={runAll} disabled={running} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">
              {running ? <Loader2 className="animate-spin" /> : <RefreshCw strokeWidth={1.75} />}
              {running ? `Checking ${run!.done} of ${run!.total}…` : measured ? "Re-check all rules" : "Check all rules with Jev"}
            </Button>
          )}
        </div>
        {running && (
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-foreground/8" role="progressbar" aria-label="Rules checked" aria-valuemin={0} aria-valuemax={run!.total} aria-valuenow={run!.done}>
            <div className="h-full rounded-full bg-[var(--accent-fill)] transition-[width]" style={{ width: `${(run!.done / Math.max(1, run!.total)) * 100}%` }} />
          </div>
        )}
        {!admin && <p className="mt-2 text-caption text-muted-foreground">Brand admins can re-check rules and apply clearer wording.</p>}
      </section>
      {shown.length === 0 ? (
        <EmptyState icon={CheckCircle2} title="Every rule reads clearly" description="No rule is vague, splits Jev's answers or gets dismissed often." />
      ) : (
        <ul className="space-y-3">{shown.map((r) => <RuleCard key={r.rule_id} r={r} admin={admin} onChange={replace} />)}</ul>
      )}
      <div className="mt-4 flex justify-center">
        <Button variant="ghost" className="rounded-full" onClick={() => setShowClear((s) => !s)}>
          <History className="size-4" strokeWidth={1.75} aria-hidden />
          {showClear ? "Hide clear rules" : `Show the ${rows.length - shown.length} clear rules too`}
        </Button>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ dashboard card */

export function UnclearRulesCard() {
  const q = useAsync(() => clarityApi.list().catch(() => [] as RuleClarity[]), []);
  const worst = (q.data ?? []).filter((r) => r.band !== "clear").slice(0, 3);
  if (q.loading && !q.data) return null;
  return (
    <section className="glass-strong p-5 md:p-6" aria-labelledby="unclear-h">
      <div className="flex items-center justify-between gap-2">
        <h3 id="unclear-h" className="text-headline">Unclear guidelines</h3>
        <Link to="/brand?tab=clarity" className="inline-flex items-center gap-1 text-footnote font-medium text-[var(--accent-text)] hover:underline">Review all <ArrowRight className="size-3.5" strokeWidth={2} aria-hidden /></Link>
      </div>
      <p className="mt-0.5 text-footnote text-muted-foreground">The rules writers and Jev are most likely to read differently.</p>
      {worst.length === 0 ? <p className="mt-4 text-footnote text-muted-foreground">Every rule reads clearly.</p> : (
        <ul className="mt-4 space-y-3">
          {worst.map((r) => (
            <li key={r.rule_id}>
              <Link to={`/brand?tab=clarity#clarity-${r.code}`} aria-label={`${r.code}: ${CLARITY_BAND_LABEL[r.band]}, clarity ${Math.round(r.score)} of 100`} className="-m-1 block rounded-2xl p-1 hover:bg-foreground/[0.04]">
                <div className="flex flex-wrap items-center gap-2"><span className="font-mono text-caption font-semibold">{r.code}</span><ClarityChip band={r.band} /></div>
                <p className="mt-1 truncate text-footnote">{r.statement}</p>
                <div className="mt-1.5"><ClarityBar score={r.score} band={r.band} /></div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
