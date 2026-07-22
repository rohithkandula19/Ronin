// Frontend logic tests — run with `node --test` (no install required).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  filterWorlds,
  isEnterable,
  modelStrategy,
  riskBadge,
  safetyNote,
  sortWorlds,
} from "./worlds.mjs";

const WORLDS = [
  {
    id: "coding",
    name: "Coding",
    description: "Software engineering workspace on the real Ronin agent",
    status: "beta",
    risk_level: "medium",
    roles: ["developer", "reviewer"],
    default_adapter: null,
    blocked_capabilities: ["embedding"],
  },
  {
    id: "healthcare",
    name: "Healthcare Information",
    description: "Educational healthcare information",
    status: "beta",
    risk_level: "high",
    roles: ["patient", "clinician"],
    default_adapter: "healthcare-en-us-v1",
    blocked_capabilities: ["autonomous_diagnosis", "prescription_change"],
  },
  {
    id: "finance",
    name: "Finance",
    description: "Planned finance world",
    status: "disabled",
    risk_level: "high",
    roles: ["member"],
    default_adapter: null,
    blocked_capabilities: ["tool_use"],
  },
];

test("riskBadge maps levels to tones", () => {
  assert.equal(riskBadge("high").tone, "danger");
  assert.equal(riskBadge("low").tone, "ok");
  assert.equal(riskBadge("bogus").tone, "neutral");
});

test("isEnterable is false only for disabled worlds", () => {
  assert.equal(isEnterable(WORLDS[0]), true);
  assert.equal(isEnterable(WORLDS[2]), false);
});

test("modelStrategy reflects adapter presence", () => {
  assert.match(modelStrategy(WORLDS[1]), /healthcare-en-us-v1/);
  assert.match(modelStrategy(WORLDS[0]), /Base model/);
});

test("filterWorlds matches id, name, description, roles", () => {
  assert.deepEqual(filterWorlds(WORLDS, { query: "clinician" }).map((w) => w.id), ["healthcare"]);
  assert.deepEqual(filterWorlds(WORLDS, { query: "engineering" }).map((w) => w.id), ["coding"]);
  assert.equal(filterWorlds(WORLDS, { query: "" }).length, 3);
});

test("filterWorlds onlyEnabled drops disabled worlds", () => {
  const ids = filterWorlds(WORLDS, { onlyEnabled: true }).map((w) => w.id);
  assert.ok(!ids.includes("finance"));
});

test("filterWorlds does not mutate input", () => {
  const before = WORLDS.length;
  filterWorlds(WORLDS, { query: "coding" });
  assert.equal(WORLDS.length, before);
});

test("sortWorlds puts enabled first, then by risk, then alpha", () => {
  const ids = sortWorlds(WORLDS).map((w) => w.id);
  // coding (enabled, medium) before healthcare (enabled, high) before finance (disabled)
  assert.deepEqual(ids, ["coding", "healthcare", "finance"]);
});

test("safetyNote surfaces blocked capabilities for high-risk worlds", () => {
  const note = safetyNote(WORLDS[1]);
  assert.match(note, /autonomous_diagnosis/);
  assert.match(note, /prescription_change/);
});
