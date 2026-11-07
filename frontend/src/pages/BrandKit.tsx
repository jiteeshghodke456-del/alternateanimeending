import { useEffect, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CalendarX2, FileUp, Loader2, Pencil, Plus, Power, Radar, Wand2, X } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/PageHeader";
import { PdfImportDialog } from "@/components/PdfImportDialog";
import { onPdfImportRequest, takePdfImport } from "@/lib/tourBus";
import { RuleClarityPanel } from "@/components/RuleClarityPanel"; // Round 3: unclear-rule detection
import { RulePacksCard } from "@/components/RulePacksCard"; // rule packs
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { SEVERITY_LABEL, SEVERITY_STYLE } from "@/lib/bands";
import {
  api, withToken, type CheckMethod, type FactCreate, type FactOut, type RuleCreate, type RuleOut, type Severity, type TermCreate, type TermKind, type TermOut,
} from "@/lib/api";
import { formatDate, reportError, useAsync } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";
import { FactImpactDialog } from "@/components/claims"; // Round 3: fact impact

const surface = "max-h-[90dvh] overflow-y-auto rounded-3xl border-[var(--glass-border)] bg-[var(--glass-strong-bg)] backdrop-blur-2xl";
const tabCls = "rounded-full px-4 data-[state=active]:bg-[var(--accent-fill)] data-[state=active]:text-white";

function Field({ label, htmlFor, children, hint }: { label: string; htmlFor: string; children: ReactNode; hint?: string }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="text-footnote font-medium">{label}</label>
      {children}
      {hint && <p className="text-caption text-muted-foreground">{hint}</p>}
    </div>
  );
}

function Skel() { return <div className="space-y-3"><Skeleton className="h-24" /><Skeleton className="h-24" /><Skeleton className="h-24" /></div>; }

/* ------------------------------------------------------------------ rules */

