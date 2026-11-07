import { RefreshCw, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Human message plus retry. Never pass a raw stack trace here. */
export function ErrorState({ title = "That did not work", message = "Something went wrong on our side. Your work is safe. Please try again.", onRetry, className }: {
  title?: string; message?: string; onRetry?: () => void; className?: string;
}) {
  return (
    <div role="alert" className={cn("glass-strong flex flex-col items-center px-6 py-12 text-center", className)}>
      <div className="grid size-14 place-items-center rounded-2xl bg-flag/12 text-flag-text">
        <TriangleAlert className="size-7" strokeWidth={1.5} aria-hidden />
      </div>
      <h3 className="mt-5 text-title3">{title}</h3>
      <p className="mt-1.5 max-w-[42ch] text-body text-muted-foreground">{message}</p>
      {onRetry && (
        <Button onClick={onRetry} className="mt-6 rounded-full bg-[#0A84FF] px-5 text-white hover:bg-[#0A84FF]/90">
          <RefreshCw strokeWidth={1.75} /> Try again
        </Button>
      )}
    </div>
  );
}
