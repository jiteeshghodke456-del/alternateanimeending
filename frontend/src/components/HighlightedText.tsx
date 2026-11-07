import { Fragment } from "react";
import { BAND } from "@/lib/bands";
import type { Band } from "@/lib/api";
import { cn } from "@/lib/utils";

export interface HighlightSpan { start: number; end: number; band: Band; id?: string; label?: string }

/** Renders text with spans coloured by band. Each span also carries an icon, so colour is never the only cue. */
export function HighlightedText({ text, spans, activeId, onSelect, className }: {
  text: string; spans: HighlightSpan[]; activeId?: string; onSelect?: (id: string) => void; className?: string;
}) {
  const sorted = [...spans].filter((s) => s.end > s.start && s.start >= 0 && s.start < text.length && text.slice(s.start, s.end).trim() !== "").sort((a, b) => a.start - b.start);
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  sorted.forEach((s, i) => {
    if (s.start < cursor) return; // skip overlaps
    if (s.start > cursor) parts.push(<Fragment key={`t${i}`}>{text.slice(cursor, s.start)}</Fragment>);
    const end = Math.min(s.end, text.length);
    const b = BAND[s.band];
    const Icon = b.icon;
    const active = s.id && s.id === activeId;
    parts.push(
      <mark
        key={`m${i}`}
        onClick={() => s.id && onSelect?.(s.id)}
        className={cn(
          "rounded-md px-1 py-0.5 text-foreground ring-1 [box-decoration-break:clone] [-webkit-box-decoration-break:clone]",
          b.bg, b.ring, onSelect && "cursor-pointer", active && "ring-2",
        )}
        title={s.label ?? b.label}
      >
        {(() => {
          // Keep the icon glued to the last word. Trailing whitespace/newlines go after it, otherwise a
          // wrapped highlight ends in an empty pill next to the icon.
          const [, head, word, tail] = /^([\s\S]*?)(\S*)(\s*)$/.exec(text.slice(s.start, end)) ?? ["", "", "", ""];
          return (
            <>
              {head}
              <span className="whitespace-nowrap">
                {word}
                <Icon className={cn("ml-1 inline size-3.5 -translate-y-px", b.text)} strokeWidth={2} aria-label={b.label} />
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
