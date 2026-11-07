import { Lock } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/** Confidential items only get local checks. No Jev, writer model or audience calls. */
export function ConfidentialBadge({ className }: { className?: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span tabIndex={0} className={cn("inline-flex items-center gap-1 rounded-full bg-foreground/8 px-2.5 py-1 text-caption font-medium text-foreground", className)}>
          <Lock className="size-3.5" strokeWidth={1.75} /> Confidential
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-[240px]">Only local checks run. No external AI service sees this content.</TooltipContent>
    </Tooltip>
  );
}
