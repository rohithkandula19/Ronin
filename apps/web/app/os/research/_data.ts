/**
 * Demo content for the Research world. Offline sample: it shows HOW Ronin
 * researches — source-first, every claim tied to a source or explicitly
 * labelled as model inference, citation URLs never fabricated — without
 * passing illustrative sources off as real. Each source is tagged `sample`;
 * when a backend is connected these become real, fetched, URL-verified sources.
 */

export type SourceType = "paper" | "article" | "docs" | "forum" | "dataset";
export type Reliability = "high" | "medium" | "low";
export type SourceStatus = "indexed" | "queued";

export interface Source {
  id: string;
  n: number; // citation number
  title: string;
  domain: string;
  type: SourceType;
  reliability: Reliability;
  status: SourceStatus;
  note: string;
}

export const QUESTION = "How are engineering teams adopting local-first and provider-agnostic AI infrastructure?";
export const RESEARCH_STATUS: "synthesized" | "researching" = "synthesized";

export const SOURCES: Source[] = [
  { id: "s1", n: 1, title: "Local-first software: you own your data, in spite of the cloud", domain: "inkandswitch.com", type: "article", reliability: "high", status: "indexed", note: "Foundational essay on the local-first principles." },
  { id: "s2", n: 2, title: "State of AI Engineering — infrastructure survey", domain: "sample-survey.org", type: "dataset", reliability: "medium", status: "indexed", note: "Aggregated responses; self-reported." },
  { id: "s3", n: 3, title: "Running open-weight models on-device: latency & cost", domain: "sample-eng-blog.dev", type: "article", reliability: "medium", status: "indexed", note: "Single-team benchmark; hardware-specific." },
  { id: "s4", n: 4, title: "Provider abstraction layers in production LLM apps", domain: "sample-proceedings.ai", type: "paper", reliability: "high", status: "indexed", note: "Peer-reviewed; small sample of case studies." },
  { id: "s5", n: 5, title: "Thread: why we moved inference in-house", domain: "sample-forum.dev", type: "forum", reliability: "low", status: "queued", note: "Anecdotal; not yet indexed." },
];

/** A synthesis paragraph is a sequence of text runs and inline citations. */
export type Run = { text: string } | { cite: number[] };
export interface Para {
  runs: Run[];
}

export const SYNTHESIS: Para[] = [
  {
    runs: [
      { text: "Adoption of local-first AI infrastructure is driven less by cost than by ownership and control: teams want their data and guardrails to stay on their own machines rather than transit a vendor's cloud" },
      { cite: [1] },
      { text: ". Survey data suggests this is a growing minority position rather than the mainstream default" },
      { cite: [2] },
      { text: "." },
    ],
  },
  {
    runs: [
      { text: "On the technical side, running open-weight models on-device is now viable for many tasks, though latency and quality remain hardware-bound and uneven across workloads" },
      { cite: [3] },
      { text: ". Teams that need both local and hosted models increasingly place a provider-abstraction layer between their app and any single vendor, so they can route by cost, capability, or privacy" },
      { cite: [4] },
      { text: "." },
    ],
  },
  {
    runs: [
      { text: "Reported motivations for bringing inference in-house include predictable spend, data residency, and avoiding lock-in — but these accounts are largely anecdotal and should be weighted accordingly" },
      { cite: [5] },
      { text: "." },
    ],
  },
];

export type Support =
  | { kind: "sourced"; sourceIds: string[] }
  | { kind: "mixed"; sourceIds: string[] }
  | { kind: "inference" };

export interface Claim {
  text: string;
  support: Support;
}

export const CLAIMS: Claim[] = [
  { text: "Ownership and control, not cost, are the primary drivers of local-first adoption.", support: { kind: "sourced", sourceIds: ["s1", "s2"] } },
  { text: "On-device open-weight inference is viable for many tasks but remains hardware-bound.", support: { kind: "sourced", sourceIds: ["s3"] } },
  { text: "Provider-abstraction layers are becoming a common pattern for teams spanning local + hosted models.", support: { kind: "mixed", sourceIds: ["s4"] } },
  { text: "This pattern will likely become a default for privacy-sensitive industries within a few years.", support: { kind: "inference" } },
];

export const SUGGESTED_FOLLOWUPS = [
  "What hardware makes on-device inference practical today?",
  "Compare provider-abstraction approaches by routing strategy.",
  "Which regulated industries lead local-first adoption?",
];
