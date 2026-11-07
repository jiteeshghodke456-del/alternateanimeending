import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ArrowLeft, ArrowRight, ChevronDown, ChevronUp, CircleCheck, Compass, Loader2, Plus, RotateCcw, SkipForward, TriangleAlert, Wand2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ROLE_LABELS } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useTour } from "@/lib/tour";
import { cn } from "@/lib/utils";

function useMedia(q: string): boolean {
  const [m, setM] = useState(() => (typeof window !== "undefined" && window.matchMedia ? window.matchMedia(q).matches : true));
  useEffect(() => {
    const mq = window.matchMedia(q);
    const on = () => setM(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [q]);
  return m;
}

interface Box { top: number; left: number; width: number; height: number }

/** Finds the first [data-tour] target on the page and follows it; never blocks when it isn't there.
 *  `bottomInset` is the height the guide card takes at the bottom of the screen (the mobile sheet). */
function useTarget(targets: string[], stepKey: string, bottomInset: number): Box | null {
  const reduce = useReducedMotion();
  const [box, setBox] = useState<Box | null>(null);
  const key = targets.join("|");
  const inset = useRef(bottomInset);
  inset.current = bottomInset;
  useEffect(() => {
    let scrolledFor: HTMLElement | null = null;
    let last = "";
    const tick = () => {
      let el: HTMLElement | null = null;
      for (const t of targets) {
        el = document.querySelector<HTMLElement>(`[data-tour="${t}"]`);
        if (el) break;
      }
      if (!el) { if (last) { last = ""; setBox(null); } return; }
      if (scrolledFor !== el) {
        // bring a new target into the free area: below the top bar, above the guide card
        scrolledFor = el;
        const r0 = el.getBoundingClientRect();
        const top = 88, bottom = window.innerHeight - inset.current - 16;
        if (r0.top < top || r0.top > bottom - Math.min(r0.height, 120)) {
          const behavior = reduce ? "auto" : "smooth";
          if (el.closest("[role=dialog]")) el.scrollIntoView({ block: "nearest", behavior });
          else window.scrollBy({ top: r0.top - top - 8, behavior });
        }
      }
      const r = el.getBoundingClientRect();
      const visible = r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight;
      const sig = visible ? `${Math.round(r.top)},${Math.round(r.left)},${Math.round(r.width)},${Math.round(r.height)}` : "";
      if (sig === last) return;
      last = sig;
      setBox(visible ? { top: r.top, left: r.left, width: r.width, height: r.height } : null);
    };
    tick();
    const t = window.setInterval(tick, 300);
    window.addEventListener("scroll", tick, true);
    window.addEventListener("resize", tick);
    return () => { window.clearInterval(t); window.removeEventListener("scroll", tick, true); window.removeEventListener("resize", tick); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, stepKey, reduce]);
  return box;
}

const PAD = 6;

/** The floating guide card: glass, bottom-right on desktop (moves left if it would cover the highlight), bottom sheet on mobile. */
export function TourGuide() {
  const tour = useTour();
  const { user } = useSession();
  const desktop = useMedia("(min-width: 768px)");
  const reduce = useReducedMotion();
  const [min, setMin] = useState(false);
  const card = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const [cardH, setCardH] = useState(0);
  const { active, index, total, step, status, error, progress, data } = tour;
  const targets = active && step.targets ? step.targets(data) : [];
  const box = useTarget(targets, `${active}:${index}:${status}`, desktop ? 0 : cardH);

  // Focus the step title when the step changes (the judge just pressed Next / Back, or the tour began).
  useEffect(() => {
    if (!active) return;
    setMin(false);
    heading.current?.focus({ preventScroll: true });
  }, [active, index]);

  // Esc leaves the tour, unless an app dialog is open (Esc closes that first).
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      const inCard = card.current?.contains(document.activeElement);
      if (!inCard && document.querySelector('[role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"]')) return;
      tour.exit();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, tour]);

  // Keep the card's height known (placement, and room at the bottom of the page on mobile).
  useLayoutEffect(() => {
    if (!active || !card.current) return;
    const el = card.current;
    const ro = new ResizeObserver(() => setCardH(el.offsetHeight));
    ro.observe(el);
    setCardH(el.offsetHeight);
    return () => ro.disconnect();
  }, [active, desktop, min]);
  useEffect(() => {
    if (!active || desktop) return;
    const prev = document.body.style.paddingBottom;
    document.body.style.paddingBottom = `${cardH + 16}px`;
    return () => { document.body.style.paddingBottom = prev; };
  }, [active, desktop, cardH]);

  if (!active) return null;

  // Desktop: bottom-right, unless the highlighted region sits under it; then bottom-left (clear of the sidebar).
  const W = 380, M = 16;
  const vw = typeof window !== "undefined" ? window.innerWidth : 1280;
  const vh = typeof window !== "undefined" ? window.innerHeight : 800;
  const overlapsRight = !!box && box.left + box.width > vw - W - M - PAD && box.top + box.height > vh - cardH - M - PAD;
  const side: "left" | "right" = desktop && overlapsRight ? "left" : "right";

  const n = index + 1;
  const who = user ? `${user.name} · ${ROLE_LABELS[user.role]}` : null;
  const live = `Step ${n} of ${total}: ${step.title}. ${status === "done" ? "Done." : status === "error" ? `Problem: ${error}` : status === "doing" ? progress ?? "Working…" : ""}`;
  const bodyNode = status === "done" && step.doneBody ? step.doneBody(data) : step.body(data);
  const canDo = !!step.doIt && (status === "ready");
  const spring = reduce ? { duration: 0 } : { type: "spring" as const, stiffness: 420, damping: 34 };

  return (
    <>
      {box && (
        <div
          aria-hidden
          className="pointer-events-none fixed z-[56] rounded-2xl ring-2 ring-[var(--accent-fill)] ring-offset-2 ring-offset-transparent shadow-[0_0_0_6px_rgba(149,30,252,0.18)] transition-[top,left,width,height] duration-200 motion-reduce:transition-none"
          style={{ top: box.top - PAD, left: box.left - PAD, width: box.width + PAD * 2, height: box.height + PAD * 2 }}
          data-testid="tour-highlight"
        />
      )}
      <motion.section
        ref={card}
        role="region"
        aria-label="Guided tour"
        data-tour-card=""
        data-tour-step={n}
        data-tour-status={status}
        initial={reduce ? false : { opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={spring}
        onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); tour.exit(); } }}
        style={{ pointerEvents: "auto" }}
        className={cn(
          "fixed z-[58] border border-[var(--glass-border)] bg-white/90 text-foreground shadow-[0_20px_60px_-12px_rgba(0,30,80,0.35)] backdrop-blur-2xl backdrop-saturate-150 dark:bg-neutral-900/90",
          desktop
            ? cn("bottom-4 w-[380px] rounded-3xl", side === "right" ? "right-4" : "left-4 md:left-[264px]")
            : "inset-x-0 bottom-0 max-h-[55dvh] overflow-y-auto rounded-t-3xl pb-[env(safe-area-inset-bottom)]",
        )}
      >
        <p className="sr-only" aria-live="polite" aria-atomic="true">{live}</p>
        <div className="flex items-center gap-2 px-4 pt-3.5 sm:px-5">
          <Compass className="size-4 text-[var(--accent-text)]" strokeWidth={1.75} aria-hidden />
          <span className="text-caption font-semibold text-[var(--accent-text)]" data-testid="tour-counter">Step {n} of {total}</span>
          <span aria-hidden className="ml-1 hidden gap-1 sm:flex">
            {Array.from({ length: total }, (_, i) => <i key={i} className={cn("h-1 w-3 rounded-full", i < n ? "bg-[var(--accent-fill)]" : "bg-foreground/15")} />)}
          </span>
          <span className="ml-auto flex items-center gap-1">
            <button type="button" onClick={() => setMin((m) => !m)} aria-expanded={!min} aria-label={min ? "Show the tour card" : "Make the tour card smaller"}
              className="grid size-9 place-items-center rounded-full hover:bg-foreground/8 focus-visible:outline-2 focus-visible:outline-[var(--accent-fill)]">
              {min ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
            </button>
            <button type="button" onClick={tour.exit} aria-label="Close tour"
              className="grid size-9 place-items-center rounded-full hover:bg-foreground/8 focus-visible:outline-2 focus-visible:outline-[var(--accent-fill)]">
              <X className="size-4" />
            </button>
          </span>
        </div>

        <div className="px-4 pb-4 sm:px-5">
          <h2 ref={heading} tabIndex={-1} className="mt-1 text-headline tracking-tight outline-none">{step.title}</h2>
          {!min && (
            <>
              {who && <p className="mt-1 text-caption text-muted-foreground" data-testid="tour-who">You are <span className="font-medium text-foreground">{who}</span></p>}
              <div className="mt-2 text-footnote leading-5 text-foreground/85">{bodyNode}</div>

              <AnimatePresence initial={false} mode="wait">
                {status === "entering" || status === "doing" ? (
                  <motion.p key="busy" initial={reduce ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} role="status"
                    className="mt-3 flex items-center gap-2 rounded-2xl bg-[var(--accent-vivid)]/10 px-3 py-2 text-footnote text-[var(--accent-text)]">
                    <Loader2 className="size-4 shrink-0 animate-spin motion-reduce:animate-none" aria-hidden /> {progress ?? (status === "entering" ? "Getting this step ready…" : "Working on it…")}
                  </motion.p>
                ) : status === "ready" && progress ? (
                  <motion.p key="prog" initial={reduce ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="mt-3 flex items-center gap-2 rounded-2xl bg-[var(--accent-vivid)]/10 px-3 py-2 text-footnote text-[var(--accent-text)]">
                    <Loader2 className="size-4 shrink-0 animate-spin motion-reduce:animate-none" aria-hidden /> {progress}
                  </motion.p>
                ) : status === "done" && step.check ? (
                  <motion.p key="done" initial={reduce ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                    className="mt-3 flex items-center gap-2 rounded-2xl bg-pass/12 px-3 py-2 text-footnote font-medium text-pass-text" data-testid="tour-done">
                    <CircleCheck className="size-4 shrink-0" strokeWidth={2} aria-hidden /> Done
                  </motion.p>
                ) : status === "error" ? (
                  <motion.div key="err" initial={reduce ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} role="alert"
                    className="mt-3 rounded-2xl bg-human/12 px-3 py-2.5 text-footnote text-human-text">
                    <p className="flex items-start gap-2"><TriangleAlert className="mt-0.5 size-4 shrink-0" strokeWidth={1.75} aria-hidden /> {error}</p>
                    <div className="mt-2 flex gap-2">
                      <Button size="sm" variant="secondary" className="rounded-full" onClick={tour.retry}><RotateCcw strokeWidth={1.75} /> Retry</Button>
                      {n < total && <Button size="sm" variant="ghost" className="rounded-full" onClick={tour.next}><SkipForward strokeWidth={1.75} /> Skip</Button>}
                    </div>
                  </motion.div>
                ) : null}
              </AnimatePresence>

              {step.finish ? (
                <div className="mt-4 flex flex-col gap-2">
                  <Button onClick={() => void tour.tryOwnBrand()} className="h-10 rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">
                    <Plus strokeWidth={2} /> Now try your own brand
                  </Button>
                  <div className="flex gap-2">
                    <Button variant="ghost" className="rounded-full" onClick={tour.back}><ArrowLeft strokeWidth={1.75} /> Back</Button>
                    <Button variant="secondary" className="flex-1 rounded-full" onClick={tour.exit}>Explore freely</Button>
                  </div>
                </div>
              ) : (
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <Button variant="ghost" size="sm" className="rounded-full" onClick={tour.back} disabled={index === 0}><ArrowLeft strokeWidth={1.75} /> Back</Button>
                  <span className="ml-auto flex flex-wrap justify-end gap-2">
                    {canDo && (
                      <Button size="sm" onClick={tour.doIt} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]" aria-label={`Do it for me: ${step.doLabel ?? step.title}`}>
                        <Wand2 strokeWidth={1.75} /> Do it for me
                      </Button>
                    )}
                    <Button size="sm" onClick={tour.next} variant={canDo || status === "doing" ? "secondary" : "default"}
                      className={cn("rounded-full", !(canDo || status === "doing") && "bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]")}>
                      Next <ArrowRight strokeWidth={1.75} />
                    </Button>
                  </span>
                </div>
              )}
              {!step.finish && <button type="button" onClick={tour.exit} className="mt-2 text-caption text-muted-foreground underline-offset-2 hover:underline">Exit tour</button>}
            </>
          )}
        </div>
      </motion.section>
    </>
  );
}
