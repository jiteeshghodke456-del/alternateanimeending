import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { FileText, ImagePlus, Loader2, ShieldCheck, X } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { TipButton } from "@/components/TipButton";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, CHANNELS, DEMO_ITEM_KEY, namesWithRole, type Channel, type WebSource } from "@/lib/api";
import { checkWebUrl, WebUrlField } from "@/components/WebUrlField";
import { reportError } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

const MAX_BYTES = 10 * 1024 * 1024;
const MAX_TEXT = 20_000;
const IMAGE_EXT = /\.(png|jpe?g|webp)$/i;
const IMAGE_MIME = ["image/png", "image/jpeg", "image/webp"];

type Kind = "text" | "image" | "pdf" | "web";

/** Same limits and wording as the server (pipeline/units.py), so a bad file is caught before it is sent. */
function checkFile(file: File, kind: "image" | "pdf"): string | null {
  if (file.size === 0) return "The file is empty.";
  if (file.size > MAX_BYTES) return `That file is ${(file.size / 1048576).toFixed(1)} MB. The limit is 10 MB.`;
  const ok = kind === "image"
    ? IMAGE_MIME.includes(file.type) || IMAGE_EXT.test(file.name)
    : file.type === "application/pdf" || /\.pdf$/i.test(file.name);
  if (!ok) return `“${file.name}” isn't a supported type. Upload ${kind === "image" ? "a PNG, JPG or WEBP image" : "a PDF"}.`;
  return null;
}

function FileDrop({ kind, file, error, onFile }: { kind: "image" | "pdf"; file: File | null; error: string | null; onFile: (f: File | null) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  useEffect(() => {
    if (!file || kind !== "image") { setPreview(null); return; }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file, kind]);
  const accept = kind === "image" ? "image/png,image/jpeg,image/webp" : "application/pdf";
  return (
    <div>
      <input ref={input} type="file" accept={accept} className="sr-only" aria-label={kind === "image" ? "Choose an image" : "Choose a PDF"} onChange={(e) => onFile(e.target.files?.[0] ?? null)} />
      {file ? (
        <div className="flex items-center gap-4 rounded-2xl border border-border bg-background/50 p-4">
          {preview ? <img src={preview} alt="Selected upload preview" className="size-20 rounded-xl object-cover" /> : <FileText className="size-10 text-muted-foreground" strokeWidth={1.5} aria-hidden />}
          <div className="min-w-0 flex-1">
            <p className="truncate text-body font-medium">{file.name}</p>
            <p className="text-caption text-muted-foreground">{(file.size / 1048576).toFixed(2)} MB</p>
          </div>
          <button type="button" aria-label="Remove file" onClick={() => { onFile(null); if (input.current) input.current.value = ""; }} className="grid size-9 place-items-center rounded-full hover:bg-foreground/8"><X className="size-4" /></button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => input.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); onFile(e.dataTransfer.files?.[0] ?? null); }}
          className={cn("grid w-full place-items-center rounded-2xl border border-dashed border-border px-6 py-12 text-center transition-colors hover:bg-foreground/4", over && "border-[#0A84FF] bg-[#0A84FF]/8")}
        >
          <ImagePlus className="size-8 text-muted-foreground" strokeWidth={1.5} aria-hidden />
          <span className="mt-3 text-body font-medium">{kind === "image" ? "PNG, JPG or WEBP, up to 10 MB (40 MP for JPG, 24 MP for PNG)" : "Text-based PDF, up to 20 pages"}</span>
          <span className="mt-1 text-footnote text-muted-foreground">Drop a file here, or click to choose one</span>
        </button>
      )}
      {error && <p role="alert" className="mt-2 text-footnote text-flag-text">{error}</p>}
    </div>
  );
}

