"use client";

import { useMemo, useState } from "react";
import {
  DEMO_FLASHCARDS,
  DEMO_PLAN,
  DEMO_QUIZ,
  DEMO_SOURCES,
  ROLES,
  SAFETY_NOTICE,
  TASK_TEMPLATES,
  isAssessedWorkRequest,
} from "../../lib/education_world.mjs";
import {
  FlashcardView,
  QuizView,
  StudyPlanView,
  type Flashcard,
  type Quiz,
  type StudyPlan,
} from "./_components/ArtifactViews";
import {
  DifficultyPanel,
  GoalsPanel,
  SourcesPanel,
  type Goal,
  type Source,
} from "./_components/RightPanel";

// Typed views onto the clearly-labeled demo fixtures from the lib.
const PLAN = DEMO_PLAN as StudyPlan;
const QUIZ = DEMO_QUIZ as Quiz;
const CARDS = DEMO_FLASHCARDS as Flashcard[];
const SOURCES = DEMO_SOURCES as Source[];

const ROLE_LIST = ROLES as { id: string; label: string; blurb: string }[];
const TEMPLATES = TASK_TEMPLATES as {
  id: string;
  label: string;
  artifact: string;
  hint: string;
}[];

type ArtifactKind = "plan" | "quiz" | "flashcards" | "summary" | "note";

const GROUNDED_SUMMARY =
  "Photosynthesis converts light energy into chemical energy: the light reactions in the thylakoid membrane produce ATP and NADPH, which the Calvin cycle uses to fix carbon dioxide into sugar.";

const INITIAL_GOALS: Goal[] = [
  { id: "g1", label: "Describe the two stages of photosynthesis", done: true },
  { id: "g2", label: "Trace energy from light to glucose", done: false },
  { id: "g3", label: "Explain three limiting factors", done: false },
];

