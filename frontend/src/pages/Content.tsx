import { useState } from "react";
import { Link } from "react-router-dom";
import { FileText, Plus } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { ItemList, ListSkeleton } from "@/components/ItemList";
import { api, type ItemState } from "@/lib/api";
import { STATE_LABEL } from "@/lib/bands";
import { useAsync } from "@/lib/hooks";

export default function Content() {
  const [mine, setMine] = useState(false);
  const [state, setState] = useState<ItemState | "all">("all");
  const q = useAsync(() => api.items({ mine: mine || undefined, state: state === "all" ? undefined : state }), [mine, state]);
  const filtered = mine || state !== "all";
  return (
    <>
      <PageHeader
        title="Content"
        subtitle="Everything that has been checked, and where it is in review."
        actions={<Button asChild className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]"><Link to="/content/new"><Plus strokeWidth={2} /> New content</Link></Button>}
      />
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <Select value={state} onValueChange={(v) => setState(v as ItemState | "all")}>
          <SelectTrigger aria-label="Filter by state" className="w-[200px] rounded-full"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All states</SelectItem>
            {Object.entries(STATE_LABEL).map(([k, v]) => <SelectItem key={k} value={k}>{v}</SelectItem>)}
          </SelectContent>
        </Select>
        <label className="flex items-center gap-2 text-body"><Switch checked={mine} onCheckedChange={setMine} aria-label="Only my items" /> Only mine</label>
      </div>
      {q.error ? <ErrorState message={q.error.message} onRetry={q.reload} />
        : q.loading && !q.data ? <ListSkeleton />
        : (q.data ?? []).length === 0 ? (
          <EmptyState icon={FileText} title={filtered ? "No items match these filters" : "Nothing here yet"}
            description={filtered ? "Try clearing a filter." : "Paste a caption or upload an image to see your first check."}
            action={filtered ? <Button variant="secondary" className="rounded-full" onClick={() => { setMine(false); setState("all"); }}>Clear filters</Button> : <Button asChild className="rounded-full"><Link to="/content/new">Check something</Link></Button>} />
        ) : <ItemList items={q.data!} />}
    </>
  );
}
