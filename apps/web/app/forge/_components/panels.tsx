"use client";

import {
  jobStateBadge,
  canRelease,
  releaseBlockReason,
  bundleIsTrained,
  datasetEligibility,
  splitCounts,
  contaminationStatus,
  evalDelta,
  DEMO_DATASET,
  DEMO_JOBS,
  DEMO_ADAPTERS,
  DEMO_EVALS,
} from "../../../lib/forge.mjs";

// ---------------------------------------------------------------------------
// Shared tone styling (light + dark), used by every badge in the studio.
// ---------------------------------------------------------------------------
const TONE_CLASS: Record<string, string> = {
  danger:
    "bg-red-50 text-red-700 border-red-200 dark:bg-red-950/40 dark:text-red-300 dark:border-red-900",
  warn: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900",
  ok: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-900",
  info: "bg-sky-50 text-sky-700 border-sky-200 dark:bg-sky-950/40 dark:text-sky-300 dark:border-sky-900",
  neutral:
    "bg-neutral-100 text-neutral-600 border-neutral-200 dark:bg-neutral-800 dark:text-neutral-300 dark:border-neutral-700",
};

export function StateBadge({
  state,
  size = "sm",
}: {
  state: string;
  size?: "sm" | "lg";
}) {
  const badge = jobStateBadge(state);
  const pad = size === "lg" ? "px-3 py-1 text-sm" : "px-2 py-0.5 text-xs";
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border font-medium ${pad} ${
        TONE_CLASS[badge.tone] ?? TONE_CLASS.neutral
      }`}
      title={badge.isTerminal ? "Terminal state" : "In progress"}
    >
      <span
        aria-hidden
        className={`h-1.5 w-1.5 rounded-full ${
          badge.tone === "danger"
            ? "bg-red-500"
            : badge.tone === "warn"
              ? "bg-amber-500"
              : badge.tone === "ok"
                ? "bg-emerald-500"
                : badge.tone === "info"
                  ? "bg-sky-500"
                  : "bg-neutral-400"
        }`}
      />
      {badge.label}
    </span>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="whitespace-nowrap px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-dim dark:text-neutral-400">
      {children}
    </th>
  );
}
function Td({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <td className={`px-3 py-2 align-top ${className}`}>{children}</td>
  );
}

const CARD =
  "rounded-xl border border-border bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900";

function DemoNote({ children }: { children: React.ReactNode }) {
  return (
    <p className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
      {children}
    </p>
  );
}

// ===========================================================================
// DATASETS
// ===========================================================================
export function DatasetsPanel() {
  const ds = DEMO_DATASET;
  const counts = splitCounts(ds.total, ds.splitRatios);
  const contam = contaminationStatus(ds.contaminationPairs);
  const rows = ds.items.map((it) => ({ it, elig: datasetEligibility(it) }));
  const failedRedaction = rows.filter(
    (r) => r.it.redaction && r.it.redaction.status === "failed"
  );

  return (
    <div className="space-y-6">
      <DemoNote>
        Demo fixtures — provenance and eligibility are illustrative, not a real
        data audit.
      </DemoNote>

      {/* Split + contamination summary */}
      <div className="grid gap-4 sm:grid-cols-2">
        <div className={CARD}>
          <h3 className="text-sm font-semibold">
            Train / val / test split
          </h3>
          <p className="mt-1 text-xs text-dim dark:text-neutral-400">
            {ds.total.toLocaleString()} eligible rows ·{" "}
            {(ds.splitRatios.train * 100).toFixed(0)}/
            {(ds.splitRatios.val * 100).toFixed(0)}/
            {(ds.splitRatios.test * 100).toFixed(0)}
          </p>
          <div className="mt-3 flex overflow-hidden rounded-lg border border-border text-center text-xs dark:border-neutral-800">
            <div className="bg-accent/15 px-2 py-2 dark:bg-accent/25" style={{ flex: counts.train }}>
              <div className="font-semibold">{counts.train}</div>
              <div className="text-dim dark:text-neutral-400">train</div>
            </div>
            <div className="border-l border-border bg-accent-soft/20 px-2 py-2 dark:border-neutral-800" style={{ flex: counts.val }}>
              <div className="font-semibold">{counts.val}</div>
              <div className="text-dim dark:text-neutral-400">val</div>
            </div>
            <div className="border-l border-border bg-accent-deep/15 px-2 py-2 dark:border-neutral-800" style={{ flex: counts.test }}>
              <div className="font-semibold">{counts.test}</div>
              <div className="text-dim dark:text-neutral-400">test</div>
            </div>
          </div>
        </div>

        <div className={CARD}>
          <h3 className="text-sm font-semibold">Contamination check</h3>
          <p className="mt-1 text-xs text-dim dark:text-neutral-400">
            Train↔eval overlap scan ({ds.contaminationPairs.length} pairs
            reviewed)
          </p>
          <div className="mt-3 flex items-center gap-2">
            <span
              className={`inline-flex items-center rounded-full border px-3 py-1 text-sm font-medium ${
                TONE_CLASS[contam.tone]
              }`}
            >
              {contam.status === "clean"
                ? "No contamination detected"
                : contam.status === "contaminated"
                  ? `Contaminated — ${contam.overlaps} overlap(s)`
                  : "Unknown"}
            </span>
          </div>
          <p className="mt-3 text-xs text-dim dark:text-neutral-400">
            Rows flagged as benchmark leaks are excluded from the eligible set
            before this split is computed.
          </p>
        </div>
      </div>

      {/* Provenance table */}
      <div className={CARD}>
        <h3 className="mb-3 text-sm font-semibold">Dataset provenance</h3>
        <div className="overflow-x-auto">
          <table className="min-w-full border-separate border-spacing-0 text-sm">
            <thead>
              <tr className="border-b border-border dark:border-neutral-800">
                <Th>Row</Th>
                <Th>Source</Th>
                <Th>License</Th>
                <Th>Consent</Th>
                <Th>Redaction</Th>
                <Th>Quality</Th>
                <Th>Training-eligible</Th>
                <Th>Exclusion reason</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border dark:divide-neutral-800">
              {rows.map(({ it, elig }) => (
                <tr
                  key={it.id}
                  className={
                    elig.eligible
                      ? ""
                      : "bg-red-50/40 dark:bg-red-950/20"
                  }
                >
                  <Td className="font-mono text-xs">{it.id}</Td>
                  <Td>{it.source}</Td>
                  <Td>
                    <span
                      className={
                        !it.license || it.license === "unknown"
                          ? "text-red-600 dark:text-red-400"
                          : ""
                      }
                    >
                      {it.license || "—"}
                    </span>
                  </Td>
                  <Td>
                    {it.consent === true || it.consent === "obtained" ? (
                      <span className="text-emerald-600 dark:text-emerald-400">yes</span>
                    ) : (
                      <span className="text-red-600 dark:text-red-400">no</span>
                    )}
                  </Td>
                  <Td>
                    <span
                      className={
                        it.redaction?.status === "failed"
                          ? "text-red-600 dark:text-red-400"
                          : it.redaction?.status === "passed"
                            ? "text-emerald-600 dark:text-emerald-400"
                            : "text-dim dark:text-neutral-400"
                      }
                    >
                      {it.redaction?.status ?? "—"}
                    </span>
                  </Td>
                  <Td className="font-mono text-xs">
                    {typeof it.qualityScore === "number"
                      ? it.qualityScore.toFixed(2)
                      : "—"}
                  </Td>
                  <Td>
                    {elig.eligible ? (
                      <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300">
                        eligible
                      </span>
                    ) : (
                      <span className="rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-xs text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
                        excluded
                      </span>
                    )}
                  </Td>
                  <Td className="text-xs text-dim dark:text-neutral-400">
                    {elig.reason ?? "—"}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Redaction review panel */}
      <div className={CARD}>
        <h3 className="text-sm font-semibold">Redaction review</h3>
        {failedRedaction.length === 0 ? (
          <p className="mt-1 text-xs text-dim dark:text-neutral-400">
            No rows currently need manual redaction review.
          </p>
        ) : (
          <ul className="mt-3 space-y-2">
            {failedRedaction.map((r) => (
              <li
                key={r.it.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm dark:border-red-900 dark:bg-red-950/30"
              >
                <span>
                  <span className="font-mono text-xs">{r.it.id}</span> ·{" "}
                  {r.it.source}
                </span>
                <span className="text-xs text-red-700 dark:text-red-300">
                  PII redaction failed — excluded pending manual review
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// ===========================================================================
// TRAINING JOBS
// ===========================================================================
export function JobsPanel() {
  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-800 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-200">
        <strong>Training runs externally on GPU.</strong> This environment has
        no GPU (<code className="font-mono">BLOCKED_HARDWARE</code>). A{" "}
        <em>generated</em> bundle is a script + config you download and run on a
        GPU host — it is <strong>not</strong> a trained model.
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {DEMO_JOBS.map((job) => {
          const trained = bundleIsTrained(job);
          return (
            <div key={job.id} className={CARD}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="font-mono text-sm font-semibold">{job.id}</h3>
                  <p className="mt-0.5 text-xs text-dim dark:text-neutral-400">
                    → adapter <span className="font-mono">{job.adapterTarget}</span>{" "}
                    · dataset {job.datasetId}@{job.datasetVersion}
                  </p>
                </div>
                <StateBadge state={job.state} size="lg" />
              </div>

              <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
                <Field k="Base model" v={job.config.baseModel} mono />
                <Field k="Method" v={job.config.method} />
                <Field k="Iterations" v={String(job.config.iters)} mono />
                <Field k="Seed" v={String(job.config.seed)} mono />
                <Field
                  k="Hardware"
                  v={`${job.hardware.estGpu} · ~${job.hardware.estHours}h`}
                />
                <Field
                  k="Cost estimate"
                  v={`$${job.costEstimate.low}–$${job.costEstimate.high} ${job.costEstimate.currency}`}
                />
              </dl>

              <p className="mt-3 rounded-lg bg-bg px-3 py-2 text-xs text-dim dark:bg-neutral-800 dark:text-neutral-400">
                {job.hardware.note}
              </p>

              <div className="mt-4 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className="inline-flex items-center rounded-md border border-border bg-white px-3 py-1.5 text-sm font-medium text-ink transition hover:bg-bg focus:outline-none focus-visible:ring-2 focus-visible:ring-accent dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-100 dark:hover:bg-neutral-700"
                  aria-label={`Download demo bundle ${job.id}`}
                >
                  Download bundle (demo)
                </button>
                <span
                  className={`text-xs font-medium ${
                    trained
                      ? "text-emerald-600 dark:text-emerald-400"
                      : "text-dim dark:text-neutral-400"
                  }`}
                >
                  {trained
                    ? "Weights exist (trained externally)"
                    : "No trained weights yet"}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Field({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex flex-col">
      <dt className="text-dim dark:text-neutral-500">{k}</dt>
      <dd className={mono ? "font-mono" : ""}>{v}</dd>
    </div>
  );
}

// ===========================================================================
// ADAPTERS
// ===========================================================================
export function AdaptersPanel() {
  return (
    <div className="space-y-6">
      <DemoNote>
        Demo registry. The lifecycle forbids state-skipping — a{" "}
        <em>generated</em> adapter can never be released directly.
      </DemoNote>

      <div className="rounded-xl border border-border bg-white dark:border-neutral-800 dark:bg-neutral-900">
        <div className="overflow-x-auto">
          <table className="min-w-full border-separate border-spacing-0 text-sm">
            <thead>
              <tr>
                <Th>Adapter ID</Th>
                <Th>Base model</Th>
                <Th>Industry / role</Th>
                <Th>Country / lang</Th>
                <Th>Ver</Th>
                <Th>Dataset</Th>
                <Th>Evals</Th>
                <Th>State</Th>
                <Th>Rollback → </Th>
                <Th>Release</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border dark:divide-neutral-800">
              {DEMO_ADAPTERS.map((a) => {
                const releasable = canRelease(a);
                const reason = releaseBlockReason(a);
                const evalCount = (a.evalResults ?? []).length;
                return (
                  <tr key={a.id}>
                    <Td className="font-mono text-xs">{a.id}</Td>
                    <Td className="font-mono text-xs">{a.baseModel}</Td>
                    <Td>
                      {a.industry}
                      <div className="text-xs text-dim dark:text-neutral-400">
                        {a.role}
                      </div>
                    </Td>
                    <Td className="text-xs">
                      {a.country} · {a.lang}
                    </Td>
                    <Td className="font-mono text-xs">{a.version}</Td>
                    <Td className="font-mono text-xs">{a.datasetVersion}</Td>
                    <Td className="text-xs">
                      {evalCount > 0 ? `${evalCount} suite(s)` : "—"}
                    </Td>
                    <Td>
                      <StateBadge state={a.state} />
                    </Td>
                    <Td className="font-mono text-xs">
                      {a.rollbackTarget ?? "—"}
                    </Td>
                    <Td>
                      {a.releaseStatus === "released" ? (
                        <span className="text-xs font-medium text-emerald-600 dark:text-emerald-400">
                          released
                        </span>
                      ) : releasable ? (
                        <button
                          type="button"
                          className="rounded-md bg-accent px-2.5 py-1 text-xs font-medium text-white transition hover:bg-accent-deep focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                        >
                          Release
                        </button>
                      ) : (
                        <span
                          className="cursor-help text-xs text-dim dark:text-neutral-500"
                          title={reason}
                        >
                          blocked
                        </span>
                      )}
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Explain the gate on any blocked adapter */}
      <div className={CARD}>
        <h3 className="text-sm font-semibold">Release gate</h3>
        <ul className="mt-2 space-y-1.5 text-xs">
          {DEMO_ADAPTERS.map((a) => {
            const reason = releaseBlockReason(a);
            return (
              <li key={a.id} className="flex flex-wrap items-center gap-2">
                <span className="font-mono">{a.id}</span>
                {a.releaseStatus === "released" ? (
                  <span className="text-emerald-600 dark:text-emerald-400">
                    released
                  </span>
                ) : reason === "" ? (
                  <span className="text-emerald-600 dark:text-emerald-400">
                    ready to release (evals + human approver + reached approved)
                  </span>
                ) : (
                  <span className="text-dim dark:text-neutral-400">{reason}</span>
                )}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

// ===========================================================================
// EVALUATION
// ===========================================================================
const SEVERITY_CLASS: Record<string, string> = {
  high: TONE_CLASS.danger,
  medium: TONE_CLASS.warn,
  low: TONE_CLASS.neutral,
};

export function EvalPanel() {
  const ev = DEMO_EVALS;
  type Delta = {
    direction: string;
    curr: number | null;
    prev: number | null;
    delta: number;
  };
  const deltas = evalDelta(ev.current, ev.previous) as Record<string, Delta>;
  const suites = Object.keys(deltas);

  return (
    <div className="space-y-6">
      <DemoNote>
        Demo eval results for <span className="font-mono">{ev.adapterId}</span>{" "}
        vs <span className="font-mono">{ev.previousVersion}</span>.
      </DemoNote>

      <div className={CARD}>
        <h3 className="mb-3 text-sm font-semibold">
          Suite results & comparison vs previous version
        </h3>
        <div className="overflow-x-auto">
          <table className="min-w-full border-separate border-spacing-0 text-sm">
            <thead>
              <tr>
                <Th>Suite</Th>
                <Th>Current</Th>
                <Th>Previous</Th>
                <Th>Pass / fail</Th>
                <Th>Change</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border dark:divide-neutral-800">
              {suites.map((suite) => {
                const d = deltas[suite];
                const cur = ev.current[suite as keyof typeof ev.current];
                const passed = cur?.passed ?? 0;
                const total = cur?.total ?? 0;
                const allPass = total > 0 && passed === total;
                const dirTone =
                  d.direction === "improved"
                    ? "ok"
                    : d.direction === "regressed"
                      ? "danger"
                      : "neutral";
                return (
                  <tr key={suite}>
                    <Td className="font-medium">{suite}</Td>
                    <Td className="font-mono text-xs">
                      {d.curr != null ? (d.curr * 100).toFixed(0) + "%" : "—"}
                    </Td>
                    <Td className="font-mono text-xs text-dim dark:text-neutral-400">
                      {d.prev != null ? (d.prev * 100).toFixed(0) + "%" : "—"}
                    </Td>
                    <Td>
                      <span
                        className={
                          allPass
                            ? "text-emerald-600 dark:text-emerald-400"
                            : "text-amber-600 dark:text-amber-400"
                        }
                      >
                        {passed}/{total} {allPass ? "pass" : "with failures"}
                      </span>
                    </Td>
                    <Td>
                      <span
                        className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${TONE_CLASS[dirTone]}`}
                      >
                        {d.direction === "improved"
                          ? `▲ +${(d.delta * 100).toFixed(0)}%`
                          : d.direction === "regressed"
                            ? `▼ ${(d.delta * 100).toFixed(0)}%`
                            : d.direction}
                      </span>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className={CARD}>
        <h3 className="mb-3 text-sm font-semibold">Failure examples</h3>
        <ul className="space-y-3">
          {ev.failures.map((f, i) => (
            <li
              key={i}
              className="rounded-lg border border-border p-3 dark:border-neutral-800"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-medium text-dim dark:text-neutral-400">
                  {f.suite}
                </span>
                <span
                  className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${
                    SEVERITY_CLASS[f.severity] ?? TONE_CLASS.neutral
                  }`}
                >
                  {f.severity} severity
                </span>
              </div>
              <p className="mt-2 text-sm">
                <span className="text-dim dark:text-neutral-400">Prompt: </span>
                {f.prompt}
              </p>
              <div className="mt-1 grid gap-1 text-xs sm:grid-cols-2">
                <p className="rounded bg-emerald-50 px-2 py-1 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300">
                  Expected: {f.expected}
                </p>
                <p className="rounded bg-red-50 px-2 py-1 text-red-800 dark:bg-red-950/40 dark:text-red-300">
                  Got: {f.got}
                </p>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
