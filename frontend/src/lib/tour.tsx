/**
 * Guided demo tour: a React context that walks a judge through Rulemint in nine steps.
 *
 * - Every step is driven by app state, not by clicks: a step is "done" when its `check()` says so
 *   (the check finished, version 2 exists, the audience runs are done...), so it works the same
 *   whether the judge clicks around themselves or presses "Do it for me".
 * - "Do it for me" calls the app's own API client and navigates. It never clicks pixels.
 * - Each step signs in as the role it needs (demo login) and the card says who you are now.
 * - Every async part has a timeout; a failure shows what happened with Retry / Skip. Never a dead end.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, ApiError, DEMO_ITEM_KEY, ROLE_LABELS, serverFeatures, withToken, type FindingOut, type ItemSummary, type Role, type UserOut } from "./api";
import { reportError, toApiError } from "./hooks";
import { scoreApi } from "./score"; // Brand Score: below-target submit needs a reason
import { useSession } from "./session";
import { importPhase, reportImportPhase, requestPdfImport, setTourActive } from "./tourBus";

/* ------------------------------------------------------------------ data */

export interface TourData {
  baselineId?: number; // highest item id before the tour's check, to spot the item the judge creates
  itemId?: number;
  v1?: number;
  v2?: number;
  v1Open?: number;
  v2Open?: number;
  first?: { id: number; code: string | null; page: number | null; p: number | null; verified: boolean; hasFix: boolean };
  au?: { before: number; after: number; groups: number };
  goodAdId?: number;
  wrongAdId?: number;
  sawWrong?: boolean;
  pdf?: { proposals: number; verified: number; cost?: number | null };
  ownItemId?: number;
  ownRefusal?: string;
  cost?: number;
}

export type TourStatus = "entering" | "ready" | "doing" | "done" | "error";

/** A failure the judge should see (as opposed to a passing network blip while polling). */
export class TourFail extends Error {}

export interface TourCtx {
  readonly data: TourData;
  set: (patch: Partial<TourData>) => void;
  go: (to: string) => void;
  as: (role: Role) => Promise<UserOut>;
  progress: (msg: string) => void;
  pathname: () => string;
}

export interface TourStep {
  id: string;
  title: string;
  role?: Role;
  body: (d: TourData) => ReactNode;
  doneBody?: (d: TourData) => ReactNode;
  /** data-tour values to highlight, first found wins */
  targets?: (d: TourData) => string[];
  enter?: (c: TourCtx) => Promise<void>;
  doIt?: (c: TourCtx) => Promise<void>;
  doLabel?: string;
  /** true = done; a string = progress text; false = not yet. Throw TourFail for a real failure. */
  check?: (c: TourCtx) => Promise<boolean | string>;
  timeoutMs?: number;
  finish?: boolean;
}

const sleep = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms));

function withTimeout<T>(p: Promise<T>, ms: number, msg: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const t = window.setTimeout(() => reject(new TourFail(msg)), ms);
    p.then((v) => { window.clearTimeout(t); resolve(v); }, (e) => { window.clearTimeout(t); reject(e); });
  });
}

const SEV = { blocking: 0, major: 1, minor: 2 } as const;
const pOf = (f: FindingOut) => f.probability ?? (f.band === "flag" ? 1 : f.band === "human" ? 0.5 : 0);
/** Same order as the findings list on the item page. */
export function sortFindings(fs: FindingOut[]): FindingOut[] {
  return fs.filter((f) => f.band !== "pass").sort((a, b) => SEV[a.severity] - SEV[b.severity] || pOf(b) - pOf(a) || a.id - b.id);
}
const openCount = (fs: FindingOut[]) => fs.filter((f) => f.band !== "pass" && f.status === "open").length;

async function findByTitle(prefix: string): Promise<ItemSummary> {
  const items = await api.items();
  const it = items.find((i) => i.title.startsWith(prefix));
  if (!it) throw new TourFail(`The demo item “${prefix}” isn't in this workspace. Skip this step, or restart the tour.`);
  return it;
}

function needItem(d: TourData): number {
  if (!d.itemId || !d.v1) throw new TourFail("This step needs the checked post from step 2. Go back to step 2, or skip.");
  return d.itemId;
}

