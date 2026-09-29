"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

type State<T> = { data: T | null; error: string | null; loading: boolean };

/**
 * Fetch data from the PayFlow API, optionally polling.
 * `intervalMs` may be a function of the latest data so pages can poll fast
 * while a pipeline is running and stop once it reaches a terminal state.
 */
export function useApi<T>(
  fetcher: () => Promise<T>,
  deps: unknown[] = [],
  intervalMs?: number | ((data: T | null) => number | undefined),
) {
  const [state, setState] = useState<State<T>>({ data: null, error: null, loading: true });
  const fetcherRef = useRef(fetcher);
  useLayoutEffect(() => {
    fetcherRef.current = fetcher;
  });
  const dataRef = useRef<T | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetcherRef.current();
      dataRef.current = data;
      setState({ data, error: null, loading: false });
    } catch (err) {
      setState((prev) => ({ ...prev, error: err instanceof Error ? err.message : String(err), loading: false }));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async () => {
      await load();
      if (cancelled) return;
      const next = typeof intervalMs === "function" ? intervalMs(dataRef.current) : intervalMs;
      if (next) timer = setTimeout(tick, next);
    };
    setState((prev) => ({ ...prev, loading: true }));
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { ...state, refresh: load };
}
