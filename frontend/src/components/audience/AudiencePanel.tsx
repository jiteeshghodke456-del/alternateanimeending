import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight, Loader2, Minus, Play, Quote, RefreshCw, Sparkles, TrendingDown, TrendingUp, Users,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { DotGrid, DotLegend, LEVELS, TOTAL_DOTS, countLevels, levelInfo, scoreColor } from "@/components/audience/DotGrid";
import type { AudienceCompare, AudienceCulprit, AudienceOut, AudienceSegment, AudienceTotals } from "@/lib/api";
import { AUDIENCE_ATTRIBUTION, AUDIENCE_LABEL, useAudience } from "@/lib/audience";
import { cn } from "@/lib/utils";

const pct = (x: number): number => Math.round(x * 100);
const f1 = (x: number): string => x.toFixed(1);
const signed = (x: number): string => `${x > 0 ? "+" : x < 0 ? "−" : "±"}${Math.abs(x).toFixed(1)}`;
/** Plain words for a 1..5 mean. */
const leanWords = (s: number): string => (s <= 2.5 ? "leans to trust" : s >= 3.5 ? "leans to doubt" : "unsure");
const MOVE = 0.15; // smaller changes in a mean score are shown as "about the same"

/* ------------------------------------------------------------------ small pieces */

/** Score change chip: lower is better. Icon + words + sign, never colour alone. */
function DeltaChip({ d, className }: { d: number; className?: string }) {
  const better = d <= -MOVE, worse = d >= MOVE;
  const Icon = better ? TrendingDown : worse ? TrendingUp : Minus;
  return (
    <span className={[ // plain join: cn() would merge text-caption away against the colour class
      "inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-caption font-semibold tabular-nums",
      better ? "bg-pass/14 text-pass-text" : worse ? "bg-flag/12 text-flag-text" : "bg-foreground/8 text-muted-foreground", className ?? "",
    ].join(" ")}>
      <Icon className="size-3.5" strokeWidth={2} aria-hidden />
      {better ? `${signed(d)} better` : worse ? `${signed(d)} worse` : "About the same"}
    </span>
  );
}

/**
 * The 1-5 reaction scale with a marker at the mean and a band for the spread (mean ± sd).
 * The marker carries the number; the ends are labelled in words.
 */
function ScaleBar({ mean, sd, label, ghost, compact }: { mean: number; sd?: number; label: string; ghost?: number | null; compact?: boolean }) {
  const x = (v: number) => `${((Math.min(5, Math.max(1, v)) - 1) / 4) * 100}%`;
  return (
    <div>
      <div
        className="relative h-6" role="meter" aria-label={label} aria-valuemin={1} aria-valuemax={5} aria-valuenow={Number(mean.toFixed(2))}
        aria-valuetext={`${f1(mean)} of 5, ${leanWords(mean)}${sd != null ? `, spread ${sd.toFixed(2)}` : ""}`}
      >
        <div className="absolute inset-x-0 top-1/2 flex h-2 -translate-y-1/2 overflow-hidden rounded-full opacity-35" aria-hidden>
          {LEVELS.map((l) => <div key={l.n} className="flex-1" style={{ background: l.bg }} />)}
        </div>
        {sd != null && sd > 0.01 && (
          <div className="absolute top-1/2 h-3.5 -translate-y-1/2 rounded-full bg-foreground/20" style={{ left: x(mean - sd), width: `calc(${x(mean + sd)} - ${x(mean - sd)})` }} aria-hidden />
        )}
        {ghost != null && (
          <div className="absolute top-1/2 grid size-5 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border-2 border-dashed border-foreground/40 bg-background/60" style={{ left: x(ghost) }} aria-hidden />
        )}
        <div className="absolute top-1/2 grid size-6 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full text-[10px] font-bold text-black/80 shadow-[0_1px_3px_rgba(0,0,0,0.3)] ring-2 ring-white dark:ring-neutral-900" style={{ left: x(mean), background: scoreColor(mean) }} aria-hidden>
          {f1(mean)}
        </div>
      </div>
      {!compact && (
        <div className="mt-0.5 flex justify-between text-[11px] text-muted-foreground" aria-hidden>
          <span>1 trusts</span><span>3 unsure</span><span>5 distrusts</span>
        </div>
      )}
    </div>
  );
}