const f2 = (n: number) => n.toFixed(2);
const Em = ({ children }: { children: ReactNode }) => <span className="font-semibold text-foreground">{children}</span>;

/* ------------------------------------------------------------------ steps */

export const STEPS: TourStep[] = [
  {
    id: "intro",
    title: "What Rulemint does",
    role: "creator",
    enter: async (c) => { c.go("/content/new?demo=1"); },
    body: () => (
      <>
        <p>Rulemint checks a post, image or PDF against a brand's own rules before it is published, and people approve it.</p>
        <ul className="mt-2 space-y-1.5">
          <li><Em>Audience Preview:</Em> simulated reader groups rate the post 1 to 5, before and after the fixes.</li>
          <li><Em>Logo Fidelity:</Em> the logo in an ad is matched to the approved logo file (SIFT + RANSAC).</li>
          <li><Em>Guideline PDF import:</Em> rules proposed from the PDF, each quote verified against its text.</li>
        </ul>
        <p className="mt-2">The model judges; the code decides.</p>
      </>
    ),
  },
  {
    id: "check",
    title: "Check a flawed post",
    role: "creator",
    timeoutMs: 150_000,
    targets: () => ["live-meter", "check-button"],
    enter: async (c) => {
      if (c.data.baselineId == null) {
        const items = await api.items();
        c.set({ baselineId: items.reduce((m, i) => Math.max(m, i.id), 0) });
      }
      c.go(c.data.itemId ? `/items/${c.data.itemId}` : "/content/new?demo=1");
    },
    body: () => <p>You are the Creator. This demo post breaks several rules on purpose. Press <Em>Check</Em>: every paragraph is judged against every rule, and findings stream in with a live meter of calls, time and cost.</p>,
    doneBody: (d) => <p>The check finished with <Em>{d.v1Open ?? "several"} open findings</Em>. Each one is a rule or an approved fact the post breaks.</p>,
    doLabel: "Check it for me",
    doIt: async (c) => {
      if (c.data.itemId) { c.go(`/items/${c.data.itemId}`); return; }
      const [demo] = await api.items({ key: DEMO_ITEM_KEY });
      if (!demo?.latest_version_id) throw new TourFail("The demo post isn't in this workspace. Restart the tour to get a fresh copy.");
      const v = await api.version(demo.latest_version_id);
      const out = await api.createItem({ title: demo.title.replace(/\s*\(demo_flawed_post\)\s*$/, ""), channel: demo.channel, text: v.text });
      c.set({ itemId: out.item_id, v1: out.version_id });
      c.go(`/items/${out.item_id}`);
    },
    check: async (c) => {
      if (!c.data.itemId) {
        // the judge pressed Check themselves: find the item they just made
        const mine = (await api.items({ mine: true })).filter((i) => i.id > (c.data.baselineId ?? 0) && !i.key && !i.confidential);
        const it = mine.sort((a, b) => b.id - a.id)[0];
        if (!it?.latest_version_id) return false;
        c.set({ itemId: it.id, v1: it.latest_version_id });
      }
      const v = await api.version(c.data.v1!);
      if (v.check_status === "error") throw new TourFail("The check stopped with an error. Retry to check the post again.");
      if (v.check_status !== "done") return "Checking… findings appear on the right as they are found.";
      c.set({ v1Open: openCount(await api.findings(c.data.v1!)) });
      return true;
    },
  },
  {
    id: "finding",
    title: "Read one finding",
    role: "creator",
    targets: () => ["first-blocking"],
    enter: async (c) => {
      const id = needItem(c.data);
      c.go(`/items/${id}?v=${c.data.v1}`);
      const [f] = sortFindings(await api.findings(c.data.v1!));
      if (f) c.set({ first: { id: f.id, code: f.rule_code ?? f.fact_code, page: f.rule?.page ?? null, p: f.probability, verified: f.fix_verified, hasFix: !!f.fix } });
    },
    body: (d) => {
      const f = d.first;
      return (
        <p>
          The top finding names the rule it breaks{f?.code ? <> (<Em>{f.code}</Em>)</> : null} and quotes the guideline{f?.page ? <> from <Em>page {f.page}</Em></> : null}.{" "}
          Jev gives a probability{f?.p != null ? <> (<Em>{f2(f.p)}</Em>)</> : null} and code decides: 0.70 or more flags it, 0.30 to 0.70 asks a person.{" "}
          {f && !f.hasFix ? "The explanation is in plain words; this one needs your own edit." : <>The explanation is in plain words, and the fix {f?.verified ? "was re-checked before you see it" : "keeps your message"}.</>}
        </p>
      );
    },
  },
  {
    id: "fix",
    title: "Accept the fixes, make version 2",
    role: "creator",
    timeoutMs: 150_000,
    targets: () => ["diff", "workflow"],
    enter: async (c) => {
      const id = needItem(c.data);
      c.go(`/items/${id}${c.data.v2 ? `?v=${c.data.v2}` : ""}`);
    },
    body: () => <p>Accept the suggested fixes, then press <Em>Resubmit as version 2</Em>. Version 2 is checked again from scratch, and the page shows what changed.</p>,
    doneBody: (d) => <p>Version 1 had <Em>{d.v1Open ?? "?"} open findings</Em>; version 2 has <Em>{d.v2Open ?? "?"}</Em>. “What changed from v1” shows every word the fixes moved.</p>,
    doLabel: "Accept all and resubmit",
    doIt: async (c) => {
      const id = needItem(c.data);
      if (!c.data.v2) {
        let accepted = 0;
        for (let guard = 0; guard < 40; guard++) {
          const next = (await api.findings(c.data.v1!)).find((f) => f.status === "open" && f.fix && f.band !== "pass");
          if (!next) break;
          try { await api.acceptFinding(next.id); accepted++; } catch (e) { if (toApiError(e).status !== 409) throw e; }
          c.progress(`Accepted ${accepted} fix${accepted === 1 ? "" : "es"}…`);
        }
        c.progress("Resubmitting as version 2…");
        const out = await api.resubmit(id);
        c.set({ v2: out.version_id });
      }
      c.go(`/items/${id}?v=${c.data.v2}`);
    },
    check: async (c) => {
      const id = needItem(c.data);
      if (!c.data.v2) {
        const d = await api.item(id);
        const v2 = d.versions.find((v) => v.n === 2);
        if (!v2) return false;
        c.set({ v2: v2.id });
      }
      const v = await api.version(c.data.v2!);
      if (v.check_status === "error") throw new TourFail("The check of version 2 stopped with an error. Retry, or skip this step.");
      if (v.check_status !== "done") return "Checking version 2…";
      c.set({ v2Open: openCount(await api.findings(c.data.v2!)) });
      return true;
    },
  },
  {
    id: "audience",
    title: "Innovation: Audience Preview",
    role: "creator",
    timeoutMs: 180_000,
    targets: () => ["audience-compare", "audience-panel"],
    enter: async (c) => {
      const id = needItem(c.data);
      c.go(`/items/${id}?tab=audience&v=${c.data.v2 ?? c.data.v1}`);
    },
    body: () => <p>Simulated reader groups, built from public persona data, react to the post on a 1 to 5 scale (lower means more trusting). Run it on version 1 and version 2 to see, group by group, what the fixes changed. It is a directional signal, not a survey.</p>,
    doneBody: (d) => {
      const a = d.au;
      if (!a) return <p>Both runs are done. Each group card shows its change from version 1.</p>;
      const diff = a.after - a.before;
      const dir = diff <= -0.15 ? "better" : diff >= 0.15 ? "worse" : "about the same";
      return <p>The average reaction moved from <Em>{f2(a.before)}</Em> to <Em>{f2(a.after)}</Em> ({dir}; lower is more trusting) across {a.groups} reader groups. Each group card shows its own before and after.</p>;
    },
    doLabel: "Run it on v1 and v2",
    doIt: async (c) => {
      const id = needItem(c.data);
      if (!c.data.v2) throw new TourFail("Version 2 doesn't exist yet. Go back one step, or skip.");
      for (const v of [c.data.v1!, c.data.v2]) {
        const a = await api.audience(v);
        if (a.status === "none" || a.status === "error") await api.startAudience(v);
      }
      c.go(`/items/${id}?tab=audience&v=${c.data.v2}`);
    },
    check: async (c) => {
      if (!c.data.v1 || !c.data.v2) return false;
      const [a1, a2] = await Promise.all([api.audience(c.data.v1), api.audience(c.data.v2)]);
      for (const a of [a1, a2]) {
        if (a.status === "error" || a.status === "unavailable") throw new TourFail(a.message ?? "The audience preview couldn't run.");
      }
      if (a1.status === "none" && a2.status === "none") return false;
      if (a1.status !== "done" || a2.status !== "done") {
        const p = (a: typeof a1) => (a.status === "done" ? "done" : a.progress ? `${a.progress.done}/${a.progress.total}` : a.status === "none" ? "not run" : "starting");
        return `Simulated readers answering… v1 ${p(a1)}, v2 ${p(a2)}`;
      }
      if (a2.overall && a1.overall) c.set({ au: { before: a1.overall.mean_score, after: a2.overall.mean_score, groups: a2.segments.length } });
      // the panel was showing a running state: reopen it on v2 so the before/after section appears
      if (c.pathname() === `/items/${c.data.itemId}`) c.go(`/items/${c.data.itemId}?tab=audience&v=${c.data.v2}`);
      return true;
    },
  },
  {
    id: "logo",
    title: "Innovation: Logo Fidelity",
    role: "creator",
    targets: () => ["logo-image"],
    enter: async (c) => {
      const [good, wrong] = await Promise.all([findByTitle("Slow mornings ad (good)"), findByTitle("Slow mornings ad (logo check)")]);
      c.set({ goodAdId: good.id, wrongAdId: wrong.id, sawWrong: false });
      c.go(`/items/${good.id}`);
    },
    body: () => <p>The logo in an ad is matched to the approved logo file with SIFT features and RANSAC (OpenCV, on this server). This ad uses the real logo, so it gets a <Em>green outline</Em>. Next, an ad with the wrong logo.</p>,
    doneBody: () => <p>The wrong logo gets a finding and a note that says what didn't match. This check runs locally, so it also works on confidential items.</p>,
    doLabel: "Open the wrong-logo ad",
    doIt: async (c) => {
      if (!c.data.wrongAdId) throw new TourFail("The wrong-logo ad isn't in this workspace. Skip this step.");
      c.set({ sawWrong: true });
      c.go(`/items/${c.data.wrongAdId}`);
    },
    check: async (c) => !!c.data.wrongAdId && (c.data.sawWrong === true || c.pathname() === `/items/${c.data.wrongAdId}`),
  },
  {
    id: "pdf",
    title: "Innovation: Guideline PDF import",
    role: "admin",
    timeoutMs: 120_000,
    targets: () => ["import-summary", "import-button"],
    enter: async (c) => {
      reportImportPhase({ phase: "closed" });
      c.set({ pdf: undefined });
      c.go("/brand");
    },
    body: () => <p>You are now the Admin. <Em>Import from PDF</Em> reads the brand's guideline: an AI model proposes rules, then code checks every quote against the PDF text and marks it Verified or not found. Nothing is added until you confirm.</p>,
    doneBody: (d) => <p><Em>{d.pdf?.proposals ?? "Several"} rules proposed, {d.pdf?.verified ?? "most"} quotes verified</Em> against the PDF. Confirming is optional; imported rules start switched off.</p>,
    doLabel: "Import the demo guideline",
    doIt: async () => {
      const b = await api.brand();
      if (!b.guideline_url) throw new TourFail("This brand has no guideline PDF to import. Skip this step.");
      const res = await fetch(withToken(b.guideline_url), { credentials: "include" });
      if (!res.ok) throw new TourFail("We couldn't load the guideline PDF. Retry, or skip.");
      requestPdfImport(new File([await res.blob()], "guideline.pdf", { type: "application/pdf" }));
    },
    check: async (c) => {
      const ph = importPhase();
      if (ph.phase === "error") throw new TourFail("The import didn't work. Retry, or skip this step.");
      if (ph.phase === "review" || ph.phase === "done") {
        c.set({ pdf: { proposals: ph.proposals ?? 0, verified: ph.verified ?? 0, cost: ph.cost ?? null } });
        return true;
      }
      return ph.phase === "working" ? "Reading the PDF and checking quotes… about 10 seconds." : false;
    },
  },
  {
    id: "workflow",
    title: "Workflow: people decide",
    role: "creator",
    timeoutMs: 60_000,
    targets: (d) => (d.ownItemId ? ["approve-button", "workflow"] : ["workflow"]),
    enter: async (c) => {
      const id = needItem(c.data);
      c.go(`/items/${id}`);
    },
    body: () => <p>Every item goes creator → reviewer → approver. The tour submits as Rohan (Creator), comments as Meera (Reviewer) and approves as Kabir (Approver). Then Kabir tries to approve his own post.</p>,
    doneBody: (d) => (
      <p>
        Approved by Kabir. Now on Kabir's own post, Approve is switched off
        {d.ownRefusal ? <>, and the server refuses it too: “{d.ownRefusal}”</> : null}. Workflow rules live in code; no model can change them.
      </p>
    ),
    doLabel: "Submit, comment, approve",
    doIt: async (c) => {
      const id = needItem(c.data);
      let it = (await api.item(id)).item;
      if (it.state !== "in_review" && it.state !== "approved") {
        await c.as("creator");
        // Brand Score below the channel target: submitting needs a reason (it never blocks by itself)
        const below = it.score != null && it.score < (it.target_score ?? 80);
        c.progress(below ? `v2 scores ${it.score}, under the ${it.target_score} target, so Rohan adds a reason and submits…` : "Rohan submits it for review…");
        it = below
          ? (await scoreApi.submit(id, "Guided tour: the remaining findings are minor; please check the tone.")).item
          : (await api.submit(id)).item;
        await sleep(900);
      }
      const meera = await c.as("reviewer");
      c.progress("You are now Meera (Reviewer). Adding a comment…");
      c.go(`/items/${id}?tab=comments`);
      if (!(await api.comments(id)).some((x) => x.user_id === meera.id)) {
        await api.addComment(id, { body: "Checked the claims against the approved facts. Fine to approve." });
        c.go(`/items/${id}?tab=comments`);
      }
      await sleep(1500);
      const kabir = await c.as("approver");
      c.progress("You are now Kabir (Approver). Approving…");
      if (it.state === "in_review") await api.approve(id);
      c.go(`/items/${id}?tab=history`);
      await sleep(1500);
      const own = (await api.items()).find((i) => i.creator_id === kabir.id && i.state === "in_review");
      if (own) {
        // the server's own answer for "may Kabir approve this?", without sending a request that must fail
        const a = (await api.item(own.id)).actions.approve;
        c.set({ ownItemId: own.id, ownRefusal: a.allowed ? undefined : (a.reason ?? "Not allowed").replace(/[.\s]+$/, "") });
        c.go(`/items/${own.id}`);
      }
    },
    check: async (c) => (await api.item(needItem(c.data))).item.state === "approved",
  },
  {
    id: "finish",
    title: "Now try your own brand",
    finish: true,
    enter: async (c) => {
      // what this run cost in AI calls: both checks, both audience runs and the PDF import
      const vs = [c.data.v1, c.data.v2].filter((v): v is number => !!v);
      const parts = await Promise.allSettled([
        ...vs.map((v) => api.version(v).then((x) => Number(x.stats.cost_usd ?? 0))),
        ...vs.map((v) => api.audience(v).then((x) => Number(x.cost_usd ?? 0))),
      ]);
      const sum = parts.reduce((s, p) => s + (p.status === "fulfilled" && Number.isFinite(p.value) ? p.value : 0), 0) + (c.data.pdf?.cost ?? 0);
      if (sum > 0) c.set({ cost: sum });
    },
    body: (d) => (
      <p>
        That's the whole loop: check, fix, preview the audience, verify the logo, import rules, approve.
        {d.cost ? <> This run used about <Em>${d.cost.toFixed(3)}</Em> of AI calls.</> : null} Start fresh with your own brand (a five-step setup), or look around this demo.
      </p>
    ),
  },
];

