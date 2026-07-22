"use client";

import { useEffect, useMemo, useState } from "react";
import { aios, type World } from "../../lib/api";
import {
  filterWorlds,
  isEnterable,
  modelStrategy,
  riskBadge,
  safetyNote,
  sortWorlds,
} from "../../lib/worlds.mjs";

type LoadState = "loading" | "ready" | "error" | "offline";

// Worlds whose workspace UI exists. Others show "in progress" until built.
const WORLD_ROUTES: Record<string, string> = {
  coding: "/coding",
  education: "/education",
  healthcare: "/healthcare",
};

const TONE_CLASS: Record<string, string> = {
  danger: "bg-red-50 text-red-700 border-red-200",
  warn: "bg-amber-50 text-amber-700 border-amber-200",
  ok: "bg-emerald-50 text-emerald-700 border-emerald-200",
  neutral: "bg-neutral-100 text-neutral-600 border-neutral-200",
};

export default function WorldNavigator() {
  const [worlds, setWorlds] = useState<World[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [query, setQuery] = useState("");
  const [onlyEnabled, setOnlyEnabled] = useState(false);

  useEffect(() => {
    let alive = true;
    aios
      .listWorlds(true)
      .then((res) => {
        if (!alive) return;
        setWorlds(res.worlds);
        setState("ready");
      })
      .catch(() => {
        if (!alive) return;
        // Honest offline/error state — never fabricate worlds.
        setState("offline");
      });
    return () => {
      alive = false;
    };
  }, []);

  const shown = useMemo(
    () => sortWorlds(filterWorlds(worlds, { query, onlyEnabled })),
    [worlds, query, onlyEnabled]
  );

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <header className="mb-8">
        <p className="text-sm font-medium text-neutral-500">Ronin AI OS</p>
        <h1 className="text-3xl font-bold tracking-tight">Choose a world</h1>
        <p className="mt-2 max-w-2xl text-neutral-600">
          Each world is a specialized workspace — its own tools, safety rules,
          memory boundaries, and evaluations — not a renamed chatbot. Enabled
          worlds are ready to enter; others are in preparation.
        </p>
      </header>

      <div className="mb-6 flex flex-wrap items-center gap-3">
        <input
          className="input flex-1 min-w-[220px] rounded-lg border border-neutral-200 px-3 py-2"
          placeholder="Search worlds, roles, capabilities…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search worlds"
        />
        <label className="flex items-center gap-2 text-sm text-neutral-600">
          <input
            type="checkbox"
            checked={onlyEnabled}
            onChange={(e) => setOnlyEnabled(e.target.checked)}
          />
          Ready to enter only
        </label>
      </div>

      {state === "loading" && (
        <p className="py-16 text-center text-neutral-400">Loading worlds…</p>
      )}

      {state === "offline" && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-6 text-amber-800">
          <p className="font-medium">Can’t reach the Ronin API.</p>
          <p className="mt-1 text-sm">
            Start it with <code>make dev</code> (or run the API on{" "}
            <code>NEXT_PUBLIC_API_URL</code>). No world data is shown until the
            API responds — this page never invents worlds.
          </p>
        </div>
      )}

      {state === "ready" && shown.length === 0 && (
        <p className="py-16 text-center text-neutral-400">
          No worlds match “{query}”.
        </p>
      )}

      {state === "ready" && shown.length > 0 && (
        <ul className="grid gap-4 sm:grid-cols-2">
          {shown.map((w) => {
            const badge = riskBadge(w.risk_level);
            const enterable = isEnterable(w);
            const note = safetyNote(w);
            return (
              <li
                key={w.id}
                className={`rounded-xl border p-5 ${
                  enterable ? "border-neutral-200 bg-white" : "border-neutral-200 bg-neutral-50 opacity-80"
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <h2 className="text-lg font-semibold">{w.name}</h2>
                  <span
                    className={`shrink-0 rounded-full border px-2 py-0.5 text-xs ${
                      TONE_CLASS[badge.tone] ?? TONE_CLASS.neutral
                    }`}
                  >
                    {badge.label}
                  </span>
                </div>
                <p className="mt-1 text-sm text-neutral-600">{w.description}</p>

                <dl className="mt-3 space-y-1 text-xs text-neutral-500">
                  <div>
                    <span className="font-medium text-neutral-700">Roles:</span>{" "}
                    {w.roles.join(", ")}
                  </div>
                  <div>
                    <span className="font-medium text-neutral-700">Available in:</span>{" "}
                    {w.countries.join(", ")} · {w.languages.join(", ")}
                  </div>
                  <div>
                    <span className="font-medium text-neutral-700">Model:</span>{" "}
                    {modelStrategy(w)}
                  </div>
                  {w.allowed_tools.length > 0 && (
                    <div>
                      <span className="font-medium text-neutral-700">Tools:</span>{" "}
                      {w.allowed_tools.length}
                    </div>
                  )}
                </dl>

                {note && (
                  <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
                    {note}
                  </p>
                )}

                <div className="mt-4">
                  {enterable && WORLD_ROUTES[w.id] ? (
                    <a
                      href={WORLD_ROUTES[w.id]}
                      className="btn inline-block rounded-lg bg-neutral-900 px-4 py-2 text-sm font-medium text-white"
                    >
                      Enter {w.name}
                    </a>
                  ) : enterable ? (
                    <span className="text-xs font-medium text-neutral-400">
                      Workspace UI in progress
                    </span>
                  ) : (
                    <span className="text-xs font-medium text-neutral-400">
                      In preparation — policies, datasets, and evaluations pending
                    </span>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </main>
  );
}
