import { useEffect, useState } from "react";
import { ExternalLink, Info, Loader2, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { reportError, useAsync } from "@/lib/hooks";
import { scoreApi, type ChannelLimit, type ChannelSettings, type ChannelsOut } from "@/lib/score";
import { cn } from "@/lib/utils";

const NAME: Record<string, string> = { instagram: "Instagram", email: "Email", website: "Website", linkedin: "LinkedIn" };
const LIMIT_LABEL: Record<string, string> = {
  caption_max_chars: "Caption length (characters)", hashtags_max: "Hashtags", mentions_max: "@ tags",
  aspect_ratio: "Image aspect ratio (width ÷ height, min–max)", image_min_width: "Image minimum width (px)",
  post_max_chars: "Post length (characters)", subject_max_chars: "Subject line (characters)",
};
const show = (v: number | number[]) => (Array.isArray(v) ? v.join("–") : v.toLocaleString());

type Draft = { target: string; limits: Record<string, string[]> };
const toDraft = (c: ChannelSettings): Draft => ({
  target: String(c.target_score),
  limits: Object.fromEntries(c.limits.map((l) => [l.key, Array.isArray(l.value) ? l.value.map(String) : [String(l.value)]])),
});

function LimitRow({ l, admin, value, onChange }: { l: ChannelLimit; admin: boolean; value: string[]; onChange: (v: string[]) => void }) {
  const id = `lim-${l.code}`;
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1 basis-56">
          <label htmlFor={id} className="text-footnote font-medium">{LIMIT_LABEL[l.key] ?? l.key}</label>
          <p className="text-caption text-muted-foreground">{l.statement}</p>
        </div>
        <span className={cn("rounded-full px-2 py-0.5 text-caption font-medium", l.kind === "hard" ? "bg-foreground/10" : "bg-foreground/6 text-muted-foreground")}>
          {l.kind === "hard" ? "Platform limit" : "Guidance"} · {l.severity}
        </span>
        {admin ? (
          <span className="flex items-center gap-1">
            {value.map((v, i) => (
              <Input key={i} id={i === 0 ? id : `${id}-${i}`} aria-label={i === 0 ? undefined : `${LIMIT_LABEL[l.key]} maximum`} inputMode="decimal" value={v}
                onChange={(e) => onChange(value.map((x, j) => (j === i ? e.target.value : x)))} className="h-9 w-24 rounded-xl bg-background/60 tabular-nums" />
            ))}
          </span>
        ) : <span id={id} className="text-body tabular-nums">{show(l.value)}</span>}
      </div>
      <p className="mt-1 flex flex-wrap items-center gap-x-1.5 text-caption text-muted-foreground">
        {l.overridden ? <span className="font-medium text-foreground">Brand setting (platform: {show(l.default)})</span> : <span>Platform value</span>}
        <span>·</span>
        <a href={l.citation.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 font-medium text-[var(--accent-text)] underline underline-offset-2">
          {l.citation.short}<ExternalLink className="size-3" strokeWidth={1.5} aria-hidden /><span className="sr-only"> (opens in a new tab)</span>
        </a>
        <span>· read {l.citation.retrieved} · {l.code}</span>
      </p>
    </li>
  );
}

function ChannelCard({ c, admin, onSaved }: { c: ChannelSettings; admin: boolean; onSaved: (out: ChannelsOut) => void }) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(c));
  const [busy, setBusy] = useState(false);
  useEffect(() => setDraft(toDraft(c)), [c]);
  async function save(reset = false) {
    setBusy(true);
    try {
      const limits: Record<string, number | number[] | null> = {};
      for (const l of c.limits) {
        const raw = draft.limits[l.key] ?? [];
        const nums = raw.map((x) => Number(x));
        if (reset) limits[l.key] = null;
        else if (nums.some((n) => !Number.isFinite(n))) throw new Error(`${LIMIT_LABEL[l.key] ?? l.key}: enter a number.`);
        else limits[l.key] = Array.isArray(l.value) ? nums : nums[0];
      }
      const t = Number(draft.target);
      if (!reset && (!Number.isFinite(t) || t < 0 || t > 100)) throw new Error("The target score must be between 0 and 100.");
      onSaved(await scoreApi.saveChannel(c.channel, { target_score: reset ? null : t, limits }));
      toast.success(reset ? `${NAME[c.channel]} reset to platform values` : `${NAME[c.channel]} settings saved`);
    } catch (e) {
      if (e instanceof Error && !("status" in e)) toast.error(e.message); else reportError(e);
    } finally { setBusy(false); }
  }
  const tid = `target-${c.channel}`;
  return (
    <section aria-labelledby={`ch-${c.channel}`} data-testid={`channel-${c.channel}`} className="glass-strong p-5">
      <div className="flex flex-wrap items-center gap-3">
        <h3 id={`ch-${c.channel}`} className="flex-1 text-headline">{NAME[c.channel] ?? c.channel}</h3>
        <label htmlFor={tid} className="text-footnote font-medium">Target Brand Score</label>
        {admin ? (
          <Input id={tid} inputMode="numeric" value={draft.target} onChange={(e) => setDraft({ ...draft, target: e.target.value })} className="h-9 w-20 rounded-xl bg-background/60 tabular-nums" />
        ) : <span id={tid} className="text-body font-semibold tabular-nums">{c.target_score}</span>}
      </div>
      <p className="mt-1 text-caption text-muted-foreground">Below the target, submitting asks the writer for a short reason; it never blocks on its own. Default {c.default_target_score}.</p>
      {c.limits.length ? (
        <ul className="mt-2 divide-y divide-border">
          {c.limits.map((l) => <LimitRow key={l.code} l={l} admin={admin} value={draft.limits[l.key] ?? []} onChange={(v) => setDraft({ ...draft, limits: { ...draft.limits, [l.key]: v } })} />)}
        </ul>
      ) : <p className="mt-3 text-footnote text-muted-foreground">No platform limits are checked for this channel.</p>}
      {admin && (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button disabled={busy} onClick={() => save(false)} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">
            {busy && <Loader2 className="animate-spin" />} Save {NAME[c.channel]}
          </Button>
          <Button disabled={busy} variant="ghost" className="rounded-full" onClick={() => save(true)}><RotateCcw strokeWidth={1.75} /> Reset to platform values</Button>
        </div>
      )}
    </section>
  );
}

/** Brand Kit -> Channels: per-channel score targets and platform limits (with their official sources). */
export function ChannelSettingsPanel({ admin }: { admin: boolean }) {
  const q = useAsync(() => scoreApi.channels(), []);
  if (q.error) return <ErrorState message={q.error.message} onRetry={q.reload} />;
  if (!q.data) return <div className="space-y-3"><Skeleton className="h-40" /><Skeleton className="h-40" /></div>;
  return (
    <div className="space-y-4">
      <p className="text-footnote text-muted-foreground">
        Each channel check cites the official page its number comes from. Change a value to set your own brand limit; the finding then says so.
      </p>
      {q.data.channels.map((c) => <ChannelCard key={c.channel} c={c} admin={admin} onSaved={(out) => q.setData(out)} />)}
      {q.data.not_included.length > 0 && (
        <section className="glass p-4 text-caption text-muted-foreground">
          <p className="mb-1 inline-flex items-center gap-1 font-medium text-foreground"><Info className="size-3.5" strokeWidth={1.75} aria-hidden /> Not checked, on purpose</p>
          <ul className="list-disc space-y-0.5 pl-5">{q.data.not_included.map((n) => <li key={n}>{n}</li>)}</ul>
        </section>
      )}
    </div>
  );
}