/* ------------------------------------------------------------------ context */

interface TourValue {
  active: boolean;
  launching: boolean;
  index: number;
  total: number;
  step: TourStep;
  status: TourStatus;
  error: string | null;
  progress: string | null;
  data: TourData;
  start: () => Promise<void>;
  next: () => void;
  back: () => void;
  exit: () => void;
  doIt: () => void;
  retry: () => void;
  tryOwnBrand: () => Promise<void>;
}

const Ctx = createContext<TourValue | null>(null);
const STORE = "bg-tour";

function load(): { index: number; data: TourData } | null {
  try { const s = sessionStorage.getItem(STORE); return s ? JSON.parse(s) : null; } catch { return null; }
}
function save(v: { index: number; data: TourData } | null) {
  try { if (v) sessionStorage.setItem(STORE, JSON.stringify(v)); else sessionStorage.removeItem(STORE); } catch { /* storage blocked */ }
}

export function TourProvider({ children }: { children: ReactNode }) {
  const session = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const sRef = useRef(session);
  sRef.current = session;
  const pathRef = useRef(location.pathname);
  pathRef.current = location.pathname;
  const navRef = useRef(navigate);
  navRef.current = navigate;

  const [active, setActive] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [index, setIndex] = useState(0);
  const [status, setStatus] = useState<TourStatus>("entering");
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const [data, setDataState] = useState<TourData>({});
  const dataRef = useRef<TourData>({});
  const run = useRef(0); // bumps on every step change / retry: stale async work is ignored
  const userRef = useRef<UserOut | null>(null);
  const failedAt = useRef<"enter" | "do">("enter");
  const doingSince = useRef(0);
  const doRunning = useRef(false); // a step isn't done while its "Do it for me" is still running
  const indexRef = useRef(0);
  indexRef.current = index;
  if (session.user?.id !== userRef.current?.id && session.user) userRef.current = session.user;

  const set = useCallback((patch: Partial<TourData>) => {
    dataRef.current = { ...dataRef.current, ...patch };
    setDataState(dataRef.current);
    save({ index: indexRef.current, data: dataRef.current });
  }, []);

  const as = useCallback(async (role: Role) => {
    if (userRef.current?.role === role) return userRef.current;
    const me = await api.me();
    const u = me.users.find((x) => x.role === role);
    if (!u) throw new TourFail(`This workspace has no ${ROLE_LABELS[role]}. Restart the tour.`);
    const out = await sRef.current.login(u.id);
    userRef.current = out;
    return out;
  }, []);

  const waitReady = useCallback(async () => {
    const t0 = Date.now();
    while (sRef.current.starting || !sRef.current.ready) {
      if (Date.now() - t0 > 120_000) throw new TourFail("The server is taking a long time to start. Retry in a moment.");
      setProgress("Waiting for the server to start…");
      await sleep(500);
    }
  }, []);

  const ctxFor = useCallback((my: number): TourCtx => ({
    get data() { return dataRef.current; },
    set: (p) => { if (my === run.current) set(p); },
    go: (to) => { if (my === run.current) navRef.current(to); },
    as,
    progress: (m) => { if (my === run.current) setProgress(m); },
    pathname: () => pathRef.current,
  }), [as, set]);

  const fail = useCallback((my: number, e: unknown) => {
    if (my !== run.current) return;
    const a = toApiError(e);
    setError(e instanceof TourFail ? e.message : a.waking ? "The server is waking up. Retry in a few seconds." : a.status === 0 ? "We lost contact with the server. Check the connection and retry." : a.message);
    setStatus("error");
  }, []);

  const enterStep = useCallback(async (i: number) => {
    const my = ++run.current;
    doRunning.current = false;
    const s = STEPS[i];
    setIndex(i); indexRef.current = i;
    setStatus("entering"); setError(null); setProgress(null);
    failedAt.current = "enter";
    save({ index: i, data: dataRef.current });
    try {
      await waitReady();
      if (s.role) await withTimeout(as(s.role), 20_000, "Signing in took too long. Retry.");
      if (my !== run.current) return;
      if (s.enter) await withTimeout(s.enter(ctxFor(my)), 30_000, "This step took too long to open. Retry, or skip.");
      if (my !== run.current) return;
      setProgress(null);
      setStatus(s.check ? "ready" : "done");
    } catch (e) { fail(my, e); }
  }, [as, ctxFor, fail, waitReady]);

  const doIt = useCallback(() => {
    const s = STEPS[indexRef.current];
    if (!s.doIt) return;
    const my = ++run.current; // a fresh run id also restarts polling
    failedAt.current = "do";
    doingSince.current = Date.now();
    setStatus("doing"); setError(null); setProgress(null);
    doRunning.current = true;
    (async () => {
      try {
        await withTimeout(s.doIt!(ctxFor(my)), s.timeoutMs ?? 60_000, "This is taking longer than expected. Retry, or skip this step.");
        if (my === run.current && !s.check) setStatus("done");
      } catch (e) { fail(my, e); } finally { if (my === run.current) doRunning.current = false; }
    })();
  }, [ctxFor, fail]);

  // Poll the step's check while it is ready or doing: the step is done when the app state says so.
  useEffect(() => {
    if (!active || (status !== "ready" && status !== "doing")) return;
    const s = STEPS[index];
    if (!s.check) return;
    const my = run.current;
    let busy = false, misses = 0, dead = false;
    const tick = async () => {
      if (busy || dead || my !== run.current) return;
      busy = true;
      try {
        const r = await withTimeout(s.check!(ctxFor(my)), 20_000, "The server didn't answer. Retry.");
        if (dead || my !== run.current) return;
        misses = 0;
        if (r === true && !(status === "doing" && doRunning.current)) { setProgress(null); setStatus("done"); return; }
        if (typeof r === "string") setProgress(r);
        if (status === "doing" && Date.now() - doingSince.current > (s.timeoutMs ?? 60_000)) {
          fail(my, new TourFail("This is taking longer than expected. Retry, or skip this step."));
        }
      } catch (e) {
        if (e instanceof TourFail) fail(my, e);
        else if (++misses >= 6) fail(my, e);
      } finally { busy = false; }
    };
    void tick();
    const t = window.setInterval(() => void tick(), 1500);
    return () => { dead = true; window.clearInterval(t); };
  }, [active, status, index, ctxFor, fail]);

  const start = useCallback(async () => {
    setLaunching(true);
    try {
      await waitReady();
      if ((await serverFeatures()).workspaces) await sRef.current.startWorkspace("demo", "creator");
      userRef.current = null;
      dataRef.current = {};
      setDataState({});
      setActive(true);
      void enterStep(0);
    } catch (e) {
      reportError(e, "We could not start the tour. Please try again.");
    } finally {
      setProgress(null);
      setLaunching(false);
    }
  }, [enterStep, waitReady]);

  const exit = useCallback(() => {
    run.current++;
    setActive(false);
    save(null);
  }, []);

  const next = useCallback(() => {
    if (indexRef.current < STEPS.length - 1) void enterStep(indexRef.current + 1);
  }, [enterStep]);
  const back = useCallback(() => {
    if (indexRef.current > 0) void enterStep(indexRef.current - 1);
  }, [enterStep]);
  const retry = useCallback(() => {
    if (failedAt.current === "do") doIt(); else void enterStep(indexRef.current);
  }, [doIt, enterStep]);

  const tryOwnBrand = useCallback(async () => {
    try {
      await sRef.current.startWorkspace("blank", "admin");
      exit();
      navRef.current("/setup");
    } catch (e) {
      reportError(e, "We could not create a workspace. Please try again.");
    }
  }, [exit]);

  // Pick up a tour that was running before a reload.
  const resumed = useRef(false);
  useEffect(() => {
    if (resumed.current || !session.ready || session.starting) return;
    resumed.current = true;
    const s = load();
    if (!s || !session.user) return;
    dataRef.current = s.data ?? {};
    setDataState(dataRef.current);
    setActive(true);
    void enterStep(Math.min(Math.max(0, s.index), STEPS.length - 1));
  }, [session.ready, session.starting, session.user, enterStep]);

  useEffect(() => { setTourActive(active); }, [active]);

  const value = useMemo<TourValue>(() => ({
    active, launching, index, total: STEPS.length, step: STEPS[index], status, error, progress, data,
    start, next, back, exit, doIt, retry, tryOwnBrand,
  }), [active, launching, index, status, error, progress, data, start, next, back, exit, doIt, retry, tryOwnBrand]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTour(): TourValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useTour must be used inside TourProvider");
  return v;
}

export { ApiError };
