import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

export function Logo({ className, showWord = true }: { className?: string; showWord?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <span className="grid size-8 place-items-center rounded-[10px] bg-[var(--accent-fill)] text-white shadow-[0_4px_14px_rgba(10,132,255,0.45)]">
        <Check className="size-[18px]" strokeWidth={2.5} />
      </span>
      {showWord && <span className="text-headline">Brand Gate</span>}
    </span>
  );
}
