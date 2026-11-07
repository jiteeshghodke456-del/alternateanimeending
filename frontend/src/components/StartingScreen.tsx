import { useEffect, useState } from "react";
import { Logo } from "@/components/Logo";
import { useSession } from "@/lib/session";

/**
 * Shown while the server starts or wakes (after a deploy, or after free-tier sleep). It overlays the
 * page instead of replacing it, so a short blip mid-session never throws away what someone typed.
 * Calm copy, a progress shimmer (static with reduced motion), no error colours.
 */
export function StartingScreen() {
  const { starting } = useSession();
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!starting) return;
    const t = window.setInterval(() => setSeconds(Math.round((Date.now() - starting.since) / 1000)), 1000);
    return () => window.clearInterval(t);
  }, [starting]);
  if (!starting) return null;
  const stage = starting.stage && starting.stage !== "starting" ? starting.stage : null;
  return (
    <div className="starting-screen fixed inset-0 z-[60] grid place-items-center bg-background/80 px-4 backdrop-blur-xl" role="status" aria-live="polite">
      <div className="glass-strong w-full max-w-[420px] p-6 text-center sm:p-8">
        <div className="flex justify-center"><Logo /></div>
        <h1 className="mt-5 text-title3">Starting the server…</h1>
        <p className="mt-2 text-body text-muted-foreground">
          Free hosting can take up to a minute the first time. The page opens by itself when it's ready.
        </p>
        <div className="starting-bar mt-5" aria-hidden><span /></div>
        <p className="mt-3 text-caption text-muted-foreground">
          {stage ? `Now: ${stage}` : "Waking up"}{seconds >= 3 ? ` · ${seconds} s` : ""}
        </p>
      </div>
    </div>
  );
}
