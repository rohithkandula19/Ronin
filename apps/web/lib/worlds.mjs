// Pure, dependency-free logic for the World Navigator.
// Kept in plain ESM so it runs under `node --test` with zero install, and is
// imported by the React page (app/worlds/page.tsx). No DOM, no fetch here.

/** Risk badge descriptor for a world's risk_level. */
export function riskBadge(riskLevel) {
  switch (riskLevel) {
    case "high":
      return { label: "High risk", tone: "danger" };
    case "medium":
      return { label: "Medium risk", tone: "warn" };
    case "low":
      return { label: "Low risk", tone: "ok" };
    default:
      return { label: "Unknown", tone: "neutral" };
  }
}

/** Is this world enterable right now? */
export function isEnterable(world) {
  return world.status !== "disabled";
}

/** Short model-strategy summary from a world's fields. */
export function modelStrategy(world) {
  if (world.default_adapter) return `Fine-tuned adapter: ${world.default_adapter}`;
  return "Base model + retrieval";
}

/**
 * Filter worlds by a free-text query (matches id, name, description, roles)
 * and optional flags. Returns a new array; never mutates input.
 */
export function filterWorlds(worlds, { query = "", onlyEnabled = false } = {}) {
  const q = query.trim().toLowerCase();
  return worlds.filter((w) => {
    if (onlyEnabled && !isEnterable(w)) return false;
    if (!q) return true;
    const haystack = [
      w.id,
      w.name,
      w.description,
      ...(w.roles || []),
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(q);
  });
}

/**
 * Sort worlds for display: enabled first, then by risk (low→high so the
 * safest are surfaced first within each group), then alphabetically.
 */
export function sortWorlds(worlds) {
  const riskOrder = { low: 0, medium: 1, high: 2, unknown: 3 };
  return [...worlds].sort((a, b) => {
    const ea = isEnterable(a) ? 0 : 1;
    const eb = isEnterable(b) ? 0 : 1;
    if (ea !== eb) return ea - eb;
    const ra = riskOrder[a.risk_level] ?? 3;
    const rb = riskOrder[b.risk_level] ?? 3;
    if (ra !== rb) return ra - rb;
    return a.name.localeCompare(b.name);
  });
}

/** The safety limitations line a high-risk world must surface up front. */
export function safetyNote(world) {
  if (!world.blocked_capabilities || world.blocked_capabilities.length === 0) {
    return "";
  }
  return `This world cannot: ${world.blocked_capabilities.join(", ")}.`;
}
