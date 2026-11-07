import { useEffect, useState, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { DISMISS_REASONS, type DismissReason } from "@/lib/api";
import { cn } from "@/lib/utils";

const surface = "rounded-3xl border-[var(--glass-border)] bg-[var(--glass-strong-bg)] backdrop-blur-2xl";

/** A note box dialog: used for override reasons, request changes and reject. */
export function NoteDialog({ open, onOpenChange, title, description, label, confirmLabel, required = true, destructive, onConfirm, extra }: {
  open: boolean; onOpenChange: (o: boolean) => void; title: string; description?: string; label: string;
  confirmLabel: string; required?: boolean; destructive?: boolean; onConfirm: (note: string) => Promise<void>; extra?: ReactNode;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) setNote(""); }, [open]);
  async function go() {
    setBusy(true);
    try { await onConfirm(note.trim()); onOpenChange(false); } catch { /* caller toasts */ } finally { setBusy(false); }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={surface}>
        <DialogHeader>
          <DialogTitle className="text-title3">{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        {extra}
        <div className="space-y-1.5">
          <label htmlFor="note-field" className="text-footnote font-medium">{label}{required ? "" : " (optional)"}</label>
          <Textarea id="note-field" value={note} onChange={(e) => setNote(e.target.value)} rows={4} className="rounded-2xl bg-background/60" />
        </div>
        <DialogFooter className="gap-2">
          <Button variant="ghost" className="rounded-full" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            disabled={busy || (required && !note.trim())}
            onClick={go}
            className={cn("rounded-full text-white", destructive ? "bg-[#C4261D] hover:bg-[#C4261D]/90" : "bg-[var(--accent-fill)] hover:bg-[var(--accent-fill-hover)]")}
          >
            {busy && <Loader2 className="animate-spin" />} {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Dismiss dialog: pick one reason; a note is required for "Other". */
export function DismissDialog({ open, onOpenChange, onConfirm }: {
  open: boolean; onOpenChange: (o: boolean) => void; onConfirm: (reason: DismissReason, note: string) => Promise<void>;
}) {
  const [reason, setReason] = useState<DismissReason>("false_alarm");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) { setReason("false_alarm"); setNote(""); } }, [open]);
  const needNote = reason === "other";
  async function go() {
    setBusy(true);
    try { await onConfirm(reason, note.trim()); onOpenChange(false); } catch { /* caller toasts */ } finally { setBusy(false); }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={surface}>
        <DialogHeader>
          <DialogTitle className="text-title3">Dismiss this finding</DialogTitle>
          <DialogDescription>Tell us why. The reason is saved in the history, helps spot unclear rules, and similar findings later show it as &ldquo;Seen before&rdquo;.</DialogDescription>
        </DialogHeader>
        <fieldset className="space-y-2">
          <legend className="sr-only">Reason</legend>
          {DISMISS_REASONS.map((r) => (
            <label key={r.value} className={cn("flex cursor-pointer items-center gap-3 rounded-2xl border border-border px-4 py-3 text-body transition-colors", reason === r.value && "border-[#0A84FF] bg-[#0A84FF]/8")}>
              <input type="radio" name="dismiss-reason" className="size-4 accent-[#0A84FF]" checked={reason === r.value} onChange={() => setReason(r.value)} />
              {r.label}
            </label>
          ))}
        </fieldset>
        <div className="space-y-1.5">
          <label htmlFor="dismiss-note" className="text-footnote font-medium">Note{needNote ? " (required)" : " (optional)"}</label>
          <Textarea id="dismiss-note" value={note} onChange={(e) => setNote(e.target.value)} rows={3} className="rounded-2xl bg-background/60" />
        </div>
        <DialogFooter className="gap-2">
          <Button variant="ghost" className="rounded-full" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button disabled={busy || (needNote && !note.trim())} onClick={go} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">
            {busy && <Loader2 className="animate-spin" />} Dismiss finding
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
