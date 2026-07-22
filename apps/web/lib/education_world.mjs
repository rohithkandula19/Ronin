// Pure, dependency-free logic for Education World.
// Plain ESM so it runs under `node --test` with zero install, and is imported
// by the React page (app/education/page.tsx). No DOM, no fetch, no React here.
//
// DEMO MODE: the DEMO_* fixtures below are clearly-labeled sample data used to
// render the UI offline. Live artifacts come from /api/v1 at runtime.

// ---------------------------------------------------------------------------
// Static config
// ---------------------------------------------------------------------------

/** Roles this world serves. */
export const ROLES = [
  { id: "student", label: "Student", blurb: "Learn a topic and track progress." },
  { id: "teacher", label: "Teacher", blurb: "Plan lessons and build materials." },
  { id: "tutor", label: "Tutor", blurb: "Guide a learner one-to-one." },
  { id: "parent", label: "Parent", blurb: "Support a learner at home." },
  { id: "researcher", label: "Researcher", blurb: "Study grounded, cited material." },
];

/** Composer task templates. `artifact` names which view a template produces. */
export const TASK_TEMPLATES = [
  { id: "explain", label: "Explain concept", artifact: "note", hint: "Explain a concept at a chosen level." },
  { id: "tutoring", label: "Guided tutoring", artifact: "note", hint: "Socratic, step-by-step guidance — never just answers." },
  { id: "study_plan", label: "Study plan", artifact: "plan", hint: "Weeks → topics → tasks." },
  { id: "quiz", label: "Quiz", artifact: "quiz", hint: "Practice questions with reveal-answer." },
  { id: "flashcards", label: "Flashcards", artifact: "flashcards", hint: "Spaced-recall cards to flip." },
  { id: "assignment_planning", label: "Assignment planning", artifact: "plan", hint: "Break an assignment into honest, ungraded steps." },
  { id: "grounded_summary", label: "Grounded summary", artifact: "summary", hint: "Summarize sources with citations." },
];

/** Difficulty ladder, easy → hard. */
export const DIFFICULTY_LEVELS = ["easy", "medium", "hard"];

/** The safety promise Education World surfaces up front. */
export const SAFETY_NOTICE = {
  title: "Education World supports learning — it does not do graded work for you.",
  points: [
    "Guidance, explanations, and practice are clearly separated from assessed work.",
    "It will not complete graded assignments, exams, or homework to be submitted as your own.",
    "Learner data — especially for minors — is minimized and never used to identify a child.",
  ],
};

// ---------------------------------------------------------------------------
// Study plan
// ---------------------------------------------------------------------------

/**
 * Percent of plan tasks completed, 0–100 (integer). Empty plan → 0.
 * @param {{weeks?: {tasks?: {done?: boolean}[]}[]}} plan
 * @returns {number}
 */
export function studyPlanProgress(plan) {
  const tasks = (plan?.weeks ?? []).flatMap((w) => w.tasks ?? []);
  if (tasks.length === 0) return 0;
  const done = tasks.filter((t) => t.done).length;
  return Math.round((done / tasks.length) * 100);
}

/**
 * The next topic that still has incomplete tasks, chosen by earliest dueDate.
 * Flags whether it is overdue relative to `now`. Returns null when every task
 * is done.
 * @param {{weeks?: {week?: number, topic?: string, dueDate?: string, tasks?: {done?: boolean}[]}[]}} plan
 * @param {Date|string|number} [now]
 * @returns {{week: number, topic: string, dueDate: string|null, overdue: boolean}|null}
 */
export function nextDueTopic(plan, now = new Date()) {
  const when = now instanceof Date ? now : new Date(now);
  const open = (plan?.weeks ?? []).filter((w) =>
    (w.tasks ?? []).some((t) => !t.done)
  );
  if (open.length === 0) return null;
  const sorted = [...open].sort((a, b) => {
    const da = a.dueDate ? Date.parse(a.dueDate) : Infinity;
    const db = b.dueDate ? Date.parse(b.dueDate) : Infinity;
    if (da !== db) return da - db;
    return (a.week ?? 0) - (b.week ?? 0);
  });
  const w = sorted[0];
  const due = w.dueDate ? Date.parse(w.dueDate) : NaN;
  return {
    week: w.week ?? 0,
    topic: w.topic ?? "",
    dueDate: w.dueDate ?? null,
    overdue: Number.isFinite(due) ? due < when.getTime() : false,
  };
}

// ---------------------------------------------------------------------------
// Quiz
// ---------------------------------------------------------------------------

/**
 * Score answers against a key. `answers` and `key` are objects keyed by
 * question id → chosen/correct choice index. Only ids present in `key` count.
 * @param {Record<string, number>} answers
 * @param {Record<string, number>} key
 * @returns {{correct: number, total: number, pct: number}}
 */
export function quizScore(answers, key) {
  const ids = Object.keys(key ?? {});
  const total = ids.length;
  if (total === 0) return { correct: 0, total: 0, pct: 0 };
  const correct = ids.filter((id) => (answers ?? {})[id] === key[id]).length;
  return { correct, total, pct: Math.round((correct / total) * 100) };
}

