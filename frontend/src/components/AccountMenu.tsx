import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { ChevronDown, Loader2, LogOut, RotateCcw, Sparkles, Wand2, type LucideIcon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { BrandMark } from "@/components/BrandMark";
import { api } from "@/lib/api";
import { reportError } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

type Action = "reset" | "blank" | "demo";

const CONFIRM: Record<Action, { title: string; body: string; cta: string }> = {
  reset: {
    title: "Reset this workspace?",
    body: "Everything in this workspace goes back to how it started. Content, checks and edits you made here are removed.",
    cta: "Reset workspace",
  },
  blank: {
    title: "Start fresh with your brand?",
    body: "You get a new, empty workspace and sign in as its Admin. A short setup walks you through name, colours, rules, facts and words.",
    cta: "Start fresh",
  },
  demo: {
    title: "Open the demo brand?",
    body: "You get a new copy of the fictional demo brand, with sample rules, facts and posts, and sign in as its Creator.",
    cta: "Open demo brand",
  },
};

export function workspaceLabel(kind: string | null): string {
  return kind === "blank" ? "Your brand" : kind === "demo" ? "Demo brand" : "Workspace";
}

function Item({ icon: Icon, label, hint, onClick, disabled }: { icon: LucideIcon; label: string; hint?: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      role="menuitem"
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="flex w-full items-start gap-3 rounded-xl px-3 py-2.5 text-left transition-colors hover:bg-foreground/6 focus-visible:bg-foreground/6 disabled:cursor-not-allowed disabled:opacity-55"
    >
      <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" strokeWidth={1.75} aria-hidden />
      <span className="min-w-0">
        <span className="block text-footnote font-medium">{label}</span>
        {hint && <span className="block text-caption text-muted-foreground">{hint}</span>}
      </span>
    </button>
  );
}

/** Workspace menu: which brand you're in, plus reset / start fresh / open demo. */
export function AccountMenu() {
  const { brand, workspaceKind, features, user, startWorkspace, logout } = useSession();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState<Action | null>(null);
  const [busy, setBusy] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const canSwitch = features?.workspaces !== false;
  const name = brand?.name ?? "Brand";

  function ask(a: Action) { setOpen(false); setConfirm(a); }

  async function run(a: Action) {
    setBusy(true);
    try {
      if (a === "reset") {
        await api.reset();
        toast.success("Workspace reset");
        window.location.assign("/dashboard");
        return;
      }
      if (a === "blank") {
        await startWorkspace("blank", "admin");
        toast.success("Fresh workspace ready. Let's set up your brand.");
        navigate("/setup");
      } else {
        await startWorkspace("demo", "creator");
        toast.success("Demo brand opened");
        navigate("/dashboard");
      }
      setConfirm(null);
    } catch (e) {
      reportError(e, "That did not work. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Workspace: ${name}, ${workspaceLabel(workspaceKind)}`}
        onClick={() => setOpen((o) => !o)}
        className="flex h-10 items-center gap-1.5 rounded-full border border-[var(--glass-border)] bg-[var(--glass-bg)] pl-1 pr-2 backdrop-blur-xl transition-colors hover:bg-foreground/6"
      >
        <BrandMark brand={brand} className="size-8" />
        <ChevronDown className={cn("size-4 text-muted-foreground transition-transform duration-200", open && "rotate-180")} strokeWidth={1.75} aria-hidden />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            role="menu"
            aria-label="Workspace"
            initial={{ opacity: 0, y: -6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.98 }}
            transition={{ type: "spring", stiffness: 520, damping: 34, mass: 0.6 }}
            className="absolute right-0 top-12 z-50 w-[min(300px,calc(100vw-2rem))] origin-top-right rounded-2xl border border-border bg-popover p-1.5 text-popover-foreground shadow-[0_20px_50px_-12px_rgba(0,0,0,0.3)]"
          >
            <div className="flex items-center gap-3 px-3 pb-3 pt-2.5">
              <BrandMark brand={brand} className="size-10" />
              <div className="min-w-0">
                <p className="truncate text-headline">{name}</p>
                <span className={cn("mt-0.5 inline-flex rounded-full px-2 py-0.5 text-caption font-medium", workspaceKind === "blank" ? "bg-pass/14 text-pass-text" : "bg-[#0A84FF]/12 text-[var(--accent-text)]")}>
                  {workspaceLabel(workspaceKind)}
                </span>
              </div>
            </div>
            <div className="my-1 h-px bg-border" />
            {user?.role === "admin" && workspaceKind === "blank" && (
              <Item icon={Wand2} label="Set up your brand" hint="Name, colours, rules, facts and words" onClick={() => { setOpen(false); navigate("/setup"); }} />
            )}
            <Item icon={RotateCcw} label="Reset this workspace" hint={user?.role === "admin" ? "Back to how it started" : "Brand admins only"} disabled={user?.role !== "admin"} onClick={() => ask("reset")} />
            <Item icon={Sparkles} label="Start fresh" hint={canSwitch ? "An empty workspace for your own brand" : "Not available on this server yet"} disabled={!canSwitch} onClick={() => ask("blank")} />
            <Item icon={Sparkles} label="Open demo brand" hint={canSwitch ? "A new copy of the sample brand" : "Not available on this server yet"} disabled={!canSwitch} onClick={() => ask("demo")} />
            <div className="my-1 h-px bg-border" />
            <Item icon={LogOut} label="Sign out" onClick={async () => { setOpen(false); await logout(); navigate("/"); }} />
          </motion.div>
        )}
      </AnimatePresence>

      <Dialog open={confirm !== null} onOpenChange={(o) => { if (!o && !busy) setConfirm(null); }}>
        <DialogContent className="rounded-3xl border-[var(--glass-border)] bg-[var(--glass-strong-bg)] backdrop-blur-2xl">
          {confirm && (
            <>
              <DialogHeader>
                <DialogTitle className="text-title3">{CONFIRM[confirm].title}</DialogTitle>
                <DialogDescription>{CONFIRM[confirm].body}</DialogDescription>
              </DialogHeader>
              <DialogFooter className="gap-2">
                <Button variant="ghost" className="rounded-full" disabled={busy} onClick={() => setConfirm(null)}>Cancel</Button>
                <Button disabled={busy} onClick={() => run(confirm)} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">
                  {busy && <Loader2 className="animate-spin" />} {CONFIRM[confirm].cta}
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
