// Education World logic tests — run with `node --test` (no install required).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEMO_FLASHCARDS,
  DEMO_PLAN,
  DEMO_QUIZ,
  DEMO_SOURCES,
  adaptDifficulty,
  flashcardOrder,
  groundedClaim,
  isAssessedWorkRequest,
  nextDueTopic,
  quizKey,
  quizScore,
  studyPlanProgress,
} from "./education_world.mjs";

test("studyPlanProgress returns integer percent of tasks done", () => {
  // DEMO_PLAN: 3 + 1 done out of 10 tasks = 40%.
  assert.equal(studyPlanProgress(DEMO_PLAN), 40);
  assert.equal(studyPlanProgress({ weeks: [] }), 0);
  assert.equal(studyPlanProgress({}), 0);
});

test("studyPlanProgress reaches 100 when all tasks done", () => {
  const plan = {
    weeks: [{ tasks: [{ done: true }, { done: true }] }],
  };
  assert.equal(studyPlanProgress(plan), 100);
});

test("nextDueTopic picks earliest-due incomplete week", () => {
  const next = nextDueTopic(DEMO_PLAN, new Date("2026-07-22"));
  assert.equal(next.week, 2);
  assert.equal(next.topic, "Light-dependent reactions");
});

test("nextDueTopic flags overdue relative to now", () => {
  const next = nextDueTopic(DEMO_PLAN, new Date("2026-08-01"));
  // Week 2 due 2026-07-27 is now overdue.
  assert.equal(next.week, 2);
  assert.equal(next.overdue, true);
});

test("nextDueTopic returns null when everything is done", () => {
  const plan = { weeks: [{ week: 1, topic: "x", tasks: [{ done: true }] }] };
  assert.equal(nextDueTopic(plan, new Date()), null);
});

test("quizScore counts correct answers against the key", () => {
  const key = quizKey(DEMO_QUIZ); // {q1:1, q2:2, q3:0}
  assert.deepEqual(quizScore({ q1: 1, q2: 2, q3: 0 }, key), {
    correct: 3,
    total: 3,
    pct: 100,
  });
  assert.deepEqual(quizScore({ q1: 1, q2: 0, q3: 0 }, key), {
    correct: 2,
    total: 3,
    pct: 67,
  });
});

test("quizScore handles empty key and missing answers", () => {
  assert.deepEqual(quizScore({}, {}), { correct: 0, total: 0, pct: 0 });
  assert.deepEqual(quizScore(undefined, { q1: 1 }), { correct: 0, total: 1, pct: 0 });
});

test("adaptDifficulty steps up on high scores, down on low, bounded", () => {
  assert.equal(adaptDifficulty("easy", 90), "medium");
  assert.equal(adaptDifficulty("medium", 90), "hard");
  assert.equal(adaptDifficulty("hard", 90), "hard"); // bounded at top
  assert.equal(adaptDifficulty("medium", 40), "easy");
  assert.equal(adaptDifficulty("easy", 40), "easy"); // bounded at bottom
  assert.equal(adaptDifficulty("medium", 70), "medium"); // hold
  assert.equal(adaptDifficulty("bogus", 90), "hard"); // unknown → medium then up
});

test("flashcardOrder is a deterministic permutation for a seed", () => {
  const a = flashcardOrder(DEMO_FLASHCARDS, 42);
  const b = flashcardOrder(DEMO_FLASHCARDS, 42);
  assert.deepEqual(a.map((c) => c.id), b.map((c) => c.id));
  // Same multiset of ids, no loss.
  assert.deepEqual(
    [...a.map((c) => c.id)].sort(),
    [...DEMO_FLASHCARDS.map((c) => c.id)].sort()
  );
});

test("flashcardOrder differs across seeds and never mutates input", () => {
  const before = DEMO_FLASHCARDS.map((c) => c.id).join(",");
  const s1 = flashcardOrder(DEMO_FLASHCARDS, 1).map((c) => c.id).join(",");
  const s7 = flashcardOrder(DEMO_FLASHCARDS, 7).map((c) => c.id).join(",");
  assert.notEqual(s1, s7);
  assert.equal(DEMO_FLASHCARDS.map((c) => c.id).join(","), before);
});

test("isAssessedWorkRequest flags dishonest requests (fail-closed)", () => {
  assert.equal(isAssessedWorkRequest("Do my homework for me").flagged, true);
  assert.equal(isAssessedWorkRequest("write my essay for me").flagged, true);
  assert.equal(isAssessedWorkRequest("give me the answers to the exam").flagged, true);
  assert.equal(isAssessedWorkRequest("take my quiz").flagged, true);
  const flagged = isAssessedWorkRequest("just turn in this graded assignment");
  assert.equal(flagged.flagged, true);
  assert.match(flagged.suggestion, /not produce work to submit/i);
});

test("isAssessedWorkRequest allows genuine learning requests", () => {
  assert.equal(isAssessedWorkRequest("Explain photosynthesis to me").flagged, false);
  assert.equal(isAssessedWorkRequest("Help me understand the Calvin cycle").flagged, false);
  assert.equal(isAssessedWorkRequest("").flagged, false);
});

test("groundedClaim marks GROUNDED when a source supports it", () => {
  const g = groundedClaim(
    "Light reactions in the thylakoid produce ATP and NADPH.",
    DEMO_SOURCES
  );
  assert.equal(g.status, "GROUNDED");
  assert.equal(g.sourceId, "s1");
});

test("groundedClaim marks INFERENCE with no supporting source", () => {
  const g = groundedClaim("Volcanoes erupt basalt lava frequently.", DEMO_SOURCES);
  assert.equal(g.status, "INFERENCE");
  assert.equal(g.sourceId, null);
  // Empty sources also inference.
  assert.equal(groundedClaim("anything at all here", []).status, "INFERENCE");
});
