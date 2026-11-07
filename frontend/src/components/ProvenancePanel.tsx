import { CircleAlert, CircleCheck, ScanSearch, Sparkles } from "lucide-react";
import type { Provenance, ProvenanceCheck, VersionOut } from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS: Record<Provenance["status"], string> = {
  ai_generated: "AI-generated",
  ai_edited: "AI-edited",
  camera_or_unknown: "No AI signal in metadata",
  no_metadata: "No provenance data",
};

/** "Content Credentials" block under an image (item page). Reads VersionOut.units[].provenance (read locally from
 *  the original upload's metadata) and stats.provenance (the R-AI-LABEL decision). Never claims an image is or
 *  isn't AI-made without metadata. */
export function ProvenancePanel({ version }: { version: VersionOut }) {
  const img = version.units.find((u) => u.kind === "image");
  const p = img?.provenance;
  if (!img || !p) return null;
  const check = (version.stats.provenance ?? []).find((c: ProvenanceCheck) => c.unit_idx === img.idx);
  const ai = p.status === "ai_generated" || p.status === "ai_edited";
  return (
    <section aria-label="Content Credentials" className="rounded-2xl bg-foreground/5 p-3.5" data-testid="provenance-panel">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-caption font-medium text-muted-foreground">Content Credentials</span>
        <span
          data-testid="provenance-chip"
          className={cn("inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-caption font-semibold",
            ai ? "bg-human/14 text-human-text" : "bg-foreground/8 text-foreground")}
        >
          {ai ? <Sparkles className="size-3.5" strokeWidth={1.75} aria-hidden /> : <ScanSearch className="size-3.5" strokeWidth={1.75} aria-hidden />}
          {STATUS[p.status]}{ai && p.generator ? ` · ${p.generator}` : ""}
        </span>
      </div>
      {p.status === "no_metadata" ? (
        <p className="mt-1.5 text-footnote text-muted-foreground">No provenance data (metadata may have been stripped). This can't tell whether the image is AI-made.</p>
      ) : (
        <>
          {check && ai && (
            check.needs_label ? (
              <p className="mt-1.5 flex items-start gap-1.5 text-footnote font-medium text-flag-text">
                <CircleAlert className="mt-0.5 size-3.5 shrink-0" strokeWidth={1.75} aria-hidden /> No AI label in the caption or alt text.
              </p>
            ) : check.disclosed ? (
              <p className="mt-1.5 flex items-start gap-1.5 text-footnote font-medium text-pass-text">
                <CircleCheck className="mt-0.5 size-3.5 shrink-0" strokeWidth={1.75} aria-hidden /> Labelled as AI-generated.
              </p>
            ) : null
          )}
          {p.evidence.length > 0 && (
            <details className="mt-1.5 text-footnote">
              <summary className="cursor-pointer text-muted-foreground hover:text-foreground">Evidence ({p.evidence.length})</summary>
              <ul className="mt-1 space-y-0.5 break-words">
                {p.evidence.map((e, i) => (
                  <li key={i}><span className="font-medium">{e.source}</span> · {e.field}: <span className="text-muted-foreground">{e.value}</span></li>
                ))}
              </ul>
            </details>
          )}
          {p.note && <p className="mt-1.5 text-caption text-muted-foreground">{p.note}</p>}
        </>
      )}
      <p className="mt-1 text-caption text-muted-foreground">Read on our server from the file's metadata, before it was stripped from the stored copy.</p>
    </section>
  );
}
