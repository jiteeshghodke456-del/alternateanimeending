import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CircleCheck, CircleX, FileUp, Loader2, ShieldCheck, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { SEVERITY_LABEL } from "@/lib/bands";
import { api, ApiError, CHECK_METHOD_LABEL, type ImportConfirmIn, type ImportJobOut, type ImportProposal, type RuleOut, type Severity } from "@/lib/api";
import { reportError, toApiError } from "@/lib/hooks";
import { reportImportPhase, tourActive } from "@/lib/tourBus";
import { cn } from "@/lib/utils";

type Edits = Record<string, { statement?: string; category?: string; severity?: Severity }>;
type Phase = "pick" | "working" | "review" | "done" | "error";

const surface = "max-h-[92dvh] w-[calc(100vw-1.5rem)] max-w-6xl overflow-y-auto rounded-3xl border-[var(--glass-border)] bg-[var(--glass-strong-bg)] p-5 backdrop-blur-2xl sm:p-6";

export function PdfImportDialog({ open, onOpenChange, onImported, onSwitchedOff, initialFile }: {
  open: boolean; onOpenChange: (o: boolean) => void;
  /** start reading this file as soon as the dialog opens (the guided tour hands over the demo guideline) */
  initialFile?: File | null;
  /** called after Confirm succeeds, with the created (disabled) rules */
  onImported: (created: RuleOut[]) => void;
  /** called when the server answers 501 (feature flag off), so the button can be hidden */
  onSwitchedOff: () => void;
}) {
  const [phase, setPhase] = useState<Phase>("pick");
  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<ImportJobOut | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [edits, setEdits] = useState<Edits>({});
  const [enable, setEnable] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [created, setCreated] = useState<RuleOut[]>([]);
  const [secs, setSecs] = useState(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const alive = useRef(true);

  const stopPoll = useCallback(() => { if (timer.current) { clearInterval(timer.current); timer.current = null; } }, []);
  useEffect(() => { alive.current = true; return () => { alive.current = false; stopPoll(); }; }, [stopPoll]);

  // fresh start each time the dialog opens
  useEffect(() => {
    if (!open) { stopPoll(); return; }
    setPhase("pick"); setFile(null); setJob(null); setErr(null); setPicked(new Set()); setEdits({}); setEnable(false); setCreated([]); setSecs(0);
  }, [open, stopPoll]);

  const review = useCallback((j: ImportJobOut) => {
    setJob(j);
    setPicked(new Set(j.proposals.filter((p) => p.selected).map((p) => p.id)));
    setEdits({});
    setPhase("review");
  }, []);

  const fail = useCallback((message: string) => { stopPoll(); setErr(message); setPhase("error"); }, [stopPoll]);

  const poll = useCallback((id: string) => {
    stopPoll();
    const t0 = Date.now();
    timer.current = setInterval(async () => {
      if (!alive.current) return;
      setSecs(Math.round((Date.now() - t0) / 1000));
      try {
        const j = await api.importJob(id);
        if (!alive.current) return;
        if (j.status === "done") { stopPoll(); review(j); }
        else if (j.status === "error") fail(j.message ?? "We couldn't read rules from that PDF.");
        else setJob(j);
      } catch (e) {
        const a = toApiError(e);
        fail(a.status === 404 ? "This import expired (the server restarted). Please upload the PDF again." : a.message);
      }
    }, 1000);
  }, [fail, review, stopPoll]);

  async function upload(f: File) {
    setFile(f); setPhase("working"); setErr(null); setSecs(0);
    try {
      const j = await api.startImport(f);
      if (!alive.current) return;
      setJob(j);
      if (j.status === "done") review(j);
      else if (j.status === "error") fail(j.message ?? "We couldn't read rules from that PDF.");
      else poll(j.import_id);
    } catch (e) {
      const a = toApiError(e);
      if (a.notImplemented) { toast.info("Guideline PDF import is switched off on this server."); onSwitchedOff(); onOpenChange(false); return; }
      fail(a.message);
    }
  }

  // the guided tour hands over a file: start at once (after the reset above, which runs first)
  useEffect(() => {
    if (open && initialFile) void upload(initialFile);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialFile]);

  // let the guided tour follow along (it only reads the phase and the counts)
  useEffect(() => {
    reportImportPhase({ phase: open ? phase : "closed", proposals: job?.proposals.length, verified: job?.verified, cost: job?.cost_usd });
  }, [open, phase, job]);

  const proposals = job?.proposals ?? [];
  const allOn = proposals.length > 0 && proposals.every((p) => picked.has(p.id));
  const someOn = proposals.some((p) => picked.has(p.id));
  const unverifiedPicked = useMemo(() => proposals.filter((p) => picked.has(p.id) && !p.quote_verified).length, [proposals, picked]);
  const toggle = (id: string) => setPicked((cur) => { const n = new Set(cur); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const edit = (id: string, patch: Edits[string]) => setEdits((cur) => ({ ...cur, [id]: { ...cur[id], ...patch } }));

  async function confirm() {
    if (!job || picked.size === 0) return;
    setConfirming(true);
    try {
      const ids = proposals.filter((p) => picked.has(p.id)).map((p) => p.id);
      const body: ImportConfirmIn = { selected: ids, enable };
      const sent: NonNullable<ImportConfirmIn["edits"]> = {};
      for (const id of ids) {
        const e = edits[id], p = proposals.find((x) => x.id === id);
        if (!e || !p) continue;
        const d: NonNullable<ImportConfirmIn["edits"]>[string] = {};
        if (e.statement != null && e.statement.trim() && e.statement !== p.rule.statement) d.statement = e.statement.trim();
        if (e.category != null && e.category.trim() && e.category !== p.rule.category) d.category = e.category.trim().toLowerCase();
        if (e.severity && e.severity !== p.rule.severity) d.severity = e.severity;
        if (Object.keys(d).length) sent[id] = d;
      }
      if (Object.keys(sent).length) body.edits = sent;
      const out = await api.confirmImport(job.import_id, body);
      setCreated(out.created);
      setPhase("done");
      onImported(out.created);
      toast.success(`${out.created.length} rule${out.created.length === 1 ? "" : "s"} imported${enable ? "" : ", switched off for now"}`);
    } catch (e) {
      reportError(e);
      if (e instanceof ApiError && e.status === 409) fail(e.message);
    } finally { setConfirming(false); }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange} modal={!tourActive()}>
      <DialogContent
        className={surface}
        // the guided tour card stays usable next to this dialog: pressing it doesn't close the dialog
        onPointerDownOutside={(e) => { if ((e.target as Element | null)?.closest?.("[data-tour-card]")) e.preventDefault(); }}
        onInteractOutside={(e) => { if ((e.target as Element | null)?.closest?.("[data-tour-card]")) e.preventDefault(); }}
      >
        <DialogHeader>
          <DialogTitle className="text-title3">Import rules from a guideline PDF</DialogTitle>
          <DialogDescription>
            An AI model proposes rules from your guideline. Each quote is checked against the PDF text, and you decide what gets added.
          </DialogDescription>
        </DialogHeader>

        {phase === "pick" && (
          <label
            htmlFor="pdf-import-file"
            className="flex cursor-pointer flex-col items-center gap-3 rounded-3xl border-2 border-dashed border-foreground/20 bg-foreground/4 px-6 py-14 text-center transition-colors hover:border-[var(--accent-vivid)]/60 hover:bg-[var(--accent-vivid)]/6 focus-within:border-[var(--accent-vivid)]"
          >
            <span className="grid size-14 place-items-center rounded-2xl bg-[var(--accent-vivid)]/12 text-[var(--accent-text)]"><FileUp className="size-7" strokeWidth={1.5} aria-hidden /></span>
            <span className="text-body font-medium">Choose a guideline PDF</span>
            <span className="text-footnote text-muted-foreground">Up to 20 pages and 10 MB. Nothing is added until you confirm.</span>
            <input
              id="pdf-import-file" type="file" accept="application/pdf,.pdf" className="sr-only" data-testid="pdf-import-input"
              onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void upload(f); }}
            />
          </label>
        )}

        {phase === "working" && (
          <div className="flex flex-col items-center gap-3 py-14 text-center" role="status" aria-live="polite">
            <Loader2 className="size-8 animate-spin text-[var(--accent-text)]" aria-hidden />
            <p className="text-body font-medium">Reading {file?.name ?? "the PDF"}…</p>
            <p className="max-w-[46ch] text-footnote text-muted-foreground">
              {job ? `Found ${job.pages} page${job.pages === 1 ? "" : "s"}. ` : "Uploading. "}Proposing rules and checking each quote against the text. This usually takes about 10 seconds{secs > 0 ? ` (${secs} s so far)` : ""}.
            </p>
          </div>
        )}

        {phase === "error" && (
          <div role="alert" className="flex flex-col items-center gap-3 py-12 text-center">
            <span className="grid size-14 place-items-center rounded-2xl bg-flag/12 text-flag-text"><TriangleAlert className="size-7" strokeWidth={1.5} aria-hidden /></span>
            <p className="text-title3">The import didn't work</p>
            <p className="max-w-[46ch] text-body text-muted-foreground">{err}</p>
            <Button onClick={() => setPhase("pick")} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">Try another PDF</Button>
          </div>
        )}

        {phase === "review" && job && (
          <>
            <div data-tour="import-summary" className="flex flex-wrap items-center gap-x-4 gap-y-1 text-footnote text-muted-foreground">
              <span className="font-medium text-foreground">{job.filename}</span>
              <span>{job.pages} pages</span>
              <span>{proposals.length} proposed rules</span>
              <span className="inline-flex items-center gap-1 text-pass-text"><CircleCheck className="size-3.5" strokeWidth={1.75} aria-hidden /> {job.verified} quotes verified</span>
              {proposals.length - job.verified > 0 && <span className="inline-flex items-center gap-1 text-human-text"><CircleX className="size-3.5" strokeWidth={1.75} aria-hidden /> {proposals.length - job.verified} not found in the PDF</span>}
              {job.elapsed_ms != null && <span>{(job.elapsed_ms / 1000).toFixed(1)} s</span>}
              {job.cost_usd != null && <span>${job.cost_usd.toFixed(4)}</span>}
            </div>
            {job.warnings.map((w) => <p key={w} className="rounded-xl bg-human/12 px-3 py-2 text-footnote text-human-text">{w}</p>)}

            {proposals.length === 0 ? (
              <p className="py-10 text-center text-body text-muted-foreground">No rules were proposed from this PDF. Try a guideline with clear do and don't statements.</p>
            ) : (
              <div className="overflow-x-auto rounded-2xl border border-border">
                <table className="w-full min-w-[980px] border-collapse text-left text-footnote">
                  <caption className="sr-only">Proposed rules. Tick the ones to add.</caption>
                  <thead className="bg-foreground/5 text-caption text-muted-foreground">
                    <tr>
                      <th scope="col" className="w-10 px-3 py-2.5">
                        <input
                          type="checkbox" aria-label="Select all rules" checked={allOn}
                          ref={(el) => { if (el) el.indeterminate = !allOn && someOn; }}
                          onChange={() => setPicked(allOn ? new Set() : new Set(proposals.map((p) => p.id)))}
                          className="size-4 cursor-pointer accent-[var(--accent-vivid)]"
                        />
                      </th>
                      <th scope="col" className="px-2 py-2.5 font-medium">Rule statement</th>
                      <th scope="col" className="w-[130px] px-2 py-2.5 font-medium">Category</th>
                      <th scope="col" className="w-[130px] px-2 py-2.5 font-medium">Severity</th>
                      <th scope="col" className="w-[80px] px-2 py-2.5 font-medium">Method</th>
                      <th scope="col" className="w-[52px] px-2 py-2.5 font-medium">Page</th>
                      <th scope="col" className="w-[300px] px-2 py-2.5 font-medium">Quote from the PDF</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {proposals.map((p) => <Row key={p.id} p={p} on={picked.has(p.id)} toggle={() => toggle(p.id)} edit={edits[p.id]} onEdit={(patch) => edit(p.id, patch)} />)}
                  </tbody>
                </table>
              </div>
            )}

            <DialogFooter className="sticky -bottom-5 z-10 -mx-5 flex-col items-stretch gap-3 border-t border-border bg-[var(--glass-strong-bg)] px-5 py-3 backdrop-blur-xl sm:-bottom-6 sm:-mx-6 sm:flex-row sm:items-center sm:justify-between sm:px-6">
              <div className="space-y-1">
                <p className="text-footnote"><span className="font-semibold tabular-nums">{picked.size}</span> of {proposals.length} selected{unverifiedPicked > 0 && <span className="text-human-text"> · {unverifiedPicked} with a quote we couldn't verify</span>}</p>
                <label className="flex cursor-pointer items-center gap-2 text-footnote text-muted-foreground">
                  <input type="checkbox" checked={enable} onChange={(e) => setEnable(e.target.checked)} className="size-4 accent-[var(--accent-vivid)]" />
                  Switch the new rules on straight away (off by default)
                </label>
              </div>
              <div className="flex gap-2 sm:justify-end">
                <Button variant="ghost" className="rounded-full" onClick={() => onOpenChange(false)}>Cancel</Button>
                <Button disabled={picked.size === 0 || confirming} onClick={confirm} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">
                  {confirming ? <Loader2 className="animate-spin" /> : <ShieldCheck strokeWidth={1.75} />} Confirm {picked.size > 0 ? `${picked.size} rule${picked.size === 1 ? "" : "s"}` : ""}
                </Button>
              </div>
            </DialogFooter>
          </>
        )}

        {phase === "done" && (
          <div className="flex flex-col items-center gap-3 py-10 text-center" role="status">
            <span className="grid size-14 place-items-center rounded-2xl bg-pass/14 text-pass-text"><CircleCheck className="size-7" strokeWidth={1.5} aria-hidden /></span>
            <p className="text-title3">{created.length} rule{created.length === 1 ? "" : "s"} imported</p>
            <p className="max-w-[52ch] text-body text-muted-foreground">
              {enable
                ? "They are switched on and apply to the next check."
                : <>They start <span className="font-medium text-foreground">switched off</span>, so nothing changes yet. In the rules list, turn on the switch next to each one, or use “Enable all” in the banner, to start checking with it.</>}
            </p>
            <p className="font-mono text-caption text-muted-foreground">{created.map((r) => r.code).join(", ")}</p>
            <Button onClick={() => onOpenChange(false)} className="rounded-full bg-[var(--accent-fill)] px-6 text-white hover:bg-[var(--accent-fill-hover)]">Back to the rules</Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function Row({ p, on, toggle, edit, onEdit }: {
  p: ImportProposal; on: boolean; toggle: () => void;
  edit?: { statement?: string; category?: string; severity?: Severity }; onEdit: (patch: { statement?: string; category?: string; severity?: Severity }) => void;
}) {
  const statement = edit?.statement ?? p.rule.statement;
  const category = edit?.category ?? p.rule.category;
  const severity = edit?.severity ?? p.rule.severity;
  return (
    <tr className={cn("align-top transition-colors", on ? "bg-[var(--accent-vivid)]/5" : "opacity-80")}>
      <td className="px-3 py-3"><input type="checkbox" checked={on} onChange={toggle} aria-label={`Add ${p.id}: ${p.rule.statement.slice(0, 60)}`} className="mt-1 size-4 cursor-pointer accent-[var(--accent-vivid)]" /></td>
      <td className="px-2 py-2.5">
        <Textarea aria-label={`${p.id} statement`} rows={3} value={statement} onChange={(e) => onEdit({ statement: e.target.value })} className="min-h-0 rounded-xl bg-background/60 px-2.5 py-1.5 text-footnote leading-5" />
      </td>
      <td className="px-2 py-2.5"><Input aria-label={`${p.id} category`} value={category} onChange={(e) => onEdit({ category: e.target.value })} className="h-8 rounded-xl bg-background/60 px-2.5 text-footnote capitalize" /></td>
      <td className="px-2 py-2.5">
        <Select value={severity} onValueChange={(v) => onEdit({ severity: v as Severity })}>
          <SelectTrigger aria-label={`${p.id} severity`} className="h-8 rounded-xl text-footnote"><SelectValue /></SelectTrigger>
          <SelectContent>{(["blocking", "major", "minor"] as Severity[]).map((s) => <SelectItem key={s} value={s}>{SEVERITY_LABEL[s]}</SelectItem>)}</SelectContent>
        </Select>
      </td>
      <td className="px-2 py-3 text-caption text-muted-foreground">{CHECK_METHOD_LABEL[p.rule.check_method] ?? p.rule.check_method}</td>
      <td className="px-2 py-3 tabular-nums">{p.rule.page ?? "-"}</td>
      <td className="px-2 py-2.5">
        <span className={cn(
          "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-caption font-semibold",
          p.quote_verified ? "bg-pass/14 text-pass-text" : "bg-human/15 text-human-text",
        )}>
          {p.quote_verified ? <CircleCheck className="size-3.5" strokeWidth={2} aria-hidden /> : <CircleX className="size-3.5" strokeWidth={2} aria-hidden />}
          {p.quote_verified ? "Verified" : "Quote not found"}
          <span className="font-normal tabular-nums opacity-80">{Math.round(p.quote_score)}</span>
        </span>
        <p className="mt-1.5 line-clamp-4 text-caption italic leading-5 text-foreground/80">&ldquo;{p.rule.source_quote}&rdquo;</p>
      </td>
    </tr>
  );
}
