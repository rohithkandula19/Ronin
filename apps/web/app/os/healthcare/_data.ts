/**
 * Demo content for the Healthcare Information world. This world's entire point
 * is its safety posture, so the data encodes it explicitly. Offline sample:
 * the summary is illustrative, sourced, and non-prescriptive; nothing here is
 * medical advice, a diagnosis, or a treatment recommendation.
 */

export const DISCLOSURE =
  "Information only — not medical advice. Ronin does not diagnose, prescribe, or replace a clinician. In an emergency, call your local emergency number.";

/** Capabilities this world refuses, shown up front (fail-closed). */
export const BLOCKED_CAPABILITIES: { label: string; why: string }[] = [
  { label: "Diagnose a condition", why: "Only a licensed clinician who can examine you may diagnose." },
  { label: "Prescribe or set a dose", why: "Prescribing requires a clinician and your full medical context." },
  { label: "Interpret your results as a verdict", why: "Ronin explains what a value means in general — not whether you are ill." },
  { label: "Tell you to start or stop treatment", why: "Changing treatment is a decision for you and your clinician." },
];

/** Red-flag terms that flip the world into emergency-guidance mode (demo). */
export const EMERGENCY_TERMS = [
  "chest pain", "can't breathe", "cant breathe", "trouble breathing", "stroke",
  "suicide", "kill myself", "overdose", "severe bleeding", "unconscious",
  "anaphylaxis", "heart attack",
];

export const EMERGENCY_GUIDANCE =
  "This may be a medical emergency. Ronin does not handle emergencies. Call your local emergency number now (e.g. 911 in the US, 112 in the EU) or go to the nearest emergency department.";

export interface KeyPoint {
  text: string;
  cite?: number[];
}

export const TOPIC = {
  title: "Statins and grapefruit: what the interaction is",
  subtitle: "General information — not a recommendation about your medication.",
  keyPoints: [
    { text: "Grapefruit can inhibit the CYP3A4 enzyme that clears some statins, which may raise the drug's level in the blood.", cite: [1] },
    { text: "The effect varies a lot by specific statin: simvastatin and lovastatin are most affected; pravastatin and rosuvastatin are largely unaffected.", cite: [1, 2] },
    { text: "Higher statin levels are associated with a greater risk of muscle-related side effects in general.", cite: [2] },
  ] as KeyPoint[],
  notAdvice:
    "This does not tell you whether to avoid grapefruit, change your dose, or switch drugs. Those depend on your specific medication and health — decide them with your clinician or pharmacist.",
};

export type Reliability = "high" | "medium";
export const SOURCES: { id: string; n: number; title: string; domain: string; reliability: Reliability }[] = [
  { id: "h1", n: 1, title: "Grapefruit–medication interactions (patient information)", domain: "sample-health-gov.org", reliability: "high" },
  { id: "h2", n: 2, title: "Statin pharmacokinetics: a clinical review", domain: "sample-journal.med", reliability: "high" },
];

export const PHI_NOTE =
  "Personal identifiers you type are masked before anything is stored, and this world is never used to train models.";

export const SAMPLE_QUESTIONS = [
  "What does an A1C number mean?",
  "How do statins and grapefruit interact?",
  "What are common side effects of ibuprofen?",
];
