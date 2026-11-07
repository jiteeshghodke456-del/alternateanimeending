import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import { AnimatePresence } from "framer-motion";
import {
  CheckCheck, CircleCheck, FileText, GitCompare, History as HistoryIcon, Info, Loader2, Lock, MessageSquare, Send, ShieldCheck, Undo2, Users, XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/PageHeader";
import { LiveMeter } from "@/components/LiveMeter";
import { RuleScanPanel, type RuleScanRow } from "@/components/RuleScanPanel";
import { FindingCard, type FixGroupInfo } from "@/components/FindingCard";
import { HighlightedText, type HighlightSpan } from "@/components/HighlightedText";
import { AudiencePanel } from "@/components/audience/AudiencePanel";
import { ImageWithLogo, logoShapes } from "@/components/LogoOverlay";
import { AiUsageDrawer } from "@/components/AiUsageDrawer";
import { ConfidentialBadge } from "@/components/ConfidentialBadge";
import { StateBadge } from "@/components/StateBadge";
import { ErrorState } from "@/components/ErrorState";
import { EmptyState } from "@/components/EmptyState";
import { DiffView } from "@/components/DiffView";
import { TipButton } from "@/components/TipButton";
import { DismissDialog, NoteDialog } from "@/components/ReasonDialog";
import { UserAvatar } from "@/components/UserAvatar";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AnimatedListItem } from "@/components/ui/animated-list";
import {
  api, ApiError, streamCheck, ROLE_LABELS,
  type AudienceCulprit, type CheckEvent, type DiffSegment, type FindingOut, type ItemDetail, type VersionOut,
} from "@/lib/api";
import { diffSegments } from "@/lib/diff";
import { reportError, timeAgo, toApiError, useAsync } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

const SEV_ORDER = { blocking: 0, major: 1, minor: 2 } as const;

function pOf(f: FindingOut): number {
  return f.probability ?? (f.band === "flag" ? 1 : f.band === "human" ? 0.5 : 0);
}

/* ------------------------------------------------------------------ content panel */