function RuleDialog({ rule, open, onOpenChange, onSaved }: { rule: RuleOut | null; open: boolean; onOpenChange: (o: boolean) => void; onSaved: () => void }) {
  const [f, setF] = useState<RuleCreate>(() => toForm(rule));
  const [busy, setBusy] = useState(false);
  const [lastKey, setLastKey] = useState<string>("");
  const key = `${open}:${rule?.id ?? "new"}`;
  if (key !== lastKey) { setLastKey(key); setF(toForm(rule)); }
  const set = <K extends keyof RuleCreate>(k: K, v: RuleCreate[K]) => setF((s) => ({ ...s, [k]: v }));
  const valid = f.code.trim() && f.category.trim() && f.statement.trim();
  async function save() {
    setBusy(true);
    try {
      if (rule) {
        const { code: _code, ...rest } = f; void _code;
        await api.updateRule(rule.id, rest);
        toast.success(`${rule.code} updated to a new version`);
      } else {
        await api.createRule({ ...f, code: f.code.trim(), category: f.category.trim().toLowerCase() });
        toast.success("Rule added. New checks use it straight away.");
      }
      onSaved(); onOpenChange(false);
    } catch (e) { reportError(e); } finally { setBusy(false); }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={surface}>
        <DialogHeader>
          <DialogTitle className="text-title3">{rule ? `Edit ${rule.code}` : "Add a rule"}</DialogTitle>
          <DialogDescription>{rule ? "Saving bumps the rule's version. Earlier findings keep the version that flagged them." : "Plain-language rules work best, e.g. “Never say ‘cheap’.”"}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Code" htmlFor="r-code"><Input id="r-code" value={f.code} disabled={!!rule} placeholder="R-TERM-09" onChange={(e) => set("code", e.target.value)} /></Field>
          <Field label="Category" htmlFor="r-cat"><Input id="r-cat" value={f.category} placeholder="terminology" onChange={(e) => set("category", e.target.value)} /></Field>
          <Field label="Severity" htmlFor="r-sev">
            <Select value={f.severity} onValueChange={(v) => set("severity", v as Severity)}>
              <SelectTrigger id="r-sev"><SelectValue /></SelectTrigger>
              <SelectContent>{(["blocking", "major", "minor"] as Severity[]).map((s) => <SelectItem key={s} value={s}>{SEVERITY_LABEL[s]}</SelectItem>)}</SelectContent>
            </Select>
          </Field>
          <Field label="Check method" htmlFor="r-m">
            <Select value={f.check_method} onValueChange={(v) => set("check_method", v as CheckMethod)}>
              <SelectTrigger id="r-m"><SelectValue /></SelectTrigger>
              <SelectContent>{(["tier0", "jev", "claim", "visual"] as CheckMethod[]).map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
            </Select>
          </Field>
        </div>
        <Field label="Statement" htmlFor="r-st"><Textarea id="r-st" rows={2} value={f.statement} onChange={(e) => set("statement", e.target.value)} className="rounded-2xl bg-background/60" /></Field>
        <div className="grid gap-4 sm:grid-cols-[1fr_100px]">
          <Field label="Guideline quote" htmlFor="r-q"><Textarea id="r-q" rows={2} value={f.source_quote} onChange={(e) => set("source_quote", e.target.value)} className="rounded-2xl bg-background/60" /></Field>
          <Field label="Page" htmlFor="r-p"><Input id="r-p" type="number" min={1} value={f.page ?? ""} onChange={(e) => set("page", e.target.value ? Number(e.target.value) : null)} /></Field>
        </div>
        <DialogFooter className="gap-2">
          <Button variant="ghost" className="rounded-full" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button disabled={!valid || busy} onClick={save} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">{busy && <Loader2 className="animate-spin" />} Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
function toForm(r: RuleOut | null): RuleCreate {
  return r
    ? { code: r.code, category: r.category, severity: r.severity, statement: r.statement, source_quote: r.source_quote, page: r.page, check_method: r.check_method }
    : { code: "", category: "", severity: "major", statement: "", source_quote: "", page: null, check_method: "tier0" };
}

function RulesTab({ admin }: { admin: boolean }) {
  const q = useAsync(() => api.rules(), []);
  const [cat, setCat] = useState("all");
  const [sev, setSev] = useState<"all" | Severity>("all");
  const [edit, setEdit] = useState<RuleOut | null>(null);
  const [adding, setAdding] = useState(false);
  const [toggling, setToggling] = useState<number | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importOff, setImportOff] = useState(false);
  const [fresh, setFresh] = useState<RuleOut[]>([]);
  const [enablingAll, setEnablingAll] = useState(false);
  const [tourFile, setTourFile] = useState<File | null>(null);
  // the guided tour asks to import the demo guideline PDF (mailbox: works whether it asked before or after mount)
  useEffect(() => {
    if (!admin) return;
    const take = () => { const f = takePdfImport(); if (f) { setTourFile(f); setImportOpen(true); } };
    take();
    return onPdfImportRequest(take);
  }, [admin]);
  if (q.error) return <ErrorState message={q.error.message} onRetry={q.reload} />;
  if (q.loading && !q.data) return <Skel />;
  const rules = q.data ?? [];
  const cats = [...new Set(rules.map((r) => r.category))].sort();
  const shown = rules.filter((r) => (cat === "all" || r.category === cat) && (sev === "all" || r.severity === sev));

  const pending = fresh.filter((f) => rules.find((r) => r.id === f.id && !r.enabled));
  async function enableAll() {
    setEnablingAll(true);
    try {
      await Promise.all(pending.map((r) => api.updateRule(r.id, { enabled: true })));
      toast.success(`${pending.length} imported rule${pending.length === 1 ? " is" : "s are"} now on`);
      setFresh([]); q.reload();
    } catch (e) { reportError(e); q.reload(); } finally { setEnablingAll(false); }
  }
  async function toggle(r: RuleOut, enabled: boolean) {
    setToggling(r.id);
    try { const u = await api.updateRule(r.id, { enabled }); q.setData((cur) => (cur ?? []).map((x) => (x.id === u.id ? u : x))); }
    catch (e) { reportError(e); } finally { setToggling(null); }
  }
  return (
    <>
      <RulePacksCard admin={admin} onChange={q.reload} />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Select value={cat} onValueChange={setCat}>
          <SelectTrigger aria-label="Filter by category" className="w-[170px] rounded-full capitalize"><SelectValue /></SelectTrigger>
          <SelectContent><SelectItem value="all">All categories</SelectItem>{cats.map((c) => <SelectItem key={c} value={c} className="capitalize">{c}</SelectItem>)}</SelectContent>
        </Select>
        <Select value={sev} onValueChange={(v) => setSev(v as "all" | Severity)}>
          <SelectTrigger aria-label="Filter by severity" className="w-[160px] rounded-full"><SelectValue /></SelectTrigger>
          <SelectContent><SelectItem value="all">All severities</SelectItem>{(["blocking", "major", "minor"] as Severity[]).map((s) => <SelectItem key={s} value={s}>{SEVERITY_LABEL[s]}</SelectItem>)}</SelectContent>
        </Select>
        <span className="text-footnote text-muted-foreground">{shown.length} of {rules.length} rules</span>
        {admin && (
          <span className="ml-auto flex flex-wrap items-center gap-2">
            {!importOff && <Button data-tour="import-button" variant="secondary" onClick={() => { setTourFile(null); setImportOpen(true); }} className="rounded-full"><FileUp strokeWidth={1.75} /> Import from PDF</Button>}
            <Button onClick={() => setAdding(true)} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]"><Plus strokeWidth={2} /> Add rule</Button>
          </span>
        )}
      </div>
      {admin && pending.length > 0 && (
        <div role="status" className="glass-strong mb-4 flex flex-wrap items-center gap-3 !rounded-2xl px-4 py-3 text-footnote">
          <Power className="size-4 shrink-0 text-human-text" strokeWidth={1.75} aria-hidden />
          <p className="min-w-0 flex-1 basis-60">
            <span className="font-semibold">{pending.length} imported rule{pending.length === 1 ? " is" : "s are"} switched off.</span>{" "}
            They don't affect checks until you turn on the switch on the right of each rule ({pending.map((r) => r.code).join(", ")}).
          </p>
          <Button size="sm" disabled={enablingAll} onClick={enableAll} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">{enablingAll && <Loader2 className="animate-spin" />} Enable all</Button>
          <Button size="icon" variant="ghost" aria-label="Dismiss" className="size-8 rounded-full" onClick={() => setFresh([])}><X strokeWidth={1.75} /></Button>
        </div>
      )}
      {shown.length === 0 ? <EmptyState title="No rules match" description="Try a different category or severity." /> : (
        <ul className="space-y-3">
          {shown.map((r) => (
            <li key={r.id} className={cn("glass-strong p-5", !r.enabled && "opacity-65")}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-footnote font-semibold">{r.code}</span>
                {r.pack_id && <span className="rounded-full bg-[#0A84FF]/12 px-2.5 py-0.5 text-caption font-medium text-[var(--accent-text)]">Regulation</span>}
                <span className={cn("rounded-full px-2.5 py-0.5 text-caption font-medium", SEVERITY_STYLE[r.severity])}>{SEVERITY_LABEL[r.severity]}</span>
                <span className="rounded-full bg-foreground/8 px-2.5 py-0.5 text-caption font-medium capitalize">{r.category}</span>
                <span className="text-caption text-muted-foreground">{r.check_method} · v{r.version}</span>
                {!r.enabled && <span className="rounded-full bg-foreground/8 px-2.5 py-0.5 text-caption font-medium">Disabled</span>}
                {admin && (
                  <span className="ml-auto flex items-center gap-2">
                    <Button size="sm" variant="ghost" className="rounded-full" onClick={() => setEdit(r)} aria-label={`Edit ${r.code}`}><Pencil strokeWidth={1.75} /> Edit</Button>
                    <Switch checked={r.enabled} disabled={toggling === r.id} aria-label={`Rule ${r.code} enabled`} onCheckedChange={(v) => toggle(r, v)} />
                  </span>
                )}
              </div>
              <p className="mt-2 text-body">{r.statement}</p>
              {r.source_quote && (
                <blockquote className="mt-3 border-l-2 border-[#0A84FF]/50 pl-3">
                  <p className="text-footnote italic text-foreground/85">&ldquo;{r.source_quote}&rdquo;</p>
                  {r.page != null && <p className="mt-0.5 text-caption text-muted-foreground">Guideline p.{r.page}</p>}
                </blockquote>
              )}
            </li>
          ))}
        </ul>
      )}
      <PdfImportDialog open={importOpen} initialFile={tourFile} onOpenChange={(o) => { setImportOpen(o); if (!o) setTourFile(null); }} onSwitchedOff={() => setImportOff(true)} onImported={(c) => { setFresh(c); q.reload(); }} />
      <RuleDialog rule={edit} open={!!edit || adding} onOpenChange={(o) => { if (!o) { setEdit(null); setAdding(false); } }} onSaved={q.reload} />
    </>
  );
}

/* ------------------------------------------------------------------ words */

const KIND_LABEL: Record<TermKind, string> = { banned: "Banned words", preferred: "Preferred wording", capitalise: "Capitalisation" };

function TermDialog({ term, open, onOpenChange, onSaved }: { term: TermOut | null; open: boolean; onOpenChange: (o: boolean) => void; onSaved: () => void }) {
  const init = (): TermCreate => term ? { kind: term.kind, value: term.value, replacement: term.replacement } : { kind: "banned", value: "", replacement: "" };
  const [f, setF] = useState<TermCreate>(init);
  const [busy, setBusy] = useState(false);
  const [lastKey, setLastKey] = useState("");
  const key = `${open}:${term?.id ?? "new"}`;
  if (key !== lastKey) { setLastKey(key); setF(init()); }
  async function save() {
    setBusy(true);
    try {
      const body = { ...f, value: f.value.trim(), replacement: f.replacement?.trim() || null };
      if (term) await api.updateTerm(term.id, body); else await api.createTerm(body);
      toast.success(term ? "Word updated" : "Word added");
      onSaved(); onOpenChange(false);
    } catch (e) { reportError(e); } finally { setBusy(false); }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={surface}>
        <DialogHeader><DialogTitle className="text-title3">{term ? "Edit word" : "Add a word"}</DialogTitle><DialogDescription>Matched exactly, ignoring case.</DialogDescription></DialogHeader>
        <Field label="Kind" htmlFor="t-kind">
          <Select value={f.kind} onValueChange={(v) => setF((s) => ({ ...s, kind: v as TermKind }))}>
            <SelectTrigger id="t-kind"><SelectValue /></SelectTrigger>
            <SelectContent>{(Object.keys(KIND_LABEL) as TermKind[]).map((k) => <SelectItem key={k} value={k}>{KIND_LABEL[k]}</SelectItem>)}</SelectContent>
          </Select>
        </Field>
        <Field label="Word or phrase" htmlFor="t-val"><Input id="t-val" value={f.value} onChange={(e) => setF((s) => ({ ...s, value: e.target.value }))} /></Field>
        <Field label="Use instead" htmlFor="t-rep" hint="Optional. Shown as the suggested replacement."><Input id="t-rep" value={f.replacement ?? ""} onChange={(e) => setF((s) => ({ ...s, replacement: e.target.value }))} /></Field>
        <DialogFooter className="gap-2">
          <Button variant="ghost" className="rounded-full" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button disabled={!f.value.trim() || busy} onClick={save} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">{busy && <Loader2 className="animate-spin" />} Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function WordsTab({ admin }: { admin: boolean }) {
  const q = useAsync(() => api.terms(), []);
  const [edit, setEdit] = useState<TermOut | null>(null);
  const [adding, setAdding] = useState(false);
  if (q.error) return <ErrorState message={q.error.message} onRetry={q.reload} />;
  if (q.loading && !q.data) return <Skel />;
  const terms = q.data ?? [];
  if (terms.length === 0 && !admin) return <EmptyState title="No words yet" description="Banned words and preferred wording appear here." />;
  return (
    <>
      {admin && <div className="mb-4 flex justify-end"><Button onClick={() => setAdding(true)} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]"><Plus strokeWidth={2} /> Add word</Button></div>}
      <div className="grid gap-4 md:grid-cols-3">
        {(Object.keys(KIND_LABEL) as TermKind[]).map((k) => {
          const rows = terms.filter((t) => t.kind === k);
          return (
            <section key={k} className="glass-strong p-5">
              <h3 className="text-headline">{KIND_LABEL[k]} <span className="text-footnote font-normal text-muted-foreground">· {rows.length}</span></h3>
              {rows.length === 0 ? <p className="mt-3 text-footnote text-muted-foreground">None yet.</p> : (
                <ul className="mt-3 space-y-2">
                  {rows.map((t) => (
                    <li key={t.id} className="flex items-center justify-between gap-2 rounded-xl bg-foreground/5 px-3 py-2 text-footnote">
                      <span className="min-w-0 break-words"><span className="font-medium">{t.value}</span>{t.replacement && <span className="text-muted-foreground"> → {t.replacement}</span>}</span>
                      {admin && <Button size="icon" variant="ghost" className="size-8 shrink-0 rounded-full" aria-label={`Edit ${t.value}`} onClick={() => setEdit(t)}><Pencil className="size-3.5" strokeWidth={1.75} /></Button>}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          );
        })}
      </div>
      <TermDialog term={edit} open={!!edit || adding} onOpenChange={(o) => { if (!o) { setEdit(null); setAdding(false); } }} onSaved={q.reload} />
    </>
  );
}

/* ------------------------------------------------------------------ palette + logo */

function PaletteTab() {
  const q = useAsync(() => api.brand(), []);
  if (q.error) return <ErrorState message={q.error.message} onRetry={q.reload} />;
  if (q.loading && !q.data) return <Skel />;
  const b = q.data!;
  return (
    <div className="space-y-4">
      <section className="glass-strong p-5 md:p-6">
        <h3 className="mb-4 text-headline">Palette</h3>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
          {b.palette.map((c) => (
            <div key={c.hex}>
              <div className="h-20 rounded-2xl border border-border" style={{ background: c.hex }} />
              <p className="mt-2 text-footnote font-medium">{c.name}</p>
              <p className="font-mono text-caption text-muted-foreground">{c.hex}</p>
            </div>
          ))}
        </div>
        {b.fonts.length > 0 && <p className="mt-5 text-footnote text-muted-foreground">Approved fonts: <span className="font-medium text-foreground">{b.fonts.join(", ")}</span></p>}
      </section>
      <section className="glass-strong p-5 md:p-6">
        <h3 className="mb-4 text-headline">Logo</h3>
        {b.logos.length === 0 ? <p className="text-footnote text-muted-foreground">No logo has been uploaded yet.</p> : (
          <div className="grid gap-4 sm:grid-cols-2">
            {b.logos.map((l) => {
              const ok = l.variant === "approved";
              return (
                <figure key={l.id} className="rounded-2xl bg-foreground/5 p-4">
                  <div className="grid h-40 place-items-center rounded-xl bg-white">
                    <img src={withToken(l.url)} alt={ok ? "Approved logo" : "Redrawn look-alike logo (not approved)"} className="max-h-32 max-w-full object-contain" />
                  </div>
                  <figcaption className="mt-3 text-footnote">
                    <span className={cn("rounded-full px-2.5 py-0.5 text-caption font-medium", ok ? "bg-pass/14 text-pass-text" : "bg-flag/12 text-flag-text")}>{ok ? "Approved logo" : "Wrong version, used for the logo check demo"}</span>
                  </figcaption>
                </figure>
              );
            })}
          </div>
        )}
        {b.guideline_url && <p className="mt-4 text-footnote"><a className="font-medium text-[var(--accent-text)] underline-offset-2 hover:underline" href={withToken(b.guideline_url)} target="_blank" rel="noreferrer">Open the guideline PDF</a></p>}
      </section>
    </div>
  );
}

/* ------------------------------------------------------------------ facts */

function FactDialog({ fact, open, onOpenChange, onSaved }: { fact: FactOut | null; open: boolean; onOpenChange: (o: boolean) => void; onSaved: (saved?: FactOut) => void }) {
  const init = (): FactCreate => fact ? { code: fact.code, statement: fact.statement, value: fact.value, source: fact.source, valid_until: fact.valid_until } : { code: "", statement: "", value: "", source: "", valid_until: null };
  const [f, setF] = useState<FactCreate>(init);
  const [busy, setBusy] = useState(false);
  const [lastKey, setLastKey] = useState("");
  const key = `${open}:${fact?.id ?? "new"}`;
  if (key !== lastKey) { setLastKey(key); setF(init()); }
  const set = <K extends keyof FactCreate>(k: K, v: FactCreate[K]) => setF((s) => ({ ...s, [k]: v }));
  async function save() {
    setBusy(true);
    try {
      let saved: FactOut | undefined;
      if (fact) { const { code: _c, ...rest } = f; void _c; saved = await api.updateFact(fact.id, rest); } else await api.createFact({ ...f, code: f.code.trim() });
      toast.success(fact ? "Fact updated" : "Fact added");
      onSaved(saved); onOpenChange(false);
    } catch (e) { reportError(e); } finally { setBusy(false); }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={surface}>
        <DialogHeader><DialogTitle className="text-title3">{fact ? `Edit ${fact.code}` : "Add an approved fact"}</DialogTitle><DialogDescription>Claims in content are checked against these facts. Expired facts no longer support a claim.</DialogDescription></DialogHeader>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Code" htmlFor="f-code"><Input id="f-code" value={f.code} disabled={!!fact} placeholder="F-27" onChange={(e) => set("code", e.target.value)} /></Field>
          <Field label="Valid until" htmlFor="f-until" hint="Leave empty if it never expires."><Input id="f-until" type="date" value={f.valid_until ?? ""} onChange={(e) => set("valid_until", e.target.value || null)} /></Field>
          {/* Round 3 (fact impact): expire a fact in one step; saving then shows the content that relies on it */}
          {fact && !fact.expired && <div className="sm:col-span-2 -mt-2"><Button type="button" size="sm" variant="ghost" className="rounded-full text-flag-text" onClick={() => set("valid_until", yesterday())}><CalendarX2 strokeWidth={1.75} /> Mark as expired</Button></div>}
        </div>
        <Field label="Statement" htmlFor="f-st"><Textarea id="f-st" rows={2} value={f.statement} onChange={(e) => set("statement", e.target.value)} className="rounded-2xl bg-background/60" /></Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Value" htmlFor="f-val"><Input id="f-val" value={f.value ?? ""} onChange={(e) => set("value", e.target.value)} /></Field>
          <Field label="Source" htmlFor="f-src"><Input id="f-src" value={f.source ?? ""} onChange={(e) => set("source", e.target.value)} /></Field>
        </div>
        <DialogFooter className="gap-2">
          <Button variant="ghost" className="rounded-full" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button disabled={!f.code.trim() || !f.statement.trim() || busy} onClick={save} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">{busy && <Loader2 className="animate-spin" />} Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** YYYY-MM-DD of yesterday (local): a fact is expired once valid_until is before today. */
function yesterday(): string {
  const d = new Date(); d.setDate(d.getDate() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function FactsTab({ admin }: { admin: boolean }) {
  const q = useAsync(() => api.facts(), []);
  const [edit, setEdit] = useState<FactOut | null>(null);
  const [adding, setAdding] = useState(false);
  const [impact, setImpact] = useState<number | null>(null); // Round 3: fact impact dialog
  function onSaved(saved?: FactOut) {
    const before = edit;
    q.reload();
    // Editing what a fact says, or expiring it, shows every item that relies on it.
    if (saved && before && (saved.used_by ?? 0) > 0 && (saved.statement !== before.statement || saved.value !== before.value
      || saved.valid_until !== before.valid_until)) setImpact(saved.id);
  }
  if (q.error) return <ErrorState message={q.error.message} onRetry={q.reload} />;
  if (q.loading && !q.data) return <Skel />;
  const facts = q.data ?? [];
  return (
    <>
      {admin && <div className="mb-4 flex justify-end"><Button onClick={() => setAdding(true)} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]"><Plus strokeWidth={2} /> Add fact</Button></div>}
      {facts.length === 0 ? <EmptyState title="No approved facts yet" description="Add facts so claims in content can be checked against them." /> : (
        <ul className="glass-strong divide-y divide-border overflow-hidden">
          {facts.map((f) => (
            <li key={f.id} className="flex flex-wrap items-start gap-x-4 gap-y-2 px-5 py-4">
              <div className="min-w-0 flex-1 basis-64">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-footnote font-semibold">{f.code}</span>
                  {f.expired && <span className="rounded-full bg-flag/12 px-2.5 py-0.5 text-caption font-medium text-flag-text">Expired</span>}
                  {f.used_by !== undefined && <span className={cn("rounded-full px-2.5 py-0.5 text-caption font-medium", f.used_by ? "bg-[#0A84FF]/12 text-[var(--accent-text)]" : "bg-foreground/8 text-muted-foreground")}>{f.used_by ? `Used by ${f.used_by} item${f.used_by === 1 ? "" : "s"}${f.used_by_approved ? ` (${f.used_by_approved} approved)` : ""}` : "Not used yet"}</span>}
                </div>
                <p className="mt-1 text-body">{f.statement}</p>
                <p className="mt-0.5 text-caption text-muted-foreground">{f.source || "No source"}{f.valid_until ? ` · valid until ${formatDate(f.valid_until)}` : " · no expiry"}</p>
              </div>
              <div className="flex shrink-0 gap-1">
                {!!f.used_by && <Button size="sm" variant="ghost" className="rounded-full" onClick={() => setImpact(f.id)} aria-label={`Impact of ${f.code}`}><Radar strokeWidth={1.75} /> Impact</Button>}
                {admin && <Button size="sm" variant="ghost" className="rounded-full" onClick={() => setEdit(f)} aria-label={`Edit ${f.code}`}><Pencil strokeWidth={1.75} /> Edit</Button>}
              </div>
            </li>
          ))}
        </ul>
      )}
      <FactDialog fact={edit} open={!!edit || adding} onOpenChange={(o) => { if (!o) { setEdit(null); setAdding(false); } }} onSaved={onSaved} />
      <FactImpactDialog factId={impact} open={impact != null} onOpenChange={(o) => { if (!o) setImpact(null); }} onDone={q.reload} />
    </>
  );
}

export default function BrandKit() {
  const { user } = useSession();
  const admin = user?.role === "admin";
  const brand = useAsync(() => api.brand(), []);
  const [params] = useSearchParams(); // ?tab=clarity (dashboard "Unclear guidelines" card)
  return (
    <>
      <PageHeader
        title="Brand Kit"
        subtitle={`${brand.data?.name ? `${brand.data.name}. ` : ""}Rules, words, palette and approved facts.${admin ? "" : " You're viewing read-only; brand admins can edit."}`}
        actions={admin ? <Button asChild variant="secondary" className="rounded-full"><Link to="/setup"><Wand2 strokeWidth={1.75} /> Setup guide</Link></Button> : undefined}
      />
      <Tabs defaultValue={params.get("tab") ?? "rules"}>
        <TabsList className="mb-5 h-11 flex-wrap rounded-full bg-[var(--glass-bg)] p-1 backdrop-blur-xl">
          <TabsTrigger value="rules" className={tabCls}>Rules{brand.data ? ` (${brand.data.rule_count})` : ""}</TabsTrigger>
          <TabsTrigger value="words" className={tabCls}>Words</TabsTrigger>
          <TabsTrigger value="palette" className={tabCls}>Palette &amp; Logo</TabsTrigger>
          <TabsTrigger value="facts" className={tabCls}>Facts</TabsTrigger>
          <TabsTrigger value="clarity" className={tabCls}>Rule clarity</TabsTrigger>
        </TabsList>
        <TabsContent value="rules"><RulesTab admin={admin} /></TabsContent>
        <TabsContent value="words"><WordsTab admin={admin} /></TabsContent>
        <TabsContent value="palette"><PaletteTab /></TabsContent>
        <TabsContent value="facts"><FactsTab admin={admin} /></TabsContent>
        <TabsContent value="clarity"><RuleClarityPanel admin={admin} /></TabsContent>
      </Tabs>
    </>
  );
}
