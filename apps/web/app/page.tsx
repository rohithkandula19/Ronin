import Link from "next/link";
import { Reveal } from "./_components/Reveal";
import { Enso } from "./_components/Enso";

/* ------------------------------------------------------------------ data */

const WORLDS = [
  {
    id: "coding",
    name: "Coding",
    href: "/os/code",
    risk: "review",
    blurb:
      "A software-engineering workspace over the real Ronin runtime — files, plan tracker, diffs, tests, approval gates.",
  },
  {
    id: "education",
    name: "Education",
    href: "/os/education",
    risk: "safe",
    blurb:
      "Role-aware tutoring, study plans and practice — grounded in sources, and fail-closed on graded work.",
  },
  {
    id: "healthcare",
    name: "Healthcare",
    href: "/os/healthcare",
    risk: "caution",
    blurb:
      "Educational, non-diagnostic health information. Sources, uncertainty and an emergency boundary on every answer.",
  },
];

const SHOTS = [
  { src: "/shots/home.png", href: "/os", label: "Ronin Home", note: "A place, not a prompt box." },
  { src: "/shots/code.png", href: "/os/code", label: "Ronin Code", note: "Plan-first, approval-gated." },
  { src: "/shots/research.png", href: "/os/research", label: "Research", note: "Source-first, never invented." },
];

const PRODUCTS = [
  { k: "Core", d: "One shared runtime — provider routing, agents, tools, approvals, memory and audit. CLI and web share the same brain." },
  { k: "Forge", d: "Datasets, fine-tuning bundles and evaluations. Provenance-tracked, consent-gated, honest about trained vs generated." },
  { k: "Vault", d: "Scoped memory with strict cross-industry isolation. Nothing is training-eligible without explicit, revocable consent." },
  { k: "Research", d: "Source-first notebooks with claim-to-source mapping. Never invents a citation — labels model inference plainly." },
  { k: "Artifacts", d: "Structured, versioned documents — reports, plans, code, diagrams — you can compare, restore and trace to source." },
  { k: "Tasks", d: "Durable, approval-gated automations with a real state machine. No high-risk action runs silently." },
];

const PROVIDERS = ["Claude", "Gemini", "Groq", "Cerebras", "Ollama (local)"];

const STATS = [
  { n: "$0", l: "to run — free tiers & local" },
  { n: "0", l: "telemetry, ever" },
  { n: "100%", l: "offline-capable" },
  { n: "7", l: "providers, no lock-in" },
];

const TRUST = [
  { h: "Approval gates by default", d: "Writes and shell commands pass a destructive-action floor and human checkpoints. The web can never bypass the terminal's safety." },
  { h: "Isolation you can see", d: "Healthcare memory never surfaces in Coding. Cross-world transfer requires an explicit, previewable action." },
  { h: "Consent-gated training", d: "Your conversations and files are never used to train a model unless you opt in — and you can revoke it." },
  { h: "Grounded, not guessed", d: "Answers separate sourced facts from model inference, and the scanner keeps secrets out of logs and datasets." },
];

const RISK_TONE: Record<string, string> = {
  caution: "bg-warn-soft text-warn",
  review: "bg-info-soft text-info",
  safe: "bg-success-soft text-success",
};
const RISK_LABEL: Record<string, string> = { caution: "Caution", review: "Review", safe: "Safe" };

function FrameBar({ url }: { url?: string }) {
  return (
    <div className="frame-bar">
      <span className="frame-dot" style={{ background: "#e06c60" }} />
      <span className="frame-dot" style={{ background: "#e6b34d" }} />
      <span className="frame-dot" style={{ background: "#57a869" }} />
      {url && (
        <span className="ml-3 truncate font-mono text-[0.7rem] text-text-faint">{url}</span>
      )}
    </div>
  );
}

/* --------------------------------------------------------------- component */