function Shares({ t }: { t: AudienceTotals }) {
  return (
    <p className="text-caption text-muted-foreground tabular-nums">
      <span className="font-medium text-foreground">{pct(t.trust_pct)}%</span> lean to trust ·{" "}
      <span className="font-medium text-foreground">{pct(t.unsure_pct)}%</span> unsure ·{" "}
      <span className="font-medium text-foreground">{pct(t.doubt_pct)}%</span> lean to doubt
      {t.confused_pct > 0 && <> · <span className="font-medium text-foreground">{pct(t.confused_pct)}%</span> may misread it</>}
    </p>
  );
}

function Footnote({ data }: { data: AudienceOut | null }) {
  return (
    <footer className="px-1 text-caption text-muted-foreground">
      <p>{data?.label ?? AUDIENCE_LABEL}. Scores are 1 (trusts it and would act) to 5 (distrusts it or is put off), averaged from each simulated reader&apos;s answer probabilities.</p>
      <p className="opacity-90">{data?.attribution ?? AUDIENCE_ATTRIBUTION}</p>
    </footer>
  );
}

/* ------------------------------------------------------------------ before / after */

function Comparison({ cmp, now, segments, thisN }: { cmp: AudienceCompare; now: AudienceTotals; segments: AudienceSegment[]; thisN: number }) {
  const prevN = cmp.version_n ?? "earlier";
  const before = cmp.overall.mean_score, after = now.mean_score;
  const d = after - before;
  const dir: "better" | "worse" | "same" = d <= -MOVE ? "better" : d >= MOVE ? "worse" : "same";
  const byKey = new Map(segments.map((s) => [s.key, s]));
  const moved = cmp.segments.map((s) => ({ s, cur: byKey.get(s.key) })).filter((x) => x.cur);
  const improved = moved.filter((x) => x.s.delta.mean_score <= -MOVE).length;
  const worse = moved.filter((x) => x.s.delta.mean_score >= MOVE).length;
  const verdict = dir === "better"
    ? `Readers react better to v${thisN}: the average moved from ${f1(before)} to ${f1(after)} (lower is more trusting)${now.mean_score > 2.5 ? ", but on average they are still not leaning to trust it" : ""}.`
    : dir === "worse" ? `Readers react worse to v${thisN}: the average moved from ${f1(before)} to ${f1(after)}.`
    : `The average reaction barely moved (${f1(before)} to ${f1(after)}).`;
  const groupLine = moved.length ? ` ${improved} of ${moved.length} groups react better${worse ? `, ${worse} worse` : ""}.` : "";
  const afterText = dir === "better" ? "text-pass-text" : dir === "worse" ? "text-flag-text" : "text-foreground";

  return (
    <section aria-label={`Before and after: version ${prevN} against version ${thisN}`} data-tour="audience-compare" className="glass-strong relative overflow-hidden p-5">
      <div aria-hidden className="pointer-events-none absolute -right-16 -top-16 size-48 rounded-full bg-[#30D158]/15 blur-3xl" />
      <div className="relative">
        <h3 className="text-headline">Before and after</h3>
        <div className="mt-4 flex items-end justify-between gap-3">
          <div className="min-w-0">
            <p className="text-caption font-medium text-muted-foreground">v{prevN} average</p>
            <p className="text-[34px] font-semibold leading-none tracking-tight text-muted-foreground tabular-nums line-through decoration-1">{f1(before)}</p>
          </div>
          <ArrowRight className="mb-2 size-5 shrink-0 text-muted-foreground" strokeWidth={1.5} aria-hidden />
          <div className="min-w-0">
            <p className="text-caption font-medium">v{thisN} (this version)</p>
            <p className={cn("text-[44px] font-semibold leading-none tracking-tight tabular-nums", afterText)}>{f1(after)}</p>
          </div>
          <DeltaChip d={d} className="mb-1 !text-footnote" />
        </div>
        <p className="mt-3 text-footnote text-muted-foreground">
          <span className="font-medium text-foreground">{verdict}</span>{groupLine}
        </p>

        <p className="mt-4 flex justify-between text-[11px] text-muted-foreground" aria-hidden><span>1 trusts it</span><span>3 unsure</span><span>5 distrusts it</span></p>
        <ul className="mt-2 space-y-4">
          {moved.map(({ s, cur }) => (
            <li key={s.key}>
              <div className="flex items-center justify-between gap-2">
                <span className="min-w-0 truncate text-footnote font-medium">{s.label}</span>
                <DeltaChip d={s.delta.mean_score} />
              </div>
              <div className="mt-1">
                <ScaleBar compact mean={cur!.mean_score} ghost={s.previous.mean_score} label={`${s.label}: version ${prevN} ${f1(s.previous.mean_score)}, version ${thisN} ${f1(cur!.mean_score)}`} />
              </div>
              <p className="text-caption text-muted-foreground tabular-nums">v{prevN} {f1(s.previous.mean_score)} <ArrowRight className="inline size-3" aria-hidden /> v{thisN} {f1(cur!.mean_score)}</p>
            </li>
          ))}
        </ul>
        <p className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-muted-foreground">
          <span className="inline-flex items-center gap-1.5"><span className="inline-block size-3.5 rounded-full border-2 border-dashed border-foreground/40" aria-hidden /> earlier version</span>
          <span className="inline-flex items-center gap-1.5"><span className="inline-block size-3.5 rounded-full bg-[#FFD60A] ring-2 ring-white dark:ring-neutral-900" aria-hidden /> this version (number = average)</span>
        </p>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ groups */

function SegmentCard({ seg, selected, onSelect, delta }: { seg: AudienceSegment; selected: boolean; onSelect: () => void; delta?: number }) {
  const v = seg.vs_overall;
  return (
    <button
      type="button" aria-pressed={selected} onClick={onSelect}
      className={cn(
        "glass-strong w-full p-4 text-left transition-[transform,box-shadow] duration-200 hover:-translate-y-0.5 motion-reduce:transition-none",
        selected && "ring-2 ring-[var(--accent-vivid)]",
      )}
    >
      <span className="flex items-start gap-1.5 text-footnote font-semibold"><Users className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" strokeWidth={1.75} aria-hidden />{seg.label}</span>
      {seg.description && <span className="mt-0.5 line-clamp-2 block text-caption text-muted-foreground">{seg.description}</span>}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <span className="rounded-full bg-foreground/8 px-2 py-0.5 text-caption font-semibold tabular-nums">{f1(seg.mean_score)} · {leanWords(seg.mean_score)}</span>
        <span className={`rounded-full px-2 py-0.5 text-caption font-medium tabular-nums ${
          v >= MOVE ? "bg-flag/12 text-flag-text" : v <= -MOVE ? "bg-pass/14 text-pass-text" : "bg-foreground/8 text-muted-foreground"}`}>
          {Math.abs(v) < MOVE ? "About the post average" : `${signed(v)} vs post average`}
        </span>
        {delta != null && <DeltaChip d={delta} />}
      </div>
      <div className="mt-2"><ScaleBar mean={seg.mean_score} sd={seg.sd} label={`${seg.label}, average reaction`} /></div>
      <div className="mt-1.5"><Shares t={seg} /></div>
      <p className="mt-1 text-caption text-muted-foreground tabular-nums">Spread {seg.sd.toFixed(2)} · {seg.answered}/{seg.n} answered</p>
    </button>
  );
}

function SegmentDetail({ seg, culprit }: { seg: AudienceSegment; culprit: AudienceCulprit | null }) {
  return (
    <section aria-label={`${seg.label}: the three most negative readers`} className="glass-strong space-y-4 p-5">
      <div>
        <h3 className="text-headline">{seg.label}</h3>
        <p className="text-footnote text-muted-foreground">The three most negative simulated readers in this group.</p>
      </div>
      {seg.why && (
        <p className="rounded-2xl bg-[var(--accent-vivid)]/8 p-3.5 text-footnote">
          <span className="mb-0.5 flex items-center gap-1.5 text-caption font-semibold text-[var(--accent-text)]"><Sparkles className="size-3.5" strokeWidth={1.75} aria-hidden /> Likely reason (an AI guess)</span>
          {seg.why}
        </p>
      )}
      <ul className="space-y-2.5">
        {seg.worst.map((p) => {
          const lv = p.score != null ? levelInfo(p.score) : null;
          return (
            <li key={p.id} className="rounded-2xl bg-foreground/5 p-3.5">
              <div className="flex items-start justify-between gap-2">
                <p className="text-footnote font-semibold">{p.bio}</p>
                <span className="inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-caption font-semibold text-black/80 tabular-nums" style={{ background: lv?.bg ?? "#ddd" }}>
                  {p.score != null ? `${f1(p.score)} · ${lv!.label}` : "n/a"}
                </span>
              </div>
              {p.confused_flag && <p className="mt-1 text-caption font-medium text-[var(--accent-text)]">May misread the main message</p>}
              <p className="mt-1 text-caption leading-5 text-muted-foreground">{p.detail}</p>
            </li>
          );
        })}
      </ul>
      {culprit ? (
        <div className="rounded-2xl border border-[#FF9F0A]/50 bg-human/10 p-3.5">
          <p className="mb-1 flex items-center gap-1.5 text-caption font-semibold text-human-text"><Quote className="size-3.5" strokeWidth={1.75} aria-hidden /> {culprit.certain ? "Paragraph that most reduces trust" : "Possibly the paragraph that most reduces trust"}</p>
          <p className="text-footnote">&ldquo;{culprit.text}&rdquo;</p>
          <p className="mt-1.5 text-caption text-muted-foreground">Marked in the content on the left.</p>
        </div>
      ) : (
        <p className="text-caption text-muted-foreground">No single paragraph stood out for this version.</p>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ panel */

export function AudiencePanel({ versionId, versionN, onCulprit, onOpenVersion, previousVersionId }: {
  versionId: number | null;
  versionN: number;
  /** called with the culprit paragraph while a segment is selected, else null */
  onCulprit: (c: AudienceCulprit | null) => void;
  onOpenVersion?: (versionId: number) => void;
  previousVersionId?: number | null;
}) {
  const { data, loading, starting, error, start, reload } = useAudience(versionId, true);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => { setSelected(null); }, [versionId]);
  useEffect(() => { onCulprit(selected && data?.culprit_paragraph ? data.culprit_paragraph : null); }, [selected, data?.culprit_paragraph, onCulprit]);
  useEffect(() => () => onCulprit(null), [onCulprit]);

  const running = data?.status === "running" || starting;
  const done = data?.status === "done";
  const segs = data?.segments ?? [];
  const segment = segs.find((s) => s.key === selected) ?? null;
  const counts = useMemo(() => countLevels(data?.personas ?? []), [data?.personas]);
  const progress = data?.progress;
  const answered = progress?.done ?? (data?.personas ?? []).filter((p) => p.level != null).length;
  const total = progress?.total ?? TOTAL_DOTS;
  const cmp = data?.compare_to_previous_version ?? null;
  const cmpDelta = new Map((cmp?.segments ?? []).map((s) => [s.key, s.delta]));

  function run() { start(); }
  const runBtn = (label: string, icon: "play" | "again") => (
    <Button onClick={run} disabled={running} className="rounded-full bg-[var(--accent-fill)] px-5 text-white hover:bg-[var(--accent-fill-hover)]">
      {running ? <Loader2 className="animate-spin" /> : icon === "play" ? <Play strokeWidth={2} /> : <RefreshCw strokeWidth={1.75} />}
      {running ? "Running…" : label}
    </Button>
  );

  if (loading && !data) return <div className="space-y-4"><Skeleton className="h-36" /><Skeleton className="h-56" /></div>;
  if (error && !data) return <ErrorState title="Can't load the audience preview" message={error.message} onRetry={reload} className="!py-8" />;

  const unavailable = data?.status === "unavailable";
  const failed = data?.status === "error";

  return (
    <div className="space-y-4">
      <section className="glass-strong p-5" aria-label="Audience preview" data-tour="audience-panel">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1 basis-56">
            <h3 className="text-headline">Audience preview</h3>
            <p className="mt-0.5 text-footnote text-muted-foreground">
              {data?.audience_source === "brand"
                ? `${total} simulated readers from ${segs.length || "the"} reader groups your brand defined react to this version, so you can see which groups may doubt or misread it before it goes out.`
                : `${total} simulated Indian readers in six groups react to this version, so you can see which groups may doubt or misread it before it goes out.`}
            </p>
          </div>
          {!unavailable && runBtn(done ? "Run again" : "Run audience preview", done ? "again" : "play")}
        </div>

        {unavailable && (
          <p className="mt-3 rounded-2xl bg-foreground/5 p-3.5 text-footnote" role="status">{data?.message ?? "The audience preview isn't available for this item."}</p>
        )}
        {failed && (
          <p className="mt-3 rounded-2xl bg-flag/10 p-3.5 text-footnote text-flag-text" role="alert">{data?.message ?? "The audience preview failed. Please run it again."}</p>
        )}
        {error && data && <p className="mt-3 text-footnote text-flag-text" role="alert">{error.message}</p>}

        {running && (
          <div className="mt-4" role="status" aria-live="polite">
            <div className="mb-1.5 flex justify-between text-caption text-muted-foreground"><span>Readers are answering…</span><span className="tabular-nums">{answered} of {total}</span></div>
            <div className="h-1.5 overflow-hidden rounded-full bg-foreground/8"><div className="h-full rounded-full bg-[var(--accent-fill)] transition-[width] duration-300" style={{ width: `${total ? (answered / total) * 100 : 0}%` }} /></div>
          </div>
        )}
        {done && data?.overall && (
          <div className="mt-4">
            <p className="text-footnote"><span className="font-semibold">Post average {f1(data.overall.mean_score)} of 5</span> <span className="text-muted-foreground">({leanWords(data.overall.mean_score)}; spread {data.overall.sd.toFixed(2)})</span></p>
            <div className="mt-1"><ScaleBar mean={data.overall.mean_score} sd={data.overall.sd} label="All simulated readers, average reaction" /></div>
            <div className="mt-1"><Shares t={data.overall} /></div>
          </div>
        )}
        {done && data && (
          <p className="mt-3 text-caption text-muted-foreground tabular-nums">
            {data.overall?.answered ?? 0} of {data.overall?.n ?? TOTAL_DOTS} answered
            {data.elapsed_ms != null && ` · ${(data.elapsed_ms / 1000).toFixed(1)} s`}
            {data.cost_usd != null && ` · $${data.cost_usd.toFixed(4)}`}
          </p>
        )}
        {done && data?.language_note && <p className="mt-2 text-caption text-muted-foreground">{data.language_note}</p>}
        {data?.warnings?.map((w) => <p key={w} className="mt-2 text-caption text-human-text">{w}</p>)}
      </section>

      {(data?.status === "none" || (!data && !error)) && !running && (
        <p className="px-2 text-center text-footnote text-muted-foreground">{data?.message ?? "Nothing has been run for this version yet. It takes a couple of seconds."}</p>
      )}

      {done && data && cmp && data.overall && <Comparison cmp={cmp} now={data.overall} segments={segs} thisN={versionN} />}
      {done && data && !cmp && previousVersionId != null && (
        <p className="glass-strong flex flex-wrap items-center justify-between gap-2 p-4 text-footnote text-muted-foreground">
          <span>Want a before and after? Run the preview on the earlier version too.</span>
          {onOpenVersion && <Button size="sm" variant="secondary" className="rounded-full" onClick={() => onOpenVersion(previousVersionId)}>Open earlier version</Button>}
        </p>
      )}

      {(running || done) && (
        <section className="glass-strong p-5" aria-label="Every simulated reader">
          <div className="mb-3 flex items-baseline justify-between gap-2">
            <h3 className="text-headline">Every reader</h3>
            <span className="text-caption text-muted-foreground">Tap a dot to open its group</span>
          </div>
          <DotGrid personas={data?.personas ?? []} segmentOrder={segs.map((s) => s.key)} finished={!!done} total={total} selected={selected} onSelect={(k) => setSelected((cur) => (cur === k ? null : k))} />
          <DotLegend counts={counts.levels} confused={counts.confused} className="mt-4" />
        </section>
      )}

      {done && segs.length > 0 && (
        <>
          <h3 className="px-1 pt-1 text-headline">{segs.length} reader groups</h3>
          <p className="-mt-2 px-1 text-caption text-muted-foreground">Tap a group to see its most negative readers{segs.some((s) => s.why) ? " and a likely reason" : ""}.</p>
          <div className="grid gap-3 sm:grid-cols-2">
            {segs.map((s) => (
              <SegmentCard key={s.key} seg={s} selected={selected === s.key} onSelect={() => setSelected((cur) => (cur === s.key ? null : s.key))} delta={cmpDelta.get(s.key)?.mean_score} />
            ))}
          </div>
          {segment && <SegmentDetail seg={segment} culprit={data?.culprit_paragraph ?? null} />}
        </>
      )}

      <Footnote data={data} />
    </div>
  );
}
