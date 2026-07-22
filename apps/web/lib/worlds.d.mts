// Types for the dependency-free world-navigator logic in worlds.mjs.
import type { World } from "./api";

export interface RiskBadge {
  label: string;
  tone: "danger" | "warn" | "ok" | "neutral";
}

export function riskBadge(riskLevel: string): RiskBadge;
export function isEnterable(world: Pick<World, "status">): boolean;
export function modelStrategy(world: Pick<World, "default_adapter">): string;
export function filterWorlds(
  worlds: World[],
  opts?: { query?: string; onlyEnabled?: boolean }
): World[];
export function sortWorlds(worlds: World[]): World[];
export function safetyNote(world: Pick<World, "blocked_capabilities">): string;
