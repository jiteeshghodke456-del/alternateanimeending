import { ClipboardCheck } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { ItemList, ListSkeleton } from "@/components/ItemList";
import { api, namesWithRole } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { useSession } from "@/lib/session";

export default function Review() {
  const { user, users } = useSession();
  const q = useAsync(() => api.items({ state: "in_review" }), []);
  const canReview = user?.role === "reviewer" || user?.role === "approver";
  return (
    <>
      <PageHeader title="Review queue" subtitle="Items waiting for a reviewer or approver." />
      {!canReview && (
        <p className="mb-4 rounded-2xl bg-[var(--accent-vivid)]/10 p-3 text-footnote text-[var(--accent-text)]">
          You're signed in as {user?.name}. Only reviewers and approvers can act on these items. Switch to {namesWithRole(users, "reviewer", "approver")} from the top bar to review.
        </p>
      )}
      {q.error ? <ErrorState message={q.error.message} onRetry={q.reload} />
        : q.loading && !q.data ? <ListSkeleton />
        : (q.data ?? []).length === 0 ? <EmptyState icon={ClipboardCheck} title="All caught up" description="Nothing is waiting for review right now." />
        : <ItemList items={q.data!} />}
    </>
  );
}
