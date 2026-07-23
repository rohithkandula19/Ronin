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
    request<WorldsResponse>(`/api/v1/worlds?include_disabled=${includeDisabled}`),
  getWorld: (id: string) => request<World>(`/api/v1/worlds/${id}`),
  listModels: () => request<{ models: ModelInfo[] }>(`/api/v1/models`),
  listAdapters: (industry?: string) =>
    request<{ adapters: AdapterInfo[] }>(
      `/api/v1/adapters${industry ? `?industry=${encodeURIComponent(industry)}` : ""}`,
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
