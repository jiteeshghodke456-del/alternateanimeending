import { Cpu, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Separator } from "@/components/ui/separator";
import { ConfidentialBadge } from "@/components/ConfidentialBadge";

export interface AiUsageGroup { provider: string; calls: { purpose: string; count: number; tokens: number; costUsd: number }[] }
export interface AiUsage { confidential: boolean; piiMasked: number; groups: AiUsageGroup[] }

/** "AI used" panel (BUILD.md P0-9). Lists every external call grouped by provider; never shows content text. */
export function AiUsageDrawer({ usage, trigger }: { usage: AiUsage; trigger: ReactNode }) {
  const total = usage.groups.flatMap((g) => g.calls).reduce((a, c) => a + c.costUsd, 0);
  return (
    <Sheet>
      <SheetTrigger asChild>{trigger}</SheetTrigger>
      <SheetContent className="w-full overflow-y-auto border-l-[var(--glass-border)] bg-[var(--glass-strong-bg)] backdrop-blur-3xl sm:max-w-md">
        <SheetHeader>
          <SheetTitle className="text-title3">AI used on this item</SheetTitle>
          <SheetDescription>Every call to an outside service, so you can see exactly what left the app.</SheetDescription>
        </SheetHeader>
        <div className="space-y-5 px-4 pb-8">
          {usage.confidential ? (
            <div className="rounded-2xl bg-pass/14 p-4">
              <p className="flex items-center gap-2 text-headline text-pass-text"><ShieldCheck className="size-5" strokeWidth={1.5} /> No external services used</p>
              <p className="mt-1 text-footnote text-muted-foreground">This item is confidential. Only local checks ran on our server.</p>
              <ConfidentialBadge className="mt-3" />
            </div>
          ) : (
            <>
              {usage.groups.map((g) => (
                <div key={g.provider}>
                  <h4 className="flex items-center gap-2 text-callout font-semibold"><Cpu className="size-4 text-muted-foreground" strokeWidth={1.5} /> {g.provider}</h4>
                  <ul className="mt-2 divide-y divide-border rounded-2xl bg-foreground/5">
                    {g.calls.map((c) => (
                      <li key={c.purpose} className="flex items-center justify-between gap-3 px-4 py-3">
                        <div className="min-w-0">
                          <p className="truncate text-body">{c.purpose}</p>
                          <p className="text-caption text-muted-foreground">{c.count} calls · {c.tokens.toLocaleString()} tokens</p>
                        </div>
                        <span className="text-footnote tabular-nums">${c.costUsd.toFixed(4)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
              <Separator />
              <div className="flex justify-between text-body"><span>Total cost</span><span className="font-semibold tabular-nums">${total.toFixed(4)}</span></div>
              <p className="text-footnote text-muted-foreground">Personal data masked: <span className="font-medium text-foreground">{usage.piiMasked}</span>. Emails, phone numbers and long digit strings are replaced before any call.</p>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
