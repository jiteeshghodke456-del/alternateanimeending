import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight, CircleHelp, Eye, Loader2, Minus, OctagonAlert, Play, Quote, RefreshCw, Sparkles, TrendingDown, TrendingUp, Users,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { DotGrid, DotLegend, TOTAL_DOTS, countKinds } from "@/components/audience/DotGrid";
import type { AudienceCompare, AudienceCulprit, AudienceOut, AudienceSegment, AudienceTotals } from "@/lib/api";
import { AUDIENCE_ATTRIBUTION, AUDIENCE_LABEL, useAudience } from "@/lib/audience";
import { cn } from "@/lib/utils";

type Metric = "distrust" | "confused" | "unsure";
const METRIC = {
  distrust: { label: "Distrust", icon: OctagonAlert, color: "#FF453A", text: "text-flag-text", hint: "would not trust it" },
  confused: { label: "Confused", icon: CircleHelp, color: "#FF9F0A", text: "text-human-text", hint: "would not understand it" },
  unsure: { label: "Unsure", icon: Eye, color: "#0A84FF", text: "text-[var(--accent-text)]", hint: "on the fence about trusting it" },
} as const;

const share = (t: AudienceTotals, m: Metric): number => t[`${m}_pct` as const];
const pct = (x: number): number => Math.round(x * 100);
const points = (d: number): number => Math.round(d * 100);
const mean = (t: AudienceTotals, m: Metric): number | null => (m === "distrust" ? t.mean_distrust : m === "confused" ? t.mean_confused : null);

/* ------------------------------------------------------------------ small pieces */

/** Change chip: down is good for every metric here. Icon + words + sign, never colour alone. */
function DeltaChip({ d, className }: { d: number; className?: string }) {
  const p = points(d);
  const better = p < 0, worse = p > 0;
  const Icon = better ? TrendingDown : worse ? TrendingUp : Minus;
  return (
    <span className={cn(
      "inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-caption font-semibold tabular-nums",
      better ? "bg-pass/14 text-pass-text" : worse ? "bg-flag/12 text-flag-text" : "bg-foreground/8 text-muted-foreground", className,
    )}>
      <Icon className="size-3.5" strokeWidth={2} aria-hidden />
      {p === 0 ? "No change" : `${p > 0 ? "+" : "−"}${Math.abs(p)} pts`}
      <span className="sr-only">{better ? ", better" : worse ? ", worse" : ""}</span>
    </span>
  );
}

function Bar({ value, color, muted, label }: { value: number; color: string; muted?: boolean; label: string }) {
  return (
    <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-foreground/8" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct(value)}>
      <div
        className="h-full rounded-full transition-[width] duration-500 ease-out motion-reduce:transition-none"
        style={{
          width: `${Math.max(value > 0 ? 2 : 0, value * 100)}%`,
          background: muted ? "repeating-linear-gradient(135deg, rgba(128,128,128,.55) 0 4px, rgba(128,128,128,.35) 4px 8px)" : color,
        }}
      />
    </div>
  );
}

function Footnote({ data }: { data: AudienceOut | null }) {
  return (
    <footer className="px-1 text-caption text-muted-foreground">
      <p>{data?.label ?? AUDIENCE_LABEL}</p>
      <p className="opacity-90">{data?.attribution ?? AUDIENCE_ATTRIBUTION}</p>
    </footer>
  );
}

/* ------------------------------------------------------------------ before / after */

