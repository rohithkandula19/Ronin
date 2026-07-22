// Pure, dependency-free logic for the Vault (memory) UI.
// Plain ESM so it runs under `node --test` with zero install, and is imported
// by the React page (app/vault/page.tsx). No DOM, no fetch, no React here.
//
// This module is the testable core. The page renders from the DEMO_* fixtures
// exported below (clearly labeled demo data); live data comes from /api/v1 at
// runtime. Nothing here performs external actions — a cross-industry transfer
// only ever produces a *preview* that a human must explicitly confirm.

// ---------------------------------------------------------------------------
// Scopes
// ---------------------------------------------------------------------------

/** Canonical memory scopes, ordered from most ephemeral to broadest. */
export const SCOPES = [
  "session",
  "conversation",
  "project",
  "user",
  "organization",
  "industry",
  "temporary",
];

const SCOPE_LABELS = {
  session: "Session",
  conversation: "Conversation",
  project: "Project",
  user: "User",
  organization: "Organization",
  industry: "Industry",
  temporary: "Temporary",
};

/** Human label for a scope; unknown scopes echo back capitalized-ish. */
export function scopeLabel(scope) {
  return SCOPE_LABELS[scope] || String(scope || "Unknown");
}

/**
 * Group memory items by scope. Returns an object keyed by every canonical
 * scope (empty arrays included so the UI can render stable sections) plus an
 * `_other` bucket for any unrecognized scope. Never mutates input.
 */
export function groupByScope(items) {
  const list = Array.isArray(items) ? items : [];
  const groups = {};
  for (const s of SCOPES) groups[s] = [];
  groups._other = [];
  for (const item of list) {
    const s = item && item.scope;
    if (Object.prototype.hasOwnProperty.call(groups, s) && s !== "_other") {
      groups[s].push(item);
    } else {
      groups._other.push(item);
    }
  }
  return groups;
}

// ---------------------------------------------------------------------------
// Training eligibility — fail closed
// ---------------------------------------------------------------------------

/** Sensitivity tags that categorically block training use. */
export const BLOCKING_SENSITIVITY = ["health", "minor", "credential", "sensitive"];

/**
 * Is this memory item eligible to be used as training data?
 * Fail-closed: anything health/minor/credential/sensitive is NEVER eligible,
 * regardless of other flags. An item must additionally opt in via
 * `training_opt_in === true`. Missing/unknown data → not eligible.
 */
export function isTrainingEligible(item) {
  if (!item || typeof item !== "object") return false;
  const sensitivity = item.sensitivity;
  if (BLOCKING_SENSITIVITY.includes(sensitivity)) return false;
  if (Array.isArray(item.tags)) {
    for (const t of item.tags) {
      if (BLOCKING_SENSITIVITY.includes(t)) return false;
    }
  }
  // Explicit opt-in required; default is off.
  return item.training_opt_in === true;
}

// ---------------------------------------------------------------------------
// Cross-industry isolation
// ---------------------------------------------------------------------------

/** Industry of a world descriptor (or the world id itself if a string). */
function worldIndustry(world) {
  if (!world) return null;
  if (typeof world === "string") return world;
  return world.industry ?? world.id ?? null;
}

/**
 * Can this item auto-surface (be silently recalled) inside targetWorld?
 * Fail-closed across industry boundaries: an item bound to one industry must
 * NEVER auto-surface in a different industry. Items marked `portable` (e.g.
 * generic user preferences) may cross; industry-scoped items may not.
 */
export function canAutoSurface(item, targetWorld) {
  if (!item || !targetWorld) return false;
  const target = worldIndustry(targetWorld);
  const origin = item.industry ?? null;
  // No origin industry + portable → safe generic memory.
  if (origin === null) return item.portable === true;
  if (origin === target) return true;
  // Different industry: only if explicitly portable AND not sensitive.
  if (item.portable === true && !BLOCKING_SENSITIVITY.includes(item.sensitivity)) {
    return true;
  }
  return false;
}

/**
 * Preview a cross-world (potentially cross-industry) memory transfer.
 * Pure and side-effect free — describes what WOULD move and what is blocked by
 * isolation, so a human can review before explicitly confirming. Never mutates.
 */
export function transferPreview(items, fromWorld, toWorld) {
  const list = Array.isArray(items) ? items : [];
  const from = worldIndustry(fromWorld);
  const to = worldIndustry(toWorld);
  const crossIndustry = from !== null && to !== null && from !== to;
  const moves = [];
  const blocked = [];
  for (const item of list) {
    const reasons = [];
    if (crossIndustry) {
      if (BLOCKING_SENSITIVITY.includes(item && item.sensitivity)) {
        reasons.push(`sensitivity:${item.sensitivity}`);
      }
      if (item && item.portable !== true) {
        reasons.push("not-portable across industries");
      }
    }
    if (reasons.length > 0) {
      blocked.push({ item, reasons });
    } else {
      moves.push(item);
    }
  }
  return {
    fromWorld: from,
    toWorld: to,
    crossIndustry,
    // A transfer is never automatic — the UI requires an explicit action.
    requiresExplicitAction: true,
    moves,
    blocked,
    summary: `${moves.length} item(s) would transfer, ${blocked.length} blocked by isolation`,
  };
}

