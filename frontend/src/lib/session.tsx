import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { DEMO_USERS, type DemoUser } from "./mock";

/**
 * Demo session. Talks to POST /api/demo/login and GET /api/me directly with fetch so this file does
 * not depend on lib/api.ts (owned by another agent). If the backend is unreachable (frontend-only dev),
 * it falls back to a local identity so the UI stays explorable; the integration agent can swap in api.ts.
 */
interface SessionValue {
  user: DemoUser | null;
  backendOnline: boolean | null;
  login: (userId: string) => Promise<DemoUser>;
  logout: () => void;
}

const Ctx = createContext<SessionValue | null>(null);
const KEY = "bg-user";

function stored(): DemoUser | null {
  try {
    const id = localStorage.getItem(KEY);
    return DEMO_USERS.find((u) => u.id === id) ?? null;
  } catch { return null; }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<DemoUser | null>(stored);
  const [backendOnline, setOnline] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    fetch("/api/me", { credentials: "include" })
      .then(async (r) => {
        if (!alive) return;
        if (!r.ok) { setOnline(r.status < 500 && r.status !== 404); return; }
        setOnline(true);
        const me = (await r.json()) as { id?: string; user_id?: string };
        const found = DEMO_USERS.find((u) => u.id === (me.id ?? me.user_id));
        if (found) setUser(found);
      })
      .catch(() => alive && setOnline(false));
    return () => { alive = false; };
  }, []);

  const login = useCallback(async (userId: string) => {
    const target = DEMO_USERS.find((u) => u.id === userId);
    if (!target) throw new Error("Unknown demo user");
    try {
      const r = await fetch("/api/demo/login", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId }),
      });
      setOnline(r.ok || (r.status !== 404 && r.status < 500));
    } catch { setOnline(false); }
    try { localStorage.setItem(KEY, userId); } catch { /* storage blocked */ }
    setUser(target);
    return target;
  }, []);

  const logout = useCallback(() => {
    try { localStorage.removeItem(KEY); } catch { /* storage blocked */ }
    setUser(null);
  }, []);

  const value = useMemo(() => ({ user, backendOnline, login, logout }), [user, backendOnline, login, logout]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSession must be used inside SessionProvider");
  return v;
}
