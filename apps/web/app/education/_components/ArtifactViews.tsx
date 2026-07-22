"use client";

import { useMemo, useState } from "react";
import {
  flashcardOrder,
  quizKey,
  quizScore,
  studyPlanProgress,
} from "../../../lib/education_world.mjs";

// ---- Shared fixture shapes (mirror the DEMO_* exports in the lib) ----------

export interface PlanTask {
  id: string;
  label: string;
  done: boolean;
}
export interface PlanWeek {
  week: number;
  topic: string;
  dueDate: string;
  tasks: PlanTask[];
}
export interface StudyPlan {
  id: string;
  title: string;
  subject: string;
  role: string;
  goal: string;
  weeks: PlanWeek[];
}
export interface QuizQuestion {
  id: string;
  prompt: string;
  choices: string[];
  answer: number;
  explanation: string;
}
export interface Quiz {
  id: string;
  title: string;
  questions: QuizQuestion[];
}
export interface Flashcard {
  id: string;
  front: string;
  back: string;
}

const CARD =
  "rounded-xl border border-border bg-white p-5 dark:border-neutral-700 dark:bg-neutral-900";

// ---------------------------------------------------------------------------
// Study plan artifact — weeks → topics → tasks with checkboxes
// ---------------------------------------------------------------------------

