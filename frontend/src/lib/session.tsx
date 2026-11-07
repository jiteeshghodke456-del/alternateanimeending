import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";
import { api, ApiError, serverFeatures, setToken, SERVER_WAKING_EVENT, WORKSPACE_RESET_EVENT, type BrandOut, type ServerFeatures, type HealthOut, type Role, type UserOut, type WorkspaceKind, type WorkspaceOut } from "./api";

interface SessionValue {
  user: UserOut | null;
  users: UserOut[];
  demoMode: boolean;
  health: HealthOut | null;
  /** null while the first /api/me is in flight */
  ready: boolean;
  backendOnline: boolean | null;
  /** The signed-in workspace's brand (null when signed out or not loaded yet). */
  brand: BrandOut | null;
  /** null when signed out, or when the server has no workspaces (older backend). */
  workspace: WorkspaceOut | null;
  /** "demo" | "blank", falling back to brand.demo when the server has no workspaces. */
  workspaceKind: WorkspaceKind | null;
  /** Newer endpoints this server has (null until known). */
  features: ServerFeatures | null;
  /** Non-null while the server is starting or waking up (deploy, free-tier sleep): the UI waits calmly. */
  starting: { stage: string | null; since: number } | null;
  login: (userId: string) => Promise<UserOut>;
  logout: () => Promise<void>;
  refreshBrand: () => Promise<void>;
  /** Create a fresh workspace and sign in as the user with `role` in it. Throws ApiError (404 on older servers). */
  startWorkspace: (kind: WorkspaceKind, role: Role) => Promise<UserOut>;
  is: (...roles: Role[]) => boolean;
}

const Ctx = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserOut | null>(null);
  const [users, setUsers] = useState<UserOut[]>([]);
  const [demoMode, setDemoMode] = useState(true);
  const [health, setHealth] = useState<HealthOut | null>(null);
  const [ready, setReady] = useState(false);
  const [backendOnline, setOnline] = useState<boolean | null>(null);
  const [brand, setBrand] = useState<BrandOut | null>(null);
  const [workspace, setWorkspace] = useState<WorkspaceOut | null>(null);
  const [features, setFeatures] = useState<ServerFeatures | null>(null);

  const [starting, setStarting] = useState<{ stage: string | null; since: number } | null>(null);
  const polling = useRef(false);

  const loadMe = useCallback(async (): Promise<boolean> => {
    try {
      const me = await api.me();
      setOnline(true);
      setUser(me.user);
      setUsers(me.users);
      setDemoMode(me.demo_mode);
      setReady(true);
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.waking) return false; // starting up: waitForServer() takes over
      setOnline(false);
      setReady(true);
      return true;
    }
  }, []);

  // The server is starting (our 503 + X-BG-Starting) or waking (host 502/503, no connection): poll
  // /api/health every 1.5 s, then load normally. Never an error screen or a red toast for this.
  const waitForServer = useCallback(() => {
    if (polling.current) return;
    polling.current = true;
    setStarting((s) => s ?? { stage: null, since: Date.now() });
    const tick = async () => {
      try {
        const h = await api.health();
        setHealth(h);
        if (h.ready === false) {
          setStarting((s) => ({ stage: h.stage ?? null, since: s?.since ?? Date.now() }));
        } else if (await loadMe()) {
          polling.current = false;
          setStarting(null);
          return;
        }
      } catch { /* still waking */ }
      window.setTimeout(() => void tick(), 1500);
    };
    window.setTimeout(() => void tick(), 1500);
  }, [loadMe]);

  useEffect(() => {
    let alive = true;
    // health and /me in parallel: no extra round trip on a warm server
    api.health().then((h) => {
      if (!alive) return;
      setHealth(h);
      if (h.ready === false) waitForServer();
    }, () => undefined);
    void loadMe().then((done) => { if (alive && !done) waitForServer(); });
    const onWaking = () => waitForServer();
    window.addEventListener(SERVER_WAKING_EVENT, onWaking);
    return () => { alive = false; window.removeEventListener(SERVER_WAKING_EVENT, onWaking); };
  }, [loadMe, waitForServer]);

  // Feature detection never blocks first paint: it runs once the server is up.
  useEffect(() => {
    if (starting || !ready) return;
    let alive = true;
    void serverFeatures().then((f) => alive && setFeatures(f));
    return () => { alive = false; };
  }, [starting, ready]);

  const refreshBrand = useCallback(async () => {
    const f = await serverFeatures();
    const [b, w] = await Promise.allSettled([api.brand(), f.workspaces ? api.workspace() : Promise.reject(new Error("no workspaces"))]);
    setBrand(b.status === "fulfilled" ? b.value : null);
    setWorkspace(w.status === "fulfilled" ? w.value : null);
  }, []);

  // A response said this browser's workspace expired: say so, then reload who is signed in and the brand.
  useEffect(() => {
    const onReset = () => {
      toast.info("Your workspace expired; a fresh demo was opened.", { id: "workspace-reset", duration: 8000 });
      api.me().then((me) => {
        setUser(me.user);
        setUsers(me.users);
        setDemoMode(me.demo_mode);
        if (me.user) void refreshBrand();
      }, () => undefined);
    };
    window.addEventListener(WORKSPACE_RESET_EVENT, onReset);
    return () => window.removeEventListener(WORKSPACE_RESET_EVENT, onReset);
  }, [refreshBrand]);

  // Brand + workspace follow the signed-in user (they need a session).
  useEffect(() => {
    if (!user) { setBrand(null); setWorkspace(null); return; }
    void refreshBrand();
  }, [user?.id, refreshBrand]); // eslint-disable-line react-hooks/exhaustive-deps

  const login = useCallback(async (userId: string) => {
    const out = await api.login(userId);
    setUser(out.user);
    setOnline(true);
    return out.user;
  }, []);

  const logout = useCallback(async () => {
    setToken(null);
    setUser(null);
    try { await api.logout(); } catch { /* already signed out */ }
  }, []);

  const startWorkspace = useCallback(async (kind: WorkspaceKind, role: Role) => {
    await api.createWorkspace(kind);
    // The server cleared the login. Keep the old user in state until the new login lands,
    // so the app shell doesn't bounce to the landing page in between.
    setToken(null);
    const me = await api.me();
    setUsers(me.users);
    setDemoMode(me.demo_mode);
    const target = me.users.find((u) => u.role === role) ?? me.users[0];
    if (!target) throw new ApiError(500, "The new workspace has no users. Please try again.");
    const out = await api.login(target.id);
    setUser(out.user);
    await refreshBrand();
    return out.user;
  }, [refreshBrand]);

  const is = useCallback((...roles: Role[]) => !!user && roles.includes(user.role), [user]);

  const workspaceKind: WorkspaceKind | null = workspace?.kind ?? (brand ? (brand.demo ? "demo" : "blank") : null);

  const value = useMemo(
    () => ({ user, users, demoMode, health, ready, backendOnline, brand, workspace, workspaceKind, features, starting, login, logout, refreshBrand, startWorkspace, is }),
    [user, users, demoMode, health, ready, backendOnline, brand, workspace, workspaceKind, features, starting, login, logout, refreshBrand, startWorkspace, is],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSession must be used inside SessionProvider");
  return v;
}
