// Pure, dependency-free logic for the Healthcare Information World.
//
// This module is EDUCATIONAL / ORGANIZATIONAL only. It never diagnoses,
// prescribes, changes dosages, or dispatches emergency services. It is kept in
// plain ESM so it runs under `node --test` with zero install, and is imported
// by the React page (app/healthcare/page.tsx). No DOM, no fetch here.
//
// Design principle: FAIL CLOSED. Every function that produces an output view
// guarantees the safety-critical disclosures are present even when the caller
// forgets them.

// ---------------------------------------------------------------------------
// Static copy / constants
// ---------------------------------------------------------------------------

/** Roles this world recognizes (demo). */
export const ROLES = [
  "patient",
  "caregiver",
  "clinician",
  "medical_student",
  "researcher",
  "administrator",
];

/** Human-facing capabilities this world offers (demo, all informational). */
export const CAPABILITIES = [
  { id: "explain_terms", label: "Explain medical terminology" },
  { id: "summarize_document", label: "Summarize an uploaded (demo) document" },
  { id: "organize_timeline", label: "Organize a health timeline" },
  { id: "appointment_questions", label: "Prepare appointment questions" },
  { id: "medication_questions", label: "Prepare a medication question list" },
  { id: "structured_summary", label: "Structured summary for human review" },
];

/** The persistent banner text that must always be visible in the UI. */
export const PERSISTENT_BANNER =
  "This is educational health information only. It does not diagnose, " +
  "prescribe, change dosages, or replace a qualified clinician. Always " +
  "confirm decisions with a licensed professional.";

/** Privacy badge: healthcare data is isolated and not training-eligible. */
export const PRIVACY_BADGE = {
  isolatedFromGeneralMemory: true,
  trainingEligibleByDefault: false,
  label:
    "Health data is isolated from general memory and is not training-eligible by default.",
};

/** Capabilities that are HARD-BLOCKED in this world under any role. */
export const BLOCKED_CAPABILITIES = [
  "diagnosis",
  "diagnose",
  "prescription",
  "prescribe",
  "dosage_change",
  "dose_change",
  "change_dosage",
  "emergency_dispatch",
  "dispatch_emergency",
  "autonomous_treatment",
  "triage_to_treatment",
];

// ---------------------------------------------------------------------------
// Emergency guidance
// ---------------------------------------------------------------------------

const EMERGENCY_NUMBERS = {
  US: "911",
  CA: "911",
  UK: "999",
  GB: "999",
  IE: "112",
  EU: "112",
  IN: "112",
  AU: "000",
  NZ: "111",
};

/**
 * Emergency-guidance boundary line for a given country code. Fail-safe: unknown
 * or missing country still returns a usable, non-empty message.
 */
export function emergencyBanner(country) {
  const code = String(country || "").trim().toUpperCase();
  const number = EMERGENCY_NUMBERS[code];
  const who = number
    ? `emergency services (${number})`
    : "your local emergency services";
  return `If this is a medical emergency, do not wait — contact ${who} now.`;
}

/** Red-flag phrases that should surface the emergency boundary prominently. */
const EMERGENCY_PHRASES = [
  "chest pain",
  "can't breathe",
  "cannot breathe",
  "difficulty breathing",
  "trouble breathing",
  "shortness of breath",
  "suicidal",
  "suicide",
  "overdose",
  "unconscious",
  "not breathing",
  "severe bleeding",
  "stroke",
  "seizure",
  "anaphylaxis",
  "allergic reaction",
];

/**
 * Heuristic: does free text mention a possible emergency? Used only to
 * emphasize the emergency boundary in the UI — never to act.
 */
export function detectEmergencyLanguage(text) {
  const t = String(text || "").toLowerCase();
  return EMERGENCY_PHRASES.some((p) => t.includes(p));
}

// ---------------------------------------------------------------------------
// Capability gating
// ---------------------------------------------------------------------------

/**
 * True if an action is a blocked capability (diagnosis, prescription, dosage
 * change, emergency dispatch, etc.). Normalizes spacing/casing so both
 * "change dosage" and "dosage_change" are caught. Fail-closed: an empty or
 * malformed action is NOT treated as safe-to-run by this function alone, but
 * this function specifically answers "is it in the blocked set?" so empty
 * returns false — callers should also require an explicit allowed capability.
 */
export function isBlockedCapability(action) {
  const raw = String(action || "").toLowerCase().trim();
  if (!raw) return false;
  const norm = raw.replace(/[\s-]+/g, "_");
  const collapsed = norm.replace(/_/g, "");
  return BLOCKED_CAPABILITIES.some((b) => {
    const bNorm = b.replace(/[\s-]+/g, "_");
    const bCollapsed = bNorm.replace(/_/g, "");
    return (
      norm === bNorm ||
      norm.includes(bNorm) ||
      collapsed.includes(bCollapsed)
    );
  });
}

/**
 * Training eligibility for a healthcare item. ALWAYS false by default — health
 * data is never training-eligible in this world unless a future explicit,
 * audited opt-in flag is set (not exposed in demo).
 */