/**
 * Build the answer key ({qid: answerIndex}) from a quiz fixture.
 * @param {{questions?: {id: string, answer: number}[]}} quiz
 * @returns {Record<string, number>}
 */
export function quizKey(quiz) {
  /** @type {Record<string, number>} */
  const key = {};
  for (const q of quiz?.questions ?? []) key[q.id] = q.answer;
  return key;
}

// ---------------------------------------------------------------------------
// Difficulty adaptation
// ---------------------------------------------------------------------------

/**
 * Adapt difficulty from recent performance, bounded within DIFFICULTY_LEVELS.
 * >= 85%: step up (harder). < 60%: step down (easier). Otherwise: hold.
 * Unknown `current` is treated as "medium".
 * @param {string} current
 * @param {number} recentScorePct
 * @returns {string}
 */
export function adaptDifficulty(current, recentScorePct) {
  let i = DIFFICULTY_LEVELS.indexOf(current);
  if (i === -1) i = DIFFICULTY_LEVELS.indexOf("medium");
  const pct = Number.isFinite(recentScorePct) ? recentScorePct : 0;
  if (pct >= 85) i += 1;
  else if (pct < 60) i -= 1;
  i = Math.max(0, Math.min(DIFFICULTY_LEVELS.length - 1, i));
  return DIFFICULTY_LEVELS[i];
}

// ---------------------------------------------------------------------------
// Flashcards
// ---------------------------------------------------------------------------

/** mulberry32 — small deterministic PRNG. */
function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Deterministic shuffle of cards for a given integer seed (Fisher–Yates driven
 * by a seeded PRNG). Same seed → same order. Never mutates the input.
 * @template T
 * @param {T[]} cards
 * @param {number} [seed]
 * @returns {T[]}
 */
