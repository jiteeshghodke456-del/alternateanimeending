import { Link } from "react-router-dom";
import { ConfidentialBadge } from "@/components/ConfidentialBadge";
import { StateBadge } from "@/components/StateBadge";
import { ScoreBadge } from "@/components/BrandScore";
import { timeAgo } from "@/lib/hooks";
import type { ItemSummary } from "@/lib/api";

export function ItemList({ items }: { items: ItemSummary[] }) {
  return (
    <ul className="glass-strong divide-y divide-border overflow-hidden">
      {items.map((it) => (
        <li key={it.id}>
          <Link to={`/items/${it.id}`} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-4 transition-colors hover:bg-foreground/4">
            <div className="min-w-0 flex-1 basis-56">
              <p className="truncate text-body font-medium">{it.title}</p>
              <p className="text-caption capitalize text-muted-foreground">{it.channel} · by {it.creator_name} · {timeAgo(it.updated_at)}{it.latest_version_n ? ` · v${it.latest_version_n}` : ""}</p>
            </div>
            {it.confidential && <ConfidentialBadge />}
            <span className="text-footnote tabular-nums text-muted-foreground">
              {it.open_findings} open{it.blocking_open > 0 && <span className="ml-1 font-medium text-flag-text">({it.blocking_open} blocking)</span>}
            </span>
            {it.score != null && <ScoreBadge score={it.score} band={it.score_band} />}
            <StateBadge state={it.state} />
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function ListSkeleton() {
  return <div className="glass-strong space-y-px overflow-hidden" aria-busy="true" aria-label="Loading">{[0, 1, 2, 3].map((i) => <div key={i} className="h-[72px] animate-pulse bg-foreground/5" style={{ opacity: 1 - i * 0.18 }} />)}</div>;
}
