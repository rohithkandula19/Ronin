"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, setToken } from "@/lib/api";

export default function SignInPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function handle(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setSubmitting(true);
    try {
      const res = await api.signup(email);
      setToken(res.api_token);
      router.push("/dashboard");
    } catch (e: any) {
      setErr(e.message ?? "signup failed");
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen flex items-center justify-center px-6">
      <div className="card w-full max-w-md">
        <h1 className="text-2xl font-bold mb-1">Sign in to Ronin</h1>
        <p className="text-sm text-dim mb-6">
          Email-only. We send a one-time API token you can rotate any time. No password to remember.
        </p>

        <form onSubmit={handle} className="space-y-4">
          <div>
            <label className="text-xs text-dim font-medium uppercase tracking-wide">Email</label>
            <input
              type="email"
              required
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@startup.com"
              className="input mt-1"
            />
          </div>
          {err && <div className="text-red-700 text-sm">{err}</div>}
          <button type="submit" disabled={submitting} className="btn btn-primary w-full">
            {submitting ? "Setting up…" : "Continue"}
          </button>
        </form>

        <p className="text-xs text-dim mt-6">
          By signing up you agree this is an early MVP. Your data is read-only and encrypted at rest.
        </p>
      </div>
    </main>
  );
}
