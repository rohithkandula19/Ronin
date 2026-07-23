import Link from "next/link";

/* ------------------------------------------------------------------ data */

const WORLDS = [
  {
    id: "coding",
    name: "Coding",
    href: "/coding",
    risk: "high",
    blurb:
      "A graphical software-engineering workspace over the real Ronin runtime — files, plan tracker, diffs, tests, approval gates.",
  },
  {
    id: "education",
    name: "Education",
    href: "/education",
    risk: "medium",
    blurb:
      "Role-aware tutoring, study plans, quizzes and flashcards — grounded in sources and integrity-safe by design.",
  },
  {
    id: "healthcare",
    name: "Healthcare",
    href: "/healthcare",
    risk: "high",
    blurb:
      "Educational, non-diagnostic health information. Every output carries sources, uncertainty and an emergency boundary.",
  },
];

const PRODUCTS = [
  {
    k: "Core",
    d: "One shared runtime — provider routing, agents, tools, approvals, memory and audit. The CLI and web use the same brain.",
  },
  {
    k: "Forge",
    d: "Datasets, fine-tuning bundles and evaluations. Provenance-tracked, consent-gated, and honest about what's trained vs generated.",
  },
  {
    k: "Vault",
    d: "Scoped memory with strict cross-industry isolation. Nothing is training-eligible without explicit, revocable consent.",
  },
  {
    k: "Research",
    d: "Source-first notebooks with claim-to-source mapping. Never invents a citation — labels model inference plainly.",
  },
  {
    k: "Artifacts",
    d: "Structured, versioned documents — reports, plans, code, diagrams — you can compare, restore and trace to their source.",
  },
  {
    k: "Tasks",
    d: "Durable, approval-gated automations with a real state machine. No high-risk action runs silently.",
  },
];

const PROVIDERS = ["Claude", "Gemini", "Groq", "Cerebras", "Ollama (local)"];

const TRUST = [
  {
    h: "Approval gates by default",
    d: "Writes and shell commands pass a destructive-action floor and human checkpoints. The web can never bypass the terminal's safety.",
  },
  {
    h: "Isolation you can see",
    d: "Healthcare memory never surfaces in Coding. Cross-world transfer requires an explicit, previewable action.",
  },
  {
    h: "Consent-gated training",
    d: "Your conversations and files are never used to train a model unless you opt in — and you can revoke it.",
  },
  {
    h: "Grounded, not guessed",
    d: "Answers separate sourced facts from model inference, and the scanner keeps secrets out of logs and datasets.",
  },
];

const RISK_TONE: Record<string, string> = {
  high: "bg-red-50 text-red-700 ring-red-200",
  medium: "bg-amber-50 text-amber-700 ring-amber-200",
  low: "bg-emerald-50 text-emerald-700 ring-emerald-200",
};

/* --------------------------------------------------------------- component */

