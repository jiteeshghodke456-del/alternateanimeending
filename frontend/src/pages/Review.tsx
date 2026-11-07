import { Link } from "react-router-dom";
import { ClipboardCheck } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { StateBadge } from "@/components/StateBadge";
import { MOCK_ITEMS } from "@/lib/mock";

export default function Review() {
  const queue = MOCK_ITEMS.filter((i) => i.state === "in_review");
  return (
    <>
      <PageHeader title="Review queue" subtitle="Items waiting for a reviewer or approver. Sample data until the workflow is connected." />
      {queue.length === 0 ? (
        <EmptyState icon={ClipboardCheck} title="All caught up" description="Nothing is waiting for review right now." />
      ) : (
        <ul className="glass-strong divide-y divide-border overflow-hidden">
          {queue.map((it) => (
            <li key={it.id}>
              <Link to={`/items/${it.id}`} className="flex items-center gap-4 px-5 py-4 hover:bg-foreground/4">
                <div className="min-w-0 flex-1"><p className="truncate text-body font-medium">{it.title}</p><p className="text-caption text-muted-foreground">{it.channel} · by {it.creator} · {it.updated}</p></div>
                <StateBadge state={it.state} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
