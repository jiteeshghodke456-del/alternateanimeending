import type { ComponentProps } from "react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/** A Button that explains itself when disabled: hover or focus the wrapper to read why. */
export function TipButton({ reason, disabled, ...props }: ComponentProps<typeof Button> & { reason?: string | null }) {
  const off = !!disabled || !!reason;
  if (!off || !reason) return <Button disabled={off} {...props} />;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span tabIndex={0} className="inline-flex rounded-full" aria-label={reason}>
          <Button disabled {...props} aria-describedby={undefined} className={`pointer-events-none ${props.className ?? ""}`} />
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-[260px]">{reason}</TooltipContent>
    </Tooltip>
  );
}
