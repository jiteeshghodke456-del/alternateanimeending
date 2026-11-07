import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<"input">>(({ className, type, ...props }, ref) => (
  <input
    ref={ref}
    type={type}
    className={cn(
      "flex h-10 w-full px-3.5 py-2 text-body rounded-xl border border-input bg-background/70 shadow-[inset_0_1px_2px_rgba(16,24,40,0.05)] transition-[border-color,box-shadow,background-color] duration-200 ease-out placeholder:text-muted-foreground hover:border-foreground/25 focus-visible:border-[var(--accent-fill)] focus-visible:bg-background focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[var(--accent-vivid)]/30 disabled:cursor-not-allowed disabled:opacity-50",
      className,
    )}
    {...props}
  />
));
Input.displayName = "Input";
