// Typed client for the apps/api FastAPI backend.
// Auth: api_token is returned at /signup, stored in localStorage, attached as Bearer.

const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const TOKEN_KEY = "csk.api_token";

export interface SignupResponse {
  id: number;
  email: string;
  api_token: string;
}

export interface ConnectionOut {
  service: string;
}

export interface BriefingOut {
  id: number;
  markdown: string;
  mrr_cents: number;
  active_subs: number;
  failed_charges_7d: number;
  created_at: string;
}

export interface ScheduleOut {
  cron: string;
  slack_channel: string;
  enabled: boolean;
  last_run_at: string | null;
}

export function setToken(token: string) {
  if (typeof window !== "undefined") localStorage.setItem(TOKEN_KEY, token);
}
export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}
export function clearToken() {
  if (typeof window !== "undefined") localStorage.removeItem(TOKEN_KEY);
}

/**
 * Base for Ronin OS `/api/v1` calls.
 * - `NEXT_PUBLIC_API_URL` set → call the backend directly (the backend's
 *   `CORS_ORIGINS` must include this web origin).
 * - unset → call same-origin `/api/v1/...` and let the Next proxy
 *   (`RONIN_API_ORIGIN`, see next.config.js) forward it — no CORS needed.
 * With neither a direct URL nor a proxy configured, these calls simply fail
 * and the UI falls back to its labelled offline sample.
 */
const AIOS_BASE = process.env.NEXT_PUBLIC_API_URL || "";

async function aiosRequest<T>(path: string): Promise<T> {
  const headers = new Headers({ "Content-Type": "application/json" });
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${AIOS_BASE}${path}`, { headers });
  if (!res.ok) {
    let detail: string;
    try { detail = (await res.json()).detail ?? res.statusText; }
    catch { detail = res.statusText; }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    let detail: string;
    try { detail = (await res.json()).detail ?? res.statusText; }
    catch { detail = res.statusText; }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  signup: (email: string) =>
    request<SignupResponse>("/signup", { method: "POST", body: JSON.stringify({ email }) }),

  listConnections: () => request<ConnectionOut[]>("/connections"),
  addConnection: (service: string, secret: string) =>
    request<ConnectionOut>("/connections", {
      method: "POST",
      body: JSON.stringify({ service, secret }),
    }),

  runBriefing: () => request<BriefingOut>("/briefings", { method: "POST" }),
  listBriefings: (limit = 25) => request<BriefingOut[]>(`/briefings?limit=${limit}`),

  setSchedule: (body: { cron?: string; slack_channel?: string; enabled?: boolean }) =>
    request<ScheduleOut>("/briefings/schedule", { method: "POST", body: JSON.stringify(body) }),
};

// ---------- Ronin AI OS v1 ----------

export interface World {
  id: string;
  name: string;
  description: string;
  status: string;
  risk_level: string;
  roles: string[];
  countries: string[];
  languages: string[];
  default_adapter: string | null;
  allowed_capabilities: string[];
  allowed_tools: string[];
  blocked_capabilities: string[];
}

export interface WorldsResponse {
  worlds: World[];
  discovery_errors: Record<string, string>;
}

export interface ModelInfo {
  id: string;
  provider?: string;
  display_name?: string;
  name?: string;
  capabilities?: string[];
  [k: string]: unknown;
}

export interface AdapterInfo {
  id: string;
  industry?: string;
  base_model?: string;
  [k: string]: unknown;
}

export const aios = {
  // World Navigator data. Read-only, no auth required to browse.
  listWorlds: (includeDisabled = true) =>
    aiosRequest<WorldsResponse>(`/api/v1/worlds?include_disabled=${includeDisabled}`),
  getWorld: (id: string) => aiosRequest<World>(`/api/v1/worlds/${id}`),
  listModels: () => aiosRequest<{ models: ModelInfo[] }>(`/api/v1/models`),
  listAdapters: (industry?: string) =>
    aiosRequest<{ adapters: AdapterInfo[] }>(
      `/api/v1/adapters${industry ? `?industry=${encodeURIComponent(industry)}` : ""}`,
    ),
  // Healthcare safety content, straight from the backend. Read-only, no auth.
  // `disclosures` is a map of {key: text}; callers use Object.values().
  healthcareLimitations: () =>
    aiosRequest<{ disclosures: Record<string, string>; blocked: string[] }>(
      `/api/v1/healthcare/limitations`,
    ),
};

/** Race a promise against a timeout; used so an unreachable API degrades to
 *  the offline sample quickly instead of hanging the UI. */
export function withTimeout<T>(p: Promise<T>, ms = 3500): Promise<T> {
  return Promise.race([
    p,
    new Promise<T>((_, reject) => setTimeout(() => reject(new Error("timeout")), ms)),
  ]);
}
