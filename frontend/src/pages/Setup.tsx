import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  ArrowLeft, ArrowRight, Check, Download, FileSpreadsheet, FileUp, ImagePlus, Info, ListChecks, Loader2, Palette as PaletteIcon, PenLine, Plus, Power, Sparkles, Trash2, Type, Upload, Wand2,
} from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { PdfImportDialog } from "@/components/PdfImportDialog";
import { SetupPackButton } from "@/components/RulePacksCard"; // rule packs
import { BrandMark } from "@/components/BrandMark";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { SEVERITY_LABEL, SEVERITY_STYLE } from "@/lib/bands";
import {
  api, withToken, type BrandOut, type FactImportOut, type PaletteColor, type RuleOut, type Severity, type TermKind,
} from "@/lib/api";
import { bestText, contrast, normalizeHex } from "@/lib/color";
import { reportError, toApiError, useAsync } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { FACTS_TEMPLATE_CSV, STARTER_RULES } from "@/lib/starterRules";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ shared bits */

const STEPS = [
  { key: "basics", label: "Name", short: "Name", icon: Type },
  { key: "look", label: "Colours & logo", short: "Colours", icon: PaletteIcon },
  { key: "rules", label: "Rules", short: "Rules", icon: ListChecks },
  { key: "facts", label: "Facts", short: "Facts", icon: FileSpreadsheet },
  { key: "words", label: "Words", short: "Words", icon: PenLine },
] as const;

const CATEGORIES = ["tone", "claims", "messaging", "terminology", "legal", "accessibility", "visual"] as const;
const primary = "rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]";

function Field({ label, htmlFor, hint, children }: { label: string; htmlFor: string; hint?: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="text-footnote font-medium">{label}</label>
      {children}
      {hint && <p className="text-caption text-muted-foreground">{hint}</p>}
    </div>
  );
}