const Badge = ({ children, tone = "neutral" }: { children: React.ReactNode; tone?: string }) => {
  const tones: Record<string, string> = {
    demo: "bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300",
    live: "bg-sky-100 text-sky-800 dark:bg-sky-950/60 dark:text-sky-300",
    neutral: "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300",
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${tones[tone]}`}>
      {children}
    </span>
  );
};

export default function EducationWorld() {
  const [role, setRole] = useState("student");
  const [templateId, setTemplateId] = useState("study_plan");
  const [prompt, setPrompt] = useState("");
  const [artifact, setArtifact] = useState<ArtifactKind>("plan");
  const [goals, setGoals] = useState<Goal[]>(INITIAL_GOALS);
  const [difficulty, setDifficulty] = useState("medium");

  const template = useMemo(
    () => TEMPLATES.find((t) => t.id === templateId) ?? TEMPLATES[0],
    [templateId]
  );

  // Fail-closed safety check on the composer text.
  const safety = useMemo(() => isAssessedWorkRequest(prompt), [prompt]);

  const recentScorePct = 72; // demo: last practice quiz result

  function generate() {
    setArtifact(template.artifact as ArtifactKind);
  }

  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      {/* Header */}
      <header className="mb-6">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-medium text-dim">Ronin AI OS</p>
          <Badge tone="demo">Demo data</Badge>
        </div>
        <h1 className="mt-1 text-3xl font-bold tracking-tight text-ink dark:text-neutral-100">
          Education World
        </h1>
        <p className="mt-2 max-w-2xl text-dim">
          A role-aware workspace that produces learning artifacts — study plans,
          quizzes, flashcards, and grounded summaries. Everything below renders
          from labeled sample fixtures; live artifacts come from{" "}
          <code className="rounded bg-bg px-1 dark:bg-neutral-800">/api/v1</code> at runtime.
        </p>
      </header>

      {/* Safety banner */}
      <section
        aria-label="Academic integrity notice"
        className="mb-6 rounded-xl border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/40"
      >
        <h2 className="text-sm font-semibold text-amber-900 dark:text-amber-200">
          {SAFETY_NOTICE.title}
        </h2>
        <ul className="mt-1.5 space-y-1 text-xs text-amber-800 dark:text-amber-300/90">
          {SAFETY_NOTICE.points.map((p: string, i: number) => (
            <li key={i} className="flex gap-2">
              <span aria-hidden>•</span>
              <span>{p}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* Role selector + study-project header */}
      <section className="mb-6 rounded-xl border border-border bg-white p-4 dark:border-neutral-700 dark:bg-neutral-900">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-dim">
              Study project
            </p>
            <p className="text-lg font-semibold text-ink dark:text-neutral-100">{PLAN.title}</p>
            <p className="text-sm text-dim">
              {PLAN.subject} · goal: {PLAN.goal}
            </p>
          </div>
          <fieldset>
            <legend className="mb-1 text-xs font-medium uppercase tracking-wide text-dim">
              Role
            </legend>
            <div role="radiogroup" aria-label="Role" className="flex flex-wrap gap-1.5">
              {ROLE_LIST.map((r) => {
                const active = role === r.id;
                return (
                  <button
                    key={r.id}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    title={r.blurb}
                    onClick={() => setRole(r.id)}
                    className={`rounded-full border px-3 py-1 text-xs transition focus:outline-none focus:ring-2 focus:ring-accent ${
                      active
                        ? "border-accent bg-accent text-white"
                        : "border-border text-dim hover:bg-bg dark:border-neutral-700 dark:hover:bg-neutral-800"
                    }`}
                  >
                    {r.label}
                  </button>
                );
              })}
            </div>
          </fieldset>
        </div>
      </section>

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        {/* Main column */}
        <div className="space-y-6">
          {/* Composer */}
          <section
            aria-label="Composer"
            className="rounded-xl border border-border bg-white p-4 dark:border-neutral-700 dark:bg-neutral-900"
          >
            <label className="mb-1.5 block text-xs font-medium uppercase tracking-wide text-dim">
              Task template
            </label>
            <div className="mb-3 flex flex-wrap gap-1.5">
              {TEMPLATES.map((t) => {
                const active = templateId === t.id;
                return (
                  <button
                    key={t.id}
                    type="button"
                    aria-pressed={active}
                    title={t.hint}
                    onClick={() => setTemplateId(t.id)}
                    className={`rounded-lg border px-2.5 py-1 text-xs transition focus:outline-none focus:ring-2 focus:ring-accent ${
                      active
                        ? "border-accent bg-accent/10 text-accent"
                        : "border-border text-dim hover:bg-bg dark:border-neutral-700 dark:hover:bg-neutral-800"
                    }`}
                  >
                    {t.label}
                  </button>
                );
              })}
            </div>

            <label htmlFor="composer" className="sr-only">
              Describe what you want to learn
            </label>
            <textarea
              id="composer"
              rows={3}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder={template.hint}
              className="input h-auto resize-y font-sans"
            />

            {safety.flagged && (
              <div
                role="alert"
                className="mt-2 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300"
              >
                <p className="font-semibold">{safety.reason}</p>
                <p className="mt-1">{safety.suggestion}</p>
              </div>
            )}

            <div className="mt-3 flex items-center justify-between">
              <p className="text-[11px] text-dim">
                Produces a <span className="font-medium">{template.artifact}</span> artifact
              </p>
              <button
                type="button"
                onClick={generate}
                className="btn btn-primary text-sm disabled:opacity-50"
                disabled={safety.flagged}
              >
                Generate (demo)
              </button>
            </div>
          </section>

          {/* Artifact view */}
          <section
            aria-label="Generated artifact"
            className="rounded-xl border border-border bg-bg/40 p-5 dark:border-neutral-700 dark:bg-neutral-950/40"
          >
            <div className="mb-4 flex items-center gap-2">
              <span className="text-xs font-medium uppercase tracking-wide text-dim">
                Artifact
              </span>
              <Badge tone="demo">Demo data</Badge>
            </div>

            {(artifact === "plan") && <StudyPlanView plan={PLAN} />}
            {artifact === "quiz" && <QuizView quiz={QUIZ} />}
            {artifact === "flashcards" && <FlashcardView cards={CARDS} />}
            {artifact === "summary" && (
              <SummaryView summary={GROUNDED_SUMMARY} sources={SOURCES} />
            )}
            {artifact === "note" && (
              <NoteView
                title={template.label}
                body="Guided explanation renders here. In tutoring mode the assistant asks questions and gives worked steps — it never hands over answers to graded work."
              />
            )}
          </section>
        </div>

        {/* Right panel */}
        <aside className="space-y-4" aria-label="Workspace panels">
          <SourcesPanel sources={SOURCES} summary={GROUNDED_SUMMARY} />
          <GoalsPanel
            plan={PLAN}
            goals={goals}
            onToggle={(id) =>
              setGoals((gs) => gs.map((g) => (g.id === id ? { ...g, done: !g.done } : g)))
            }
          />
          <DifficultyPanel
            current={difficulty}
            recentScorePct={recentScorePct}
            onChange={setDifficulty}
          />
        </aside>
      </div>
    </main>
  );
}

function SummaryView({ summary, sources }: { summary: string; sources: Source[] }) {
  return (
    <section className="space-y-3">
      <h3 className="text-lg font-semibold text-ink dark:text-neutral-100">Grounded summary</h3>
      <p className="text-sm leading-relaxed text-ink dark:text-neutral-200">{summary}</p>
      <p className="text-xs text-dim">
        Claims are checked against the {sources.length} source cards in the panel.
        Unsupported statements are marked INFERENCE.
      </p>
    </section>
  );
}

function NoteView({ title, body }: { title: string; body: string }) {
  return (
    <section className="space-y-2">
      <h3 className="text-lg font-semibold text-ink dark:text-neutral-100">{title}</h3>
      <p className="text-sm leading-relaxed text-ink dark:text-neutral-200">{body}</p>
    </section>
  );
}
