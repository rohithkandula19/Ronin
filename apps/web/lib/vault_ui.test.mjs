// Vault (memory) UI logic tests — run with `node --test` (no install required).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BLOCKING_SENSITIVITY,
  DEMO_MEMORY_ITEMS,
  DEMO_WORLDS,
  SCOPES,
  canAutoSurface,
  groupByScope,
  isTrainingEligible,
  retentionLabel,
  scopeLabel,
  transferPreview,
} from "./vault_ui.mjs";

test("groupByScope buckets every canonical scope and preserves items", () => {
  const groups = groupByScope(DEMO_MEMORY_ITEMS);
  for (const s of SCOPES) assert.ok(Array.isArray(groups[s]), `${s} bucket exists`);
  assert.equal(groups.user.length, 2);
  assert.equal(groups.project.length, 1);
  assert.equal(groups.session.length, 1);
});

test("groupByScope sends unknown scopes to _other and never mutates input", () => {
  const before = DEMO_MEMORY_ITEMS.length;
  const groups = groupByScope([{ scope: "galaxy", id: "x" }]);
  assert.equal(groups._other.length, 1);
  assert.equal(DEMO_MEMORY_ITEMS.length, before);
});

test("groupByScope tolerates non-array input", () => {
  const groups = groupByScope(null);
  assert.equal(groups.user.length, 0);
});

test("isTrainingEligible fails closed for health/minor/credential/sensitive", () => {
  for (const s of BLOCKING_SENSITIVITY) {
    assert.equal(
      isTrainingEligible({ sensitivity: s, training_opt_in: true }),
      false,
      `sensitivity ${s} must never be eligible`
    );
  }
});

test("isTrainingEligible requires explicit opt-in", () => {
  assert.equal(isTrainingEligible({ sensitivity: "normal" }), false);
  assert.equal(isTrainingEligible({ sensitivity: "normal", training_opt_in: true }), true);
});

test("isTrainingEligible blocks via tags too, and handles junk input", () => {
  assert.equal(
    isTrainingEligible({ sensitivity: "normal", training_opt_in: true, tags: ["minor"] }),
    false
  );
  assert.equal(isTrainingEligible(null), false);
  assert.equal(isTrainingEligible(undefined), false);
});

test("canAutoSurface is false across industry boundaries", () => {
  const health = DEMO_MEMORY_ITEMS.find((i) => i.id === "mem_health_note");
  assert.equal(canAutoSurface(health, DEMO_WORLDS.software), false);
  assert.equal(canAutoSurface(health, DEMO_WORLDS.healthcare), true);
});

test("canAutoSurface lets portable generic prefs cross; sensitive never crosses", () => {
  const pref = DEMO_MEMORY_ITEMS.find((i) => i.id === "mem_pref_tone");
  assert.equal(canAutoSurface(pref, DEMO_WORLDS.software), true);
  assert.equal(canAutoSurface(pref, DEMO_WORLDS.healthcare), true);
  const cred = { industry: "software", portable: true, sensitivity: "credential" };
  assert.equal(canAutoSurface(cred, DEMO_WORLDS.healthcare), false);
});

test("transferPreview requires explicit action and blocks non-portable/sensitive cross-industry", () => {
  const preview = transferPreview(
    DEMO_MEMORY_ITEMS,
    DEMO_WORLDS.healthcare,
    DEMO_WORLDS.software
  );
  assert.equal(preview.requiresExplicitAction, true);
  assert.equal(preview.crossIndustry, true);
  // The health note must be blocked.
  const blockedIds = preview.blocked.map((b) => b.item.id);
  assert.ok(blockedIds.includes("mem_health_note"));
  // Portable generic pref should move.
  const moveIds = preview.moves.map((m) => m.id);
  assert.ok(moveIds.includes("mem_pref_tone"));
});

test("transferPreview within same industry moves everything (no isolation block)", () => {
  const preview = transferPreview(DEMO_MEMORY_ITEMS, DEMO_WORLDS.software, DEMO_WORLDS.software);
  assert.equal(preview.crossIndustry, false);
  assert.equal(preview.blocked.length, 0);
  assert.equal(preview.moves.length, DEMO_MEMORY_ITEMS.length);
});

test("retentionLabel renders each policy kind and fails safe", () => {
  assert.match(retentionLabel({ kind: "forever" }), /indefinitely/i);
  assert.match(retentionLabel({ kind: "until_deleted" }), /until you delete/i);
  assert.match(retentionLabel({ kind: "ttl", days: 30 }), /30 days/);
  assert.match(retentionLabel({ kind: "ttl", days: 1 }), /1 day/);
  assert.match(retentionLabel("session"), /session/i);
  assert.equal(retentionLabel({ kind: "ttl", days: 0 }), "Unknown retention");
  assert.equal(retentionLabel(null), "Unknown retention");
});

test("scopeLabel maps known scopes", () => {
  assert.equal(scopeLabel("organization"), "Organization");
  assert.equal(scopeLabel("weird"), "weird");
});
