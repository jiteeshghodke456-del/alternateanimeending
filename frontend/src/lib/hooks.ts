import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "./api";

export interface AsyncState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
  setData: (updater: T | null | ((prev: T | null) => T | null)) => void;
}

/** Run an async loader on mount and whenever `deps` change. Stale responses are ignored. */
export function useAsync<T>(loader: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const seq = useRef(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    const mine = ++seq.current;
    setLoading(true);
    setError(null);
    loaderRef.current().then(
      (d) => { if (mine === seq.current) { setData(d); setLoading(false); } },
      (e) => {
        if (mine !== seq.current) return;
        setError(toApiError(e));
        setLoading(false);
      },
    );
    return () => { seq.current++; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload, setData };
}

export function toApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e;
  return new ApiError(0, e instanceof Error && e.message ? e.message : "Something went wrong. Please try again.");
}

/** Toast a human message for a failed action. 501 means the server feature is still coming online. */
export function reportError(e: unknown, fallback?: string): void {
  const err = toApiError(e);
  if (err.notImplemented) {
    toast.info("This part is still coming online on this server. Please try again in a moment.");
    return;
  }
  toast.error(err.message || fallback || "Something went wrong. Please try again.");
}

export function timeAgo(iso: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 86400 * 14) return `${Math.round(s / 86400)} d ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function formatDate(d: string | null): string {
  if (!d) return "";
  const t = new Date(d.length <= 10 ? `${d}T00:00:00` : d);
  return Number.isNaN(t.getTime()) ? d : t.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}
