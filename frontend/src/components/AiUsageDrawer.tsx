import { Cpu, Globe, Loader2, ShieldCheck } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Separator } from "@/components/ui/separator";
import { ConfidentialBadge } from "@/components/ConfidentialBadge";
import { ErrorState } from "@/components/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type AIUsageGroup } from "@/lib/api";
import { useAsync } from "@/lib/hooks";

function Body({ itemId, refreshKey }: { itemId: number; refreshKey: unknown }) {
  const { data, error, loading, reload } = useAsync(() => api.aiUsage(itemId), [itemId, refreshKey]);
  if (loading && !data) return <div className="space-y-3 px-4"><Skeleton className="h-20" /><Skeleton className="h-28" /></div>;
  if (error) return <div className="px-4"><ErrorState title="Can't load AI usage" message={error.message} onRetry={reload} className="!px-4 !py-8" /></div>;
  if (!data) return null;

  const byProvider = new Map<string, AIUsageGroup[]>();
  for (const g of data.groups) byProvider.set(g.provider, [...(byProvider.get(g.provider) ?? []), g]);

  return (
    <div className="space-y-5 px-4 pb-8">
      {data.no_external_services ? (
        <div className="rounded-2xl bg-pass/14 p-4">
          <p className="flex items-center gap-2 text-headline text-pass-text"><ShieldCheck className="size-5" strokeWidth={1.5} /> No external services used</p>
          <p className="mt-1 text-footnote text-muted-foreground">
            {data.confidential
              ? "This item is confidential. Only local checks ran on our server."
              : data.summary || "Nothing has left this server for this item yet."}
          </p>
          {data.confidential && <ConfidentialBadge className="mt-3" />}
        </div>
      ) : (
        <>
          {data.summary && <p className="text-footnote text-muted-foreground">{data.summary}</p>}
          {[...byProvider.entries()].map(([provider, groups]) => (
            <div key={provider}>
              <h4 className="flex items-center gap-2 text-callout font-semibold"><Cpu className="size-4 text-muted-foreground" strokeWidth={1.5} /> {provider}</h4>
              <ul className="mt-2 divide-y divide-border rounded-2xl bg-foreground/5">
                {groups.map((g) => (
                  <li key={`${g.provider}-${g.purpose}`} className="flex items-center justify-between gap-3 px-4 py-3">
                    <div className="min-w-0">
                      <p className="truncate text-body capitalize">{g.purpose.replace(/_/g, " ")}</p>
                      <p className="text-caption text-muted-foreground">{g.count} call{g.count === 1 ? "" : "s"} · {g.input_tokens.toLocaleString()} tokens</p>
                    </div>
                    <span className="text-footnote tabular-nums">${g.cost_usd.toFixed(4)}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          <Separator />
          <div className="flex justify-between text-body"><span>Total cost</span><span className="font-semibold tabular-nums">${data.total_cost_usd.toFixed(4)}</span></div>
        </>
      )}
      {(data.fetches ?? []).length > 0 && (
        <div>
          <h4 className="flex items-center gap-2 text-callout font-semibold"><Globe className="size-4 text-muted-foreground" strokeWidth={1.5} /> Page fetch</h4>
          <ul className="mt-2 divide-y divide-border rounded-2xl bg-foreground/5">
            {data.fetches!.map((f) => <li key={f} className="break-all px-4 py-3 text-footnote">{f}</li>)}
          </ul>
        </div>
      )}
      <p className="text-footnote text-muted-foreground">
        Personal data masked: <span className="font-medium text-foreground">{data.pii_masked}</span>.
        {" "}Emails, phone numbers and long digit strings are replaced before any outside call. {data.external_calls} outside call{data.external_calls === 1 ? "" : "s"} in total.
      </p>
      <p className="text-footnote text-muted-foreground" data-testid="provenance-local-note">
        The Content Credentials check (C2PA, XMP and EXIF metadata on images) runs locally on our server, also for confidential items. It never sends the image anywhere.
      </p>
    </div>
  );
}

/** "AI used" panel (BUILD.md P0-9). Loads /api/items/{id}/ai-usage when opened; never shows content text. */
export function AiUsageDrawer({ itemId, refreshKey, trigger }: { itemId: number; refreshKey?: unknown; trigger: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>{trigger}</SheetTrigger>
      <SheetContent className="w-full overflow-y-auto border-l-[var(--glass-border)] bg-[var(--glass-strong-bg)] backdrop-blur-3xl sm:max-w-md">
        <SheetHeader>
          <SheetTitle className="text-title3">AI used on this item</SheetTitle>
          <SheetDescription>Every call to an outside service, so you can see exactly what left the app.</SheetDescription>
        </SheetHeader>
        {open ? <Body itemId={itemId} refreshKey={refreshKey} /> : <Loader2 className="hidden" />}
      </SheetContent>
    </Sheet>
  );
}
