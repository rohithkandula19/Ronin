"use client";

import { useEffect, useState } from "react";
import { aios, withTimeout, type World } from "@/lib/api";

/**
 * Shared live/offline data layer for the worlds. Each world attempts its real,
 * no-auth /api/v1 read on mount; an unreachable API (the default with no
 * backend deployed) reports `offline` and the UI keeps its labelled sample.
 * Nothing here fabricates a backend.
 */
export type LiveStatus = "loading" | "connected" | "offline";

export interface WorldLive {
  status: LiveStatus;
  world?: World;
  error?: string;
}

export function useWorld(id: string): WorldLive {
  const [state, setState] = useState<WorldLive>({ status: "loading" });
  useEffect(() => {
    let cancelled = false;
    withTimeout(aios.getWorld(id))
      .then((world) => !cancelled && setState({ status: "connected", world }))
      .catch((e) => !cancelled && setState({ status: "offline", error: e instanceof Error ? e.message : "unreachable" }));
    return () => {
      cancelled = true;
    };
  }, [id]);
  return state;
}

export interface HealthcareLive extends WorldLive {
  disclosures?: string[];
  blocked?: string[];
}

/** Healthcare additionally pulls the real disclosures + blocked capabilities. */
export function useHealthcareLive(): HealthcareLive {
  const [state, setState] = useState<HealthcareLive>({ status: "loading" });
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [world, limits] = await Promise.all([
          withTimeout(aios.getWorld("healthcare")),
          withTimeout(aios.healthcareLimitations()),
        ]);
        if (!cancelled)
          setState({
            status: "connected",
            world,
            disclosures: Object.values(limits.disclosures),
            blocked: limits.blocked,
          });
      } catch (e) {
        if (!cancelled) setState({ status: "offline", error: e instanceof Error ? e.message : "unreachable" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  return state;
}
