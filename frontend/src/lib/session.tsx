import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError, serverFeatures, setToken, type BrandOut, type ServerFeatures, type HealthOut, type Role, type UserOut, type WorkspaceKind, type WorkspaceOut } from "./api";

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

  useEffect(() => {
    let alive = true;
    api.health().then((h) => alive && setHealth(h), () => undefined);
    void serverFeatures().then((f) => alive && setFeatures(f));
    api.me().then(
      (me) => {
        if (!alive) return;
        setOnline(true);
        setUser(me.user);
        setUsers(me.users);
        setDemoMode(me.demo_mode);
        setReady(true);
      },
      () => { if (alive) { setOnline(false); setReady(true); } },
    );
    return () => { alive = false; };
  }, []);

  const refreshBrand = useCallback(async () => {
    const f = await serverFeatures();
    const [b, w] = await Promise.allSettled([api.brand(), f.workspaces ? api.workspace() : Promise.reject(new Error("no workspaces"))]);
    setBrand(b.status === "fulfilled" ? b.value : null);
    setWorkspace(w.status === "fulfilled" ? w.value : null);
  }, []);

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
    () => ({ user, users, demoMode, health, ready, backendOnline, brand, workspace, workspaceKind, features, login, logout, refreshBrand, startWorkspace, is }),
    [user, users, demoMode, health, ready, backendOnline, brand, workspace, workspaceKind, features, login, logout, refreshBrand, startWorkspace, is],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSession must be used inside SessionProvider");
  return v;
}
