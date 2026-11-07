import { ExternalLink, Globe, ImageIcon } from "lucide-react";
import { HighlightedText, type HighlightSpan } from "@/components/HighlightedText";
import { ImageWithLogo, logoShapes } from "@/components/LogoOverlay";
import type { FindingOut, VersionOut, WebSource } from "@/lib/api";
import { timeAgo } from "@/lib/hooks";
import { cn } from "@/lib/utils";

const HEADING_CLASS: Record<string, string> = {
  h1: "text-title3 font-semibold tracking-tight",
  h2: "text-headline font-semibold tracking-tight",
  h3: "text-callout font-semibold",
};

function plural(n: number, one: string, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

/** Where the page came from and what was extracted. Shown above the page content while it is checked and after. */
export function WebSourceCard({ source, className }: { source: WebSource; className?: string }) {
  const host = (() => { try { return new URL(source.final_url).host; } catch { return source.final_url; } })();
  const fetched = new Date(source.fetched_at);
  const notes = [
    source.truncated > 0 && `${plural(source.truncated, "more text block")} not checked (limit 40 blocks or 20,000 characters)`,
    source.images_skipped > 0 && `${plural(source.images_skipped, "image")} skipped (too small, not PNG/JPG/WEBP, or slow)`,
    source.robots === "allowed" ? "robots.txt allows this page" : "no robots.txt found",
  ].filter(Boolean) as string[];
  return (
    <section aria-label="Web page source" className={cn("rounded-2xl border border-border bg-foreground/4 p-4", className)}>
      <div className="flex items-start gap-3">
        <Globe className="mt-0.5 size-5 shrink-0 text-muted-foreground" strokeWidth={1.5} aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="truncate text-body font-semibold" title={source.title || host}>{source.title || host}</p>
          <a
            href={source.final_url} target="_blank" rel="noopener noreferrer nofollow"
            className="inline-flex max-w-full items-center gap-1 text-footnote text-[var(--accent-text)] underline-offset-2 hover:underline"
          >
            <span className="truncate">{source.final_url}</span><ExternalLink className="size-3.5 shrink-0" strokeWidth={1.5} aria-hidden />
            <span className="sr-only">(opens in a new tab)</span>
          </a>
          <p className="mt-1 text-caption text-muted-foreground">
            Fetched <time dateTime={source.fetched_at} title={fetched.toLocaleString()}>{timeAgo(source.fetched_at) || fetched.toLocaleString()}</time>
            {" "}in {(source.elapsed_ms / 1000).toFixed(1)} s · {(source.bytes / 1024).toFixed(0)} KB · by Brand Gate itself, no third-party service
          </p>
        </div>
      </div>
      <ul className="mt-3 flex flex-wrap gap-2" aria-label="Extracted from the page">
        {[plural(source.headings, "heading"), plural(source.paragraphs, "paragraph"), plural(source.images, "image"),
          plural(source.a11y_issues, "accessibility issue")].map((t) => (
          <li key={t} className="rounded-full bg-background/70 px-3 py-1 text-caption font-medium">{t}</li>
        ))}
      </ul>
      {notes.length > 0 && <p className="mt-2 text-caption text-muted-foreground">{notes.join(" · ")}</p>}
    </section>
  );
}

/** A checked web page: source card, then the extracted headings and paragraphs (with highlights), then its images. */
export function WebPagePanel({ version, findings, spansFor, active, onSelect }: {
  version: VersionOut; findings: FindingOut[]; spansFor: (from: number, to: number) => HighlightSpan[];
  active?: number; onSelect: (id: number) => void;
}) {
  const tags = version.source?.block_tags ?? [];
  const texts = version.units.filter((u) => u.kind === "text");
  const images = version.units.filter((u) => u.kind === "image" && u.image_url);
  // Web images report only strong logo evidence; "not found" is normal on a web page, so it isn't shown.
  const logo = Array.isArray(version.stats.logo)
    ? (version.stats.logo as { unit_idx?: number; verdict?: string }[]).filter((c) => c.verdict !== "not_found") : [];
  return (
    <div className="space-y-5">
      {version.source && <WebSourceCard source={version.source} />}
      {texts.length > 0 && (
        <div className="space-y-3 rounded-2xl bg-foreground/4 p-4">
          {texts.map((u, i) => {
            const tag = tags[i] ?? "p";
            const heading = /^h[1-6]$/.test(tag);
            return (
              <div key={u.id} className={cn(tag === "li" && "flex gap-2")}>
                {tag === "li" && <span aria-hidden className="leading-[1.75] text-muted-foreground">•</span>}
                <HighlightedText
                  text={u.text} className={heading ? cn(HEADING_CLASS[tag] ?? "font-semibold", "leading-snug") : undefined}
                  spans={u.char_start != null && u.char_end != null ? spansFor(u.char_start, u.char_end) : []}
                  activeId={active ? String(active) : undefined} onSelect={(id) => onSelect(Number(id))}
                />
              </div>
            );
          })}
        </div>
      )}
      {images.length > 0 && (
        <section aria-label="Images from the page" className="space-y-4">
          <h3 className="flex items-center gap-2 text-caption font-medium text-muted-foreground"><ImageIcon className="size-3.5" strokeWidth={1.5} aria-hidden /> Images from the page</h3>
          {images.map((u, i) => {
            const own = findings.filter((f) => f.unit_id === u.id);
            const v = { ...version, stats: { ...version.stats, logo: logo.filter((c) => c.unit_idx === u.idx) } };
            return (
              <ImageWithLogo
                key={u.id} src={u.image_url!} alt={`Image ${i + 1} from the page`}
                shapes={logoShapes(v, own)} activeFindingId={active} onSelectFinding={onSelect}
              />
            );
          })}
        </section>
      )}
    </div>
  );
}