export default function LandingPage() {
  return (
    <main className="min-h-screen">
      {/* ============================================================ hero */}
      <section className="relative overflow-hidden border-b border-border">
        <div className="pointer-events-none absolute inset-0 hero-grid" aria-hidden />
        <div
          className="pointer-events-none absolute inset-x-0 top-0 h-[520px] bg-gradient-to-b from-accent/10 via-transparent to-transparent"
          aria-hidden
        />
        <div className="relative mx-auto max-w-5xl px-6 pt-24 pb-20 text-center fadeup">
          <span className="inline-flex items-center gap-2 rounded-full border border-border bg-white/70 px-3 py-1 text-xs font-medium text-dim backdrop-blur">
            <span className="h-1.5 w-1.5 rounded-full bg-accent" />
            Local-first · Provider-agnostic · Open-source
          </span>

          <h1 className="mx-auto mt-6 max-w-4xl text-5xl font-bold leading-[1.05] tracking-tight sm:text-6xl md:text-7xl">
            Specialized intelligence,
            <br />
            <span className="bg-gradient-to-r from-accent-deep via-accent to-accent-soft bg-clip-text text-transparent">
              one world at a time.
            </span>
          </h1>

          <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-dim">
            Ronin AI OS is an operating system for AI work. Instead of a blank
            prompt box, you enter an industry <em className="not-italic text-ink">world</em> —
            with its own tools, safety rules, memory boundaries and evaluations —
            running on the models you choose, cloud or fully local.
          </p>

          <div className="mt-10 flex flex-wrap items-center justify-center gap-3">
            <Link
              href="/os"
              className="btn btn-primary rounded-lg px-6 py-3 text-base shadow-sm transition hover:shadow-md"
            >
              Enter Ronin →
            </Link>
            <Link
              href="/worlds"
              className="btn btn-secondary rounded-lg px-6 py-3 text-base"
            >
              Browse worlds
            </Link>
          </div>

          <div className="mt-10 flex flex-wrap items-center justify-center gap-x-5 gap-y-2 text-sm text-dim">
            <span className="text-xs uppercase tracking-wider text-dim/70">Runs on</span>
            {PROVIDERS.map((p) => (
              <span key={p} className="font-medium text-ink/80">
                {p}
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* ========================================================== worlds */}
      <section className="mx-auto max-w-6xl px-6 py-20">
        <div className="max-w-2xl">
          <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
            Enter a world, not a prompt box.
          </h2>
          <p className="mt-3 text-lg text-dim">
            Each world is a complete professional workspace — dedicated
            interface, agents, tools, knowledge and guardrails. Three are live;
            more are in preparation.
          </p>
        </div>

        <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {WORLDS.map((w) => (
            <Link
              key={w.id}
              href={w.href}
              className="group relative flex flex-col rounded-2xl border border-border bg-white p-6 transition hover:-translate-y-0.5 hover:border-accent/40 hover:shadow-lg"
            >
              <div className="flex items-center justify-between">
                <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent/10 text-lg font-bold text-accent-deep">
                  {w.name[0]}
                </span>
                <span
                  className={`rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${
                    RISK_TONE[w.risk] ?? "bg-neutral-100 text-neutral-600 ring-neutral-200"
                  }`}
                >
                  {w.risk} risk
                </span>
              </div>
              <h3 className="mt-4 text-xl font-semibold">{w.name}</h3>
              <p className="mt-2 flex-1 text-sm leading-relaxed text-dim">{w.blurb}</p>
              <span className="mt-4 text-sm font-medium text-accent-deep transition group-hover:translate-x-0.5">
                Open {w.name} →
              </span>
            </Link>
          ))}

          {/* future worlds */}
          <div className="flex flex-col justify-center rounded-2xl border border-dashed border-border bg-neutral-50/60 p-6 sm:col-span-2 lg:col-span-3">
            <p className="text-sm text-dim">
              <span className="font-semibold text-ink">17 more worlds in preparation</span>{" "}
              — Finance, Legal, Science, Business, Cybersecurity, Manufacturing,
              Government, Creative and more. Each stays disabled until its
              policies, datasets and evaluations exist. No world ships on
              optimism.
            </p>
          </div>
        </div>
      </section>

      {/* ================================================= not a chatbot band */}
      <section className="bg-ink text-bg">
        <div className="mx-auto grid max-w-6xl gap-10 px-6 py-20 md:grid-cols-2 md:items-center">
          <div>
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
              Not another chatbot.
            </h2>
            <p className="mt-4 text-lg leading-relaxed text-bg/70">
              A blank chat box makes you do all the work — the context, the
              guardrails, the format. A world already knows the role, the
              country, the language, the safety rules and how to show its work.
              You get an operator, not an autocomplete.
            </p>
            <ul className="mt-6 space-y-2 text-sm text-bg/80">
              {[
                "Auditable actions — every tool call, approval and source is on the record",
                "Editable, versioned artifacts instead of throwaway messages",
                "One runtime behind CLI, web and API — no divergent brains",
              ].map((t) => (
                <li key={t} className="flex gap-3">
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-accent-soft" />
                  {t}
                </li>
              ))}
            </ul>
          </div>

          <div className="rounded-2xl border border-white/10 bg-white/5 p-6 font-mono text-sm leading-relaxed text-bg/90 backdrop-blur">
            <div className="text-bg/50">// coding world · plan tracker</div>
            <div className="mt-3 space-y-1">
              <div><span className="text-emerald-300">✓</span> Read auth module</div>
              <div><span className="text-accent-soft">▶</span> Patch token-refresh bug</div>
              <div className="text-bg/50">☐ Run focused tests</div>
              <div className="text-bg/50">☐ Summarize the diff for review</div>
            </div>
            <div className="mt-4 rounded-lg border border-white/10 bg-black/20 p-3 text-xs">
              <div className="text-bg/60">⏺ edit(auth/token.py)</div>
              <div className="mt-1 text-amber-200">
                ⚠ write requires approval — floored tool, awaiting you
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ======================================================== products */}
      <section className="mx-auto max-w-6xl px-6 py-20">
        <div className="max-w-2xl">
          <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
            One runtime, many surfaces.
          </h2>
          <p className="mt-3 text-lg text-dim">
            The same core powers a family of products — each doing one thing
            well, all sharing safety, memory and evaluations.
          </p>
        </div>

        <div className="mt-10 grid gap-px overflow-hidden rounded-2xl border border-border bg-border sm:grid-cols-2 lg:grid-cols-3">
          {PRODUCTS.map((p) => (
            <div key={p.k} className="bg-white p-6 transition hover:bg-bg">
              <h3 className="text-lg font-semibold">
                Ronin <span className="text-accent-deep">{p.k}</span>
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-dim">{p.d}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ========================================================== trust */}
      <section className="border-y border-border bg-white">
        <div className="mx-auto max-w-6xl px-6 py-20">
          <div className="max-w-2xl">
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
              Built to be trusted.
            </h2>
            <p className="mt-3 text-lg text-dim">
              Safety isn't a setting you flip — it's the floor everything stands
              on. These invariants are enforced in code and covered by tests.
            </p>
          </div>

          <div className="mt-10 grid gap-8 sm:grid-cols-2">
            {TRUST.map((t, i) => (
              <div key={t.h} className="flex gap-4">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-sm font-bold text-accent-deep">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div>
                  <h3 className="font-semibold">{t.h}</h3>
                  <p className="mt-1 text-sm leading-relaxed text-dim">{t.d}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* =========================================================== cta */}
      <section className="mx-auto max-w-4xl px-6 py-24 text-center">
        <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
          Pick a world and get to work.
        </h2>
        <p className="mx-auto mt-3 max-w-xl text-lg text-dim">
          Start on the models you already have — including fully local ones — and
          keep ownership of your data, your spend and your guardrails.
        </p>
        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <Link href="/os" className="btn btn-primary rounded-lg px-6 py-3 text-base">
            Enter Ronin →
          </Link>
          <Link href="/coding" className="btn btn-secondary rounded-lg px-6 py-3 text-base">
            Open Coding
          </Link>
        </div>
        <p className="mt-6 text-xs text-dim">
          Staging preview · demo mode. Live world data is served by the Ronin API
          in the full deployment.
        </p>
      </section>

      {/* ======================================================== footer */}
      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 py-10 text-sm text-dim sm:flex-row">
          <span>
            <span className="font-semibold text-ink">Ronin</span>
            <span className="text-accent"> AI OS</span> · Open source · MIT
          </span>
          <nav className="flex items-center gap-5">
            <Link href="/worlds" className="hover:text-ink">Worlds</Link>
            <Link href="/forge" className="hover:text-ink">Forge</Link>
            <Link href="/research" className="hover:text-ink">Research</Link>
            <a
              href="https://github.com/rohithkandula19/Ronin"
              className="hover:text-ink"
            >
              GitHub
            </a>
          </nav>
        </div>
      </footer>
    </main>
  );
}