function ContentPanel({ version, findings, active, onSelect, palette, culprit }: {
  version: VersionOut; findings: FindingOut[]; active?: number; onSelect: (id: number) => void;
  palette: { name: string; hex: string }[]; culprit?: AudienceCulprit | null;
}) {
  const live = findings.filter((f) => f.band !== "pass" && f.status !== "dismissed" && f.span_start != null && f.span_end != null);
  // Findings that share a region (same span) become one highlight: the wide span is tinted lightly and each
  // finding's exact problem words (hit_start..hit_end) are marked inside it.
  const spansFor = (from: number, to: number): HighlightSpan[] => {
    const groups = new Map<string, FindingOut[]>();
    for (const f of live.filter((f) => f.span_start! >= from && f.span_end! <= to)) {
      const k = `${f.span_start}:${f.span_end}`;
      groups.set(k, [...(groups.get(k) ?? []), f]);
    }
    return [...groups.values()].map((fs) => {
      const lead = fs.find((f) => f.band === "flag") ?? fs[0];
      const hits = fs.filter((f) => f.hit_start != null && f.hit_end != null && f.hit_end > f.hit_start && f.hit_start! >= f.span_start! && f.hit_end! <= f.span_end!)
        .map((f) => ({ start: f.hit_start! - from, end: f.hit_end! - from }));
      return {
        start: lead.span_start! - from, end: lead.span_end! - from, band: lead.band, id: String(lead.id), ids: fs.map((f) => String(f.id)),
        label: fs.map((f) => f.rule_code ?? f.fact_code).filter(Boolean).join(", ") || undefined, hits,
      };
    });
  };

  if (version.kind === "image") {
    const alt = version.units.find((u) => u.kind === "alt_text")?.text ?? version.alt_text;
    return (
      <div className="space-y-4">
        {version.file_url && (
          <div data-tour="logo-image"><ImageWithLogo
            src={version.file_url} alt={alt || "Uploaded image under review"}
            shapes={logoShapes(version, findings)} activeFindingId={active} onSelectFinding={onSelect}
          /></div>
        )}
        {palette.length > 0 && (
          <div>
            <p className="mb-1.5 text-caption font-medium text-muted-foreground">Brand palette</p>
            <div className="flex overflow-hidden rounded-xl border border-border" role="list" aria-label="Brand palette">
              {palette.map((c) => <div key={c.hex} role="listitem" title={`${c.name} ${c.hex}`} className="h-8 flex-1" style={{ background: c.hex }} />)}
            </div>
          </div>
        )}
        {version.text && (
          <div>
            <p className="mb-1 text-caption font-medium text-muted-foreground">Caption</p>
            <HighlightedText text={version.text} spans={spansFor(0, version.text.length)} activeId={active ? String(active) : undefined} onSelect={(id) => onSelect(Number(id))} />
          </div>
        )}
        <p className="text-footnote text-muted-foreground"><span className="font-medium text-foreground">Alt text:</span> {alt || "none provided"}</p>
      </div>
    );
  }

  if (version.kind === "pdf") {
    const pages = new Map<number, typeof version.units>();
    for (const u of version.units.filter((u) => u.kind === "text")) {
      const p = u.page ?? 1;
      pages.set(p, [...(pages.get(p) ?? []), u]);
    }
    return (
      <div className="space-y-5">
        {[...pages.entries()].sort((a, b) => a[0] - b[0]).map(([page, units]) => (
          <section key={page} aria-label={`Page ${page}`}>
            <h3 className="mb-2 flex items-center gap-2 text-caption font-medium text-muted-foreground"><FileText className="size-3.5" strokeWidth={1.5} /> Page {page}</h3>
            <div className="space-y-3 rounded-2xl bg-foreground/4 p-4">
              {units.map((u) => (
                <HighlightedText
                  key={u.id} text={u.text}
                  spans={u.char_start != null && u.char_end != null ? spansFor(u.char_start, u.char_end) : []}
                  activeId={active ? String(active) : undefined} onSelect={(id) => onSelect(Number(id))}
                />
              ))}
            </div>
          </section>
        ))}
      </div>
    );
  }

  const hl = (from: number, to: number) => (
    <HighlightedText
      text={version.text.slice(from, to)} spans={spansFor(from, to)}
      activeId={active ? String(active) : undefined} onSelect={(id) => onSelect(Number(id))}
    />
  );
  const cs = culprit?.char_start, ce = culprit?.char_end;
  if (culprit && cs != null && ce != null && cs >= 0 && ce > cs && ce <= version.text.length) {
    return (
      <div>
        {cs > 0 && hl(0, cs)}
        <div id="audience-culprit" className="my-2 rounded-2xl border-l-4 border-[#FF9F0A] bg-human/10 py-2 pl-3 pr-2">
          <p className="mb-1 flex items-center gap-1.5 text-caption font-semibold text-human-text">
            <Users className="size-3.5" strokeWidth={1.75} aria-hidden /> {culprit.certain ? "Audience concern" : "Possible audience concern"}: this paragraph worries readers most
          </p>
          {hl(cs, ce)}
        </div>
        {ce < version.text.length && hl(ce, version.text.length)}
      </div>
    );
  }
  return hl(0, version.text.length);
}

/* ------------------------------------------------------------------ page */

/** Each navigation to an item (also from the guided tour) opens it fresh: `?tab=` and `?v=` pick the tab and version. */
export default function ItemRoute() {
  const loc = useLocation();
  return <Item key={loc.key} />;
}

