import type { DiffSegment } from "@/lib/api";

/** Word diff between two versions: removed words struck through, added words highlighted. Icons/marks, not colour alone. */
export function DiffView({ segments }: { segments: DiffSegment[] }) {
  const changed = segments.filter((s) => s.op !== "equal").length;
  if (changed === 0) return <p className="text-footnote text-muted-foreground">No wording changed.</p>;
  return (
    <p className="whitespace-pre-wrap break-words text-callout leading-[1.75]">
      {segments.map((s, i) =>
        s.op === "equal" ? <span key={i}>{s.text}</span>
        : s.op === "delete" ? <del key={i} className="rounded bg-flag/15 px-0.5 decoration-flag-text/70"><span className="sr-only">removed: </span>{s.text}</del>
        : <ins key={i} className="rounded bg-pass/18 px-0.5 no-underline"><span className="sr-only">added: </span>{s.text}</ins>,
      )}
    </p>
  );
}