export function StudyPlanView({ plan }: { plan: StudyPlan }) {
  const [done, setDone] = useState<Record<string, boolean>>(() => {
    const init: Record<string, boolean> = {};
    for (const w of plan.weeks) for (const t of w.tasks) init[t.id] = t.done;
    return init;
  });

  const livePlan = useMemo(
    () => ({
      ...plan,
      weeks: plan.weeks.map((w) => ({
        ...w,
        tasks: w.tasks.map((t) => ({ ...t, done: !!done[t.id] })),
      })),
    }),
    [plan, done]
  );
  const pct = studyPlanProgress(livePlan);

  return (
    <section aria-labelledby="plan-heading" className="space-y-4">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h3 id="plan-heading" className="text-lg font-semibold text-ink dark:text-neutral-100">
            {plan.title}
          </h3>
          <p className="text-sm text-dim">{plan.goal}</p>
        </div>
        <div className="shrink-0 text-right">
          <p className="text-2xl font-semibold tabular-nums text-ink dark:text-neutral-100">{pct}%</p>
          <p className="text-xs text-dim">complete</p>
        </div>
      </div>

      <div className="h-2 w-full overflow-hidden rounded-full bg-border dark:bg-neutral-800" aria-hidden>
        <div
          className="h-full rounded-full bg-accent transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>

      <ol className="space-y-3">
        {livePlan.weeks.map((w) => (
          <li key={w.week} className={CARD}>
            <div className="mb-2 flex items-baseline justify-between gap-3">
              <h4 className="font-medium text-ink dark:text-neutral-100">
                Week {w.week}: {w.topic}
              </h4>
              <span className="text-xs text-dim">due {w.dueDate}</span>
            </div>
            <ul className="space-y-1.5">
              {w.tasks.map((t) => (
                <li key={t.id}>
                  <label className="flex cursor-pointer items-center gap-2.5 rounded-md px-1 py-1 text-sm text-ink hover:bg-bg dark:text-neutral-200 dark:hover:bg-neutral-800">
                    <input
                      type="checkbox"
                      className="h-4 w-4 rounded border-border text-accent focus:ring-2 focus:ring-accent"
                      checked={t.done}
                      onChange={(e) =>
                        setDone((d) => ({ ...d, [t.id]: e.target.checked }))
                      }
                    />
                    <span className={t.done ? "text-dim line-through" : ""}>{t.label}</span>
                  </label>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Quiz artifact — questions + reveal-answer
// ---------------------------------------------------------------------------

export function QuizView({ quiz }: { quiz: Quiz }) {
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});
  const key = useMemo(() => quizKey(quiz), [quiz]);
  const score = quizScore(answers, key);
  const anyAnswered = Object.keys(answers).length > 0;

  return (
    <section aria-labelledby="quiz-heading" className="space-y-4">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h3 id="quiz-heading" className="text-lg font-semibold text-ink dark:text-neutral-100">
            {quiz.title}
          </h3>
          <p className="text-sm text-dim">Practice only — answers reveal on request.</p>
        </div>
        {anyAnswered && (
          <div className="shrink-0 text-right" aria-live="polite">
            <p className="text-2xl font-semibold tabular-nums text-ink dark:text-neutral-100">
              {score.pct}%
            </p>
            <p className="text-xs text-dim">
              {score.correct}/{score.total} correct
            </p>
          </div>
        )}
      </div>

      <ol className="space-y-3">
        {quiz.questions.map((q, qi) => {
          const chosen = answers[q.id];
          const show = revealed[q.id];
          return (
            <li key={q.id} className={CARD}>
              <fieldset>
                <legend className="mb-2 font-medium text-ink dark:text-neutral-100">
                  {qi + 1}. {q.prompt}
                </legend>
                <div className="space-y-1.5">
                  {q.choices.map((c, ci) => {
                    const isChosen = chosen === ci;
                    const isCorrect = ci === q.answer;
                    let tone =
                      "border-border hover:bg-bg dark:border-neutral-700 dark:hover:bg-neutral-800";
                    if (show && isCorrect)
                      tone = "border-emerald-300 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/40";
                    else if (show && isChosen && !isCorrect)
                      tone = "border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/40";
                    else if (isChosen)
                      tone = "border-accent bg-accent/5";
                    return (
                      <label
                        key={ci}
                        className={`flex cursor-pointer items-center gap-2.5 rounded-md border px-3 py-1.5 text-sm text-ink dark:text-neutral-200 ${tone}`}
                      >
                        <input
                          type="radio"
                          name={q.id}
                          className="text-accent focus:ring-2 focus:ring-accent"
                          checked={isChosen}
                          onChange={() => setAnswers((a) => ({ ...a, [q.id]: ci }))}
                        />
                        {c}
                      </label>
                    );
                  })}
                </div>
              </fieldset>
              <div className="mt-2 flex items-center gap-3">
                <button
                  type="button"
                  className="btn btn-secondary text-xs dark:bg-neutral-800 dark:text-neutral-100"
                  aria-expanded={!!show}
                  onClick={() => setRevealed((r) => ({ ...r, [q.id]: !r[q.id] }))}
                >
                  {show ? "Hide answer" : "Reveal answer"}
                </button>
                {show && <p className="text-xs text-dim">{q.explanation}</p>}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Flashcard artifact — flip, deterministic shuffle
// ---------------------------------------------------------------------------

export function FlashcardView({ cards }: { cards: Flashcard[] }) {
  const [seed, setSeed] = useState(1);
  const [idx, setIdx] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const ordered = useMemo(() => flashcardOrder(cards, seed), [cards, seed]);
  const card = ordered[idx];

  function go(delta: number) {
    setFlipped(false);
    setIdx((i) => (i + delta + ordered.length) % ordered.length);
  }

  return (
    <section aria-labelledby="fc-heading" className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <h3 id="fc-heading" className="text-lg font-semibold text-ink dark:text-neutral-100">
          Flashcards
        </h3>
        <button
          type="button"
          className="btn btn-secondary text-xs dark:bg-neutral-800 dark:text-neutral-100"
          onClick={() => {
            setSeed((s) => s + 1);
            setIdx(0);
            setFlipped(false);
          }}
        >
          Shuffle
        </button>
      </div>

      <button
        type="button"
        onClick={() => setFlipped((f) => !f)}
        aria-pressed={flipped}
        aria-label={`Flashcard ${idx + 1} of ${ordered.length}. ${
          flipped ? "Showing answer" : "Showing term"
        }. Click to flip.`}
        className="flex min-h-[160px] w-full flex-col items-center justify-center rounded-xl border border-border bg-white p-6 text-center transition hover:border-accent focus:outline-none focus:ring-2 focus:ring-accent dark:border-neutral-700 dark:bg-neutral-900"
      >
        <span className="mb-1 text-xs uppercase tracking-wide text-dim">
          {flipped ? "Definition" : "Term"}
        </span>
        <span className="text-lg font-medium text-ink dark:text-neutral-100">
          {flipped ? card?.back : card?.front}
        </span>
        <span className="mt-3 text-xs text-dim">Click or press Enter to flip</span>
      </button>

      <div className="flex items-center justify-between">
        <button type="button" className="btn btn-secondary text-xs dark:bg-neutral-800 dark:text-neutral-100" onClick={() => go(-1)}>
          ← Prev
        </button>
        <span className="text-xs text-dim tabular-nums">
          {idx + 1} / {ordered.length}
        </span>
        <button type="button" className="btn btn-secondary text-xs dark:bg-neutral-800 dark:text-neutral-100" onClick={() => go(1)}>
          Next →
        </button>
      </div>
    </section>
  );
}