function Comparison({ cmp, now, segments, thisN }: { cmp: AudienceCompare; now: AudienceTotals; segments: AudienceSegment[]; thisN: number }) {
  const [metric, setMetric] = useState<Metric>("distrust");
  const M = METRIC[metric];
  const prevN = cmp.version_n ?? "earlier";
  const before = share(cmp.overall, metric), after = share(now, metric);
  const d = after - before;
  const mb = mean(cmp.overall, metric), ma = mean(now, metric);
  const byKey = new Map(segments.map((s) => [s.key, s]));
  const verdict = points(d) < 0 ? `${M.label} went down. The rewrite reads better to this audience.`
    : points(d) > 0 ? `${M.label} went up. The rewrite reads worse to this audience.`
    : mb != null && ma != null && Math.abs(ma - mb) >= 0.03
      ? `The share barely moved, but the average ${M.label.toLowerCase()} score ${ma < mb ? "dropped" : "rose"}.`
      : `${M.label} did not change much.`;

  return (
    <section aria-label={`Before and after: version ${prevN} against version ${thisN}`} className="glass-strong relative overflow-hidden p-5">
      <div aria-hidden className="pointer-events-none absolute -right-16 -top-16 size-48 rounded-full bg-[#30D158]/15 blur-3xl" />
      <div className="relative">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-headline">Before and after</h3>
          <div role="tablist" aria-label="Compare by" className="inline-flex rounded-full bg-foreground/6 p-0.5">
            {(Object.keys(METRIC) as Metric[]).map((m) => (
              <button
                key={m} role="tab" aria-selected={metric === m} onClick={() => setMetric(m)}
                className={cn("rounded-full px-3 py-1 text-caption font-medium transition-colors", metric === m ? "bg-[#0A84FF] text-white" : "text-muted-foreground hover:text-foreground")}
              >{METRIC[m].label}</button>
            ))}
          </div>
        </div>

        <div className="mt-4 flex items-end justify-between gap-3">
          <div className="min-w-0">
            <p className="text-caption font-medium text-muted-foreground">v{prevN}</p>
            <p className="text-[34px] font-semibold leading-none tracking-tight text-muted-foreground tabular-nums line-through decoration-1">{pct(before)}%</p>
          </div>
          <ArrowRight className="mb-2 size-5 shrink-0 text-muted-foreground" strokeWidth={1.5} aria-hidden />
          <div className="min-w-0">
            <p className="text-caption font-medium">v{thisN} (this version)</p>
            <p className={cn("text-[44px] font-semibold leading-none tracking-tight tabular-nums", M.text)}>{pct(after)}%</p>
          </div>
          <DeltaChip d={d} className="mb-1 !text-footnote" />
        </div>
        <p className="mt-3 text-footnote text-muted-foreground">
          {pct(before)}% of the simulated readers {M.hint} in v{prevN}; now {pct(after)}%.{" "}
          {mb != null && ma != null && <>Average score {mb.toFixed(2)} <ArrowRight className="inline size-3" aria-hidden /> {ma.toFixed(2)}. </>}
          <span className="font-medium text-foreground">{verdict}</span>
        </p>

        <ul className="mt-5 space-y-4">
          {cmp.segments.map((s) => {
            const cur = byKey.get(s.key);
            if (!cur) return null;
            const b = share(s.previous, metric), a = share(cur, metric);
            return (
              <li key={s.key}>
                <div className="flex items-center justify-between gap-2">
                  <span className="min-w-0 truncate text-footnote font-medium">{s.label}</span>
                  <DeltaChip d={a - b} />
                </div>
                <div className="mt-1.5 space-y-1">
                  <div className="flex items-center gap-2"><span className="w-6 text-caption text-muted-foreground">v{prevN}</span><Bar value={b} color={M.color} muted label={`${s.label} ${M.label} in version ${prevN}`} /><span className="w-9 text-right text-caption tabular-nums text-muted-foreground">{pct(b)}%</span></div>
                  <div className="flex items-center gap-2"><span className="w-6 text-caption font-medium">v{thisN}</span><Bar value={a} color={M.color} label={`${s.label} ${M.label} in version ${thisN}`} /><span className="w-9 text-right text-caption font-semibold tabular-nums">{pct(a)}%</span></div>
                </div>
              </li>
            );
          })}
        </ul>
        <p className="mt-4 flex items-center gap-1.5 text-caption text-muted-foreground">
          <span className="inline-block h-2 w-5 rounded-full" style={{ background: "repeating-linear-gradient(135deg, rgba(128,128,128,.55) 0 4px, rgba(128,128,128,.35) 4px 8px)" }} aria-hidden /> earlier version
          <span className="ml-2 inline-block h-2 w-5 rounded-full" style={{ background: M.color }} aria-hidden /> this version
        </p>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ segments */

function SegmentCard({ seg, selected, onSelect, delta }: { seg: AudienceSegment; selected: boolean; onSelect: () => void; delta?: number }) {
  return (
    <button
      type="button" aria-pressed={selected} onClick={onSelect}
      className={cn(
        "glass-strong w-full p-4 text-left transition-[transform,box-shadow] duration-200 hover:-translate-y-0.5 motion-reduce:transition-none",
        selected && "ring-2 ring-[#0A84FF]",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="flex items-center gap-1.5 text-footnote font-semibold"><Users className="size-3.5 shrink-0 text-muted-foreground" strokeWidth={1.75} aria-hidden />{seg.label}</span>
        {delta != null && <DeltaChip d={delta} />}
      </div>
      <div className="mt-3 space-y-2">
        {(Object.keys(METRIC) as Metric[]).map((m) => {
          const Ic = METRIC[m].icon, v = share(seg, m);
          return (
            <div key={m} className="flex items-center gap-2">
              <span className="flex w-[76px] shrink-0 items-center gap-1 text-caption text-muted-foreground"><Ic className="size-3.5" strokeWidth={1.75} aria-hidden />{METRIC[m].label}</span>
              <Bar value={v} color={METRIC[m].color} label={`${seg.label}, ${METRIC[m].label}`} />
              <span className="w-9 text-right text-caption font-semibold tabular-nums">{pct(v)}%</span>
            </div>
          );
        })}
      </div>
      <p className="mt-2.5 text-caption text-muted-foreground">Average distrust score {seg.mean_distrust.toFixed(2)} · {seg.answered}/{seg.n} answered</p>
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
        <p className="rounded-2xl bg-[#0A84FF]/8 p-3.5 text-footnote">
          <span className="mb-0.5 flex items-center gap-1.5 text-caption font-semibold text-[var(--accent-text)]"><Sparkles className="size-3.5" strokeWidth={1.75} aria-hidden /> Likely reason (an AI guess)</span>
          {seg.why}
        </p>
      )}
      <ul className="space-y-2.5">
        {seg.worst.map((p) => (
          <li key={p.id} className="rounded-2xl bg-foreground/5 p-3.5">
            <div className="flex items-start justify-between gap-2">
              <p className="text-footnote font-semibold">{p.bio}</p>
              <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-flag/12 px-2 py-0.5 text-caption font-medium text-flag-text tabular-nums">
                <OctagonAlert className="size-3.5" strokeWidth={1.75} aria-hidden /> {p.distrust != null ? p.distrust.toFixed(2) : "n/a"}
              </span>
            </div>
            <p className="mt-1 text-caption leading-5 text-muted-foreground">{p.detail}</p>
          </li>
        ))}
      </ul>
      {culprit ? (
        <div className="rounded-2xl border border-[#FF9F0A]/50 bg-human/10 p-3.5">
          <p className="mb-1 flex items-center gap-1.5 text-caption font-semibold text-human-text"><Quote className="size-3.5" strokeWidth={1.75} aria-hidden /> {culprit.certain ? "Paragraph that worries this audience most" : "Possibly the paragraph that worries this audience most"}</p>
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
  const counts = useMemo(() => countKinds(data?.personas ?? []), [data?.personas]);
  const progress = data?.progress;
  const answered = progress?.done ?? (data?.personas ?? []).filter((p) => p.signal).length;
  const total = progress?.total ?? TOTAL_DOTS;
  const cmp = data?.compare_to_previous_version ?? null;
  const cmpDelta = new Map((cmp?.segments ?? []).map((s) => [s.key, s.delta.distrust_pct]));

  function run() { start(); }
  const runBtn = (label: string, icon: "play" | "again") => (
    <Button onClick={run} disabled={running} className="rounded-full bg-[#0A84FF] px-5 text-white hover:bg-[#0A84FF]/90">
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
      <section className="glass-strong p-5" aria-label="Audience preview">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1 basis-56">
            <h3 className="text-headline">Audience preview</h3>
            <p className="mt-0.5 text-footnote text-muted-foreground">
              240 simulated Indian readers react to this version, so you can see who may distrust or misread it before it goes out.
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
            <div className="h-1.5 overflow-hidden rounded-full bg-foreground/8"><div className="h-full rounded-full bg-[#0A84FF] transition-[width] duration-300" style={{ width: `${total ? (answered / total) * 100 : 0}%` }} /></div>
          </div>
        )}
        {done && data && (
          <p className="mt-3 text-caption text-muted-foreground tabular-nums">
            {data.overall?.answered ?? 0} of {data.overall?.n ?? TOTAL_DOTS} answered
            {data.elapsed_ms != null && ` · ${(data.elapsed_ms / 1000).toFixed(1)} s`}
            {data.cost_usd != null && ` · $${data.cost_usd.toFixed(4)}`}
          </p>
        )}
        {data?.warnings?.map((w) => <p key={w} className="mt-2 text-caption text-human-text">{w}</p>)}
      </section>

      {(data?.status === "none" || (!data && !error)) && !running && (
        <p className="px-2 text-center text-footnote text-muted-foreground">Nothing has been run for this version yet. It takes a couple of seconds.</p>
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
          <DotGrid personas={data?.personas ?? []} segmentOrder={segs.map((s) => s.key)} finished={!!done} selected={selected} onSelect={(k) => setSelected((cur) => (cur === k ? null : k))} />
          <DotLegend counts={counts} className="mt-4" />
        </section>
      )}

      {done && segs.length > 0 && (
        <>
          <h3 className="px-1 pt-1 text-headline">Six audience groups</h3>
          <div className="grid gap-3 sm:grid-cols-2">
            {segs.map((s) => (
              <SegmentCard key={s.key} seg={s} selected={selected === s.key} onSelect={() => setSelected((cur) => (cur === s.key ? null : s.key))} delta={cmpDelta.get(s.key)} />
            ))}
          </div>
          {segment && <SegmentDetail seg={segment} culprit={data?.culprit_paragraph ?? null} />}
        </>
      )}

      <Footnote data={data} />
    </div>
  );
}