function Item() {
  const params = useParams();
  const [search] = useSearchParams();
  const id = Number(params.id);
  const { user } = useSession();
  const detailQ = useAsync(() => api.item(id), [id]);
  const rulesQ = useAsync(() => api.rules({ enabled: true }), []);
  const brandQ = useAsync(() => api.brand(), []);
  const detail = detailQ.data;
  const item = detail?.item;

  const [vid, setVid] = useState<number | null>(() => Number(search.get("v")) || null);
  const [version, setVersion] = useState<VersionOut | null>(null);
  const [findings, setFindings] = useState<Record<number, FindingOut>>({});
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [ev, setEv] = useState<CheckEvent | null>(null);
  const [running, setRunning] = useState(false);
  const [streamError, setStreamError] = useState<ApiError | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [active, setActive] = useState<number | undefined>();
  const [lastDiff, setLastDiff] = useState<{ vid: number; segments: DiffSegment[] } | null>(null);
  const [prevText, setPrevText] = useState<{ vid: number; text: string } | null>(null);
  const [tab, setTab] = useState(() => {
    const t = search.get("tab");
    return t === "audience" || t === "comments" || t === "history" ? t : "findings";
  });
  const [culprit, setCulprit] = useState<AudienceCulprit | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [dismissFor, setDismissFor] = useState<FindingOut | null>(null);
  const [overrideFor, setOverrideFor] = useState<FindingOut | null>(null);
  const [noteAction, setNoteAction] = useState<"request" | "reject" | null>(null);
  const unsub = useRef<() => void>(() => undefined);

  // default to the latest version once the item is known
  useEffect(() => {
    if (item && vid == null) setVid(item.latest_version_id);
  }, [item, vid]);
  // a ?v= link to a version of another item falls back to this item's latest version
  useEffect(() => {
    if (detail && vid != null && !detail.versions.some((v) => v.id === vid)) setVid(detail.item.latest_version_id);
  }, [detail, vid]);

  const refreshVersion = useCallback(async (versionId: number) => {
    const [v, fs] = await Promise.all([api.version(versionId), api.findings(versionId, { include_pass: true })]);
    setVersion(v);
    setFindings(Object.fromEntries(fs.map((f) => [f.id, f])));
    return v;
  }, []);

  // load the selected version, then follow its live check
  useEffect(() => {
    if (vid == null) return;
    let dead = false;
    unsub.current();
    setLoadError(null); setStreamError(null); setEv(null); setRunning(false);
    (async () => {
      try {
        const v = await refreshVersion(vid);
        if (dead) return;
        if (v.check_status === "queued" || v.check_status === "running") {
          setRunning(true);
          unsub.current = streamCheck(vid, {
            onEvent: (e) => { if (!dead) setEv(e); },
            onFinding: (f) => { if (!dead) setFindings((cur) => ({ ...cur, [f.id]: { ...cur[f.id], ...f } })); },
            onDone: () => {
              if (dead) return;
              setRunning(false);
              void refreshVersion(vid).catch(() => undefined);
              detailQ.reload();
            },
            onError: (err) => {
              if (dead) return;
              setRunning(false);
              setStreamError(err);
              void refreshVersion(vid).catch(() => undefined);
              detailQ.reload();
            },
          });
        }
      } catch (e) {
        if (!dead) setLoadError(toApiError(e));
      }
    })();
    return () => { dead = true; unsub.current(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vid, attempt]);

  // diff against the previous version for v2+
  useEffect(() => {
    if (!detail || !version || version.n < 2) { setPrevText(null); return; }
    const prev = detail.versions.find((v) => v.n === version.n - 1);
    if (!prev) return;
    let dead = false;
    api.version(prev.id).then((p) => { if (!dead) setPrevText({ vid: version.id, text: p.text }); }, () => undefined);
    return () => { dead = true; };
  }, [detail, version]);

  const list = useMemo(
    () => Object.values(findings).filter((f) => f.band !== "pass")
      .sort((a, b) => SEV_ORDER[a.severity] - SEV_ORDER[b.severity] || pOf(b) - pOf(a) || a.id - b.id),
    [findings],
  );
  const scanRows = useMemo<RuleScanRow[]>(() => {
    const m = new Map<string, RuleScanRow>();
    for (const f of Object.values(findings)) {
      const key = f.rule_code ?? f.fact_code;
      if (!key) continue;
      const p = pOf(f);
      const cur = m.get(key);
      if (!cur || p > cur.p) m.set(key, { id: key, label: f.rule?.statement ?? f.fact?.statement ?? f.category, p });
    }
    return [...m.values()].sort((a, b) => b.p - a.p);
  }, [findings]);

  if (!Number.isFinite(id)) return <ErrorState title="That link doesn't look right" message="Open the item from the Content list." />;
  if (detailQ.error) return <ErrorState title="Can't open this item" message={detailQ.error.message} onRetry={detailQ.reload} />;
  if (!detail || !item) {
    return <div className="space-y-4"><Skeleton className="h-12 w-80" /><Skeleton className="h-28" /><Skeleton className="h-96" /></div>;
  }

  /* ---------------------------------------------------------------- permissions */
  const acts = detail.actions;
  const role = user?.role;
  const isCreator = user?.id === item.creator_id;
  const isLatest = vid === item.latest_version_id;
  const checking = running || item.state === "checking";
  const locked = item.state === "approved" || item.state === "rejected";
  // Findings are read-only once the item is decided, for anyone but its creator, and on older versions.
  const nextVersionN = detail.versions.find((v) => v.n === (version?.n ?? 0) + 1)?.n ?? null;
  const readOnly = locked || !isCreator || !isLatest ? { itemState: item.state, nextVersionN } : null;
  const handledSteps = list.filter((f) => f.status === "accepted" || f.status === "edited").length;
  const serverWhy = (k: keyof ItemDetail["actions"]) => (acts[k].allowed ? null : acts[k].reason ?? "Not available right now.");

  const submitWhy = !isCreator ? "Only the person who created this item can submit it for review."
    : checking ? "Wait for the check to finish."
    : !isLatest ? "Switch to the latest version to submit."
    : item.blocking_open > 0 ? `Fix, dismiss or get an override for ${item.blocking_open} blocking finding${item.blocking_open === 1 ? "" : "s"} first.`
    : serverWhy("submit");
  const resubmitWhy = !isCreator ? "Only the creator can resubmit."
    : checking ? "Wait for the check to finish."
    : !isLatest ? "Switch to the latest version to resubmit."
    : handledSteps === 0 && version?.check_status === "done" ? "Accept or edit at least one fix first."
    : serverWhy("resubmit");
  const approveWhy = role !== "approver" ? "Only an approver can approve."
    : isCreator ? "You can't approve your own item. Ask another approver."
    : item.state !== "in_review" ? "Only items in review can be approved."
    : serverWhy("approve");
  const reviewWhy = (k: "request_changes" | "reject") => isCreator ? "You can't review your own item."
    : item.state !== "in_review" ? "Only items in review can be sent back or rejected."
    : serverWhy(k);
  const overrideWhy = isCreator ? "You can't override findings on your own item."
    : !(role === "reviewer" || role === "approver") ? "Only a reviewer or approver can override."
    : serverWhy("override");

  /* ---------------------------------------------------------------- actions */
  // A fix covers a whole region, so siblings change too: always refetch the version's findings afterwards.
  const upsert = (f: FindingOut) => {
    setFindings((cur) => ({ ...cur, [f.id]: f }));
    if (vid != null) void refreshVersion(vid).catch(() => undefined);
  };
  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    try { await fn(); } catch (e) { reportError(e); throw e; } finally { setBusy(null); }
  };
  const swallow = <T,>(p: Promise<T>) => p.catch(() => undefined);

  const onAccept = (f: FindingOut) => swallow(run("accept", async () => { upsert(await api.acceptFinding(f.id)); detailQ.reload(); toast.success("Fix accepted"); }));
  const onEdit = (f: FindingOut, text: string) => run("edit", async () => { upsert(await api.editFinding(f.id, { text })); detailQ.reload(); toast.success("Your edit was saved"); });
  const onDismiss = async (reason: Parameters<typeof api.dismissFinding>[1]["reason"], note: string) => {
    if (!dismissFor) return;
    await run("dismiss", async () => { upsert(await api.dismissFinding(dismissFor.id, { reason, note: note || null })); detailQ.reload(); toast.success("Finding dismissed"); });
  };
  const onOverride = async (reason: string) => {
    if (!overrideFor) return;
    await run("override", async () => { upsert(await api.override(overrideFor.id, { reason })); detailQ.reload(); toast.success("Override recorded"); });
  };
  const applyDetail = (d: ItemDetail, msg: string) => { detailQ.setData(d); toast.success(msg); };
  const onSubmit = () => swallow(run("submit", async () => applyDetail(await api.submit(id), "Sent for review")));
  const onApprove = () => swallow(run("approve", async () => applyDetail(await api.approve(id), "Approved")));
  const onResubmit = () => swallow(run("resubmit", async () => {
    const out = await api.resubmit(id);
    setLastDiff({ vid: out.version_id, segments: out.diff ?? [] });
    const fresh = await api.item(id);
    detailQ.setData(fresh);
    setVid(out.version_id);
    setAttempt((a) => a + 1);
    setTab("findings");
    toast.success(`Version ${out.n} created. Checking it now.`);
    if (out.skipped?.length) toast.info(`${out.skipped.length} fix${out.skipped.length === 1 ? " was" : "es were"} skipped: ${out.skipped[0].reason}`);
  }));
  const onNote = async (note: string) => {
    const kind = noteAction;
    await run(kind ?? "note", async () => {
      applyDetail(kind === "reject" ? await api.reject(id, { note }) : await api.requestChanges(id, { note }), kind === "reject" ? "Item rejected" : "Changes requested");
    });
  };

  /* ---------------------------------------------------------------- derived view */
  const stats = version?.stats ?? {};
  const elapsedMs = ev?.elapsed_ms ?? stats.elapsed_ms ?? 0;
  const judgments = ev?.judgments ?? stats.judgments ?? 0;
  const unitsDone = ev?.units_done ?? stats.units_done ?? 0;
  const unitsTotal = ev?.units_total ?? stats.units_total ?? version?.units.length;
  const comingOnline = !!ev?.message && /not implemented|stub|coming online/i.test(ev.message);
  const diffSegs: DiffSegment[] | null =
    version && version.n > 1
      ? lastDiff?.vid === version.id && lastDiff.segments.length ? lastDiff.segments
        : prevText?.vid === version.id ? diffSegments(prevText.text, version.text) : null
      : null;
  const groupOf = new Map<number, FixGroupInfo>();
  {
    const byGroup = new Map<string, FindingOut[]>();
    for (const f of list) if (f.fix_group) byGroup.set(f.fix_group, [...(byGroup.get(f.fix_group) ?? []), f]);
    for (const members of byGroup.values()) {
      if (members.length < 2) continue;
      const lead = members.find((f) => f.status === "open") ?? members[0];
      for (const f of members) groupOf.set(f.id, { lead, others: members.filter((o) => o.id !== lead.id), isLead: f.id === lead.id });
    }
  }
  const jumpTo = (fid: number) => { setActive(fid); document.getElementById(`finding-${fid}`)?.scrollIntoView({ behavior: "smooth", block: "center" }); };
  const openCount = list.filter((f) => f.status === "open").length;
  const palette = brandQ.data?.palette ?? [];
  const canReview = role === "reviewer" || role === "approver";
  const showWorkflow = isCreator || canReview;
  const done = version?.check_status === "done";

  return (
    <>
      <PageHeader
        title={item.title}
        subtitle={`${item.channel[0].toUpperCase()}${item.channel.slice(1)} · by ${item.creator_name} · updated ${timeAgo(item.updated_at)}`}
        actions={
          <>
            <StateBadge state={item.state} />
            {item.confidential && <ConfidentialBadge />}
            <AiUsageDrawer
              itemId={id} refreshKey={`${vid}:${running}`}
              trigger={<Button variant="secondary" className="rounded-full"><ShieldCheck strokeWidth={1.5} /> AI used</Button>}
            />
          </>
        }
      />

      {detail.versions.length > 1 && (
        <nav aria-label="Versions" className="mb-4 flex flex-wrap items-center gap-2">
          <span className="text-footnote text-muted-foreground">Version</span>
          {detail.versions.map((v) => (
            <button
              key={v.id}
              aria-pressed={v.id === vid}
              onClick={() => { setVid(v.id); setActive(undefined); }}
              className={cn("rounded-full px-3.5 py-1.5 text-footnote font-medium transition-colors", v.id === vid ? "bg-[var(--accent-fill)] text-white" : "bg-foreground/8 hover:bg-foreground/12")}
            >
              v{v.n}{v.id === item.latest_version_id ? " (latest)" : ""}
            </button>
          ))}
        </nav>
      )}

      {loadError ? (
        <ErrorState title="Can't load this version" message={loadError.message} onRetry={() => setAttempt((a) => a + 1)} />
      ) : (
        <>
          <div data-tour="live-meter">{!version ? <Skeleton className="h-[132px]" /> : <LiveMeter
            paragraphs={running ? unitsDone : (unitsTotal ?? unitsDone)} paragraphsTotal={unitsTotal}
            judgments={judgments}
            judgmentsPerSec={elapsedMs > 0 ? judgments / (elapsedMs / 1000) : 0}
            seconds={elapsedMs / 1000}
            costUsd={ev?.cost_usd ?? stats.cost_usd ?? 0}
            running={running}
          />}</div>

          {!running && (stats.warnings ?? []).length > 0 && (
            <div role="status" className="glass-strong mt-4 flex flex-wrap items-start gap-3 !rounded-2xl px-4 py-3 text-footnote">
              <Info className="mt-0.5 size-4 shrink-0 text-[var(--accent-text)]" strokeWidth={1.75} aria-hidden />
              <div className="min-w-0 flex-1 basis-60 space-y-1">{(stats.warnings ?? []).map((w) => <p key={w}>{w}</p>)}</div>
              {user?.role === "admin" && (stats.warnings ?? []).some((w) => /add rules/i.test(w)) && (
                <Link to="/setup?step=3" className="rounded-full bg-[var(--accent-fill)] px-3 py-1.5 text-caption font-semibold text-white hover:bg-[var(--accent-fill-hover)]">Add rules</Link>
              )}
            </div>
          )}
          {streamError && (
            <div className="mt-4"><ErrorState title="The live check stopped" message={streamError.message} onRetry={() => setAttempt((a) => a + 1)} className="!py-8" /></div>
          )}
          {comingOnline && running && (
            <div className="glass mt-4 flex items-center gap-3 !rounded-2xl px-4 py-3 text-footnote">
              <Loader2 className="size-4 animate-spin text-[var(--accent-text)]" aria-hidden />
              The checking pipeline is still coming online on this server. Results will appear here as soon as it is.
            </div>
          )}

          {showWorkflow && (
            <section aria-label="Workflow" data-tour="workflow" className="glass-strong mt-4 flex flex-wrap items-center gap-2 p-4">
              <span className="mr-2 text-footnote font-medium text-muted-foreground">Next step</span>
              {isCreator && (
                <>
                  <TipButton reason={submitWhy} disabled={busy !== null} onClick={onSubmit} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">
                    {busy === "submit" ? <Loader2 className="animate-spin" /> : <Send strokeWidth={1.75} />} Submit for review
                  </TipButton>
                  <TipButton reason={resubmitWhy} disabled={busy !== null} onClick={onResubmit} variant="secondary" className="rounded-full">
                    {busy === "resubmit" ? <Loader2 className="animate-spin" /> : <GitCompare strokeWidth={1.75} />} Resubmit as version {(version && detail.versions.length ? detail.versions[detail.versions.length - 1].n : 1) + 1}
                  </TipButton>
                </>
              )}
              {canReview && (
                <>
                  <span data-tour="approve-button" className="inline-flex"><TipButton reason={approveWhy} disabled={busy !== null} onClick={onApprove} className="rounded-full bg-[#167A34] text-white hover:bg-[#167A34]/90">
                    {busy === "approve" ? <Loader2 className="animate-spin" /> : <CheckCheck strokeWidth={1.75} />} Approve
                  </TipButton></span>
                  <TipButton reason={reviewWhy("request_changes")} disabled={busy !== null} onClick={() => setNoteAction("request")} variant="secondary" className="rounded-full">
                    <Undo2 strokeWidth={1.75} /> Request changes
                  </TipButton>
                  <TipButton reason={reviewWhy("reject")} disabled={busy !== null} onClick={() => setNoteAction("reject")} variant="outline" className="rounded-full text-flag-text">
                    <XCircle strokeWidth={1.75} /> Reject
                  </TipButton>
                </>
              )}
            </section>
          )}

          <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,460px)]">
            <div className="min-w-0 space-y-6 lg:sticky lg:top-24 lg:max-h-[calc(100dvh-7rem)] lg:self-start lg:overflow-y-auto lg:pr-1">
              <section className="glass-strong p-5 md:p-6" aria-label="Content">
                <h2 className="mb-3 text-headline">Content{version ? ` · v${version.n}` : ""}</h2>
                {version ? (
                  <ContentPanel version={version} findings={Object.values(findings)} active={active} onSelect={(fid) => { setActive(fid); setTab("findings"); document.getElementById(`finding-${fid}`)?.scrollIntoView({ block: "center", behavior: "smooth" }); }} palette={palette} culprit={tab === "audience" ? culprit : null} />
                ) : <div className="space-y-2"><Skeleton className="h-5" /><Skeleton className="h-5 w-4/5" /><Skeleton className="h-5 w-3/5" /></div>}
              </section>

              {diffSegs && version && (
                <section className="glass-strong p-5 md:p-6" aria-label="Changes from the previous version" data-tour="diff">
                  <h2 className="mb-1 flex items-center gap-2 text-headline"><GitCompare className="size-4" strokeWidth={1.5} /> What changed from v{version.n - 1}</h2>
                  <p className="mb-3 text-footnote text-muted-foreground">Removed words are struck through; added words are highlighted.</p>
                  <DiffView segments={diffSegs} />
                </section>
              )}

              <RuleScanPanel rows={scanRows} totalRules={rulesQ.data?.length} running={running} />
            </div>

            <div className="min-w-0">
              <Tabs value={tab} onValueChange={setTab}>
                <TabsList className="h-11 w-full rounded-full bg-[var(--glass-bg)] p-1 backdrop-blur-xl">
                  <TabsTrigger value="findings" className="flex-1 rounded-full data-[state=active]:bg-[var(--accent-fill)] data-[state=active]:text-white">Findings{list.length ? ` (${openCount})` : ""}</TabsTrigger>
                  {!item.confidential && <TabsTrigger value="audience" className="flex-1 rounded-full data-[state=active]:bg-[var(--accent-fill)] data-[state=active]:text-white">Audience</TabsTrigger>}
                  <TabsTrigger value="comments" className="flex-1 rounded-full data-[state=active]:bg-[var(--accent-fill)] data-[state=active]:text-white">Comments</TabsTrigger>
                  <TabsTrigger value="history" className="flex-1 rounded-full data-[state=active]:bg-[var(--accent-fill)] data-[state=active]:text-white">History</TabsTrigger>
                </TabsList>

                <TabsContent value="findings" className="mt-4 space-y-4">
                  {list.length === 0 && (
                    running ? (
                      <div className="glass-strong space-y-3 p-5" role="status"><Skeleton className="h-4 w-1/3" /><Skeleton className="h-16" /><p className="text-footnote text-muted-foreground">Findings appear here as the check finds them.</p></div>
                    ) : version?.check_status === "not_run" ? (
                      <EmptyState icon={FileText} title="Not checked yet" description="This item hasn't been run through the checks. Resubmit it as a new version to start a check." />
                    ) : version?.check_status === "error" ? (
                      <ErrorState title="The check failed" message="Something went wrong while checking this version. Resubmit to try again." />
                    ) : done ? (
                      <EmptyState icon={CircleCheck} title="No issues found" description="Every rule passed on this version. Nothing needs fixing." />
                    ) : null
                  )}
                  <AnimatePresence initial={false}>
                    {list.map((f, i) => (
                      <AnimatedListItem key={f.id}>
                        <div id={`finding-${f.id}`} data-tour={i === 0 ? "first-blocking" : undefined}>
                          <FindingCard
                            finding={f} group={groupOf.get(f.id) ?? null} onJump={jumpTo} active={active === f.id} onSelect={setActive}
                            canAct={!locked && isLatest && !checking && (isCreator || canReview)}
                            readOnly={readOnly}
                            canOverride={canReview && !locked && isLatest}
                            overrideDisabledReason={overrideWhy}
                            onAccept={onAccept} onEdit={onEdit}
                            onDismiss={setDismissFor} onOverride={setOverrideFor}
                          />
                        </div>
                      </AnimatedListItem>
                    ))}
                  </AnimatePresence>
                </TabsContent>

                {!item.confidential && (
                  <TabsContent value="audience" className="mt-4">
                    {tab === "audience" && (
                      <AudiencePanel
                        versionId={vid} versionN={version?.n ?? 1} onCulprit={setCulprit}
                        previousVersionId={detail.versions.find((v) => v.n === (version?.n ?? 1) - 1)?.id ?? null}
                        onOpenVersion={(v) => { setVid(v); setActive(undefined); }}
                      />
                    )}
                  </TabsContent>
                )}
                <TabsContent value="comments" className="mt-4"><CommentsTab itemId={id} canComment={acts.comment.allowed} why={acts.comment.reason} /></TabsContent>
                <TabsContent value="history" className="mt-4"><HistoryTab itemId={id} refreshKey={`${item.state}:${item.updated_at}`} /></TabsContent>
                {item.confidential && (
                  <p className="mt-3 flex items-start gap-2 px-2 text-caption text-muted-foreground">
                    <Lock className="mt-0.5 size-3.5 shrink-0" strokeWidth={1.75} aria-hidden />
                    <span>The Audience tab is hidden because this item is confidential. The preview sends the text to an outside AI service, and confidential items never leave this server.</span>
                  </p>
                )}
              </Tabs>
            </div>
          </div>
        </>
      )}

      <DismissDialog open={!!dismissFor} onOpenChange={(o) => !o && setDismissFor(null)} onConfirm={onDismiss} />
      <NoteDialog
        open={!!overrideFor} onOpenChange={(o) => !o && setOverrideFor(null)}
        title="Override this blocking finding"
        description="The item can move on with this finding still present. Your name and reason are saved in the history."
        label="Reason (at least 10 characters)" confirmLabel="Record override"
        onConfirm={async (n) => { if (n.length < 10) { toast.error("Please give a reason of at least 10 characters."); throw new Error("short"); } await onOverride(n); }}
      />
      <NoteDialog
        open={noteAction !== null} onOpenChange={(o) => !o && setNoteAction(null)}
        title={noteAction === "reject" ? "Reject this item" : "Request changes"}
        description={noteAction === "reject" ? "The creator is told why. A rejected item can't be approved later." : "The note goes to the creator as a comment so they know what to fix."}
        label="Note for the creator" confirmLabel={noteAction === "reject" ? "Reject" : "Send back"} destructive={noteAction === "reject"}
        onConfirm={onNote}
      />
    </>
  );
}

