import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type AudienceOut } from "./api";
import { toApiError } from "./hooks";

export const AUDIENCE_LABEL = "Simulated audience, directional signal, not a survey";
export const AUDIENCE_ATTRIBUTION = "Personas: NVIDIA Nemotron-Personas-India (CC-BY-4.0)";

export interface AudienceState {
  data: AudienceOut | null;
  loading: boolean; // first load of the saved result
  starting: boolean; // POST in flight
  error: ApiError | null;
  start: () => void;
  reload: () => void;
}

/**
 * Loads the saved Audience Preview of a version and follows a running one.
 * Progress comes over SSE; if the stream fails or never delivers, it falls back to polling every 0.6 s.
 */
export function useAudience(versionId: number | null, enabled: boolean): AudienceState {
  const [data, setData] = useState<AudienceOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [tick, setTick] = useState(0);
  const stop = useRef<() => void>(() => undefined);
  const alive = useRef(true);
  const current = useRef<number | null>(null);

  useEffect(() => { alive.current = true; return () => { alive.current = false; stop.current(); }; }, []);

  const follow = useCallback((vid: number) => {
    stop.current();
    let closed = false;
    let es: EventSource | null = null;
    let timer: ReturnType<typeof setInterval> | null = null;
    const accept = (d: AudienceOut) => { if (!closed && alive.current && current.current === vid) setData(d); };
    const close = () => { closed = true; es?.close(); if (timer) clearInterval(timer); };
    stop.current = close;

    const finish = async () => {
      close();
      try { accept(await api.audience(vid)); } catch (e) { if (alive.current) setError(toApiError(e)); }
    };
    const poll = () => {
      if (closed || timer) return;
      es?.close();
      timer = setInterval(async () => {
        try {
          const d = await api.audience(vid);
          accept(d);
          if (d.status !== "running") void finish();
        } catch (e) { if (alive.current) setError(toApiError(e)); close(); }
      }, 600);
    };

    try {
      es = new EventSource(api.audienceEventsUrl(vid), { withCredentials: true });
      let gotAny = false;
      const onMsg = (msg: Event) => {
        if (!(msg instanceof MessageEvent)) { // connection-level error, not a server "error" event
          if (!gotAny || es?.readyState === EventSource.CLOSED) poll();
          return;
        }
        try {
          const d = JSON.parse(msg.data as string) as AudienceOut;
          gotAny = true;
          accept(d);
          if (d.status !== "running") void finish();
        } catch { /* ignore a malformed frame */ }
      };
      es.addEventListener("progress", onMsg);
      es.addEventListener("done", onMsg);
      es.addEventListener("error", onMsg);
      es.onmessage = onMsg;
      // if the stream stays silent, start polling anyway
      setTimeout(() => { if (!gotAny) poll(); }, 2500);
    } catch {
      poll();
    }
  }, []);

  useEffect(() => {
    current.current = versionId;
    stop.current();
    setData(null); setError(null);
    if (versionId == null || !enabled) { setLoading(false); return; }
    let dead = false;
    setLoading(true);
    api.audience(versionId).then(
      (d) => {
        if (dead) return;
        setData(d); setLoading(false);
        if (d.status === "running") follow(versionId);
      },
      (e) => { if (!dead) { setError(toApiError(e)); setLoading(false); } },
    );
    return () => { dead = true; stop.current(); };
  }, [versionId, enabled, tick, follow]);

  const start = useCallback(() => {
    if (versionId == null) return;
    setStarting(true); setError(null);
    api.startAudience(versionId).then(
      (d) => {
        if (!alive.current || current.current !== versionId) return;
        setData(d); setStarting(false);
        if (d.status === "running") follow(versionId);
      },
      (e) => { if (alive.current) { setError(toApiError(e)); setStarting(false); } },
    );
  }, [versionId, follow]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, loading, starting, error, start, reload };
}
