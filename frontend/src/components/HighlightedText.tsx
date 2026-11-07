import { Fragment } from "react";
import { BAND } from "@/lib/bands";
import type { Band } from "@/lib/api";
import { cn } from "@/lib/utils";

/** `hits` are the exact problem words inside a wider span (offsets relative to the same text as start/end). `ids` lists every finding sharing the span. */
export interface HighlightSpan {
  start: number; end: number; band: Band; id?: string; ids?: string[]; label?: string; noIcon?: boolean;
  hits?: { start: number; end: number }[];
}

/** A highlight that crosses a paragraph break is split per paragraph, so no empty pill is left on the blank line. Only the last piece gets the icon. */
function splitAtBreaks(text: string, spans: HighlightSpan[]): HighlightSpan[] {
  const out: HighlightSpan[] = [];
  for (const sp of spans) {
    const end = Math.min(sp.end, text.length);
    const pieces: HighlightSpan[] = [];
    let from = sp.start;
    const re = /\n\s*\n/g;
    const seg = text.slice(sp.start, end);
    for (let m = re.exec(seg); m; m = re.exec(seg)) {
      pieces.push({ ...sp, start: from, end: sp.start + m.index });
      from = sp.start + m.index + m[0].length;
    }
    pieces.push({ ...sp, start: from, end });
    if (pieces.length > 1) pieces.forEach((q) => { q.hits = (sp.hits ?? []).filter((h) => h.start >= q.start && h.end <= q.end); });
    const kept = pieces.filter((q) => text.slice(q.start, q.end).trim() !== "");
    kept.forEach((q, i) => out.push({ ...q, noIcon: i < kept.length - 1 }));
  }
  return out;
}

/** Renders text with spans coloured by band. Each span also carries an icon, so colour is never the only cue. */
export function HighlightedText({ text, spans, activeId, onSelect, className }: {
  text: string; spans: HighlightSpan[]; activeId?: string; onSelect?: (id: string) => void; className?: string;
}) {
  const sorted = splitAtBreaks(text, spans).filter((s) => s.end > s.start && s.start >= 0 && s.start < text.length && text.slice(s.start, s.end).trim() !== "").sort((a, b) => a.start - b.start);
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  sorted.forEach((s, i) => {
    if (s.start < cursor) return; // skip overlaps
    if (s.start > cursor) parts.push(<Fragment key={`t${i}`}>{text.slice(cursor, s.start)}</Fragment>);
    const end = Math.min(s.end, text.length);
    const b = BAND[s.band];
    const Icon = b.icon;
    const active = !!activeId && (s.id === activeId || !!s.ids?.includes(activeId));
    const hits = (s.hits ?? []).filter((h) => h.start >= s.start && h.end <= end && h.end > h.start).sort((a, b) => a.start - b.start);
    const wide = hits.length > 0;
    parts.push(
      <mark
        key={`m${i}`}
        onClick={() => s.id && onSelect?.(s.id)}
        className={cn(
          "rounded-md px-1 py-0.5 text-foreground [box-decoration-break:clone] [-webkit-box-decoration-break:clone]",
          wide ? cn("bg-foreground/[0.04] ring-1 ring-foreground/15", active && b.ring + " ring-2") : cn("ring-1", b.bg, b.ring, active && "ring-2"),
          onSelect && "cursor-pointer",
        )}
        title={s.label ?? b.label}
      >
        {(() => {
          // Keep the icon glued to the last word. Trailing whitespace/newlines go after it, otherwise a
          // wrapped highlight ends in an empty pill next to the icon.
          const raw = text.slice(s.start, end);
          const [, head, word, tail] = /^([\s\S]*?)(\S*)(\s*)$/.exec(raw) ?? ["", "", "", ""];
          const lastStart = s.start + head.length; // the last word (+ icon) stays glued together
          const hitMark = (a: number, z: number, k: string) => (
            <span key={k} data-hit className={cn("rounded px-0.5 font-medium underline decoration-wavy decoration-1 underline-offset-4", b.bg, b.text)}>{text.slice(a, z)}</span>
          );
          const body = (from: number, to: number, key: string) => {
            // render [from,to) with the hit words emphasised
            const out: React.ReactNode[] = [];
            let cur = from;
            hits.forEach((h, hi) => {
              const a = Math.max(h.start, from), z = Math.min(h.end, to);
              if (z <= a || a < cur) return;
              if (a > cur) out.push(<Fragment key={`${key}p${hi}`}>{text.slice(cur, a)}</Fragment>);
              out.push(hitMark(a, z, `${key}h${hi}`));
              cur = z;
            });
            if (cur < to) out.push(<Fragment key={`${key}e`}>{text.slice(cur, to)}</Fragment>);
            return out;
          };
          return (
            <>
              {wide ? body(s.start, lastStart, "a") : head}
              <span className="whitespace-nowrap">
                {wide ? body(lastStart, lastStart + word.length, "b") : word}
                {!s.noIcon && <Icon className={cn("ml-1 inline size-3.5 -translate-y-px", b.text)} strokeWidth={2} aria-label={b.label} />}
              </span>
              {tail}
            </>
          );
        })()}
      </mark>,
    );
    cursor = end;
  });
  if (cursor < text.length) parts.push(<Fragment key="tail">{text.slice(cursor)}</Fragment>);
  return <p className={cn("whitespace-pre-wrap break-words text-callout leading-[1.75]", className)}>{parts}</p>;
}