export default function ContentNew() {
  const [params] = useSearchParams();
  const demo = params.get("demo") === "1";
  const navigate = useNavigate();
  const { user, users } = useSession();
  const [kind, setKind] = useState<Kind>("text");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [channel, setChannel] = useState<Channel>("instagram");
  const [confidential, setConfidential] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [caption, setCaption] = useState("");
  const [altText, setAltText] = useState("");
  const [busy, setBusy] = useState(false);
  const [url, setUrl] = useState("");
  const [webPreview, setWebPreview] = useState<WebSource | null>(null);
  const web = checkWebUrl(url);
  const [demoState, setDemoState] = useState<"idle" | "loading" | "loaded" | "missing">(demo ? "loading" : "idle");

  useEffect(() => {
    if (!demo) return;
    let alive = true;
    (async () => {
      try {
        const [item] = await api.items({ key: DEMO_ITEM_KEY });
        if (!item?.latest_version_id) throw new Error("missing");
        const v = await api.version(item.latest_version_id);
        if (!alive) return;
        setText(v.text);
        setChannel(item.channel);
        setTitle(item.title.replace(/\s*\(demo_flawed_post\)\s*$/, ""));
        setDemoState("loaded");
      } catch {
        if (alive) setDemoState("missing");
      }
    })();
    return () => { alive = false; };
  }, [demo]);

  function pick(f: File | null) {
    setFile(f);
    setFileError(f && (kind === "image" || kind === "pdf") ? checkFile(f, kind) : null);
  }
  function switchKind(k: string) {
    setKind(k as Kind);
    if (k === "web") setChannel("website");
    setFile(null);
    setFileError(null);
  }

  const canCreate = user?.role === "creator" || user?.role === "admin";
  const textTooLong = text.length > MAX_TEXT;
  const ready = kind === "web" ? !!web.url : kind === "text" ? text.trim().length > 0 && !textTooLong : !!file && !fileError;
  const derivedTitle = title.trim() || (kind === "web" ? "" : kind === "text" ? text.trim().split("\n")[0].slice(0, 60) : file?.name ?? "Untitled");
  const disabledReason = !canCreate
    ? `Only creators and brand admins can add content. Switch to ${namesWithRole(users, "creator", "admin")}.`
    : kind === "web" ? (web.error ?? (web.url ? null : "Paste the address of a web page."))
    : kind === "text" && !text.trim() ? "Paste some text to check."
    : kind !== "text" && !file ? "Choose a file to check."
    : fileError ?? (textTooLong ? `The text is ${text.length.toLocaleString()} characters. The limit is ${MAX_TEXT.toLocaleString()}.` : null);

  async function submit() {
    if (!ready || !canCreate) return;
    setBusy(true);
    try {
      if (kind === "web") {
        const out = await api.createItemFromUrl({ url: web.url!, title: title.trim() || undefined, channel, confidential });
        setWebPreview(out.page);
        navigate(`/items/${out.item_id}`);
        return;
      }
      const out = await api.createItem(
        kind === "text"
          ? { title: derivedTitle, channel, confidential, text }
          : { title: derivedTitle, channel, confidential, file: file!, ...(kind === "image" ? { caption: caption.trim() || undefined, alt_text: altText.trim() || undefined } : {}) },
      );
      navigate(`/items/${out.item_id}`);
    } catch (e) {
      reportError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="New content" subtitle="Paste text, add an image or PDF, or check a live web page. We check it against the brand guidelines before anyone publishes." />
      <div className="glass-strong p-5 md:p-7">
        <Tabs value={kind} onValueChange={switchKind}>
          <TabsList className="rounded-full bg-foreground/6 p-1">
            <TabsTrigger value="text" className="rounded-full px-4">Text</TabsTrigger>
            <TabsTrigger value="image" className="rounded-full px-4">Image</TabsTrigger>
            <TabsTrigger value="pdf" className="rounded-full px-4">PDF</TabsTrigger>
            <TabsTrigger value="web" className="rounded-full px-4">Web page</TabsTrigger>
          </TabsList>

          <TabsContent value="text" className="mt-5 space-y-4">
            {demoState === "loaded" && <p className="rounded-2xl bg-[#0A84FF]/10 p-3 text-footnote text-[var(--accent-text)]">Demo post loaded. It is deliberately flawed so you can watch the check work.</p>}
            {demoState === "missing" && <p className="rounded-2xl bg-human/12 p-3 text-footnote text-human-text">The demo post isn't on this server (it may have been reset). Paste your own text instead.</p>}
            {demoState === "loading" ? (
              <Skeleton className="h-52" />
            ) : (
              <>
                <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={9} aria-label="Content text" placeholder="Paste your caption, email or page copy" className="resize-none rounded-2xl bg-background/60 text-callout leading-7" />
                <p className={cn("text-right text-caption", textTooLong ? "text-flag-text" : "text-muted-foreground")}>{text.length.toLocaleString()} / {MAX_TEXT.toLocaleString()}</p>
              </>
            )}
          </TabsContent>

          <TabsContent value="image" className="mt-5 space-y-4">
            <FileDrop kind="image" file={file} error={fileError} onFile={pick} />
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <label htmlFor="caption" className="text-footnote font-medium">Caption <span className="font-normal text-muted-foreground">(optional)</span></label>
                <Textarea id="caption" value={caption} onChange={(e) => setCaption(e.target.value)} rows={3} className="resize-none rounded-2xl bg-background/60" />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="alt" className="text-footnote font-medium">Alt text <span className="font-normal text-muted-foreground">(describes the image for screen readers)</span></label>
                <Textarea id="alt" value={altText} onChange={(e) => setAltText(e.target.value)} rows={3} className="resize-none rounded-2xl bg-background/60" />
              </div>
            </div>
          </TabsContent>

          <TabsContent value="pdf" className="mt-5">
            <FileDrop kind="pdf" file={file} error={fileError} onFile={pick} />
          </TabsContent>

          <TabsContent value="web" className="mt-5">
            <WebUrlField value={url} onChange={(v) => { setUrl(v); setWebPreview(null); }} error={web.error} busy={busy} preview={webPreview} />
          </TabsContent>
        </Tabs>

        <div className="mt-5 grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end">
          <div className="space-y-1.5">
            <label htmlFor="title" className="text-footnote font-medium">Title <span className="font-normal text-muted-foreground">(optional)</span></label>
            <Input id="title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder={derivedTitle || (kind === "web" ? "The page's title" : "Name this piece")} maxLength={120} />
          </div>
          <div className="flex flex-wrap items-center gap-4">
            <Select value={channel} onValueChange={(v) => setChannel(v as Channel)}>
              <SelectTrigger aria-label="Channel" className="w-[170px] rounded-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {CHANNELS.map((c) => <SelectItem key={c} value={c} className="capitalize">{c}</SelectItem>)}
              </SelectContent>
            </Select>
            <label className="flex items-center gap-2 text-body"><Switch checked={confidential} onCheckedChange={setConfidential} aria-label="Confidential" /> Confidential</label>
          </div>
        </div>
        {confidential && <p className="mt-3 flex items-center gap-2 text-footnote text-muted-foreground"><ShieldCheck className="size-4 text-pass-text" strokeWidth={1.5} /> Only local checks will run. No outside AI sees this.</p>}

        <div className="mt-6">
          <TipButton disabled={!ready || busy || !canCreate} reason={!ready || !canCreate ? disabledReason : null} onClick={submit} className="rounded-full bg-[var(--accent-fill)] px-8 text-white hover:bg-[var(--accent-fill-hover)]">
            {busy && <Loader2 className="animate-spin" />} {kind === "web" ? (busy ? "Fetching…" : "Fetch and check") : "Check"}
          </TipButton>
        </div>
      </div>
    </>
  );
}
