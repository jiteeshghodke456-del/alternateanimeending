import { Globe, Loader2, ShieldCheck } from "lucide-react";
import { Input } from "@/components/ui/input";
import type { WebSource } from "@/lib/api";

/** Client-side check with the same wording as the server (pipeline/webfetch.py). Returns the URL to send, or an error. */
export function checkWebUrl(raw: string): { url: string | null; error: string | null } {
  let s = raw.trim();
  if (!s) return { url: null, error: null };
  if (!/^[a-z][a-z0-9+.-]*:/i.test(s) && /^[\w.-]+\.[a-z]{2,}([:/?#]|$)/i.test(s)) s = `https://${s}`;
  let u: URL;
  try { u = new URL(s); } catch { return { url: null, error: "That doesn't look like a web address. Paste one that starts with https://." }; }
  if (u.protocol !== "http:" && u.protocol !== "https:") return { url: null, error: "Only http:// and https:// web pages can be checked." };
  if (u.username || u.password) return { url: null, error: "Addresses with a user name or password in them can't be checked." };
  if (u.port && !["80", "443", "8080", "8443"].includes(u.port)) return { url: null, error: `Port ${u.port} isn't allowed. Only the standard web ports can be fetched.` };
  if (!u.hostname.includes(".") && !u.hostname.startsWith("[")) return { url: null, error: "Paste a public web address, like https://example.com/page." };
  return { url: u.toString(), error: null };
}

export function WebUrlField({ value, onChange, error, busy, preview }: {
  value: string; onChange: (v: string) => void; error: string | null; busy: boolean; preview: WebSource | null;
}) {
  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <label htmlFor="web-url" className="text-footnote font-medium">Web page address</label>
        <div className="relative">
          <Globe className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" strokeWidth={1.5} aria-hidden />
          <Input
            id="web-url" type="url" inputMode="url" autoComplete="url" spellCheck={false} value={value}
            onChange={(e) => onChange(e.target.value)} placeholder="https://example.com/landing-page"
            aria-invalid={!!error} aria-describedby="web-url-help" className="rounded-full pl-10"
          />
        </div>
        {error
          ? <p id="web-url-help" role="alert" className="text-footnote text-flag-text">{error}</p>
          : <p id="web-url-help" className="text-footnote text-muted-foreground">
              We fetch the page once, keep its main text and up to 4 large images, and check them with the brand rules plus basic accessibility checks. Pages that build their text with JavaScript may come back empty.
            </p>}
      </div>
      {busy && (
        <p role="status" className="flex items-center gap-2 rounded-2xl bg-[#0A84FF]/10 p-3 text-footnote text-[var(--accent-text)]">
          <Loader2 className="size-4 animate-spin" aria-hidden /> Fetching the page and its images (up to about 20 seconds)…
        </p>
      )}
      {preview && (
        <p role="status" className="rounded-2xl bg-pass/14 p-3 text-footnote text-pass-text">
          Extracted “{preview.title || preview.final_url}”: {preview.headings} headings, {preview.paragraphs} paragraphs, {preview.images} images. Starting the check…
        </p>
      )}
      <p className="flex items-start gap-2 text-caption text-muted-foreground">
        <ShieldCheck className="mt-0.5 size-3.5 shrink-0" strokeWidth={1.5} aria-hidden />
        Only public pages: private and internal addresses are refused, robots.txt is respected, and no cookies or logins are sent.
      </p>
    </div>
  );
}
