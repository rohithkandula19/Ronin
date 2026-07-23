"use client";

import { useEffect, useState } from "react";
import { aios, withTimeout, type World, type ModelInfo, type AdapterInfo } from "@/lib/api";

/**
 * Live coding-world data from /api/v1. Attempts the real, no-auth endpoints
 * (worlds/coding, models, adapters) on mount; if the API is unreachable — the
 * default in an offline/no-backend deployment — it reports `offline` and the
 * UI falls back to the labelled sample. Nothing here fabricates a backend.
 */
export interface CodingLive {
  status: "loading" | "connected" | "offline";
  world?: World;
  models: ModelInfo[];
  adapters: AdapterInfo[];
  error?: string;
}

export function useCodingData(): CodingLive {
  const [state, setState] = useState<CodingLive>({ status: "loading", models: [], adapters: [] });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // The world config is the gate for "connected": it's read-only and
        // needs no auth, so it's the honest signal that the API is up.
        const world = await withTimeout(aios.getWorld("coding"));
        // Enrich with models/adapters, but don't fail the connection if these
        // 404 or error — a reachable API still counts as connected.
        const [models, adapters] = await Promise.all([
          withTimeout(aios.listModels()).then((r) => r.models).catch(() => []),
          withTimeout(aios.listAdapters("software")).then((r) => r.adapters).catch(() => []),
        ]);
        if (!cancelled) setState({ status: "connected", world, models, adapters });
      } catch (e) {
        if (!cancelled)
          setState({ status: "offline", models: [], adapters: [], error: e instanceof Error ? e.message : "unreachable" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
