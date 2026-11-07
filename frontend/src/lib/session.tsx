import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, setToken, type HealthOut, type Role, type UserOut } from "./api";

interface SessionValue {
  user: UserOut | null;
  users: UserOut[];
  demoMode: boolean;
  health: HealthOut | null;
  /** null while the first /api/me is in flight */
  ready: boolean;
  backendOnline: boolean | null;
  login: (userId: string) => Promise<UserOut>;
  logout: () => Promise<void>;
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

  useEffect(() => {
    let alive = true;
    api.health().then((h) => alive && setHealth(h), () => undefined);
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

  const is = useCallback((...roles: Role[]) => !!user && roles.includes(user.role), [user]);

  const value = useMemo(
    () => ({ user, users, demoMode, health, ready, backendOnline, login, logout, is }),
    [user, users, demoMode, health, ready, backendOnline, login, logout, is],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSession must be used inside SessionProvider");
  return v;
}