/* ------------------------------------------------------------------ comments + history */

function CommentsTab({ itemId, canComment, why }: { itemId: number; canComment: boolean; why: string | null }) {
  const q = useAsync(() => api.comments(itemId), [itemId]);
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  async function send() {
    if (!body.trim()) return;
    setBusy(true);
    try {
      const c = await api.addComment(itemId, { body: body.trim() });
      q.setData((cur) => [...(cur ?? []), c]);
      setBody("");
    } catch (e) { reportError(e); } finally { setBusy(false); }
  }
  if (q.error) return <ErrorState title="Can't load comments" message={q.error.message} onRetry={q.reload} className="!py-8" />;
  return (
    <div className="space-y-4">
      {q.loading && !q.data ? <Skeleton className="h-24" /> : (q.data ?? []).length === 0 ? (
        <EmptyState icon={MessageSquare} title="No comments yet" description="Reviewers and the creator can talk about this item here." className="!py-8" />
      ) : (
        <ul className="space-y-3">
          {q.data!.map((c) => (
            <li key={c.id} className="glass-strong flex gap-3 p-4">
              <UserAvatar id={c.user_id} name={c.user_name} role={c.role} className="size-8 text-footnote" />
              <div className="min-w-0">
                <p className="text-footnote"><span className="font-semibold">{c.user_name}</span> <span className="text-muted-foreground">· {ROLE_LABELS[c.role]} · {timeAgo(c.created_at)}</span></p>
                <p className="mt-1 whitespace-pre-wrap break-words text-body">{c.body}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
      <div className="glass-strong space-y-2 p-4">
        <label htmlFor="new-comment" className="sr-only">Add a comment</label>
        <Textarea id="new-comment" value={body} onChange={(e) => setBody(e.target.value)} rows={3} placeholder="Add a comment" disabled={!canComment} className="resize-none rounded-2xl bg-background/60" />
        <div className="flex items-center justify-between gap-3">
          <p className="text-caption text-muted-foreground">{!canComment ? why ?? "You can't comment on this item." : ""}</p>
          <Button disabled={!canComment || busy || !body.trim()} onClick={send} className="rounded-full bg-[var(--accent-fill)] text-white hover:bg-[var(--accent-fill-hover)]">
            {busy ? <Loader2 className="animate-spin" /> : <Send strokeWidth={1.75} />} Comment
          </Button>
        </div>
      </div>
    </div>
  );
}

const ACTION_LABEL: Record<string, string> = {
  created: "Created", submitted: "Submitted for review", approved: "Approved", rejected: "Rejected",
  changes_requested: "Changes requested", resubmitted: "Resubmitted", commented: "Commented",
  finding_accepted: "Fix accepted", finding_edited: "Fix edited", finding_dismissed: "Finding dismissed",
  finding_overridden: "Blocking finding overridden", override: "Blocking finding overridden",
  accept: "Fix accepted", edit: "Fix edited", dismiss: "Finding dismissed",
};

function HistoryTab({ itemId, refreshKey }: { itemId: number; refreshKey: string }) {
  const q = useAsync(() => api.history(itemId), [itemId, refreshKey]);
  if (q.error) return <ErrorState title="Can't load the history" message={q.error.message} onRetry={q.reload} className="!py-8" />;
  if (q.loading && !q.data) return <Skeleton className="h-40" />;
  const events = [...(q.data ?? [])].reverse();
  if (events.length === 0) return <EmptyState icon={HistoryIcon} title="No history yet" className="!py-8" />;
  return (
    <ol className="glass-strong divide-y divide-border overflow-hidden">
      {events.map((e) => {
        const d = e.detail as Record<string, unknown>;
        const bits = [
          d.from && d.to ? `${String(d.from).replace(/_/g, " ")} → ${String(d.to).replace(/_/g, " ")}` : null,
          typeof d.version === "number" ? `v${d.version}` : null,
          typeof d.reason === "string" ? `Reason: ${d.reason}` : null,
          typeof d.note === "string" ? `Note: ${d.note}` : null,
        ].filter(Boolean);
        return (
          <li key={e.id} className="px-4 py-3">
            <p className="text-body font-medium">{ACTION_LABEL[e.action] ?? e.action.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())}</p>
            <p className="text-caption text-muted-foreground">{e.user_name ?? "System"} · {timeAgo(e.created_at)}{bits.length ? ` · ${bits.join(" · ")}` : ""}</p>
          </li>
        );
      })}
    </ol>
  );
}
