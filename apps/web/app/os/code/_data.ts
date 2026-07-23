/**
 * Demo repository + agent state for Ronin Code. Offline sample content: it
 * shows the *experience* of the coding world (plan-first, approval-gated,
 * sandbox-by-default) without claiming any real code ran on your machine.
 * When a backend is connected this is replaced by a real workspace read.
 */

export type GitStatus = "" | "M" | "A" | "U";

export interface FileNode {
  name: string;
  path: string;
  kind: "file" | "dir";
  status?: GitStatus;
  children?: FileNode[];
  lang?: "ts" | "python" | "md" | "json";
}

export const REPO_NAME = "acme/public-api";

export const FILE_TREE: FileNode[] = [
  {
    name: "src",
    path: "src",
    kind: "dir",
    children: [
      {
        name: "server",
        path: "src/server",
        kind: "dir",
        children: [
          { name: "app.ts", path: "src/server/app.ts", kind: "file", lang: "ts" },
          { name: "middleware.ts", path: "src/server/middleware.ts", kind: "file", lang: "ts", status: "M" },
        ],
      },
      {
        name: "lib",
        path: "src/lib",
        kind: "dir",
        children: [
          { name: "rateLimit.ts", path: "src/lib/rateLimit.ts", kind: "file", lang: "ts", status: "A" },
        ],
      },
    ],
  },
  {
    name: "tests",
    path: "tests",
    kind: "dir",
    children: [
      { name: "rateLimit.test.ts", path: "tests/rateLimit.test.ts", kind: "file", lang: "ts", status: "M" },
    ],
  },
  { name: "package.json", path: "package.json", kind: "file", lang: "json" },
  { name: "README.md", path: "README.md", kind: "file", lang: "md" },
];

export interface OpenFile {
  path: string;
  lang: "ts" | "python" | "md" | "json";
  content: string;
  /** When present, the file has a proposed change the agent wants to apply. */
  diff?: { before: string; after: string };
}

const MIDDLEWARE_BEFORE = `import type { Request, Response, NextFunction } from "express";

export function withRequestId(req: Request, res: Response, next: NextFunction) {
  const id = req.header("x-request-id") ?? crypto.randomUUID();
  res.setHeader("x-request-id", id);
  next();
}

export function withSecurityHeaders(_req: Request, res: Response, next: NextFunction) {
  res.setHeader("x-content-type-options", "nosniff");
  res.setHeader("referrer-policy", "no-referrer");
  next();
}
`;

const MIDDLEWARE_AFTER = `import type { Request, Response, NextFunction } from "express";
import { rateLimit } from "../lib/rateLimit";

export function withRequestId(req: Request, res: Response, next: NextFunction) {
  const id = req.header("x-request-id") ?? crypto.randomUUID();
  res.setHeader("x-request-id", id);
  next();
}

export function withSecurityHeaders(_req: Request, res: Response, next: NextFunction) {
  res.setHeader("x-content-type-options", "nosniff");
  res.setHeader("referrer-policy", "no-referrer");
  next();
}

// Emits X-RateLimit-* headers so clients can back off before they are throttled.
export function withRateLimitHeaders(req: Request, res: Response, next: NextFunction) {
  const { limit, remaining, reset } = rateLimit.peek(req.ip);
  res.setHeader("x-ratelimit-limit", String(limit));
  res.setHeader("x-ratelimit-remaining", String(remaining));
  res.setHeader("x-ratelimit-reset", String(reset));
  next();
}
`;

export const OPEN_FILES: OpenFile[] = [
  {
    path: "src/server/middleware.ts",
    lang: "ts",
    content: MIDDLEWARE_AFTER,
    diff: { before: MIDDLEWARE_BEFORE, after: MIDDLEWARE_AFTER },
  },
  {
    path: "src/lib/rateLimit.ts",
    lang: "ts",
    content: `interface Window { count: number; reset: number; }

const WINDOW_MS = 60_000;
const MAX = 100;

const windows = new Map<string, Window>();

export const rateLimit = {
  peek(ip: string) {
    const now = Date.now();
    const w = windows.get(ip);
    if (!w || now > w.reset) {
      return { limit: MAX, remaining: MAX, reset: now + WINDOW_MS };
    }
    return { limit: MAX, remaining: Math.max(0, MAX - w.count), reset: w.reset };
  },
};
`,
  },
];

/** Sandboxed test run — labelled as a sample, no real execution claimed. */
export const TEST_RUN = {
  command: "pnpm test -- rateLimit",
  status: "passed" as "passed" | "failed" | "running",
  duration: "1.2s",
  lines: [
    { t: "cmd", text: "$ pnpm test -- rateLimit" },
    { t: "dim", text: "ronin › sandbox › node 22 · isolated working copy" },
    { t: "ok", text: "✓ tests/rateLimit.test.ts (4)" },
    { t: "ok", text: "  ✓ emits x-ratelimit-limit header" },
    { t: "ok", text: "  ✓ decrements remaining on each request" },
    { t: "ok", text: "  ✓ resets after the window elapses" },
    { t: "ok", text: "  ✓ never reports negative remaining" },
    { t: "pass", text: "Test Files  1 passed (1)  ·  Tests  4 passed (4)  ·  1.2s" },
  ],
};

export const PROBLEMS = [
  { file: "src/server/middleware.ts", line: 22, sev: "info", msg: "New export `withRateLimitHeaders` is not yet wired in app.ts." },
];

export type Autonomy = "ask" | "sandbox" | "auto";
export const AUTONOMY_LEVELS: { id: Autonomy; label: string; detail: string }[] = [
  { id: "ask", label: "Ask first", detail: "Ronin proposes; you approve every step." },
  { id: "sandbox", label: "Run in sandbox", detail: "Edits & tests run on an isolated copy; applying to your tree still asks." },
  { id: "auto", label: "Auto (guarded)", detail: "Applies low-risk steps automatically; consequential ones still stop." },
];
