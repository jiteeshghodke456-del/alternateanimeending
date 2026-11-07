import { useState } from "react";
import { Check, ChevronDown, ExternalLink, Landmark, Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { SEVERITY_LABEL, SEVERITY_STYLE } from "@/lib/bands";
import { INDIA_PACK_ID, packsApi, type PackOut } from "@/lib/api";
import { reportError, useAsync } from "@/lib/hooks";
import { cn } from "@/lib/utils";

/** Brand Kit → Rules: ready-made rule packs (e.g. the India regulatory pack), each rule citing its source. */
export function RulePacksCard({ admin, onChange }: { admin: boolean; onChange?: () => void }) {
  const q = useAsync(() => packsApi.list(), []);
  const [busy, setBusy] = useState<string | null>(null);
  if (q.error || !q.data?.length) return null; // packs are optional; the rules list below still works

  async function apply(p: PackOut) {
    setBusy(p.id);
    try {
      const r = await packsApi.apply(p.id);
      const parts = [r.added && `${r.added} added`, r.updated && `${r.updated} updated`].filter(Boolean).join(", ");
      toast.success(`${p.name}: ${parts || "already up to date"}`, {
        description: [
          Object.keys(r.linked).length ? `${Object.keys(r.linked).length} linked to your own rules, so the same words are flagged once.` : "",
          r.skipped.length ? `Skipped ${r.skipped.join(", ")}: your brand already uses that code.` : "",
        ].filter(Boolean).join(" ") || undefined,
      });
      q.reload(); onChange?.();
    } catch (e) { reportError(e); } finally { setBusy(null); }
  }
  async function remove(p: PackOut) {
    setBusy(p.id);
    try {
      const r = await packsApi.remove(p.id);
      toast.success(`${p.name} removed (${r.removed} rules)`);
      q.reload(); onChange?.();
    } catch (e) { reportError(e); } finally { setBusy(null); }
  }

  return (
    <section aria-labelledby="rule-packs-title" className="mb-5 space-y-3" data-testid="rule-packs">
      {q.data.map((p) => (
        <div key={p.id} className="glass-strong p-5">
          <div className="flex flex-wrap items-start gap-3">
            <div className="grid size-10 shrink-0 place-items-center rounded-2xl bg-[var(--accent-vivid)]/12 text-[var(--accent-text)]">
              <Landmark className="size-5" strokeWidth={1.5} aria-hidden />
            </div>
            <div className="min-w-0 flex-1 basis-56">
              <h2 id="rule-packs-title" className="text-headline font-semibold tracking-tight">
                {p.name}
                <span className="ml-2 align-middle text-caption font-normal text-muted-foreground">Rule pack · {p.rule_count} rules</span>
              </h2>
              <p className="mt-1 text-footnote text-muted-foreground">{p.description}</p>
              <p className="mt-1.5 text-caption" role="status">
                {p.applied
                  ? <span className="font-medium text-pass-text">Applied: {p.applied_count} rules in this brand.</span>
                  : <span className="text-muted-foreground">Not applied. Sources read {p.retrieved}.</span>}
              </p>
            </div>
            {admin && (
              <div className="flex shrink-0 flex-wrap gap-2">
                <Button size="sm" disabled={busy === p.id} onClick={() => apply(p)} data-testid="pack-apply"
                  className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">
                  {busy === p.id ? <Loader2 className="animate-spin" /> : <Plus strokeWidth={2} />} {p.applied ? "Update" : "Apply"}
                </Button>
                {p.applied && (
                  <Button size="sm" variant="ghost" disabled={busy === p.id} onClick={() => remove(p)} data-testid="pack-remove" className="rounded-full">
                    <Trash2 strokeWidth={1.75} /> Remove
                  </Button>
                )}
              </div>
            )}
          </div>
          <details className="group mt-3">
            <summary className="flex cursor-pointer list-none items-center gap-1 rounded-lg text-footnote font-medium text-[var(--accent-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-vivid)]/60">
              <ChevronDown className="size-4 transition-transform group-open:rotate-180" strokeWidth={1.75} aria-hidden />
              Rules and sources
            </summary>
            <ul className="mt-3 space-y-2">
              {p.rules.map((r) => (
                <li key={r.code} className="rounded-2xl bg-foreground/5 px-3 py-2.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-mono text-caption font-semibold">{r.code}</span>
                    <span className={cn("rounded-full px-2 py-0.5 text-caption font-medium", SEVERITY_STYLE[r.severity])}>{SEVERITY_LABEL[r.severity]}</span>
                    <span className="text-caption text-muted-foreground">{r.check_method === "tier0" ? "word list" : r.check_method === "jev" ? "Jev judgment" : "claim check"}</span>
                    {p.linked[r.code]?.length ? <span className="text-caption text-muted-foreground">· backs {p.linked[r.code].join(", ")}</span> : null}
                  </div>
                  <p className="mt-1 text-footnote">{r.statement}</p>
                  <p className="mt-0.5 text-caption text-muted-foreground">
                    <a href={r.url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2">{r.source_short}, {r.clause}</a>
                  </p>
                </li>
              ))}
            </ul>
            <h3 className="mt-4 text-footnote font-semibold">Sources</h3>
            <ul className="mt-1.5 space-y-1.5">
              {p.sources.map((s) => (
                <li key={s.id} className="text-caption text-muted-foreground">
                  <a href={s.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 font-medium text-[var(--accent-text)] underline underline-offset-2">
                    {s.title}<ExternalLink className="size-3 shrink-0" strokeWidth={1.5} aria-hidden /><span className="sr-only"> (opens in a new tab)</span>
                  </a>
                  <span> · {s.issuer}. {s.status}. Read {s.retrieved}.</span>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-caption text-muted-foreground">Checks, not legal advice: a reviewer still decides. Quotes are short phrases; follow the links for the full text.</p>
          </details>
        </div>
      ))}
    </section>
  );
}

/** /setup → rules step: a one-click "Add the India regulatory pack" next to the starter set. */
export function SetupPackButton({ packId = INDIA_PACK_ID, onDone }: { packId?: string; onDone?: () => void }) {
  const q = useAsync(() => packsApi.list(), []);
  const [busy, setBusy] = useState(false);
  const pack = q.data?.find((p) => p.id === packId);
  if (q.error || (!q.loading && !pack)) return null;
  async function add() {
    setBusy(true);
    try {
      const r = await packsApi.apply(packId);
      toast.success(`India regulatory pack: ${r.added + r.updated} rules added, each citing its source.`);
      q.reload(); onDone?.();
    } catch (e) { reportError(e); } finally { setBusy(false); }
  }
  return (
    <Button variant="secondary" className="mt-2 h-auto min-h-9 whitespace-normal rounded-full" disabled={busy || q.loading || pack?.applied} onClick={add} data-testid="setup-pack-apply">
      {busy ? <Loader2 className="animate-spin" /> : pack?.applied ? <Check strokeWidth={2} /> : <Landmark strokeWidth={1.5} />}
      {pack?.applied ? "India regulatory pack added" : `Add the India regulatory pack${pack ? ` (${pack.rule_count})` : ""}`}
    </Button>
  );
}
