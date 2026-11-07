import * as React from "react"

import { cn } from "@/lib/utils"

const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.ComponentProps<"textarea">
>(({ className, ...props }, ref) => {
  return (
    <textarea
      className={cn(
        "flex min-h-[60px] w-full px-3 py-2 text-base rounded-xl border border-input bg-background/70 shadow-[inset_0_1px_2px_rgba(16,24,40,0.05)] transition-[border-color,box-shadow,background-color] duration-200 ease-out placeholder:text-muted-foreground hover:border-foreground/25 focus-visible:border-[var(--accent-fill)] focus-visible:bg-background focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[var(--accent-vivid)]/30 disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
        className
      )}
      ref={ref}
      {...props}
    />
  )
})
Textarea.displayName = "Textarea"

export { Textarea }