export function trainingEligible(item) {
  return false;
}

// ---------------------------------------------------------------------------
// PHI redaction flags
// ---------------------------------------------------------------------------

/**
 * Flag likely PHI / identifiers in free text so the UI can mask them.
 * Returns an array of { type, value, index, length }. Heuristic only — this is
 * a safety aid, not a guarantee, and the UI labels it as such.
 */
export function redactPHIFlags(text) {
  const src = String(text || "");
  const flags = [];
  const push = (type, m) => {
    flags.push({ type, value: m[0], index: m.index, length: m[0].length });
  };

  // Email addresses.
  for (const m of src.matchAll(/[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/gi)) {
    push("email", m);
  }
  // MRN-shaped: "MRN" label followed by an alphanumeric id.
  for (const m of src.matchAll(/\bMRN[:\s#-]*([A-Z0-9-]{4,})/gi)) {
    push("mrn", m);
  }
  // Phone numbers (US-ish and international-ish).
  for (const m of src.matchAll(
    /(?<!\d)(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)/g
  )) {
    push("phone", m);
  }
  // SSN-shaped.
  for (const m of src.matchAll(/(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)/g)) {
    push("ssn", m);
  }
  // Dates: 2024-01-05, 01/05/2024, Jan 5 2024, 5 January 2024.
  const dateRe = new RegExp(
    "\\b(?:\\d{4}-\\d{2}-\\d{2}" +
      "|\\d{1,2}[\\/.]\\d{1,2}[\\/.]\\d{2,4}" +
      "|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\\.?\\s+\\d{1,2}(?:,?\\s+\\d{4})?" +
      "|\\d{1,2}\\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\\.?(?:,?\\s+\\d{4})?)\\b",
    "gi"
  );
  for (const m of src.matchAll(dateRe)) push("date", m);
  // Names: honorific + capitalized name, or "Name:" labeled field.
  for (const m of src.matchAll(
    /\b(?:Mr|Mrs|Ms|Dr|Miss|Prof)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*/g
  )) {
    push("name", m);
  }
  for (const m of src.matchAll(
    /\b(?:Patient|Name|Provider|Physician)\s*[:]\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)/g
  )) {
    push("name", m);
  }

  return flags.sort((a, b) => a.index - b.index);
}

/** Apply PHI flags to produce a masked string (for display previews). */
export function maskPHI(text, flags) {
  const src = String(text || "");
  const list = (flags || redactPHIFlags(src))
    .slice()
    .sort((a, b) => b.index - a.index);
  let out = src;
  for (const f of list) {
    if (typeof f.index !== "number" || typeof f.length !== "number") continue;
    out = out.slice(0, f.index) + "[REDACTED]" + out.slice(f.index + f.length);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------------

/**
 * Build a health timeline sorted chronologically (ascending). Events with
 * invalid/missing dates are pushed to the end (never dropped) so nothing is
 * silently lost. Returns a new array; never mutates input.
 */
export function buildTimeline(events) {
  const list = Array.isArray(events) ? events.slice() : [];
  const withKey = list.map((e, i) => {
    const t = Date.parse(e && e.date);
    return { e, i, t: Number.isNaN(t) ? null : t };
  });
  withKey.sort((a, b) => {
    if (a.t === null && b.t === null) return a.i - b.i;
    if (a.t === null) return 1;
    if (b.t === null) return -1;
    if (a.t !== b.t) return a.t - b.t;
    return a.i - b.i; // stable within same date
  });
  return withKey.map((w) => w.e);
}

// ---------------------------------------------------------------------------
// Question builders
// ---------------------------------------------------------------------------

/**
 * Prepare appointment questions from a (demo) document summary. Returns a de-
 * duplicated list of neutral, non-directive questions for the patient to ask a
 * clinician. Never proposes a diagnosis or treatment.
 */
export function appointmentQuestions(docSummary) {
  const s = docSummary || {};
  const questions = [];
  const add = (q) => {
    if (q && !questions.includes(q)) questions.push(q);
  };

  for (const dx of s.conditions || s.diagnoses || []) {
    add(`What does "${dx}" mean for my day-to-day health?`);
    add(`What are the next steps or monitoring for ${dx}?`);
  }
  for (const med of s.medications || []) {
    const name = typeof med === "string" ? med : med && med.name;
    if (name) add(`Why am I taking ${name}, and how long should I continue?`);
  }
  for (const term of s.unclearTerms || s.terms || []) {
    add(`Can you explain what "${term}" means in plain language?`);
  }
  for (const f of s.followUps || s.followups || []) {
    add(`You mentioned ${f} — when and how should that happen?`);
  }
  // Always-useful baseline questions.
  add("Which symptoms should prompt me to seek care sooner?");
  add("Is there anything in these results I should be most aware of?");
  add("What should I bring or prepare for the next visit?");

  return questions;
}

/**
 * Prepare a medication question list. Accepts an array of medication names or
 * objects. Neutral, non-directive; never suggests dose changes.
 */
export function medicationQuestions(medications) {
  const list = Array.isArray(medications) ? medications : [];
  const out = [];
  const add = (q) => {
    if (q && !out.includes(q)) out.push(q);
  };
  for (const med of list) {
    const name = typeof med === "string" ? med : med && med.name;
    if (!name) continue;
    add(`What is ${name} for?`);
    add(`How and when should ${name} be taken?`);
    add(`What side effects of ${name} should be reported?`);
    add(`Does ${name} interact with my other medications or foods?`);
  }
  add("Who should I contact if I have questions about a medication?");
  return out;
}

// ---------------------------------------------------------------------------
// Output disclosures (FAIL CLOSED)
// ---------------------------------------------------------------------------

/**
 * The disclosure set that MUST accompany every output view. Given a partial
 * output object, returns a fully-populated disclosure record. This function is
 * fail-closed: it can NEVER return a view missing the informational-purpose
 * banner, the emergency-guidance boundary, or a sources field. Missing pieces
 * are replaced with explicit safe defaults rather than being omitted.
 */
export function outputDisclosures(output) {
  const o = output || {};
  const country = o.applicableCountry || o.country || null;

  const sources = Array.isArray(o.sources) ? o.sources.filter(Boolean) : [];

  const disclosures = {
    // Required, non-negotiable trio:
    informational: PERSISTENT_BANNER,
    emergency: emergencyBanner(country),
    sources:
      sources.length > 0
        ? sources
        : ["No sources were attached to this output. Treat as unverified."],

    // Additional required context fields (safe defaults when absent):
    uncertainty:
      o.uncertainty ||
      "Confidence is not established. This may be incomplete or wrong; verify with a clinician.",
    missingInformation: Array.isArray(o.missingInformation)
      ? o.missingInformation
      : o.missingInformation
        ? [o.missingInformation]
        : ["Unknown — key clinical context may be missing."],
    sourceDate: o.sourceDate || "Unknown source date",
    applicableCountry: country || "Not specified",
    professionalReview:
      o.professionalReview ||
      "Have a licensed clinician review this before acting on it.",
    hasSources: sources.length > 0,
  };

  return disclosures;
}

/**
 * Wrap a raw output body with its guaranteed disclosures. The returned view is
 * always safe to render: it can never lack informational + emergency + sources.
 */
export function safeOutputView(output) {
  const o = output || {};
  return {
    kind: o.kind || "informational_summary",
    title: o.title || "Informational summary",
    body: o.body ?? "",
    disclosures: outputDisclosures(o),
  };
}

// ---------------------------------------------------------------------------
// Demo fixtures (clearly labeled, synthetic)
// ---------------------------------------------------------------------------

/** A synthetic DEMO medical document — NOT a real patient record. */
export const DEMO_DOC = {
  demo: true,
  label: "DEMO document — synthetic, not a real patient",
  title: "After-visit summary (DEMO)",
  country: "US",
  sourceDate: "2026-03-14",
  rawText:
    "Patient: Alex Demo\n" +
    "MRN: DEMO-4471\n" +
    "Visit date: 2026-03-14\n" +
    "Contact: alex.demo@example.com, (555) 123-4567\n" +
    "Assessment: Type 2 diabetes, well controlled. Mild hypertension noted.\n" +
    "Plan: Continue metformin. Recheck HbA1c in 3 months. Lifestyle counseling.\n" +
    "Note: Values are illustrative only.",
  conditions: ["Type 2 diabetes", "Mild hypertension"],
  medications: [{ name: "Metformin", note: "as previously prescribed" }],
  unclearTerms: ["HbA1c"],
  followUps: ["recheck HbA1c in 3 months"],
  sources: ["DEMO after-visit summary (synthetic fixture)"],
};

/** A synthetic DEMO health timeline (intentionally out of order). */
export const DEMO_TIMELINE = [
  { date: "2026-03-14", label: "After-visit summary", kind: "note" },
  { date: "2025-11-02", label: "Annual physical", kind: "visit" },
  { date: "2026-01-20", label: "Lab: HbA1c 6.8%", kind: "lab" },
  { date: "2025-06-15", label: "Started metformin (DEMO)", kind: "medication" },
  { date: "unknown", label: "Family history noted", kind: "note" },
];

/** A synthetic DEMO glossary of terms for the "explain terminology" feature. */
export const DEMO_TERMS = [
  {
    term: "HbA1c",
    plain:
      "A blood test that estimates average blood sugar over the past ~3 months.",
    source: "General patient-education glossary (DEMO)",
    sourceDate: "2025-09-01",
    country: "US",
  },
  {
    term: "Hypertension",
    plain: "The medical word for high blood pressure.",
    source: "General patient-education glossary (DEMO)",
    sourceDate: "2025-09-01",
    country: "US",
  },
  {
    term: "Metformin",
    plain:
      "A commonly used medicine that helps lower blood sugar. Ask your clinician about your specific use.",
    source: "General patient-education glossary (DEMO)",
    sourceDate: "2025-09-01",
    country: "US",
  },
];