function Notice({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warn" }) {
  return (
    <p role="status" className={cn("flex items-start gap-2 rounded-2xl p-3 text-footnote", tone === "warn" ? "bg-human/12 text-human-text" : "bg-[#0A84FF]/10 text-[var(--accent-text)]")}>
      <Info className="mt-0.5 size-4 shrink-0" strokeWidth={1.75} aria-hidden /> <span>{children}</span>
    </p>
  );
}

function StepFooter({ onBack, onSkip, children }: { onBack?: () => void; onSkip?: () => void; children?: ReactNode }) {
  return (
    <div className="mt-8 flex flex-wrap items-center gap-2 border-t border-border pt-5">
      {onBack && <Button variant="ghost" className="rounded-full" onClick={onBack}><ArrowLeft strokeWidth={1.75} /> Back</Button>}
      <span className="ml-auto flex flex-wrap items-center gap-2">
        {onSkip && <Button variant="ghost" className="rounded-full text-muted-foreground" onClick={onSkip}>Skip for now</Button>}
        {children}
      </span>
    </div>
  );
}

const MISSING = "This server doesn't support this yet. You can skip this step and come back after an update.";

/** First step that still looks empty, so the wizard resumes where the admin left off. */
function firstOpenStep(b: BrandOut): number {
  const c = b.counts;
  const done = [
    !!(b.tagline || b.description),
    b.palette.length > 0 || !!b.logo_url || b.logos.length > 0,
    (c?.rules ?? b.rule_count) > 0,
    (c?.facts ?? b.fact_count) > 0,
    (c?.terms ?? b.term_count) > 0,
  ];
  const i = done.indexOf(false);
  return i === -1 ? 0 : i;
}

/* ------------------------------------------------------------------ 1 basics */

function BasicsStep({ brand, next }: { brand: BrandOut; next: () => void }) {
  const { features, refreshBrand } = useSession();
  const [name, setName] = useState(brand.name);
  const [tagline, setTagline] = useState(brand.tagline ?? "");
  const [description, setDescription] = useState(brand.description ?? "");
  const [busy, setBusy] = useState(false);
  const unsupported = features?.brandEdit === false;

  async function save() {
    setBusy(true);
    try {
      await api.updateBrand({ name: name.trim(), tagline: tagline.trim(), description: description.trim() });
      await refreshBrand();
      toast.success("Saved");
      next();
    } catch (e) {
      const err = toApiError(e);
      if (err.missing) toast.info(MISSING); else reportError(err);
    } finally { setBusy(false); }
  }
  return (
    <>
      <h2 className="text-title2">What's your brand called?</h2>
      <p className="mt-1 text-body text-muted-foreground">This name appears across the app. The description helps reviewers understand who you write for.</p>
      {unsupported && <div className="mt-4"><Notice tone="warn">{MISSING}</Notice></div>}
      <div className="mt-6 grid gap-4">
        <Field label="Brand name" htmlFor="b-name"><Input id="b-name" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder="e.g. Northwind Studio" /></Field>
        <Field label="Tagline" htmlFor="b-tag" hint="Optional. One short line."><Input id="b-tag" value={tagline} maxLength={120} onChange={(e) => setTagline(e.target.value)} placeholder="e.g. Furniture that lasts" /></Field>
        <Field label="Description" htmlFor="b-desc" hint="Optional. Who you are and who you write for, in two or three sentences.">
          <Textarea id="b-desc" rows={4} value={description} maxLength={1000} onChange={(e) => setDescription(e.target.value)} className="rounded-2xl bg-background/60" />
        </Field>
      </div>
      <StepFooter onSkip={next}>
        <Button className={primary} disabled={!name.trim() || busy || unsupported} onClick={save}>{busy && <Loader2 className="animate-spin" />} Save and continue <ArrowRight strokeWidth={2} /></Button>
      </StepFooter>
    </>
  );
}

/* ------------------------------------------------------------------ 2 colours + logo */

type Swatch = PaletteColor & { key: number; text: string };
let swatchKey = 0;
const toSwatch = (c: PaletteColor): Swatch => ({ ...c, hex: normalizeHex(c.hex) ?? "#000000", text: c.hex, key: ++swatchKey });

function ContrastHint({ hex }: { hex: string }) {
  const t = bestText(hex);
  const onWhite = contrast(hex, "#ffffff");
  const ok = t.ratio >= 4.5;
  return (
    <p className="text-caption text-muted-foreground">
      <span className={cn("font-medium", ok ? "text-pass-text" : "text-human-text")}>{ok ? "AA" : t.ratio >= 3 ? "AA large only" : "Low contrast"}</span>
      {" · "}{t.color === "#ffffff" ? "white" : "dark"} text {t.ratio.toFixed(1)}:1 · on white {onWhite.toFixed(1)}:1
    </p>
  );
}

function LookStep({ brand, back, next }: { brand: BrandOut; back: () => void; next: () => void }) {
  const { features, refreshBrand } = useSession();
  const [rows, setRows] = useState<Swatch[]>(() => (brand.palette.length ? brand.palette : [{ name: "Primary", hex: "#0071e3" }]).map(toSwatch));
  const [busy, setBusy] = useState(false);
  const [logo, setLogo] = useState<File | null>(null);
  const [logoErr, setLogoErr] = useState<string | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const reduce = useReducedMotion();
  const currentLogo = brand.logo_url ?? brand.logos.find((l) => l.variant === "approved")?.url ?? null;

  useEffect(() => {
    if (!logo) { setPreview(null); return; }
    const u = URL.createObjectURL(logo);
    setPreview(u);
    return () => URL.revokeObjectURL(u);
  }, [logo]);

  const invalid = rows.some((r) => !normalizeHex(r.text) || !r.name.trim());
  const update = (key: number, patch: Partial<Swatch>) => setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  function pickLogo(f: File | null) {
    setLogoErr(null);
    if (!f) { setLogo(null); return; }
    if (!/^image\/(png|jpeg)$/.test(f.type) && !/\.(png|jpe?g)$/i.test(f.name)) { setLogoErr("Use a PNG or JPEG file."); return; }
    if (f.size > 5 * 1024 * 1024) { setLogoErr(`That file is ${(f.size / 1048576).toFixed(1)} MB. The limit is 5 MB.`); return; }
    setLogo(f);
  }

  async function uploadLogo() {
    if (!logo) return;
    setUploading(true);
    try {
      await api.uploadLogo(logo);
      await refreshBrand();
      setLogo(null);
      if (input.current) input.current.value = "";
      toast.success("Logo uploaded");
    } catch (e) {
      const err = toApiError(e);
      if (err.missing) toast.info(MISSING); else reportError(err);
    } finally { setUploading(false); }
  }

  async function save() {
    setBusy(true);
    try {
      if (features?.palette === false && (!logo || features?.logo === false)) { next(); return; }
      if (features?.palette !== false) await api.setPalette(rows.map((r) => ({ name: r.name.trim(), hex: normalizeHex(r.text)! })));
      if (logo && features?.logo !== false) await api.uploadLogo(logo);
      await refreshBrand();
      toast.success("Colours saved");
      next();
    } catch (e) {
      const err = toApiError(e);
      if (err.missing) toast.info(MISSING); else reportError(err);
    } finally { setBusy(false); }
  }

  return (
    <>
      <h2 className="text-title2">Colours and logo</h2>
      <p className="mt-1 text-body text-muted-foreground">Images are checked against these colours, and the logo check compares uploads with your logo.</p>
      {(features?.palette === false || features?.logo === false) && <div className="mt-4"><Notice tone="warn">{MISSING}</Notice></div>}

      <h3 className="mt-6 text-headline">Palette <span className="text-footnote font-normal text-muted-foreground">· {rows.length} of 12</span></h3>
      <ul className="mt-3 grid gap-3 sm:grid-cols-2">
        <AnimatePresence initial={false}>
          {rows.map((r, i) => {
            const hex = normalizeHex(r.text);
            return (
              <motion.li
                key={r.key}
                layout={!reduce}
                initial={reduce ? false : { opacity: 0, scale: 0.97 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.97 }}
                transition={{ type: "spring", stiffness: 420, damping: 34 }}
                className="flex gap-3 rounded-2xl border border-border bg-background/50 p-3"
              >
                <label className="relative block size-16 shrink-0 cursor-pointer overflow-hidden rounded-xl border border-border shadow-inner" style={{ background: hex ?? r.hex }}>
                  <span className="sr-only">Pick colour {i + 1}</span>
                  <input type="color" value={hex ?? r.hex} onChange={(e) => update(r.key, { hex: e.target.value, text: e.target.value })} className="absolute inset-0 size-full cursor-pointer opacity-0" />
                </label>
                <div className="min-w-0 flex-1 space-y-1.5">
                  <div className="flex flex-wrap gap-2">
                    <Input aria-label={`Colour ${i + 1} name`} value={r.name} maxLength={40} onChange={(e) => update(r.key, { name: e.target.value })} placeholder="Name" className="h-9 min-w-[8rem] flex-1" />
                    <Input
                      aria-label={`Colour ${i + 1} hex`} value={r.text} maxLength={7} spellCheck={false}
                      aria-invalid={!hex}
                      onChange={(e) => { const v = e.target.value; update(r.key, { text: v, hex: normalizeHex(v) ?? r.hex }); }}
                      className={cn("h-9 w-[104px] shrink-0 font-mono", !hex && "border-flag text-flag-text")}
                    />
                    <Button size="icon" variant="ghost" className="size-9 shrink-0 rounded-full" aria-label={`Remove colour ${i + 1}`} disabled={rows.length <= 1} onClick={() => setRows((rs) => rs.filter((x) => x.key !== r.key))}>
                      <Trash2 className="size-4" strokeWidth={1.75} />
                    </Button>
                  </div>
                  {hex ? <ContrastHint hex={hex} /> : <p className="text-caption text-flag-text">Use a hex colour like #1A2B3C.</p>}
                </div>
              </motion.li>
            );
          })}
        </AnimatePresence>
      </ul>
      <Button variant="secondary" className="mt-3 rounded-full" disabled={rows.length >= 12} onClick={() => setRows((rs) => [...rs, toSwatch({ name: `Colour ${rs.length + 1}`, hex: "#8e8e93" })])}>
        <Plus strokeWidth={2} /> Add colour
      </Button>

      <h3 className="mt-8 text-headline">Logo</h3>
      <div className="mt-3 flex flex-wrap items-center gap-4">
        <div className="grid size-28 place-items-center overflow-hidden rounded-2xl border border-border bg-white">
          {preview ? <img src={preview} alt="New logo preview" className="max-h-24 max-w-24 object-contain" />
            : currentLogo ? <img src={withToken(currentLogo)} alt="Current logo" className="max-h-24 max-w-24 object-contain" />
            : <ImagePlus className="size-8 text-neutral-500" strokeWidth={1.5} aria-hidden />}
        </div>
        <div className="min-w-0 flex-1 space-y-2">
          <input ref={input} type="file" accept="image/png,image/jpeg" className="sr-only" aria-label="Choose a logo file" onChange={(e) => pickLogo(e.target.files?.[0] ?? null)} />
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" className="rounded-full" onClick={() => input.current?.click()}><Upload strokeWidth={1.75} /> {logo ? "Choose another" : currentLogo ? "Replace logo" : "Choose a logo"}</Button>
            {logo && <Button className={primary} disabled={uploading} onClick={uploadLogo}>{uploading && <Loader2 className="animate-spin" />} Upload now</Button>}
          </div>
          <p className="text-caption text-muted-foreground">{logo ? logo.name : "PNG or JPEG, up to 5 MB. A transparent PNG works best."}</p>
          {logoErr && <p role="alert" className="text-footnote text-flag-text">{logoErr}</p>}
        </div>
      </div>

      <StepFooter onBack={back} onSkip={next}>
        <Button className={primary} disabled={invalid || busy} onClick={save}>{busy && <Loader2 className="animate-spin" />} {features?.palette === false ? "Continue" : "Save and continue"} <ArrowRight strokeWidth={2} /></Button>
      </StepFooter>
    </>
  );
}

/* ------------------------------------------------------------------ 3 rules */

function RulesStep({ back, next }: { back: () => void; next: () => void }) {
  const { refreshBrand } = useSession();
  const q = useAsync(() => api.rules(), []);
  const [importOpen, setImportOpen] = useState(false);
  const [importOff, setImportOff] = useState(false);
  const [imported, setImported] = useState<RuleOut[]>([]);
  const [starting, setStarting] = useState(false);
  const [enabling, setEnabling] = useState(false);
  const [statement, setStatement] = useState("");
  const [category, setCategory] = useState<string>("tone");
  const [severity, setSeverity] = useState<Severity>("major");
  const [adding, setAdding] = useState(false);
  const rules = q.data ?? [];
  const codes = new Set(rules.map((r) => r.code));
  const starterLeft = STARTER_RULES.filter((r) => !codes.has(r.code));
  const offImported = imported.filter((c) => rules.find((r) => r.id === c.id && !r.enabled));

  async function addStarter() {
    setStarting(true);
    let made = 0;
    try {
      for (const r of starterLeft) { await api.createRule(r); made++; }
      toast.success(`${made} starter rule${made === 1 ? "" : "s"} added. Edit them any time in the Brand Kit.`);
    } catch (e) { reportError(e); } finally { setStarting(false); q.reload(); void refreshBrand(); }
  }

  async function addOne() {
    setAdding(true);
    try {
      const nums = rules.map((r) => /^R-USR-(\d+)$/.exec(r.code)?.[1]).filter(Boolean).map(Number);
      const code = `R-USR-${String((nums.length ? Math.max(...nums) : 0) + 1).padStart(2, "0")}`;
      await api.createRule({ code, category, severity, statement: statement.trim(), check_method: "jev" });
      toast.success(`${code} added`);
      setStatement("");
      q.reload(); void refreshBrand();
    } catch (e) { reportError(e); } finally { setAdding(false); }
  }

  async function enableImported() {
    setEnabling(true);
    try {
      await Promise.all(offImported.map((r) => api.updateRule(r.id, { enabled: true })));
      toast.success("Imported rules are on");
      setImported([]); q.reload();
    } catch (e) { reportError(e); } finally { setEnabling(false); }
  }

  return (
    <>
      <h2 className="text-title2">Your rules</h2>
      <p className="mt-1 text-body text-muted-foreground">Rules are what every post is checked against. Start with a set, import your guideline, or write your own.</p>

      <div className="mt-6 grid gap-3 md:grid-cols-3">
        <div className="flex flex-col rounded-2xl border border-border bg-background/50 p-4">
          <Sparkles className="size-5 text-[var(--accent-text)]" strokeWidth={1.5} aria-hidden />
          <p className="mt-2 text-headline">Starter set</p>
          <p className="mt-1 flex-1 text-footnote text-muted-foreground">{STARTER_RULES.length} common rules: unsupported superlatives, facts, plain language, alt text, one call to action, no ALL CAPS, no pressure tactics.</p>
          <Button className={cn(primary, "mt-3")} disabled={starting || starterLeft.length === 0 || q.loading} onClick={addStarter}>
            {starting && <Loader2 className="animate-spin" />} {starterLeft.length === 0 ? <><Check strokeWidth={2} /> Added</> : "Add the starter set"}
          </Button>
          <SetupPackButton onDone={() => { q.reload(); void refreshBrand(); }} />
        </div>
        <div className="flex flex-col rounded-2xl border border-border bg-background/50 p-4">
          <FileUp className="size-5 text-[var(--accent-text)]" strokeWidth={1.5} aria-hidden />
          <p className="mt-2 text-headline">From your guideline PDF</p>
          <p className="mt-1 flex-1 text-footnote text-muted-foreground">We propose rules with the exact quote and page. You pick which to keep.</p>
          <Button variant="secondary" className="mt-3 rounded-full" disabled={importOff} onClick={() => setImportOpen(true)}>
            {importOff ? "Not available on this server" : "Import from PDF"}
          </Button>
        </div>
        <div className="flex flex-col rounded-2xl border border-border bg-background/50 p-4">
          <PenLine className="size-5 text-[var(--accent-text)]" strokeWidth={1.5} aria-hidden />
          <p className="mt-2 text-headline">Write one</p>
          <label htmlFor="r-new" className="sr-only">Rule statement</label>
          <Textarea id="r-new" rows={2} value={statement} maxLength={1000} onChange={(e) => setStatement(e.target.value)} placeholder="e.g. Never call the product 'cheap'." className="mt-2 rounded-xl bg-background/60 text-footnote" />
          <div className="mt-2 flex gap-2">
            <Select value={category} onValueChange={setCategory}>
              <SelectTrigger aria-label="Category" className="h-9 flex-1 rounded-full capitalize"><SelectValue /></SelectTrigger>
              <SelectContent>{CATEGORIES.map((c) => <SelectItem key={c} value={c} className="capitalize">{c}</SelectItem>)}</SelectContent>
            </Select>
            <Select value={severity} onValueChange={(v) => setSeverity(v as Severity)}>
              <SelectTrigger aria-label="Severity" className="h-9 w-[110px] rounded-full"><SelectValue /></SelectTrigger>
              <SelectContent>{(["blocking", "major", "minor"] as Severity[]).map((s) => <SelectItem key={s} value={s}>{SEVERITY_LABEL[s]}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <Button variant="secondary" className="mt-2 rounded-full" disabled={!statement.trim() || adding} onClick={addOne}>{adding ? <Loader2 className="animate-spin" /> : <Plus strokeWidth={2} />} Add rule</Button>
        </div>
      </div>

      {offImported.length > 0 && (
        <div role="status" className="mt-4 flex flex-wrap items-center gap-3 rounded-2xl bg-human/12 px-4 py-3 text-footnote">
          <Power className="size-4 shrink-0 text-human-text" strokeWidth={1.75} aria-hidden />
          <p className="min-w-0 flex-1 text-human-text">{offImported.length} imported rule{offImported.length === 1 ? " is" : "s are"} switched off until you turn {offImported.length === 1 ? "it" : "them"} on.</p>
          <Button size="sm" className={primary} disabled={enabling} onClick={enableImported}>{enabling && <Loader2 className="animate-spin" />} Turn on</Button>
        </div>
      )}

      <h3 className="mt-8 text-headline">In this workspace <span className="text-footnote font-normal text-muted-foreground">· {rules.length}</span></h3>
      {q.loading && !q.data ? <Skeleton className="mt-3 h-20" /> : q.error ? <Notice tone="warn">{q.error.message}</Notice> : rules.length === 0 ? (
        <p className="mt-2 text-footnote text-muted-foreground">No rules yet. Checks still run the built-in claim and word checks, but rules make findings specific to you.</p>
      ) : (
        <ul className="mt-3 max-h-[280px] space-y-2 overflow-y-auto pr-1">
          {rules.map((r) => (
            <li key={r.id} className={cn("flex items-start gap-3 rounded-xl bg-foreground/[0.04] px-3 py-2.5", !r.enabled && "opacity-70")}>
              <span className={cn("mt-0.5 shrink-0 rounded-full px-2 py-0.5 text-caption font-medium", SEVERITY_STYLE[r.severity])}>{SEVERITY_LABEL[r.severity]}</span>
              <span className="min-w-0 text-footnote">{r.statement}{!r.enabled && <span className="text-muted-foreground"> (off)</span>}</span>
            </li>
          ))}
        </ul>
      )}

      <PdfImportDialog open={importOpen} onOpenChange={setImportOpen} onSwitchedOff={() => setImportOff(true)} onImported={(c) => { setImported(c); q.reload(); void refreshBrand(); }} />
      <StepFooter onBack={back} onSkip={rules.length ? undefined : next}>
        {rules.length > 0 && <Button className={primary} onClick={next}>Continue <ArrowRight strokeWidth={2} /></Button>}
      </StepFooter>
    </>
  );
}

/* ------------------------------------------------------------------ 4 facts */

function downloadTemplate() {
  const url = URL.createObjectURL(new Blob([FACTS_TEMPLATE_CSV], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url; a.download = "facts-template.csv";
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function FactsStep({ back, next }: { back: () => void; next: () => void }) {
  const { features, refreshBrand } = useSession();
  const q = useAsync(() => api.facts(), []);
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<FactImportOut | null>(null);
  const [statement, setStatement] = useState("");
  const [value, setValue] = useState("");
  const [source, setSource] = useState("");
  const [until, setUntil] = useState("");
  const [adding, setAdding] = useState(false);
  const facts = q.data ?? [];

  async function upload(f: File | null) {
    if (!f) return;
    if (!/\.csv$/i.test(f.name) && f.type !== "text/csv") { toast.error("Choose a .csv file."); return; }
    setBusy(true); setResult(null);
    try {
      const r = await api.importFacts(f);
      setResult(r);
      toast.success(`${r.imported} fact${r.imported === 1 ? "" : "s"} imported`);
      q.reload(); void refreshBrand();
    } catch (e) {
      const err = toApiError(e);
      if (err.missing) toast.info("CSV import isn't available on this server yet. Add facts one at a time below."); else reportError(err);
    } finally { setBusy(false); if (input.current) input.current.value = ""; }
  }

  async function addOne() {
    setAdding(true);
    try {
      const nums = facts.map((f) => /^F-(\d+)$/.exec(f.code)?.[1]).filter(Boolean).map(Number);
      const code = `F-${String((nums.length ? Math.max(...nums) : 0) + 1).padStart(2, "0")}`;
      await api.createFact({ code, statement: statement.trim(), value: value.trim(), source: source.trim(), valid_until: until || null });
      toast.success(`${code} added`);
      setStatement(""); setValue(""); setSource(""); setUntil("");
      q.reload(); void refreshBrand();
    } catch (e) { reportError(e); } finally { setAdding(false); }
  }

  return (
    <>
      <h2 className="text-title2">Approved facts</h2>
      <p className="mt-1 text-body text-muted-foreground">Numbers, prices, ratings and awards in a post must match one of these. Expired facts stop backing a claim.</p>

      <div className="mt-6 rounded-2xl border border-dashed border-border bg-background/40 p-4 sm:p-5">
        <div className="flex flex-wrap items-center gap-3">
          <FileSpreadsheet className="size-6 text-[var(--accent-text)]" strokeWidth={1.5} aria-hidden />
          <div className="min-w-0 flex-1 basis-56">
            <p className="text-headline">Upload a CSV</p>
            <p className="text-footnote text-muted-foreground">Columns: statement, value, source, valid_until (YYYY-MM-DD, optional).</p>
          </div>
          <Button variant="ghost" className="rounded-full" onClick={downloadTemplate}><Download strokeWidth={1.75} /> Template</Button>
          <input ref={input} type="file" accept=".csv,text/csv" className="sr-only" aria-label="Choose a facts CSV" onChange={(e) => upload(e.target.files?.[0] ?? null)} />
          <Button className={primary} disabled={busy || features?.factsImport === false} onClick={() => input.current?.click()}>{busy ? <Loader2 className="animate-spin" /> : <Upload strokeWidth={1.75} />} Upload CSV</Button>
        </div>
        {features?.factsImport === false && <div className="mt-3"><Notice tone="warn">CSV import isn't available on this server yet. Add facts one at a time below.</Notice></div>}
        {result && (
          <div className="mt-4 rounded-xl bg-background/70 p-3 text-footnote" role="status">
            <p><span className="font-semibold text-pass-text">{result.imported} imported</span>{result.skipped.length > 0 && <>, <span className="font-semibold text-human-text">{result.skipped.length} skipped</span></>}</p>
            {result.skipped.length > 0 && (
              <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto">
                {result.skipped.map((s) => <li key={s.row} className="text-muted-foreground">Row {s.row}: {s.reason}</li>)}
              </ul>
            )}
          </div>
        )}
      </div>

      <h3 className="mt-8 text-headline">Or add one</h3>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2"><Field label="Statement" htmlFor="f-st"><Input id="f-st" value={statement} maxLength={1000} onChange={(e) => setStatement(e.target.value)} placeholder="e.g. Customers rate us 4.6 out of 5" /></Field></div>
        <Field label="Value" htmlFor="f-val"><Input id="f-val" value={value} onChange={(e) => setValue(e.target.value)} placeholder="4.6" /></Field>
        <Field label="Source" htmlFor="f-src"><Input id="f-src" value={source} onChange={(e) => setSource(e.target.value)} placeholder="Review site, June 2026" /></Field>
        <Field label="Valid until" htmlFor="f-until" hint="Leave empty if it never expires."><Input id="f-until" type="date" value={until} onChange={(e) => setUntil(e.target.value)} /></Field>
        <div className="flex items-end"><Button variant="secondary" className="rounded-full" disabled={!statement.trim() || adding} onClick={addOne}>{adding ? <Loader2 className="animate-spin" /> : <Plus strokeWidth={2} />} Add fact</Button></div>
      </div>

      <h3 className="mt-8 text-headline">In this workspace <span className="text-footnote font-normal text-muted-foreground">· {facts.length}</span></h3>
      {q.loading && !q.data ? <Skeleton className="mt-3 h-16" /> : facts.length === 0 ? <p className="mt-2 text-footnote text-muted-foreground">No facts yet.</p> : (
        <ul className="mt-3 max-h-[240px] space-y-2 overflow-y-auto pr-1">
          {facts.map((f) => (
            <li key={f.id} className="rounded-xl bg-foreground/[0.04] px-3 py-2 text-footnote">
              <span className="font-mono text-caption font-semibold">{f.code}</span> {f.statement}
              {f.expired && <span className="ml-2 rounded-full bg-flag/12 px-2 py-0.5 text-caption font-medium text-flag-text">Expired</span>}
            </li>
          ))}
        </ul>
      )}
      <StepFooter onBack={back} onSkip={facts.length ? undefined : next}>
        {facts.length > 0 && <Button className={primary} onClick={next}>Continue <ArrowRight strokeWidth={2} /></Button>}
      </StepFooter>
    </>
  );
}

/* ------------------------------------------------------------------ 5 words */

function WordsStep({ back, finish }: { back: () => void; finish: () => void }) {
  const { refreshBrand } = useSession();
  const q = useAsync(() => api.terms(), []);
  const [kind, setKind] = useState<Exclude<TermKind, "capitalise">>("banned");
  const [value, setValue] = useState("");
  const [replacement, setReplacement] = useState("");
  const [adding, setAdding] = useState(false);
  const terms = q.data ?? [];

  async function add() {
    setAdding(true);
    try {
      await api.createTerm({ kind, value: value.trim(), replacement: replacement.trim() || null });
      setValue(""); setReplacement("");
      q.reload(); void refreshBrand();
    } catch (e) { reportError(e); } finally { setAdding(false); }
  }

  return (
    <>
      <h2 className="text-title2">Words to avoid and prefer</h2>
      <p className="mt-1 text-body text-muted-foreground">Banned words are always flagged. Preferred wording swaps an old phrase for the one you use now.</p>
      <form className="mt-6 grid gap-3 sm:grid-cols-[150px_1fr_1fr_auto] sm:items-end" onSubmit={(e) => { e.preventDefault(); if (value.trim()) void add(); }}>
        <Field label="Kind" htmlFor="w-kind">
          <Select value={kind} onValueChange={(v) => setKind(v as "banned" | "preferred")}>
            <SelectTrigger id="w-kind" className="rounded-full"><SelectValue /></SelectTrigger>
            <SelectContent><SelectItem value="banned">Banned</SelectItem><SelectItem value="preferred">Preferred</SelectItem></SelectContent>
          </Select>
        </Field>
        <Field label={kind === "banned" ? "Word or phrase" : "Old wording"} htmlFor="w-val"><Input id="w-val" value={value} maxLength={200} onChange={(e) => setValue(e.target.value)} placeholder={kind === "banned" ? "e.g. cheap" : "e.g. sign up"} /></Field>
        <Field label="Use instead" htmlFor="w-rep"><Input id="w-rep" value={replacement} maxLength={200} onChange={(e) => setReplacement(e.target.value)} placeholder={kind === "banned" ? "Optional, e.g. affordable" : "e.g. join"} /></Field>
        <Button type="submit" variant="secondary" className="rounded-full" disabled={!value.trim() || adding || (kind === "preferred" && !replacement.trim())}>{adding ? <Loader2 className="animate-spin" /> : <Plus strokeWidth={2} />} Add</Button>
      </form>

      {q.loading && !q.data ? <Skeleton className="mt-6 h-16" /> : (
        <div className="mt-6 grid gap-3 sm:grid-cols-2">
          {(["banned", "preferred"] as const).map((k) => {
            const rows = terms.filter((t) => t.kind === k);
            return (
              <section key={k} className="rounded-2xl bg-foreground/[0.04] p-4">
                <h3 className="text-headline">{k === "banned" ? "Banned" : "Preferred"} <span className="text-footnote font-normal text-muted-foreground">· {rows.length}</span></h3>
                {rows.length === 0 ? <p className="mt-2 text-footnote text-muted-foreground">None yet.</p> : (
                  <ul className="mt-2 flex flex-wrap gap-1.5">
                    {rows.map((t) => <li key={t.id} className="rounded-full bg-background/80 px-2.5 py-1 text-footnote"><span className="font-medium">{t.value}</span>{t.replacement && <span className="text-muted-foreground"> → {t.replacement}</span>}</li>)}
                  </ul>
                )}
              </section>
            );
          })}
        </div>
      )}
      <StepFooter onBack={back}>
        <Button className={primary} onClick={finish}>Finish <Check strokeWidth={2} /></Button>
      </StepFooter>
    </>
  );
}

/* ------------------------------------------------------------------ done */

function Done({ brand, restart }: { brand: BrandOut; restart: () => void }) {
  const navigate = useNavigate();
  const c = brand.counts;
  const stats = [
    { label: "Rules", n: c?.rules ?? brand.rule_count },
    { label: "Facts", n: c?.facts ?? brand.fact_count },
    { label: "Words", n: c?.terms ?? brand.term_count },
    { label: "Colours", n: brand.palette.length },
  ];
  return (
    <div className="flex flex-col items-center py-6 text-center">
      <motion.div initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ type: "spring", stiffness: 300, damping: 18 }}>
        <BrandMark brand={brand} className="size-16 rounded-2xl text-title2" />
      </motion.div>
      <h2 className="mt-5 text-title1">{brand.name} is ready</h2>
      <p className="mt-2 max-w-[48ch] text-body text-muted-foreground">Paste a post, upload an image or a PDF, and see every issue with the rule it breaks and a suggested fix.</p>
      <dl className="mt-6 grid w-full max-w-md grid-cols-4 gap-2">
        {stats.map((s) => <div key={s.label} className="rounded-2xl bg-foreground/[0.04] py-3"><dt className="text-caption text-muted-foreground">{s.label}</dt><dd className="text-title3 tabular-nums">{s.n}</dd></div>)}
      </dl>
      <div className="mt-8 flex flex-wrap justify-center gap-2">
        <Button size="lg" className={cn(primary, "h-11 px-6")} onClick={() => navigate("/content/new")}><Wand2 strokeWidth={1.75} /> Check your first post</Button>
        <Button size="lg" variant="ghost" className="h-11 rounded-full" onClick={restart}>Review the steps</Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ page */

export default function Setup() {
  const { user, users, brand, login, refreshBrand } = useSession();
  const [params, setParams] = useSearchParams();
  const reduce = useReducedMotion();
  const [step, setStep] = useState<number | null>(null);
  const [dir, setDir] = useState(1);
  const [done, setDone] = useState(false);

  // Resume: an explicit ?step=, else the first step that still looks empty.
  useEffect(() => {
    if (step !== null || !brand) return;
    const s = Number(params.get("step"));
    setStep(s >= 1 && s <= STEPS.length ? s - 1 : firstOpenStep(brand));
  }, [brand, step, params]);

  const go = useMemo(() => (to: number) => {
    setDir(to >= (step ?? 0) ? 1 : -1);
    setStep(to);
    setDone(false);
    setParams({ step: String(to + 1) }, { replace: true });
    window.scrollTo({ top: 0, behavior: reduce ? "auto" : "smooth" });
  }, [step, setParams, reduce]);

  if (user && user.role !== "admin") {
    const admin = users.find((u) => u.role === "admin");
    return (
      <>
        <PageHeader title="Set up your brand" />
        <EmptyState
          icon={Wand2} title="Only a brand admin can set up the brand"
          description={`You're signed in as ${user.name}. Switch to the admin to edit the name, colours, rules, facts and words.`}
          action={admin ? <Button className={primary} onClick={() => login(admin.id).catch(reportError)}>Switch to {admin.name}</Button> : undefined}
        />
      </>
    );
  }
  if (!brand || step === null) {
    return <><PageHeader title="Set up your brand" /><Skeleton className="h-12" /><Skeleton className="mt-4 h-80" /></>;
  }

  const pct = done ? 100 : Math.round((step / STEPS.length) * 100);
  const back = () => go(Math.max(0, step - 1));
  const next = () => (step < STEPS.length - 1 ? go(step + 1) : (setDone(true), void refreshBrand()));

  return (
    <>
      <PageHeader title="Set up your brand" subtitle="Five short steps. Skip anything and come back later; your progress is saved as you go." />

      <nav aria-label="Setup steps" className="mb-5">
        <div className="mb-3 h-1.5 overflow-hidden rounded-full bg-foreground/8" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label="Setup progress">
          <motion.div className="h-full rounded-full bg-[var(--accent-fill)]" initial={false} animate={{ width: `${Math.max(4, pct)}%` }} transition={{ type: "spring", stiffness: 200, damping: 30 }} />
        </div>
        <ol className="grid grid-cols-5 gap-1.5">
          {STEPS.map((s, i) => {
            const active = !done && i === step;
            const past = done || i < step;
            const Icon = s.icon;
            return (
              <li key={s.key}>
                <button
                  type="button"
                  onClick={() => go(i)}
                  aria-current={active ? "step" : undefined}
                  className={cn(
                    "flex w-full flex-col items-center gap-1 rounded-2xl px-1 py-2 text-caption font-medium transition-colors sm:flex-row sm:justify-center sm:gap-2 sm:text-footnote",
                    active ? "bg-[var(--glass-strong-bg)] text-foreground shadow-sm ring-1 ring-[var(--glass-border)]" : "text-muted-foreground hover:bg-foreground/5",
                  )}
                >
                  <span className={cn("grid size-6 shrink-0 place-items-center rounded-full text-caption", past ? "bg-[var(--accent-fill)] text-white" : active ? "bg-foreground text-background" : "bg-foreground/10")}>
                    {past ? <Check className="size-3.5" strokeWidth={2.5} aria-hidden /> : <Icon className="size-3.5" strokeWidth={2} aria-hidden />}
                  </span>
                  <span className="max-w-full truncate"><span className="sm:hidden">{s.short}</span><span className="hidden sm:inline">{s.label}</span></span>
                  <span className="sr-only">{past ? "(done)" : ""}</span>
                </button>
              </li>
            );
          })}
        </ol>
      </nav>

      <section className="glass-strong overflow-hidden p-5 md:p-8">
        <AnimatePresence mode="wait" initial={false} custom={dir}>
          <motion.div
            key={done ? "done" : step}
            initial={reduce ? { opacity: 0 } : { opacity: 0, x: 24 * dir }}
            animate={{ opacity: 1, x: 0 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, x: -24 * dir }}
            transition={{ type: "spring", stiffness: 380, damping: 36, mass: 0.7 }}
          >
            {done ? <Done brand={brand} restart={() => go(0)} />
              : step === 0 ? <BasicsStep brand={brand} next={next} />
              : step === 1 ? <LookStep brand={brand} back={back} next={next} />
              : step === 2 ? <RulesStep back={back} next={next} />
              : step === 3 ? <FactsStep back={back} next={next} />
              : <WordsStep back={back} finish={next} />}
          </motion.div>
        </AnimatePresence>
      </section>
    </>
  );
}
