import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Coffee, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BlurFade } from "@/components/ui/blur-fade";
import { Logo } from "@/components/Logo";
import { MeshBackground } from "@/components/MeshBackground";
import { ThemeToggle } from "@/components/ThemeToggle";
import { UserAvatar } from "@/components/UserAvatar";
import { HighlightedText } from "@/components/HighlightedText";
import { api, ROLE_LABELS, type RuleOut } from "@/lib/api";
import { reportError } from "@/lib/hooks";
import { useSession } from "@/lib/session";

const LANDING_HOME: Record<string, string> = { asha: "/brand", rohan: "/content/new?demo=1", meera: "/review", kabir: "/review" };
const BLURB: Record<string, string> = {
  admin: "Owns the guidelines, facts and palette.",
  creator: "Writes the posts and sends them for review.",
  reviewer: "Reads every flagged line before it moves on.",
  approver: "Gives the final yes. Never on his own work.",
};

const SAMPLE = "Meet our new blend, the #1 coffee in India. Fresh every morning.";
const SPAN = "the #1 coffee in India";

/** The real "#1 / superlatives" rule from the brand kit, so the example never drifts from the guideline. */
function pickClaimRule(rules: RuleOut[]): RuleOut | undefined {
  return (
    rules.find((r) => r.code === "R-CLAIM-01") ??
    rules.find((r) => r.category === "claims" && /#\s?1|superlative|number one|best/i.test(`${r.statement} ${r.source_quote}`))
  );
}

export default function Landing() {
  const { user, users, ready, demoMode, login } = useSession();
  const navigate = useNavigate();
  const [busy, setBusy] = useState<string | null>(null);
  const [rule, setRule] = useState<RuleOut | null | undefined>(undefined); // undefined = loading, null = unavailable

  // The brand kit needs a session. In demo mode we open a quiet preview session so the example quotes a real rule.
  useEffect(() => {
    if (!ready) return;
    let alive = true;
    (async () => {
      try {
        if (!user && demoMode && users.length) await login(users.find((u) => u.role === "creator")?.id ?? users[0].id);
        const rules = await api.rules();
        if (alive) setRule(pickClaimRule(rules) ?? null);
      } catch {
        if (alive) setRule(null);
      }
    })();
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  async function enter(id: string, to: string) {
    setBusy(id);
    try {
      await login(id);
      navigate(to);
    } catch (e) {
      reportError(e, "We could not start the demo. Please try again.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="min-h-dvh">
      <MeshBackground />
      <header className="mx-auto flex max-w-[1200px] items-center justify-between px-4 py-5 md:px-8">
        <Logo />
        <ThemeToggle />
      </header>

      <main className="mx-auto max-w-[1200px] px-4 pb-16 md:px-8">
        <section className="mx-auto flex max-w-[860px] flex-col items-center pb-16 pt-12 text-center md:pb-20 md:pt-20">
          <BlurFade delay={0.05}>
            <span className="glass inline-flex items-center gap-2 !rounded-full px-3.5 py-1.5 text-footnote text-foreground/85">
              <Coffee className="size-3.5 text-[var(--accent-text)]" strokeWidth={1.75} aria-hidden />
              Fictional demo brand: Saffron &amp; Steam Coffee Co.
            </span>
          </BlurFade>
          <BlurFade delay={0.12}>
            <h1 className="mt-7 text-display">Brand Gate</h1>
          </BlurFade>
          <BlurFade delay={0.2}>
            <p className="mt-4 text-[clamp(22px,3.4vw,32px)] font-semibold leading-[1.15] tracking-[-0.03em] text-foreground/90">
              Catch off-brand content before it goes live.
            </p>
          </BlurFade>
          <BlurFade delay={0.28}>
            <p className="mx-auto mt-5 max-w-[56ch] text-callout text-muted-foreground">
              Every post, image and PDF is checked against your guidelines and approved facts. Each issue cites the exact rule, explains why in plain words, and offers a fix that keeps your message.
            </p>
          </BlurFade>
          <BlurFade delay={0.36}>
            <Button
              size="lg"
              disabled={busy !== null}
              onClick={() => enter("rohan", "/content/new?demo=1")}
              className="mt-9 h-12 rounded-full bg-[#0A84FF] px-8 text-callout font-semibold text-white shadow-[0_10px_30px_rgba(10,132,255,0.4)] hover:bg-[#0A84FF]/90"
            >
              {busy === "rohan" ? <Loader2 className="animate-spin" /> : null}
              Try the demo <ArrowRight strokeWidth={2} />
            </Button>
            <p className="mt-3 text-caption text-muted-foreground">One click. You will sign in as Rohan, a Creator.</p>
          </BlurFade>

          <BlurFade delay={0.46} className="mt-14 w-full">
            <div className="glass-strong mx-auto max-w-[620px] p-5 text-left md:p-6">
              <div className="mb-3 flex items-center justify-between text-caption text-muted-foreground">
                <span>Example check</span>
                <span className="rounded-full bg-flag/12 px-2.5 py-1 font-medium text-flag-text">1 finding</span>
              </div>
              <HighlightedText text={SAMPLE} spans={[{ start: SAMPLE.indexOf(SPAN), end: SAMPLE.indexOf(SPAN) + SPAN.length, band: "flag" }]} />
              <div className="mt-3 border-t border-border pt-3 text-footnote text-muted-foreground">
                {rule === undefined ? (
                  <div className="space-y-2" aria-hidden><div className="h-3 w-3/4 animate-pulse rounded bg-foreground/8" /><div className="h-3 w-1/2 animate-pulse rounded bg-foreground/8" /></div>
                ) : rule ? (
                  <>
                    <p>
                      <span className="font-mono text-caption">{rule.code}</span>
                      {rule.page != null && <> · Guideline p.{rule.page}</>}
                      {" · "}{rule.severity}
                    </p>
                    <p className="mt-1 italic text-foreground/80">&ldquo;{rule.source_quote || rule.statement}&rdquo;</p>
                  </>
                ) : (
                  <p>Every finding cites the exact guideline rule and page it comes from.</p>
                )}
              </div>
            </div>
          </BlurFade>
        </section>

        <section aria-labelledby="roles-h" className="pb-14">
          <BlurFade inView>
            <h2 id="roles-h" className="mb-1 text-center text-title2">Step into any role</h2>
            <p className="mb-8 text-center text-body text-muted-foreground">Four people, one workflow. Pick one and look around.</p>
          </BlurFade>
          <ul className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
            {users.map((u, i) => (
              <li key={u.id}>
                <BlurFade inView delay={0.05 * i} className="h-full">
                  <button
                    onClick={() => enter(u.id, LANDING_HOME[u.id] ?? "/dashboard")}
                    disabled={busy !== null}
                    className="glass group flex h-full w-full flex-col items-start p-4 text-left sm:p-6 transition-transform duration-200 hover:-translate-y-0.5 focus-visible:-translate-y-0.5 disabled:opacity-70"
                  >
                    <UserAvatar id={u.id} name={u.name} className="size-11 text-title3 sm:size-12" />
                    <span className="mt-4 text-headline">{u.name}</span>
                    <span className="text-footnote font-medium text-[var(--accent-text)]">{ROLE_LABELS[u.role]}</span>
                    <span className="mt-2 text-footnote text-muted-foreground">{BLURB[u.role]}</span>
                    <span className="mt-4 inline-flex items-center gap-1 text-footnote font-medium text-foreground/80 transition-transform group-hover:translate-x-0.5">
                      {busy === u.id ? <Loader2 className="size-4 animate-spin" /> : <>Continue as {u.name} <ArrowRight className="size-4" strokeWidth={1.75} /></>}
                    </span>
                  </button>
                </BlurFade>
              </li>
            ))}
          </ul>
        </section>
      </main>

      <footer className="mx-auto max-w-[1200px] px-4 pb-10 text-center text-footnote text-muted-foreground md:px-8">
        Powered by Jev (TypeSafe AI)
      </footer>
    </div>
  );
}
