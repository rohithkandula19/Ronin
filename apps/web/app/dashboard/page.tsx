"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, BriefingOut, ConnectionOut, clearToken, getToken } from "@/lib/api";

const SERVICES = [
  { id: "anthropic",   label: "Anthropic API Key",     hint: "sk-ant-..." },
  { id: "stripe",      label: "Stripe API Key",        hint: "rk_live_... (use a Restricted Key, read-only)" },
  { id: "linear",      label: "Linear API Key",        hint: "lin_api_..." },
  { id: "slack_bot",   label: "Slack Bot Token",       hint: "xoxb-... (scope: chat:write, channels:read)" },
  { id: "notion",      label: "Notion Integration",    hint: "secret_..." },
  { id: "database_url",label: "Postgres URL",          hint: "postgres://readonly_user:...@host:5432/db" },
];

export default function DashboardPage() {
  const router = useRouter();
  const [connections, setConnections] = useState<ConnectionOut[]>([]);
  const [briefings, setBriefings] = useState<BriefingOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) { router.replace("/signin"); return; }
    Promise.all([api.listConnections(), api.listBriefings()])
      .then(([c, b]) => { setConnections(c); setBriefings(b); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [router]);

  async function runNow() {
    setRunning(true);
    setError(null);
    try {
      const b = await api.runBriefing();
      setBriefings((prev) => [b, ...prev]);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }

  function signOut() { clearToken(); router.push("/"); }

  if (loading) return <main className="p-8 text-dim">Loading…</main>;

  const connectedSet = new Set(connections.map((c) => c.service));

  return (
    <main className="min-h-screen">
      <header className="border-b border-border bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <span className="font-bold text-lg">Ronin</span>
          <div className="flex items-center gap-3">
            <button onClick={runNow} disabled={running} className="btn btn-primary text-sm">
              {running ? "Running…" : "Run briefing now"}
            </button>
            <button onClick={signOut} className="btn btn-secondary text-sm">Sign out</button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-6 py-10 space-y-10">
        {error && <div className="card border-red-200 bg-red-50 text-red-800 text-sm">{error}</div>}

        <section>
          <h2 className="text-lg font-semibold mb-4">Connections</h2>
          <div className="grid gap-4 md:grid-cols-2">
            {SERVICES.map((s) => (
              <ConnectionCard
                key={s.id}
                service={s.id}
                label={s.label}
                hint={s.hint}
                connected={connectedSet.has(s.id)}
                onSaved={() => api.listConnections().then(setConnections)}
              />
            ))}
          </div>
        </section>

        <section>
          <h2 className="text-lg font-semibold mb-4">Recent briefings</h2>
          {briefings.length === 0 ? (
            <div className="card text-dim text-sm">
              No briefings yet. Connect at least one integration above, then click <b>Run briefing now</b>.
            </div>
          ) : (
            <div className="space-y-4">
              {briefings.map((b) => (
                <div key={b.id} className="card">
                  <div className="flex justify-between items-center mb-3 text-sm text-dim">
                    <span>{new Date(b.created_at).toLocaleString()}</span>
                    <span>
                      MRR <b className="text-ink">${(b.mrr_cents / 100).toFixed(0)}</b> ·
                      active <b className="text-ink">{b.active_subs}</b> ·
                      failed <b className="text-ink">{b.failed_charges_7d}</b>
                    </span>
                  </div>
                  <pre className="font-mono text-xs whitespace-pre-wrap leading-relaxed">{b.markdown}</pre>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

function ConnectionCard({
  service, label, hint, connected, onSaved,
}: {
  service: string; label: string; hint: string; connected: boolean; onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    if (!secret.trim()) return;
    setSaving(true);
    setErr(null);
    try {
      await api.addConnection(service, secret.trim());
      setSecret("");
      setEditing(false);
      onSaved();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-1">
        <div className="font-semibold">{label}</div>
        {connected
          ? <span className="text-xs bg-green-100 text-green-800 rounded-full px-2 py-0.5">connected</span>
          : <span className="text-xs bg-stone-100 text-dim rounded-full px-2 py-0.5">not connected</span>}
      </div>
      <p className="text-xs text-dim mb-3 font-mono">{hint}</p>

      {editing ? (
        <div className="space-y-2">
          <input
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            placeholder="paste credential"
            className="input"
            autoFocus
          />
          {err && <div className="text-red-700 text-xs">{err}</div>}
          <div className="flex gap-2">
            <button onClick={save} disabled={saving} className="btn btn-primary text-sm flex-1">
              {saving ? "Saving…" : "Save"}
            </button>
            <button onClick={() => { setEditing(false); setSecret(""); setErr(null); }} className="btn btn-secondary text-sm">
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button onClick={() => setEditing(true)} className="btn btn-secondary text-sm w-full">
          {connected ? "Update key" : "Connect"}
        </button>
      )}
    </div>
  );
}