export default function LandingPage() {
  return (
    <main className="min-h-screen">
      {/* ============================================================ hero */}
      <section className="grain relative overflow-hidden border-b border-border">
        {/* living clay light + faint grid + a whisper of the ensō, layered for depth */}
        <div className="aurora" aria-hidden />
        <div className="pointer-events-none absolute inset-0 hero-grid" aria-hidden />
        <div className="pointer-events-none absolute left-1/2 top-[46%] -translate-x-1/2 -translate-y-1/2" aria-hidden>
          <Enso size={720} className="opacity-[0.05]" />
        </div>

        <div className="relative mx-auto max-w-5xl px-6 pt-24 pb-24 text-center sm:pt-28">
          <Enso size={96} className="mx-auto mb-8" />

          <span className="fadeup inline-flex items-center gap-2 rounded-full border border-border bg-surface/70 px-3.5 py-1.5 text-xs font-medium text-dim shadow-sm backdrop-blur">
            <span className="h-1.5 w-1.5 rounded-full bg-accent" />
            Free · Local-first · Air-gapped · Open-source
          </span>

          <h1 className="fadeup font-display display-xl mx-auto mt-8 max-w-4xl">
            The coding agent that
            <br />
            <span className="text-gradient italic">never leaves your machine.</span>
          </h1>

          <p className="fadeup mx-auto mt-7 max-w-2xl text-lg leading-relaxed text-dim sm:text-xl">
            Ronin reads, edits and runs your code from the terminal — on any
            provider or a <em className="not-italic text-ink">fully local model</em>,
            offline if you want, with zero telemetry and a hard safety floor
            under every destructive command.
          </p>

          <div className="fadeup mx-auto mt-10 max-w-2xl">
            <div className="frame hairline text-left">
              <FrameBar url="terminal" />
              <pre className="overflow-x-auto px-5 py-4 font-mono text-sm leading-relaxed text-ink">
                <code>{`curl -sSL https://raw.githubusercontent.com/rohithkandula19/Ronin/main/install.sh | bash
ronin code "fix the failing test"`}</code>
              </pre>
            </div>
          </div>

          <div className="fadeup mt-8 flex flex-wrap items-center justify-center gap-3">
            <a
              href="https://github.com/rohithkandula19/Ronin"
              className="btn btn-primary rounded-xl px-7 py-3.5 text-base transition hover:-translate-y-0.5"
            >
              View on GitHub →
            </a>
            <Link href="/os" className="btn btn-secondary rounded-xl px-7 py-3.5 text-base transition hover:-translate-y-0.5">
              Try the web workspace
            </Link>
          </div>

          <div className="fadeup mt-11 flex flex-wrap items-center justify-center gap-x-1 gap-y-2 text-sm">
            <span className="mr-2 text-xs uppercase tracking-[0.16em] text-text-faint">Runs on</span>
            {PROVIDERS.map((p, i) => (
              <span key={p} className="flex items-center">
                {i > 0 && <span className="mx-2.5 text-border-strong">·</span>}
                <span className="font-medium text-ink/80">{p}</span>
              </span>
            ))}
          </div>
        </div>

        {/* stat band grounds the hero */}
        <div className="relative border-t border-border bg-surface/40 backdrop-blur">
          <div className="mx-auto grid max-w-5xl grid-cols-2 divide-x divide-border sm:grid-cols-4">
            {STATS.map((s) => (
              <div key={s.l} className="px-4 py-6 text-center">
                <div className="font-display text-3xl text-ink">{s.n}</div>
                <div className="mt-1 text-xs uppercase tracking-wider text-text-faint">{s.l}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ================================================= product showcase */}
      <section className="relative overflow-hidden border-b border-border bg-surface-sunken/40">
        <div className="mx-auto max-w-6xl px-6 py-24">
          <Reveal className="mx-auto max-w-4xl">
            <div className="frame glow-accent hairline">
              <FrameBar url="ronin-ai-os-staging.vercel.app/os" />
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/shots/home.png" alt="Ronin Home — the OS workspace" className="block w-full" loading="lazy" />
            </div>
          </Reveal>

          <div className="mt-8 grid gap-6 sm:grid-cols-2">
            {SHOTS.slice(1).map((s, i) => (
              <Reveal key={s.src} delay={120 * (i + 1)}>
                <Link href={s.href} className="group block">
                  <div className="frame card-lift hairline">
                    <FrameBar />
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={s.src} alt={s.label} className="block w-full" loading="lazy" />
                  </div>
                  <div className="mt-4 flex items-baseline justify-between">
                    <span className="text-sm font-semibold text-ink">{s.label}</span>
                    <span className="text-xs text-dim">{s.note}</span>
                  </div>
                </Link>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      {/* ========================================================== worlds */}
      <section className="mx-auto max-w-6xl px-6 py-24">
        <Reveal className="max-w-2xl">
          <span className="text-xs font-semibold uppercase tracking-[0.16em] text-accent-deep">The web workspaces</span>
          <h2 className="font-display display-lg mt-3">The same agent, with a UI when you want one.</h2>
          <p className="mt-4 text-lg text-dim">
            The terminal is the product. These companion workspaces run on the same
            runtime and the same safety rules — for the times a browser beats a shell.
          </p>
        </Reveal>

        <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {WORLDS.map((w, i) => (
            <Reveal key={w.id} delay={90 * i}>
              <Link
                href={w.href}
                className="card-lift hairline group relative flex h-full flex-col overflow-hidden rounded-2xl border border-border bg-surface p-6"
              >
                <div
                  className="pointer-events-none absolute -right-16 -top-16 h-40 w-40 rounded-full opacity-0 blur-2xl transition-opacity duration-500 group-hover:opacity-100"
                  style={{ background: "radial-gradient(circle, color-mix(in srgb, var(--rds-accent) 40%, transparent), transparent 70%)" }}
                  aria-hidden
                />
                <div className="relative flex items-center justify-between">
                  <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-accent-tint to-surface-sunken font-display text-xl text-accent-deep shadow-sm">
                    {w.name[0]}
                  </span>
                  <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${RISK_TONE[w.risk]}`}>
                    {RISK_LABEL[w.risk]}
                  </span>
                </div>
                <h3 className="relative mt-5 text-xl font-semibold">{w.name}</h3>
                <p className="relative mt-2 flex-1 text-sm leading-relaxed text-dim">{w.blurb}</p>
                <span className="relative mt-5 text-sm font-medium text-accent-deep transition group-hover:translate-x-0.5">
                  Open {w.name} →
                </span>
              </Link>
            </Reveal>
          ))}

        </div>
      </section>

      {/* ================================================= not a chatbot band */}
      {/* Pinned to sumi-dark in every theme so it stays a dramatic ink band. */}
      <section className="relative overflow-hidden bg-[#161412] text-[#f5f4f2]">
        <div
          className="pointer-events-none absolute inset-0 opacity-60"
          aria-hidden
          style={{ background: "radial-gradient(50% 60% at 15% 0%, color-mix(in srgb, #d4a373 22%, transparent), transparent 70%)" }}
        />
        <div className="relative mx-auto grid max-w-6xl gap-12 px-6 py-24 md:grid-cols-2 md:items-center">
          <Reveal>
            <span className="text-xs font-semibold uppercase tracking-[0.16em] text-accent-soft">The difference</span>
            <h2 className="font-display display-lg mt-3">Not another chatbot.</h2>
            <p className="mt-5 text-lg leading-relaxed text-[#f5f4f2]/70">
              A blank chat box makes you do all the work — the context, the
              guardrails, the format. A world already knows the role, the country,
              the language, the safety rules and how to show its work. You get an
              operator, not an autocomplete.
            </p>
            <ul className="mt-7 space-y-3 text-sm text-[#f5f4f2]/80">
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
          </Reveal>

          <Reveal delay={140}>
            <div className="glow-accent rounded-2xl border border-white/10 bg-white/[0.04] p-1.5 backdrop-blur">
              <div className="flex items-center gap-1.5 px-3 py-2">
                <span className="frame-dot" style={{ background: "#e06c60" }} />
                <span className="frame-dot" style={{ background: "#e6b34d" }} />
                <span className="frame-dot" style={{ background: "#57a869" }} />
                <span className="ml-2 font-mono text-[0.7rem] text-[#f5f4f2]/45">ronin · coding world</span>
              </div>
              <div className="rounded-xl bg-black/30 p-5 font-mono text-sm leading-relaxed text-[#f5f4f2]/90">
                <div className="text-[#f5f4f2]/50">// plan tracker</div>
                <div className="mt-3 space-y-1.5">
                  <div><span className="text-emerald-300">✓</span> Read auth module</div>
                  <div><span className="text-accent-soft">▶</span> Patch token-refresh bug</div>
                  <div className="text-[#f5f4f2]/50">☐ Run focused tests</div>
                  <div className="text-[#f5f4f2]/50">☐ Summarize the diff for review</div>
                </div>
                <div className="mt-4 rounded-lg border border-white/10 bg-black/30 p-3 text-xs">
                  <div className="text-[#f5f4f2]/60">⏺ edit(auth/token.py)</div>
                  <div className="mt-1 text-amber-200">⚠ write requires approval — floored tool, awaiting you</div>
                </div>
              </div>
            </div>
          </Reveal>
        </div>
      </section>

      {/* ======================================================== products */}
      <section className="mx-auto max-w-6xl px-6 py-24">
        <Reveal className="max-w-2xl">
          <span className="text-xs font-semibold uppercase tracking-[0.16em] text-accent-deep">The system</span>
          <h2 className="font-display display-lg mt-3">One runtime, many surfaces.</h2>
          <p className="mt-4 text-lg text-dim">
            The same core powers a family of products — each doing one thing well,
            all sharing safety, memory and evaluations.
          </p>
        </Reveal>

        <div className="mt-12 grid gap-px overflow-hidden rounded-2xl border border-border bg-border sm:grid-cols-2 lg:grid-cols-3">
          {PRODUCTS.map((p, i) => (
            <Reveal key={p.k} delay={60 * i} className="group relative bg-surface p-7 transition-colors hover:bg-surface-sunken">
              <h3 className="text-lg font-semibold">
                Ronin <span className="text-accent-deep">{p.k}</span>
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-dim">{p.d}</p>
              <span className="absolute inset-x-0 bottom-0 h-0.5 origin-left scale-x-0 bg-accent transition-transform duration-300 group-hover:scale-x-100" aria-hidden />
            </Reveal>
          ))}
        </div>
      </section>

      {/* ========================================================== trust */}
      <section className="border-y border-border bg-surface">
        <div className="mx-auto max-w-6xl px-6 py-24">
          <Reveal className="max-w-2xl">
            <span className="text-xs font-semibold uppercase tracking-[0.16em] text-accent-deep">The floor</span>
            <h2 className="font-display display-lg mt-3">Built to be trusted.</h2>
            <p className="mt-4 text-lg text-dim">
              Safety isn't a setting you flip — it's the floor everything stands
              on. These invariants are enforced in code and covered by tests.
            </p>
          </Reveal>

          <div className="mt-12 grid gap-x-10 gap-y-9 sm:grid-cols-2">
            {TRUST.map((t, i) => (
              <Reveal key={t.h} delay={80 * i} className="flex gap-4">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent-tint font-display text-sm font-semibold text-accent-deep">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div>
                  <h3 className="font-semibold">{t.h}</h3>
                  <p className="mt-1.5 text-sm leading-relaxed text-dim">{t.d}</p>
                </div>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      {/* =========================================================== cta */}
      <section className="grain relative overflow-hidden">
        <div className="aurora" aria-hidden />
        <div className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2" aria-hidden>
          <Enso size={480} className="opacity-[0.07]" />
        </div>
        <div className="relative mx-auto max-w-4xl px-6 py-28 text-center">
          <Reveal>
            <h2 className="font-display display-lg">Pick a world and get to work.</h2>
            <p className="mx-auto mt-4 max-w-xl text-lg text-dim">
              Start on the models you already have — including fully local ones —
              and keep ownership of your data, your spend and your guardrails.
            </p>
            <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
              <Link href="/os" className="btn btn-primary rounded-xl px-7 py-3.5 text-base transition hover:-translate-y-0.5">
                Enter Ronin →
              </Link>
              <Link href="/os/code" className="btn btn-secondary rounded-xl px-7 py-3.5 text-base transition hover:-translate-y-0.5">
                Open Ronin Code
              </Link>
            </div>
          </Reveal>
        </div>
      </section>

      {/* ======================================================== footer */}
      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 py-10 text-sm text-dim sm:flex-row">
          <span>
            <span className="font-display font-semibold text-ink">Ronin</span>
            <span className="text-accent"> AI OS</span> · Open source · MIT
          </span>
          <nav className="flex items-center gap-5">
            <Link href="/os" className="hover:text-ink">Enter</Link>
            <Link href="/worlds" className="hover:text-ink">Worlds</Link>
            <Link href="/research" className="hover:text-ink">Research</Link>
            <a href="https://github.com/rohithkandula19/Ronin" className="hover:text-ink">GitHub</a>
          </nav>
        </div>
      </footer>
    </main>
  );
}