// ---------------------------------------------------------------------------
// Retention labels
// ---------------------------------------------------------------------------

/**
 * Human-readable retention label from a policy descriptor.
 * Accepts { kind: "forever" | "until_deleted" | "ttl", days?: number } or a
 * shorthand string. Fail-safe: unknown → "Unknown retention".
 */
export function retentionLabel(policy) {
  if (!policy) return "Unknown retention";
  if (typeof policy === "string") return retentionLabel({ kind: policy });
  switch (policy.kind) {
    case "forever":
      return "Kept indefinitely";
    case "until_deleted":
      return "Kept until you delete it";
    case "session":
      return "Discarded at end of session";
    case "ttl": {
      const days = Number(policy.days);
      if (!Number.isFinite(days) || days <= 0) return "Unknown retention";
      if (days === 1) return "Expires in 1 day";
      return `Expires in ${days} days`;
    }
    default:
      return "Unknown retention";
  }
}

// ---------------------------------------------------------------------------
// Demo fixtures (clearly labeled — NOT live data)
// ---------------------------------------------------------------------------

export const DEMO_MEMORY_ITEMS = [
  {
    id: "mem_pref_tone",
    scope: "user",
    summary: "Prefers concise, direct answers without preamble",
    owner: "you@example.com",
    source: "Learned from conversation feedback",
    created_at: "2026-05-01T09:12:00Z",
    updated_at: "2026-07-10T14:03:00Z",
    confidence: 0.92,
    sensitivity: "normal",
    retention: { kind: "until_deleted" },
    training_opt_in: true,
    visibility: "private",
    industry: null,
    portable: true,
    tags: ["preference"],
  },
  {
    id: "mem_proj_stack",
    scope: "project",
    summary: "Project uses Next.js 16, React 19, Tailwind v4",
    owner: "you@example.com",
    source: "Extracted from repository README",
    created_at: "2026-06-20T11:00:00Z",
    updated_at: "2026-06-20T11:00:00Z",
    confidence: 0.98,
    sensitivity: "normal",
    retention: { kind: "until_deleted" },
    training_opt_in: false,
    visibility: "project",
    industry: "software",
    portable: false,
    tags: ["tech-stack"],
  },
  {
    id: "mem_health_note",
    scope: "user",
    summary: "Manages a peanut allergy — avoid recipe suggestions with nuts",
    owner: "you@example.com",
    source: "Stated by you in Healthcare world",
    created_at: "2026-04-15T08:30:00Z",
    updated_at: "2026-04-15T08:30:00Z",
    confidence: 0.99,
    sensitivity: "health",
    retention: { kind: "until_deleted" },
    training_opt_in: false,
    visibility: "private",
    industry: "healthcare",
    portable: false,
    tags: ["health", "allergy"],
  },
  {
    id: "mem_org_policy",
    scope: "organization",
    summary: "All external emails require manager approval",
    owner: "acme-corp",
    source: "Organization policy import",
    created_at: "2026-03-02T16:45:00Z",
    updated_at: "2026-07-01T10:15:00Z",
    confidence: 0.9,
    sensitivity: "normal",
    retention: { kind: "forever" },
    training_opt_in: false,
    visibility: "organization",
    industry: "software",
    portable: false,
    tags: ["policy"],
  },
  {
    id: "mem_session_scratch",
    scope: "session",
    summary: "Currently comparing two vendor quotes",
    owner: "you@example.com",
    source: "Current session context",
    created_at: "2026-07-22T13:00:00Z",
    updated_at: "2026-07-22T13:20:00Z",
    confidence: 0.6,
    sensitivity: "normal",
    retention: { kind: "session" },
    training_opt_in: false,
    visibility: "private",
    industry: null,
    portable: false,
    tags: [],
  },
  {
    id: "mem_cred_token",
    scope: "temporary",
    summary: "Redacted API credential referenced once (not stored in clear)",
    owner: "you@example.com",
    source: "Detected in pasted text — redacted",
    created_at: "2026-07-18T12:00:00Z",
    updated_at: "2026-07-18T12:00:00Z",
    confidence: 0.5,
    sensitivity: "credential",
    retention: { kind: "ttl", days: 1 },
    training_opt_in: false,
    visibility: "private",
    industry: null,
    portable: false,
    tags: ["credential"],
  },
];

export const DEMO_WORLDS = {
  software: { id: "coding", industry: "software", name: "Coding" },
  healthcare: { id: "healthcare", industry: "healthcare", name: "Healthcare Information" },
};
