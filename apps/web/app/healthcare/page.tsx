"use client";

import { useMemo, useState } from "react";
import { DisclosurePanel } from "./_components/DisclosurePanel";
import {
  ROLES,
  CAPABILITIES,
  PERSISTENT_BANNER,
  PRIVACY_BADGE,
  DEMO_DOC,
  DEMO_TIMELINE,
  DEMO_TERMS,
  buildTimeline,
  appointmentQuestions,
  medicationQuestions,
  redactPHIFlags,
  maskPHI,
  isBlockedCapability,
  detectEmergencyLanguage,
} from "../../lib/healthcare_world.mjs";

type Role = (typeof ROLES)[number];
type CapId = (typeof CAPABILITIES)[number]["id"];

const ROLE_LABEL: Record<string, string> = {
  patient: "Patient",
  caregiver: "Caregiver",
  clinician: "Clinician",
  medical_student: "Medical student",
  researcher: "Researcher",
  administrator: "Administrator",
};

export default function HealthcareWorld() {
  const [role, setRole] = useState<Role>("patient");
  const [capability, setCapability] = useState<CapId>("summarize_document");
  const [showMasked, setShowMasked] = useState(true);

  // Demo document rendered from a clearly-labeled fixture (no live upload).
  const phiFlags = useMemo(() => redactPHIFlags(DEMO_DOC.rawText), []);
  const docText = useMemo(
    () => (showMasked ? maskPHI(DEMO_DOC.rawText, phiFlags) : DEMO_DOC.rawText),
    [showMasked, phiFlags]
  );
  const timeline = useMemo(() => buildTimeline(DEMO_TIMELINE), []);
  const apptQs = useMemo(() => appointmentQuestions(DEMO_DOC), []);
  const medQs = useMemo(
    () => medicationQuestions(DEMO_DOC.medications),
    []
  );

  // Base output object each view attaches its disclosures to.
  const baseOutput = {
    country: DEMO_DOC.country,
    sourceDate: DEMO_DOC.sourceDate,
    sources: DEMO_DOC.sources,
    applicableCountry: DEMO_DOC.country,
  };

  const emergencyFlag = detectEmergencyLanguage(DEMO_DOC.rawText);

  return (
    <div className="min-h-screen bg-bg text-ink dark:bg-neutral-950 dark:text-neutral-100">
      <main className="mx-auto max-w-5xl px-6 py-10">
        <Header />

        <PersistentBanner />

        <PrivacyBadge />

        {/* Role + capability selectors */}
        <div className="mt-6 grid gap-4 sm:grid-cols-2">
          <Selector
            label="Your role"
            value={role}
            onChange={(v) => setRole(v as Role)}
            options={ROLES.map((r) => ({ value: r, label: ROLE_LABEL[r] ?? r }))}
          />
          <Selector
            label="What would you like help with?"
            value={capability}
            onChange={(v) => setCapability(v as CapId)}
            options={CAPABILITIES.map((c) => ({ value: c.id, label: c.label }))}
          />
        </div>

        {isBlockedCapability(capability) && (
          <BlockedNotice capability={capability} />
        )}

        {emergencyFlag && (
          <div
            role="alert"
            className="mt-6 rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-200"
          >
            This demo document mentions urgent-sounding language. If you are
            experiencing an emergency, contact your local emergency services now
            — this tool cannot help in an emergency.
          </div>
        )}

        {/* Capability views */}
        <div className="mt-8 space-y-8">
          {capability === "summarize_document" && (
            <Panel title="Summarize an uploaded document" demo>
              <div className="mb-3 flex flex-wrap items-center gap-3">
                <span className="rounded-full bg-accent/10 px-2.5 py-0.5 text-xs font-medium text-accent-deep dark:bg-accent/20 dark:text-accent-soft">
                  {DEMO_DOC.label}
                </span>
                <label className="flex items-center gap-2 text-sm text-dim dark:text-neutral-400">
                  <input
                    type="checkbox"
                    checked={showMasked}
                    onChange={(e) => setShowMasked(e.target.checked)}
                    className="accent-[color:var(--tw-prose-links)]"
                  />
                  Mask likely identifiers ({phiFlags.length} flagged)
                </label>
              </div>
              <pre className="overflow-x-auto whitespace-pre-wrap rounded-md border border-border bg-white p-3 font-mono text-[13px] leading-relaxed dark:border-neutral-800 dark:bg-neutral-900">
                {docText}
              </pre>
              <p className="mt-3 text-sm text-dim dark:text-neutral-400">
                Plain-language read: this appears to be an after-visit summary
                describing well-controlled conditions and a plan to continue an
                existing medication and recheck labs. This is a neutral
                organizational summary — not a diagnosis.
              </p>
              <DisclosurePanel output={baseOutput} />
            </Panel>
          )}

          {capability === "explain_terms" && (
            <Panel title="Explain medical terminology" demo>
              <ul className="divide-y divide-border dark:divide-neutral-800">
                {DEMO_TERMS.map((t) => (
                  <li key={t.term} className="py-3">
                    <p className="font-semibold">{t.term}</p>
                    <p className="text-sm text-dim dark:text-neutral-400">{t.plain}</p>
                    <p className="mt-1 text-xs text-dim dark:text-neutral-500">
                      Source: {t.source} · {t.sourceDate} · {t.country}
                    </p>
                  </li>
                ))}
              </ul>
              <DisclosurePanel
                output={{
                  ...baseOutput,
                  sources: DEMO_TERMS.map((t) => `${t.source} (${t.term})`),
                  sourceDate: DEMO_TERMS[0]?.sourceDate,
                }}
              />
            </Panel>
          )}

          {capability === "organize_timeline" && (
            <Panel title="Organize a health timeline" demo>
              <ol className="relative space-y-3 border-l border-border pl-5 dark:border-neutral-800">
                {timeline.map((e, i) => (
                  <li key={i} className="relative">
                    <span className="absolute -left-[23px] top-1.5 h-2.5 w-2.5 rounded-full bg-accent" />
                    <p className="text-sm font-medium">
                      {Number.isNaN(Date.parse(e.date)) ? "Date unknown" : e.date}
                    </p>
                    <p className="text-sm text-dim dark:text-neutral-400">
                      {e.label}{" "}
                      <span className="text-xs uppercase tracking-wide text-dim dark:text-neutral-500">
                        · {e.kind}
                      </span>
                    </p>
                  </li>
                ))}
              </ol>
              <DisclosurePanel output={baseOutput} />
            </Panel>
          )}

          {capability === "appointment_questions" && (
            <Panel title="Prepare appointment questions" demo>
              <ul className="list-disc space-y-1.5 pl-5 text-sm">
                {apptQs.map((q, i) => (
                  <li key={i}>{q}</li>
                ))}
              </ul>
              <DisclosurePanel output={baseOutput} />
            </Panel>
          )}

          {capability === "medication_questions" && (
            <Panel title="Prepare a medication question list" demo>
              <ul className="list-disc space-y-1.5 pl-5 text-sm">
                {medQs.map((q, i) => (
                  <li key={i}>{q}</li>
                ))}
              </ul>
              <p className="mt-3 text-sm text-dim dark:text-neutral-400">
                These are questions to ask a clinician or pharmacist. This tool
                never suggests starting, stopping, or changing a dose.
              </p>
              <DisclosurePanel output={baseOutput} />
            </Panel>
          )}

          {capability === "structured_summary" && (
            <Panel title="Structured summary for human review" demo>
              <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
                <SummaryRow label="Conditions noted">
                  {DEMO_DOC.conditions.join(", ")}
                </SummaryRow>
                <SummaryRow label="Medications noted">
                  {DEMO_DOC.medications.map((m) => m.name).join(", ")}
                </SummaryRow>
                <SummaryRow label="Unclear terms">
                  {DEMO_DOC.unclearTerms.join(", ")}
                </SummaryRow>
                <SummaryRow label="Follow-ups">
                  {DEMO_DOC.followUps.join(", ")}
                </SummaryRow>
              </dl>
              <p className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
                This structured summary is prepared for a human to review. It is
                not a clinical conclusion and must be verified against the source
                document by a qualified person.
              </p>
              <DisclosurePanel output={baseOutput} />
            </Panel>
          )}
        </div>

        <LiveDataNote />
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Presentational pieces
// ---------------------------------------------------------------------------

function Header() {
  return (
    <header className="mb-6">
      <p className="text-sm font-medium text-dim dark:text-neutral-500">
        Ronin AI OS · Healthcare Information World
      </p>
      <h1 className="mt-1 text-3xl font-bold tracking-tight">
        Healthcare information workspace
      </h1>
      <p className="mt-2 max-w-2xl text-dim dark:text-neutral-400">
        An educational and organizational workspace. It helps you understand and
        organize health information — it does not provide medical care.
      </p>
    </header>
  );
}

function PersistentBanner() {
  return (
    <div
      role="note"
      className="rounded-lg border border-accent/30 bg-accent/5 px-4 py-3 text-sm text-accent-deep dark:border-accent/30 dark:bg-accent/10 dark:text-accent-soft"
    >
      <span className="font-semibold">Important:</span> {PERSISTENT_BANNER}
    </div>
  );
}

function PrivacyBadge() {
  return (
    <div className="mt-3 flex items-center gap-2 text-xs text-dim dark:text-neutral-400">
      <span
        aria-hidden
        className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300"
      >
        ✓
      </span>
      <span>{PRIVACY_BADGE.label}</span>
    </div>
  );
}

function BlockedNotice({ capability }: { capability: string }) {
  return (
    <div
      role="alert"
      className="mt-6 rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-200"
    >
      The action &ldquo;{capability}&rdquo; is not available in this world. This
      workspace cannot diagnose, prescribe, change dosages, or dispatch
      emergency services.
    </div>
  );
}

function Selector({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-dim dark:text-neutral-400">
        {label}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-md border border-border bg-white px-3 py-2 text-sm text-ink focus:outline-none focus:ring-2 focus:ring-accent dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-100"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function Panel({
  title,
  demo,
  children,
}: {
  title: string;
  demo?: boolean;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-border bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">{title}</h2>
        {demo && (
          <span className="shrink-0 rounded-full border border-border px-2 py-0.5 text-xs text-dim dark:border-neutral-700 dark:text-neutral-400">
            Demo fixture
          </span>
        )}
      </div>
      {children}
    </section>
  );
}

function SummaryRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] font-semibold uppercase tracking-wide text-dim dark:text-neutral-500">
        {label}
      </dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}

function LiveDataNote() {
  // Honest note: this page renders demo fixtures. Real data would come from the
  // Ronin API (/api/v1) at runtime.
  return (
    <p className="mt-10 border-t border-border pt-4 text-xs text-dim dark:border-neutral-800 dark:text-neutral-500">
      Demo mode: everything above is rendered from clearly-labeled synthetic
      fixtures. In a live deployment, documents and records load from the Ronin
      API (<code>/api/v1</code>) at runtime — this page never fabricates
      external actions or real patient data.
    </p>
  );
}
