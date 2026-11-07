import { Link } from "react-router-dom";
import { FileText, Plus } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { StateBadge } from "@/components/StateBadge";
import { ConfidentialBadge } from "@/components/ConfidentialBadge";
import { EmptyState } from "@/components/EmptyState";
import { MOCK_ITEMS } from "@/lib/mock";

export default function Content() {
  const items = MOCK_ITEMS;
  return (
    <>
      <PageHeader
        title="Content"
        subtitle="Everything that has been checked, and where it is in review. Sample items until live data is connected."
        actions={<Button asChild className="rounded-full bg-[#0A84FF] text-white hover:bg-[#0A84FF]/90"><Link to="/content/new"><Plus strokeWidth={2} /> New content</Link></Button>}
      />
      {items.length === 0 ? (
        <EmptyState icon={FileText} title="Nothing here yet" description="Paste a caption or upload an image to see your first check." action={<Button asChild className="rounded-full"><Link to="/content/new">Check something</Link></Button>} />
      ) : (
        <ul className="glass-strong divide-y divide-border overflow-hidden">
          {items.map((it) => (
            <li key={it.id}>
              <Link to={`/items/${it.id}`} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-4 transition-colors hover:bg-foreground/4">
                <div className="min-w-0 flex-1 basis-56">
                  <p className="truncate text-body font-medium">{it.title}</p>
                  <p className="text-caption text-muted-foreground">{it.channel} · {it.creator} · {it.updated}</p>
                </div>
                {it.confidential && <ConfidentialBadge />}
                <span className="text-footnote tabular-nums text-muted-foreground">{it.findings} findings</span>
                <StateBadge state={it.state} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
