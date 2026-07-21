import Link from "next/link";

export default function LandingPage() {
  return (
    <main className="min-h-screen">
      <header className="border-b border-border bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <span className="font-bold text-lg tracking-tight">Ronin</span>
          <nav className="flex items-center gap-3">
            <a href="https://github.com/rohithkandula19/Ronin" className="text-sm text-dim hover:text-ink">GitHub</a>
            <Link href="/signin" className="btn btn-primary text-sm">Sign in</Link>
          </nav>
        </div>
      </header>

      <section className="mx-auto max-w-3xl px-6 pt-20 pb-12 text-center">
        <h1 className="text-5xl font-bold tracking-tight leading-tight">
          The Monday-morning founder briefing,<br />
          <span className="text-accent">in one click.</span>
        </h1>
        <p className="mt-6 text-lg text-dim">
          Connect your Stripe and Linear. Get a weekly report on revenue, churn, payment failures, and your most urgent
          engineering issues. Auto-posted to Slack on Mondays at 9am.
        </p>
        <div className="mt-10 flex justify-center gap-3">
          <Link href="/signin" className="btn btn-primary text-base">Start free</Link>
          <a href="#how" className="btn btn-secondary text-base">See it work</a>
        </div>
        <p className="mt-3 text-xs text-dim">No credit card. Read-only access to your data.</p>
      </section>

      <section id="how" className="mx-auto max-w-5xl px-6 py-16">
        <h2 className="text-2xl font-bold mb-8">What you get every Monday at 9am</h2>
        <div className="card font-mono text-sm whitespace-pre-wrap">{`# Founder briefing — 2026-05-11

## 💰 Revenue
- MRR: $4,230 (ARR ~$50,760) · vs last week: +$340
- New this week: 2 · Churned: 1 — ⚠️  Pro customer, $588 ARR loss

## 💳 Payments (last 7 days)
- 28 succeeded · 2 failed · 1 refunded
- Failed charges to retry: cus_xxx ($49) — card_declined
- ⚠️  1 subscription past due

## 🛠 Engineering
- Urgent open: 2 · High open: 5 · In-progress: 3
- ENG-101 Stripe webhook flake — Alice, In Progress

## ✅ Suggested action items
- Reach out to recently churned customers for exit interviews
- Retry failed payments / dunning for past-due subs
- Unblock or escalate every Urgent issue`}</div>
      </section>

      <section className="mx-auto max-w-5xl px-6 py-16">
        <h2 className="text-2xl font-bold mb-8">How it works</h2>
        <div className="grid gap-6 md:grid-cols-3">
          {[
            { n: "1", h: "Connect", b: "Sign in, paste a Stripe Restricted Key (read-only) and a Linear API key. Encrypted at rest." },
            { n: "2", h: "Schedule", b: "Pick a day and time. Default: Mondays 9am your timezone." },
            { n: "3", h: "Inbox", b: "Briefing lands in your chosen Slack channel — or run it on demand any time." },
          ].map((s) => (
            <div key={s.n} className="card">
              <div className="text-accent font-bold mb-2">Step {s.n}</div>
              <div className="font-semibold mb-1">{s.h}</div>
              <p className="text-sm text-dim">{s.b}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="mx-auto max-w-5xl px-6 py-16">
        <h2 className="text-2xl font-bold mb-8">Pricing</h2>
        <div className="grid gap-6 md:grid-cols-3">
          {[
            { plan: "Free", price: "$0", features: ["1 weekly briefing", "Stripe + Linear", "Email or Slack delivery"] },
            { plan: "Pro", price: "$19/mo", features: ["Unlimited briefings", "All integrations", "Custom prompts", "Week-over-week history"], primary: true },
            { plan: "Team", price: "$99/mo", features: ["Everything in Pro", "Multiple workspaces", "Up to 10 seats", "Priority support"] },
          ].map((t) => (
            <div key={t.plan} className={`card ${t.primary ? "ring-2 ring-accent" : ""}`}>
              <div className="text-sm text-dim">{t.plan}</div>
              <div className="text-3xl font-bold mt-1">{t.price}</div>
              <ul className="mt-4 space-y-1 text-sm">
                {t.features.map((f) => <li key={f}>· {f}</li>)}
              </ul>
              <Link href="/signin" className={`btn ${t.primary ? "btn-primary" : "btn-secondary"} mt-5 w-full`}>Get started</Link>
            </div>
          ))}
        </div>
      </section>

      <footer className="mt-10 border-t border-border py-8 text-center text-sm text-dim">
        Open source · MIT · Built for Claude · <a href="https://github.com/rohithkandula19/Ronin" className="underline hover:text-ink">GitHub</a>
      </footer>
    </main>
  );
}