export function flashcardOrder(cards, seed = 1) {
  const out = [...(cards ?? [])];
  const rand = mulberry32(Math.trunc(seed) || 1);
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

// ---------------------------------------------------------------------------
// Safety: assessed-work detection (fail-closed)
// ---------------------------------------------------------------------------

const ASSESSED_PATTERNS = [
  /\b(do|complete|finish|write|solve)\b[^.?!]{0,40}\bmy\b[^.?!]{0,40}\b(homework|assignment|essay|test|exam|quiz|coursework|paper|lab report)\b/i,
  /\b(answers?|solutions?)\b[^.?!]{0,30}\b(to|for)\b[^.?!]{0,30}\b(the|my|this)\b[^.?!]{0,20}\b(test|exam|quiz|homework|assignment|worksheet)\b/i,
  /\btake\b[^.?!]{0,20}\bmy\b[^.?!]{0,20}\b(exam|test|quiz)\b/i,
  /\bwrite\b[^.?!]{0,30}\bfor me\b[^.?!]{0,30}\b(essay|paper|assignment|report)\b/i,
  /\b(graded|for a grade|for credit|to submit|hand in|turn in)\b/i,
];

// Words that mark a request as legitimate learning even if it mentions homework.
const LEARNING_MARKERS = /\b(explain|understand|help me learn|how do i|walk me through|check my|review my|guide|practice|study)\b/i;

/**
 * Flag likely academic-dishonesty requests. Fail-closed: when a request looks
 * like it targets graded/assessed work, it is flagged even if wording is
 * ambiguous, so the UI can reframe it as guidance rather than doing the work.
 * @param {string} text
 * @returns {{flagged: boolean, reason: string, suggestion: string}}
 */
export function isAssessedWorkRequest(text) {
  const t = String(text ?? "");
  const hit = ASSESSED_PATTERNS.some((re) => re.test(t));
  if (!hit) {
    return { flagged: false, reason: "No assessed-work request detected.", suggestion: "" };
  }
  const learning = LEARNING_MARKERS.test(t);
  return {
    flagged: true,
    reason: learning
      ? "Mentions graded work — routing to guidance to keep it honest."
      : "Looks like a request to complete graded/assessed work.",
    suggestion:
      "Reframed as learning: I can explain the concepts, walk through a worked example, and help you check your own answer — not produce work to submit as your own.",
  };
}

// ---------------------------------------------------------------------------
// Grounding
// ---------------------------------------------------------------------------

function tokenize(s) {
  return String(s ?? "")
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((w) => w.length > 3);
}

/**
 * Mark a claim GROUNDED (with the best matching source id) when its content
 * overlaps a provided source, otherwise INFERENCE. `minOverlap` sets how many
 * shared content words are required.
 * @param {string} text
 * @param {{id: string, title?: string, snippet?: string, tags?: string[]}[]} sources
 * @param {number} [minOverlap]
 * @returns {{status: "GROUNDED"|"INFERENCE", sourceId: string|null, overlap: number}}
 */
export function groundedClaim(text, sources, minOverlap = 2) {
  const claimWords = new Set(tokenize(text));
  let best = { sourceId: null, overlap: 0 };
  for (const s of sources ?? []) {
    const hay = tokenize([s.title, s.snippet, ...(s.tags ?? [])].join(" "));
    let overlap = 0;
    for (const w of hay) if (claimWords.has(w)) overlap++;
    if (overlap > best.overlap) best = { sourceId: s.id, overlap };
  }
  if (best.overlap >= minOverlap) {
    return { status: "GROUNDED", sourceId: best.sourceId, overlap: best.overlap };
  }
  return { status: "INFERENCE", sourceId: null, overlap: best.overlap };
}

// ---------------------------------------------------------------------------
// DEMO FIXTURES (clearly-labeled sample data)
// ---------------------------------------------------------------------------

/** @type {{id:string,title:string,subject:string,role:string,goal:string,weeks:{week:number,topic:string,dueDate:string,tasks:{id:string,label:string,done:boolean}[]}[]}} */
export const DEMO_PLAN = {
  id: "demo-plan-photosynthesis",
  title: "Intro to Photosynthesis — 4-week plan",
  subject: "Biology",
  role: "student",
  goal: "Explain how plants convert light into chemical energy.",
  weeks: [
    {
      week: 1,
      topic: "Light & the leaf",
      dueDate: "2026-07-20",
      tasks: [
        { id: "w1t1", label: "Read: structure of a chloroplast", done: true },
        { id: "w1t2", label: "Watch: light vs. dark reactions overview", done: true },
        { id: "w1t3", label: "Practice: label a leaf cross-section", done: true },
      ],
    },
    {
      week: 2,
      topic: "Light-dependent reactions",
      dueDate: "2026-07-27",
      tasks: [
        { id: "w2t1", label: "Read: photosystems I and II", done: true },
        { id: "w2t2", label: "Practice: trace electron flow", done: false },
        { id: "w2t3", label: "Self-quiz: ATP & NADPH", done: false },
      ],
    },
    {
      week: 3,
      topic: "The Calvin cycle",
      dueDate: "2026-08-03",
      tasks: [
        { id: "w3t1", label: "Read: carbon fixation steps", done: false },
        { id: "w3t2", label: "Draw: the 3-stage cycle", done: false },
      ],
    },
    {
      week: 4,
      topic: "Limiting factors & review",
      dueDate: "2026-08-10",
      tasks: [
        { id: "w4t1", label: "Investigate: light, CO2, temperature", done: false },
        { id: "w4t2", label: "Mixed practice quiz", done: false },
      ],
    },
  ],
};

/** @type {{id:string,title:string,questions:{id:string,prompt:string,choices:string[],answer:number,explanation:string}[]}} */
export const DEMO_QUIZ = {
  id: "demo-quiz-photosynthesis",
  title: "Photosynthesis — practice quiz",
  questions: [
    {
      id: "q1",
      prompt: "Where do the light-dependent reactions occur?",
      choices: ["Stroma", "Thylakoid membrane", "Cytoplasm", "Mitochondria"],
      answer: 1,
      explanation: "The thylakoid membranes hold the photosystems that capture light.",
    },
    {
      id: "q2",
      prompt: "What gas do plants take in for the Calvin cycle?",
      choices: ["Oxygen", "Nitrogen", "Carbon dioxide", "Hydrogen"],
      answer: 2,
      explanation: "CO2 is fixed into sugar during the Calvin cycle.",
    },
    {
      id: "q3",
      prompt: "Which molecules carry energy from the light reactions to the Calvin cycle?",
      choices: ["ATP and NADPH", "DNA and RNA", "Glucose and starch", "Water and CO2"],
      answer: 0,
      explanation: "ATP and NADPH power carbon fixation.",
    },
  ],
};

/** @type {{id:string,front:string,back:string}[]} */
export const DEMO_FLASHCARDS = [
  { id: "f1", front: "Chlorophyll", back: "Green pigment that absorbs light energy." },
  { id: "f2", front: "Stroma", back: "Fluid where the Calvin cycle happens." },
  { id: "f3", front: "Thylakoid", back: "Membrane sac where light reactions occur." },
  { id: "f4", front: "ATP", back: "Energy-carrying molecule made in the light reactions." },
  { id: "f5", front: "Calvin cycle", back: "Light-independent reactions that fix carbon into sugar." },
];

/** @type {{id:string,title:string,url:string,snippet:string,tags:string[]}[]} */
export const DEMO_SOURCES = [
  {
    id: "s1",
    title: "OpenStax Biology 2e — Photosynthesis",
    url: "https://openstax.org/books/biology-2e/pages/8-introduction",
    snippet:
      "Photosynthesis converts light energy into chemical energy stored in glucose; light reactions occur in the thylakoid membrane and produce ATP and NADPH.",
    tags: ["photosynthesis", "thylakoid", "atp", "nadph", "glucose"],
  },
  {
    id: "s2",
    title: "Khan Academy — The Calvin cycle",
    url: "https://www.khanacademy.org/science/biology/photosynthesis-in-plants",
    snippet:
      "The Calvin cycle fixes carbon dioxide in the stroma, using ATP and NADPH to build sugar through carbon fixation.",
    tags: ["calvin", "cycle", "carbon", "stroma", "fixation"],
  },
];

export const DEMO = { DEMO_PLAN, DEMO_QUIZ, DEMO_FLASHCARDS, DEMO_SOURCES };
