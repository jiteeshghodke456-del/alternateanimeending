import type { LucideIcon } from "lucide-react";
import { Inbox } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function EmptyState({ icon: Icon = Inbox, title, description, action, className }: {
  icon?: LucideIcon; title: string; description?: string; action?: ReactNode; className?: string;
}) {
  return (
    <div className={cn("glass-strong flex flex-col items-center px-6 py-12 text-center", className)}>
      <div className="grid size-14 place-items-center rounded-2xl bg-[var(--accent-vivid)]/12 text-[var(--accent-text)]">
        <Icon className="size-7" strokeWidth={1.5} aria-hidden />
      </div>
      <h3 className="mt-5 text-title3">{title}</h3>
      {description && <p className="mt-1.5 max-w-[42ch] text-body text-muted-foreground">{description}</p>}
      {action && <div className="mt-6">{action}</div>}
    </div>
  );
}
