import type { IconName } from "@ronin/design-system";
import type { RiskTone } from "@ronin/design-system";

/**
 * Demo content for the Ronin OS shell. This is the OFFLINE surface: with no
 * backend connected, the shell shows this labelled sample so the experience is
 * legible without inventing live data. Everything here is explicitly a sample —
 * the UI marks it as such. When `NEXT_PUBLIC_API_URL` points at a running API,
 * these will be replaced by real reads.
 */

export interface World {
  id: string;
  name: string;
  tagline: string;
  icon: IconName;
  risk: RiskTone;
  /** Enterable route today, or null when the world is still in preparation. */
  href: string | null;
  status: "available" | "preparing";
  note?: string;
}

export const WORLDS: World[] = [
  {
    id: "coding",
    name: "Coding",
    tagline: "Read, reason about, and change codebases — behind approval gates.",
    icon: "code",
    risk: "review",
    href: "/os/code",
    status: "available",
  },
  {
    id: "education",
    name: "Education",
    tagline: "Study plans, explanations, and practice — grounded, never graded in secret.",
    icon: "education",
    risk: "safe",
    href: "/education",
    status: "available",
  },
  {
    id: "healthcare",
    name: "Healthcare",
    tagline: "Health information and summaries. Never diagnoses. Never prescribes.",
    icon: "healthcare",
    risk: "caution",
    href: "/os/healthcare",
    status: "available",
    note: "Information only — not medical advice.",
  },
  { id: "finance", name: "Finance", tagline: "Analysis and modeling with a clear audit trail.", icon: "finance", risk: "caution", href: null, status: "preparing" },
  { id: "business", name: "Business", tagline: "Operations, strategy, and documents you can trust.", icon: "business", risk: "review", href: null, status: "preparing" },
  { id: "legal", name: "Legal", tagline: "Drafts marked DRAFT — always requires legal review.", icon: "legal", risk: "restricted", href: null, status: "preparing", note: "Output is never legal advice." },
  { id: "science", name: "Science", tagline: "Literature, methods, and reproducible reasoning.", icon: "science", risk: "review", href: null, status: "preparing" },
  { id: "creative", name: "Creative", tagline: "Writing and ideation with your voice, not a template.", icon: "creative", risk: "safe", href: null, status: "preparing" },
  { id: "marketing", name: "Marketing", tagline: "Campaigns and copy with provenance and brand guardrails.", icon: "marketing", risk: "review", href: null, status: "preparing" },
  { id: "research", name: "Research", tagline: "Multi-source research with citations you can follow.", icon: "research", risk: "review", href: "/os/research", status: "available" },
];

export const GLOBAL_DESTINATIONS: { id: string; label: string; icon: IconName; href: string }[] = [
  { id: "agents", label: "Agents", icon: "agents", href: "/os" },
  { id: "artifacts", label: "Artifacts", icon: "artifacts", href: "/artifacts" },
  { id: "memory", label: "Memory", icon: "memory", href: "/os" },
  { id: "knowledge", label: "Knowledge", icon: "knowledge", href: "/os" },
  { id: "tasks", label: "Tasks", icon: "tasks", href: "/tasks" },
  { id: "vault", label: "Vault", icon: "vault", href: "/vault" },
];

/** A sample plan the Coding world might be executing — with an approval gate. */
export interface PlanStep {
  label: string;
  state: "done" | "active" | "todo" | "approval";
}
export const SAMPLE_PLAN: { title: string; world: string; steps: PlanStep[] } = {
  title: "Add rate-limit headers to the public API",
  world: "Coding",
  steps: [
    { label: "Locate the middleware stack", state: "done" },
    { label: "Draft the header-injection change", state: "done" },
    { label: "Run the test suite in a sandbox", state: "active" },
    { label: "Apply the diff to your working tree", state: "approval" },
    { label: "Open a draft pull request", state: "todo" },
  ],
};

export interface RecentArtifact {
  id: string;
  title: string;
  kind: string;
  world: string;
  when: string;
}
export const RECENT_ARTIFACTS: RecentArtifact[] = [
  { id: "a1", title: "Q3 architecture review", kind: "document", world: "Coding", when: "2h ago" },
  { id: "a2", title: "Cell biology — spaced-repetition plan", kind: "study_plan", world: "Education", when: "yesterday" },
  { id: "a3", title: "Medication interaction summary", kind: "medical_summary", world: "Healthcare", when: "yesterday" },
  { id: "a4", title: "Migration runbook v3", kind: "plan", world: "Coding", when: "2d ago" },
];

export interface SuggestedAction {
  id: string;
  label: string;
  detail: string;
  icon: IconName;
  world: string;
}
export const SUGGESTED_ACTIONS: SuggestedAction[] = [
  { id: "s1", label: "Resume the rate-limit change", detail: "Coding · waiting on your approval", icon: "code", world: "Coding" },
  { id: "s2", label: "Review today's study plan", detail: "Education · 3 topics due", icon: "education", world: "Education" },
  { id: "s3", label: "Summarize 4 new sources", detail: "Research · queued", icon: "research", world: "Research" },
];

/** Memory items shown in the right panel — scoped, visible, revocable. */
export const MEMORY_ITEMS: { id: string; text: string; scope: string }[] = [
  { id: "m1", text: "Prefers concise, citation-first answers.", scope: "Global" },
  { id: "m2", text: "Primary repo is a uv/pnpm monorepo.", scope: "Coding" },
  { id: "m3", text: "Studying for a cell-biology exam in three weeks.", scope: "Education" },
];

export function greeting(hour: number): string {
  if (hour < 5) return "Still up";
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  if (hour < 22) return "Good evening";
  return "Good night";
}
