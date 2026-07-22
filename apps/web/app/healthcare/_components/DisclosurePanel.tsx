"use client";

import { outputDisclosures } from "../../../lib/healthcare_world.mjs";

type RawOutput = Parameters<typeof outputDisclosures>[0];

/**
 * Renders the safety-critical disclosure set that MUST accompany every output
 * view: informational purpose, sources, uncertainty, missing info, source
 * date, applicable country, when professional review is appropriate, and the
 * emergency-guidance boundary. Fail-closed via outputDisclosures().
 */
export function DisclosurePanel({ output }: { output: RawOutput }) {
  const d = outputDisclosures(output);

  return (
    <section
      aria-label="Required disclosures for this output"
      className="mt-4 rounded-lg border border-border bg-bg/60 p-4 text-sm dark:border-neutral-800 dark:bg-neutral-900/60"
    >
      <p className="mb-3 rounded-md bg-accent/10 px-3 py-2 text-[13px] font-medium text-accent-deep dark:bg-accent/20 dark:text-accent-soft">
        Informational purpose only — {d.informational}
      </p>

      <div
        role="alert"
        className="mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-[13px] font-medium text-red-800 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-200"
      >
        {d.emergency}
      </div>

      <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
        <Field label="Sources">
          {d.hasSources ? (
            <ul className="list-disc pl-4">
              {d.sources.map((s: string, i: number) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
          ) : (
            <span className="text-amber-700 dark:text-amber-400">{d.sources[0]}</span>
          )}
        </Field>
        <Field label="Source date">{d.sourceDate}</Field>
        <Field label="Applicable country">{d.applicableCountry}</Field>
        <Field label="Uncertainty">{d.uncertainty}</Field>
        <Field label="Missing information">
          <ul className="list-disc pl-4">
            {d.missingInformation.map((m: string, i: number) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        </Field>
        <Field label="Professional review">{d.professionalReview}</Field>
      </dl>
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] font-semibold uppercase tracking-wide text-dim dark:text-neutral-500">
        {label}
      </dt>
      <dd className="mt-0.5 text-ink dark:text-neutral-200">{children}</dd>
    </div>
  );
}
