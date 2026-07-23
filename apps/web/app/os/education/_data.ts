/**
 * Demo content for the Education world. Offline sample: the explanation is
 * illustrative and sourced; the study plan and practice set are examples. The
 * world's defining behavior is academic integrity — it teaches and coaches,
 * and fails closed on assessed/graded work rather than producing submittable
 * answers.
 */

import type { IconName } from "@ronin/design-system";

export type Role = "student" | "educator" | "learner";
export const ROLES: { id: Role; label: string; icon: IconName; note: string }[] = [
  { id: "student", label: "Student", icon: "education", note: "Coaching, not answers to graded work." },
  { id: "educator", label: "Educator", icon: "business", note: "Lesson material, rubrics, and examples." },
  { id: "learner", label: "Self-learner", icon: "spark", note: "Go as deep as you like, at your pace." },
];

/** Phrasing that flips the world into integrity/coaching mode (demo). */
export const INTEGRITY_TERMS = [
  "do my homework", "answers to the exam", "exam answers", "test answers",
  "quiz answers", "graded", "write my essay", "solve my assignment",
  "answer key", "do my assignment", "answers for the test",
];

export const INTEGRITY_GUIDANCE =
  "This looks like assessed or graded work. Ronin won't produce answers for you to submit — that isn't learning, and it isn't honest. It can explain the concept, walk a similar example, or build a practice set instead.";

export interface KeyPoint {
  text: string;
  cite?: number[];
}

export const TOPIC = {
  subject: "Cell biology",
  title: "How the mitochondrion makes ATP",
  keyPoints: [
    { text: "The mitochondrion converts energy from food into ATP, the cell's usable energy currency, mostly through aerobic respiration.", cite: [1] },
    { text: "The electron transport chain pumps protons across the inner membrane, creating a gradient (the proton-motive force).", cite: [1, 2] },
    { text: "ATP synthase lets protons flow back through it, and that flow drives the mechanical synthesis of ATP from ADP.", cite: [2] },
  ] as KeyPoint[],
  checkYourself: "In your own words: why does the cell bother building a proton gradient instead of making ATP directly?",
};

export interface PlanItem {
  topic: string;
  due: string;
  mastery: number; // 0..100
  state: "due" | "soon" | "solid";
}
export const STUDY_PLAN: PlanItem[] = [
  { topic: "Glycolysis — inputs & outputs", due: "Due today", mastery: 45, state: "due" },
  { topic: "The citric acid cycle", due: "Due today", mastery: 30, state: "due" },
  { topic: "Electron transport chain", due: "Tomorrow", mastery: 60, state: "soon" },
  { topic: "ATP synthase mechanism", due: "In 3 days", mastery: 80, state: "solid" },
];

export interface QuizItem {
  q: string;
  a: string;
}
export const QUIZ: QuizItem[] = [
  { q: "What gradient does the electron transport chain build?", a: "A proton (H⁺) gradient across the inner mitochondrial membrane — the proton-motive force." },
  { q: "What enzyme actually synthesizes ATP?", a: "ATP synthase, driven by protons flowing back down their gradient." },
  { q: "Roughly where does aerobic respiration occur in the cell?", a: "In the mitochondria — the matrix (citric acid cycle) and the inner membrane (electron transport)." },
];

export type Reliability = "high" | "medium";
export const SOURCES: { id: string; n: number; title: string; domain: string; reliability: Reliability }[] = [
  { id: "e1", n: 1, title: "Cellular respiration — an overview", domain: "sample-openbio.org", reliability: "high" },
  { id: "e2", n: 2, title: "Chemiosmosis and ATP synthase", domain: "sample-textbook.edu", reliability: "high" },
];

export const SAMPLE_PROMPTS = [
  "Explain the citric acid cycle simply",
  "Make me a 5-question practice quiz",
  "Give me a spaced-repetition plan for this week",
];
