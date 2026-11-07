import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { ArrowRight, BookOpenCheck, CircleAlert, Compass, FileCheck2, Loader2, Plus, ShieldCheck, Users, Wand2, type LucideIcon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { BlurFade } from "@/components/ui/blur-fade";
import { Logo } from "@/components/Logo";
import { MeshBackground } from "@/components/MeshBackground";
import { ThemeToggle } from "@/components/ThemeToggle";
import { UserAvatar } from "@/components/UserAvatar";
import { HighlightedText } from "@/components/HighlightedText";
import { api, ROLE_LABELS, serverFeatures, type Role } from "@/lib/api";
import { reportError } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { useTour } from "@/lib/tour";
import { cn } from "@/lib/utils";

const ROLE_HOME: Record<Role, string> = { admin: "/brand", creator: "/content/new?demo=1", reviewer: "/review", approver: "/review" };
const BLURB: Record<Role, string> = {
  admin: "Owns the guidelines, facts and palette.",
  creator: "Writes the posts and sends them for review.",
  reviewer: "Reads every flagged line before it moves on.",
  approver: "Gives the final yes. Never on their own work.",
};

// A neutral, made-up example (not tied to any brand) that shows the shape of a finding.
const SAMPLE = "Our new planner app is the #1 tool for small teams. Try it free today.";
const SPAN = "the #1 tool for small teams";
const FIX = "a planner built for small teams";

const FEATURES: { icon: LucideIcon; title: string; body: string }[] = [
  { icon: BookOpenCheck, title: "Cites the exact rule", body: "Every issue quotes the guideline line and page it comes from, so nobody has to take it on trust." },
  { icon: FileCheck2, title: "Checks claims against facts", body: "Numbers, awards and superlatives are matched to your approved facts, including when they expire." },
  { icon: Users, title: "People make the call", body: "Creator, reviewer and approver each see what they need. The model answers; your team decides." },
];

const spring = { type: "spring", stiffness: 380, damping: 30 } as const;

// framer-motion on the shadcn Button itself (no wrapper element): one focusable, native <button>.
const MotionButton = motion.create(Button);
// A clear 2px ring with an offset that shows on the blue pill, the glass pill and the role cards alike.
const CTA_FOCUS = "focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-offset-[3px] focus-visible:outline-[var(--accent-fill)]";

function ProductPreview() {
  const start = SAMPLE.indexOf(SPAN);
  return (
    <div className="relative mx-auto w-full max-w-[680px]">
      <div aria-hidden className="hero-glow" />
      <div className="glass-strong relative overflow-hidden text-left shadow-[0_30px_80px_-20px_rgba(0,40,120,0.25)]">
        <div className="flex items-center gap-2 border-b border-border/70 px-4 py-2.5">
          <span aria-hidden className="flex gap-1.5"><i className="size-2.5 rounded-full bg-foreground/15" /><i className="size-2.5 rounded-full bg-foreground/15" /><i className="size-2.5 rounded-full bg-foreground/15" /></span>
          <span className="ml-2 text-caption font-medium text-muted-foreground">Illustrative example</span>
          <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-flag/12 px-2.5 py-0.5 text-caption font-medium text-flag-text">
            <CircleAlert className="size-3.5" strokeWidth={2} aria-hidden /> 1 finding
          </span>
        </div>
        <div className="grid gap-0 md:grid-cols-[1.1fr_1fr]">
          <div className="p-5 md:p-6">
            <p className="text-caption font-medium uppercase tracking-[0.06em] text-muted-foreground">Draft post</p>
            <HighlightedText className="mt-2 text-callout leading-7" text={SAMPLE} spans={[{ start, end: start + SPAN.length, band: "flag" }]} />
          </div>
          <div className="border-t border-border/70 bg-foreground/[0.025] p-5 md:border-l md:border-t-0 md:p-6">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="rounded-full bg-flag/12 px-2 py-0.5 text-caption font-semibold text-flag-text">Blocking</span>
              <span className="rounded-full bg-foreground/8 px-2 py-0.5 text-caption font-medium">Claims</span>
            </div>
            <p className="mt-2 text-footnote font-semibold">No superlatives without an approved fact</p>
            <p className="mt-1 text-footnote text-muted-foreground">“#1” needs a source. No approved fact backs it, so readers may doubt it.</p>
            <div className="mt-3 rounded-xl bg-pass/10 px-3 py-2 text-footnote">
              <span className="font-medium text-pass-text">Suggested fix: </span>
              <span className="line-through decoration-flag/70 opacity-70">{SPAN}</span>{" "}
              <span className="font-medium">{FIX}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Landing() {
  const { users, login, startWorkspace } = useSession();
  const navigate = useNavigate();
  const [busy, setBusy] = useState<string | null>(null);
  const tour = useTour();
  const anyBusy = busy !== null || tour.launching;

  async function tryDemo() {
    setBusy("demo");
    try {
      // The user list may still be loading if the button is clicked right away.
      const list = users.length ? users : (await api.me()).users;
      const creator = list.find((u) => u.role === "creator") ?? list[0];
      if (creator) await login(creator.id);
      // If this browser is in its own (blank) workspace, open a fresh copy of the demo brand instead.
      let demoBrand = !!creator;
      if (creator) { try { demoBrand = (await api.brand()).demo; } catch { /* keep going */ } }
      if (!demoBrand && (await serverFeatures()).workspaces) await startWorkspace("demo", "creator");
      navigate("/content/new?demo=1");
    } catch (e) {
      reportError(e, "We could not start the demo. Please try again.");
    } finally {
      setBusy(null);
    }
  }

  async function startFresh() {
    if (!(await serverFeatures()).workspaces) {
      toast.info("Starting with your own brand isn't available on this server yet. Try the demo brand for now.");
      return;
    }
    setBusy("blank");
    try {
      await startWorkspace("blank", "admin");
      navigate("/setup");
    } catch (e) {
      reportError(e, "We could not create a workspace. Please try again.");
    } finally {
      setBusy(null);
    }
  }

  async function enter(id: string, to: string) {
    setBusy(id);
    try {
      await login(id);
      navigate(to);
    } catch (e) {
      reportError(e, "We could not sign you in. Please try again.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="min-h-dvh overflow-x-clip">
      <MeshBackground />
      <header className="mx-auto flex max-w-[1200px] items-center justify-between px-4 py-5 md:px-8">
        <Logo />
        <ThemeToggle />
      </header>

      <main className="mx-auto max-w-[1200px] px-4 pb-16 md:px-8">
        <section className="mx-auto flex max-w-[920px] flex-col items-center pb-14 pt-8 text-center md:pb-20 md:pt-16">
          <BlurFade delay={0.05}>
            <span className="glass inline-flex items-center gap-2 !rounded-full px-3.5 py-1.5 text-footnote font-medium text-foreground/85">
              <ShieldCheck className="size-4 text-[var(--accent-text)]" strokeWidth={1.75} aria-hidden />
              Brand checks before you publish
            </span>
          </BlurFade>
          <BlurFade delay={0.12}>
            <h1 className="mt-6 text-display text-balance">
              Catch off-brand content <span className="hero-gradient-text">before it goes live.</span>
            </h1>
          </BlurFade>
          <BlurFade delay={0.2}>
            <p className="mx-auto mt-5 max-w-[58ch] text-callout text-muted-foreground md:text-[18px] md:leading-[28px]">
              Load your guidelines, approved facts and palette once. Every post, image and PDF is then checked against them. Each issue cites the exact rule, explains why in plain words, and offers a fix that keeps your message.
            </p>
          </BlurFade>
          <BlurFade delay={0.28} className="w-full">
            <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row sm:flex-wrap">
              <MotionButton
                type="button"
                size="lg"
                whileHover={{ y: -1 }}
                whileTap={{ scale: 0.98 }}
                transition={spring}
                disabled={anyBusy}
                aria-busy={tour.launching}
                onClick={() => void tour.start()}
                className={cn("h-12 w-full rounded-full bg-[var(--accent-fill)] px-7 text-callout font-semibold text-white shadow-[0_10px_30px_rgba(0,113,227,0.35)] hover:bg-[var(--accent-fill-hover)] sm:w-auto", CTA_FOCUS)}
              >
                {tour.launching ? <Loader2 className="animate-spin" /> : <Compass strokeWidth={2} />}
                Take the 2-minute tour
              </MotionButton>
              {/* One real <button> each: the motion props live on the button itself, so there is a single
                  tab stop and Enter / Space activate it natively (a motion wrapper with whileTap became
                  its own focusable stop that ignored both keys). */}
              <MotionButton
                type="button"
                size="lg"
                whileHover={{ y: -1 }}
                whileTap={{ scale: 0.98 }}
                transition={spring}
                variant="secondary"
                disabled={anyBusy}
                aria-busy={busy === "demo"}
                onClick={tryDemo}
                className={cn("glass h-12 w-full !rounded-full px-7 text-callout font-semibold text-[var(--accent-text)] hover:bg-foreground/6 sm:w-auto", CTA_FOCUS)}
              >
                {busy === "demo" ? <Loader2 className="animate-spin" /> : null}
                Try the demo brand <ArrowRight strokeWidth={2} />
              </MotionButton>
              <MotionButton
                type="button"
                size="lg"
                variant="secondary"
                whileHover={{ y: -1 }}
                whileTap={{ scale: 0.98 }}
                transition={spring}
                disabled={anyBusy}
                aria-busy={busy === "blank"}
                onClick={startFresh}
                className={cn("glass h-12 w-full !rounded-full px-7 text-callout font-semibold text-foreground hover:bg-foreground/6 sm:w-auto", CTA_FOCUS)}
              >
                {busy === "blank" ? <Loader2 className="animate-spin" /> : <Plus strokeWidth={2} />}
                Start fresh with your brand
              </MotionButton>
            </div>
            <p className="mt-3 text-caption text-muted-foreground">
              Tour: nine short steps in a fresh copy of the demo; each one can run itself. Demo: a fictional brand with sample rules, signed in as its Creator. Fresh: an empty workspace and a five-step setup.
            </p>
          </BlurFade>

          <BlurFade delay={0.38} className="mt-12 w-full md:mt-16">
            <ProductPreview />
          </BlurFade>
        </section>

        <section aria-labelledby="how-h" className="pb-16">
          <h2 id="how-h" className="sr-only">What it does</h2>
          <ul className="grid gap-3 sm:gap-4 md:grid-cols-3">
            {FEATURES.map(({ icon: Icon, title, body }, i) => (
              <li key={title}>
                <BlurFade inView delay={0.05 * i} className="h-full">
                  <div className="glass h-full p-5 sm:p-6">
                    <span className="grid size-10 place-items-center rounded-xl bg-[#0A84FF]/12 text-[var(--accent-text)]"><Icon className="size-5" strokeWidth={1.5} aria-hidden /></span>
                    <h3 className="mt-4 text-headline">{title}</h3>
                    <p className="mt-1 text-footnote text-muted-foreground">{body}</p>
                  </div>
                </BlurFade>
              </li>
            ))}
          </ul>
        </section>

        {users.length > 0 && (
          <section aria-labelledby="roles-h" className="pb-14">
            <BlurFade inView>
              <h2 id="roles-h" className="mb-1 text-center text-title2">Step into any role</h2>
              <p className="mb-8 text-center text-body text-muted-foreground">{users.length} people, one workflow. Pick one and look around.</p>
            </BlurFade>
            <ul className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
              {users.map((u, i) => (
                <li key={u.id}>
                  <BlurFade inView delay={0.05 * i} className="h-full">
                    <motion.button
                      type="button"
                      whileHover={{ y: -2 }}
                      whileTap={{ scale: 0.985 }}
                      transition={spring}
                      onClick={() => enter(u.id, ROLE_HOME[u.role] ?? "/dashboard")}
                      disabled={busy !== null}
                      aria-busy={busy === u.id}
                      className={cn("glass group flex h-full w-full flex-col items-start p-4 text-left disabled:opacity-70 sm:p-6", CTA_FOCUS)}
                    >
                      <UserAvatar id={u.id} name={u.name} role={u.role} className="size-11 text-title3 sm:size-12" />
                      <span className="mt-4 text-headline">{u.name}</span>
                      <span className="text-footnote font-medium text-[var(--accent-text)]">{ROLE_LABELS[u.role]}</span>
                      <span className="mt-2 text-footnote text-muted-foreground">{BLURB[u.role]}</span>
                      <span className="mt-4 inline-flex items-center gap-1 text-footnote font-medium text-foreground/80 transition-transform group-hover:translate-x-0.5">
                        {busy === u.id ? <Loader2 className="size-4 animate-spin" /> : <>Continue as {u.name} <ArrowRight className="size-4" strokeWidth={1.75} /></>}
                      </span>
                    </motion.button>
                  </BlurFade>
                </li>
              ))}
            </ul>
          </section>
        )}
      </main>

      <footer className="mx-auto flex max-w-[1200px] flex-col items-center gap-1 px-4 pb-10 text-center text-footnote text-muted-foreground md:px-8">
        <span className="inline-flex items-center gap-1.5"><Wand2 className="size-3.5" strokeWidth={1.75} aria-hidden /> Powered by Jev (TypeSafe AI)</span>
      </footer>
    </div>
  );
}
