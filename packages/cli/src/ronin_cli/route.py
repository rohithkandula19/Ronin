"""Auto-Router — a learned, cost-aware blade picker, productized.

ronin already has the pieces of a model router scattered across a few modules:
``routing.py`` classifies turns into simple/complex and resolves a per-turn
provider; ``router_stats.py`` learns per-blade reliability on *your* repo;
``cost.py`` prices every blade and tallies what routing saved. This module ties
them into one first-class, explainable decision:

    classify the task → score the candidate blades → pick the CHEAPEST one whose
    learned reliability clears the bar (else the strongest) → run it → report the
    savings vs the strong baseline → record the outcome so it learns.

``ronin route "<task>"`` is the user-facing front door. The two scoring steps —
``classify_task`` and ``pick_blade`` — are pure and deterministic so they unit-
test without a network, the LLM, or any disk I/O.

Built ON the existing machinery, not beside it:
  * candidates come from the config's failover chain + the route_fast/route_strong
    targets (the blades the user already trusts), priced via ``cost.price_for``;
  * reliability comes from ``router_stats`` (the same per-repo learned profile the
    interactive router self-tunes on), so a blade that has been flaky here is
    already known to be flaky here;
  * the chosen task class maps onto the router_stats *tier* vocabulary
    ("simple"/"complex") so a ``route`` run and an interactive turn share — and
    grow — the same learned reliability data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from .config import RoninConfig

# ---------------------------------------------------------------------------
# 1. classify_task — pure heuristic task tiering
# ---------------------------------------------------------------------------

# Tasks that are cheap to get right: cosmetic / mechanical edits a small model
# nails. Keyword hit (and not a hard signal) → "cheap".
_CHEAP_KW = (
    "format", "rename", "typo", "spelling", "indent", "lint", "whitespace",
    "comment", "docstring", "rephrase", "reword", "spacing", "import order",
)

# Tasks that genuinely need the strong model: deep reasoning, broad blast radius,
# or correctness-critical work. Any hit → "hard".
_HARD_KW = (
    "refactor", "debug", "migrate", "migration", "architecture", "architect",
    "redesign", "concurrency", "race condition", "deadlock", "security",
    "vulnerab", "optimize performance", "rewrite", "design the", "root cause",
    "distributed", "thread-saf", "data model", "schema change",
)

# A long ask is, on its own, a "hard" signal even without a keyword: more to hold
# in context, more to get right.
_HARD_LEN = 280
# A genuinely tiny ask leans cheap when nothing else fires.
_CHEAP_LEN = 40


def classify_task(text: str) -> str:
    """Tier a task as ``"cheap"``, ``"standard"`` or ``"hard"`` — pure heuristic.

    Precedence (first match wins): explicit *hard* keywords, then a code block /
    long ask, then explicit *cheap* keywords, then length, else ``"standard"``.
    Hard signals beat cheap ones so "rename every call site while refactoring the
    auth architecture" is correctly ``hard``, not ``cheap``.

    Deterministic and side-effect free; the unit of all routing decisions.
    """
    m = (text or "").lower().strip()
    if not m:
        return "standard"

    # Hard wins outright — a correctness-critical word anywhere dominates.
    if any(k in m for k in _HARD_KW):
        return "hard"
    # A code block or a long, dense ask is hard even without a keyword.
    if "```" in text or len(m) > _HARD_LEN:
        return "hard"
    # Cosmetic / mechanical → cheap (only reached when no hard signal fired).
    if any(k in m for k in _CHEAP_KW):
        return "cheap"
    # Very short, signal-free asks ("rename a var", "add a log line") → cheap.
    if len(m) <= _CHEAP_LEN:
        return "cheap"
    return "standard"


# Map a task class onto the router_stats *tier* vocabulary ("simple"/"complex")
# so this router shares the learned reliability profile the interactive Cost
# Router already self-tunes on. cheap → simple; standard/hard → complex.
_TIER_FOR_CLASS = {"cheap": "simple", "standard": "complex", "hard": "complex"}

# Per-class reliability bar a blade's learned success rate must clear to be
# eligible as the cheap pick. Harder tasks demand a more proven blade.
_RELIABILITY_BAR = {"cheap": 0.55, "standard": 0.65, "hard": 0.75}
# Used when a blade has no learned reliability (never tried here): assume it is
# good enough for cheap work but make it earn standard/hard via real samples.
_DEFAULT_RELIABILITY = 0.7


def tier_for_class(task_class: str) -> str:
    """The router_stats tier ("simple"/"complex") a task class records under."""
    return _TIER_FOR_CLASS.get(task_class, "complex")


def reliability_bar(task_class: str) -> float:
    """The minimum learned reliability a blade needs to be the cheap pick."""
    return _RELIABILITY_BAR.get(task_class, 0.65)


# ---------------------------------------------------------------------------
# 2. pick_blade — pure, deterministic, cost-aware selection
# ---------------------------------------------------------------------------
#
# A *candidate* (a blade) is a plain dict:
#     {
#       "provider":       str,            # e.g. "anthropic", "cerebras"
#       "model":          str | None,     # e.g. "claude-sonnet-4-6"; None = default
#       "price_per_mtok": float,          # blended USD / 1M tokens (lower = cheaper)
#       "reliability":    float | None,   # optional precomputed 0..1; else read from stats
#     }
#
# ``stats`` is a RouterStats-like object exposing ``rate(tier, provider) ->
# float | None`` (None when too few samples). ``pick_blade`` reads a candidate's
# learned reliability from it when the candidate didn't carry one.
#
# Returns ONE candidate dict (the same object from ``candidates``) annotated with
# two extra keys describing the decision:
#     "reason":       "cheapest above the bar" | "strongest (none cleared the bar)"
#                     | "only candidate" | "cheapest under budget"
#     "eff_reliability": float            # the reliability actually used to decide


def _effective_reliability(cand: dict, task_class: str,
                           stats: Optional[Any]) -> float:
    """The reliability score to judge ``cand`` by: an explicit ``reliability`` on
    the candidate wins; else the learned rate from ``stats`` for this class's
    tier; else a neutral default. Always a concrete float so sorting is total."""
    r = cand.get("reliability")
    if r is not None:
        return float(r)
    if stats is not None:
        learned = stats.rate(tier_for_class(task_class), cand["provider"])
        if learned is not None:
            return float(learned)
    return _DEFAULT_RELIABILITY


def _price(cand: dict) -> float:
    try:
        return float(cand.get("price_per_mtok", 0.0))
    except (TypeError, ValueError):
        return 0.0


def pick_blade(task_class: str,
               candidates: Sequence[dict],
               stats: Optional[Any] = None,
               budget: Optional[float] = None) -> dict:
    """Choose a blade for ``task_class`` from ``candidates`` — pure & deterministic.

    Policy: among the blades whose effective reliability clears this class's bar,
    pick the **cheapest** (ties broken by higher reliability, then provider:model
    name for full determinism). If none clear the bar, fall back to the
    **strongest** available blade (highest reliability, then cheapest). When a
    ``budget`` (max USD/Mtok) is given, blades priced above it are dropped first;
    if that empties the field, the single cheapest blade is returned so a turn is
    never left with nothing to run on.

    The returned dict is one of the input candidates, annotated in place with
    ``reason`` and ``eff_reliability``. Raises ``ValueError`` on no candidates.

    Args:
        task_class: "cheap" | "standard" | "hard" (from :func:`classify_task`).
        candidates: blade dicts (see module docstring for the shape).
        stats: optional RouterStats-like reliability source.
        budget: optional max blended price per 1M tokens; ``None`` = no cap.
    """
    if not candidates:
        raise ValueError("pick_blade: no candidates to choose from")

    # Annotate every candidate with the reliability we'll actually judge it by,
    # so the decision is explainable and sorting is stable.
    pool = list(candidates)
    for c in pool:
        c["eff_reliability"] = _effective_reliability(c, task_class, stats)

    # Budget gate: drop blades that price out. If that empties the field, keep the
    # single cheapest so we always return *something* runnable.
    affordable = pool
    if budget is not None:
        affordable = [c for c in pool if _price(c) <= budget] or [
            min(pool, key=lambda c: (_price(c), str(c.get("provider")), str(c.get("model"))))
        ]
        if len(affordable) == 1 and affordable is not pool:
            chosen = affordable[0]
            chosen["reason"] = "cheapest under budget"
            return chosen

    bar = reliability_bar(task_class)
    eligible = [c for c in affordable if c["eff_reliability"] >= bar]

    if len(affordable) == 1:
        chosen = affordable[0]
        chosen.setdefault("reason", "only candidate")
        return chosen

    if eligible:
        # Cheapest blade that clears the bar; tie → more reliable, then by name.
        chosen = min(
            eligible,
            key=lambda c: (_price(c), -c["eff_reliability"],
                           str(c.get("provider")), str(c.get("model"))),
        )
        chosen["reason"] = "cheapest above the bar"
        return chosen

    # Nobody cleared the bar — fall back to the strongest blade we can afford.
    chosen = max(
        affordable,
        key=lambda c: (c["eff_reliability"], -_price(c),
                       str(c.get("provider")), str(c.get("model"))),
    )
    chosen["reason"] = "strongest (none cleared the bar)"
    return chosen


# ---------------------------------------------------------------------------
# 3. route — the user-facing, side-effecting orchestrator
# ---------------------------------------------------------------------------


@dataclass
class RouteRun:
    """The outcome of a :func:`route` call (returned for callers/tests).

    Attributes:
        task_class: the tier :func:`classify_task` assigned.
        blade:      the chosen candidate dict (with ``reason``/``eff_reliability``).
        result:     the ``AgentResultRich`` from running the task (None if it
                    short-circuited before running, e.g. missing creds).
        saved:      estimated USD saved vs the strong baseline for this turn.
        spent:      estimated USD this turn actually cost on the chosen blade.
        error:      a human message when the run couldn't proceed, else None.
    """
    task_class: str
    blade: dict
    result: Optional[Any] = None
    saved: float = 0.0
    spent: float = 0.0
    error: Optional[str] = None


def _blade_label(blade: dict) -> str:
    prov = blade.get("provider", "?")
    model = blade.get("model")
    return f"{prov}:{model}" if model else str(prov)


def build_candidates(config: RoninConfig, stats: Optional[Any] = None) -> list[dict]:
    """Assemble the candidate blades for ``config`` — the blades the user already
    trusts, priced and (optionally) reliability-annotated.

    Sources, de-duplicated by (provider, model):
      * the active provider/model,
      * the route_fast / route_strong targets (if routing is configured),
      * every entry in the failover chain (explicit or the automatic same-key one).

    Each blade is priced via :func:`cost.price_for` (blended = mean of in/out
    $/Mtok), and given the learned reliability from ``stats`` when available so
    the pure :func:`pick_blade` doesn't have to reach for it.
    """
    from .cost import price_for
    from .routing import _parse_target
    from .runner import default_failover_specs

    specs: list[tuple[str, Optional[str]]] = [(config.provider, config.resolved_model())]
    for target in (config.route_fast, config.route_strong):
        if target:
            specs.append(_parse_target(target, config.provider))
    for fo in (config.failover or default_failover_specs(config)):
        if isinstance(fo, dict) and fo.get("provider"):
            specs.append((fo["provider"], fo.get("model")))

    out: list[dict] = []
    seen: set[tuple[str, Optional[str]]] = set()
    for provider, model in specs:
        key = (provider, model)
        if key in seen:
            continue
        seen.add(key)
        pin, pout = price_for(provider, model)
        blade: dict = {
            "provider": provider,
            "model": model,
            "price_per_mtok": (pin + pout) / 2.0,
        }
        if stats is not None:
            # Pre-attach nothing class-specific here; pick_blade resolves per-class
            # reliability from stats itself. We only set an explicit value when the
            # caller already computed one, which they don't here.
            pass
        out.append(blade)
    return out


def route(config: RoninConfig, task: str, *, console) -> RouteRun:
    """Classify ``task``, pick the cheapest reliable blade, run it, and report.

    Pipeline:
      1. ``classify_task(task)`` → cheap | standard | hard.
      2. Build candidate blades from the config (active + route targets + failover),
         priced via cost.py and reliability-scored via router_stats for this repo.
      3. ``pick_blade`` selects the cheapest blade above the class's reliability
         bar (honoring ``config.budget`` as a price cap), else the strongest.
      4. Print the routing decision (e.g.
         ``→ routing a [hard] task to anthropic:claude-sonnet-4-6 — cheapest above the bar``).
      5. Run the task on that blade via ``config_for_spec`` + ``run_ask``
         (read-only ask agent — fine for v1).
      6. Print the CostLedger savings vs the strong baseline for this turn.
      7. Record the outcome to router_stats so the next route learns.

    Side-effecting (prints, runs the agent, writes router_stats). The pure scoring
    lives in :func:`classify_task` / :func:`pick_blade`. Returns a :class:`RouteRun`.
    """
    from .cost import CostLedger
    from .router_stats import load_stats, save_stats
    from .routing import baseline_for
    from .runner import config_for_spec, run_ask
    from .selfupdate import find_repo_root

    task_class = classify_task(task)

    root = find_repo_root() or __import__("pathlib").Path.cwd()
    stats = load_stats(root)

    candidates = build_candidates(config, stats)
    budget = getattr(config, "budget", None)
    blade = pick_blade(task_class, candidates, stats=stats, budget=budget)
    label = _blade_label(blade)

    console.print(
        f"[bold cyan]→ routing[/bold cyan] a [bold]\\[{task_class}][/bold] task to "
        f"[bold]{label}[/bold] [dim]— {blade.get('reason', 'chosen')} "
        f"(reliability ~{blade['eff_reliability']:.0%}, "
        f"${blade['price_per_mtok']:.2f}/Mtok)[/dim]"
    )

    # Guard: the chosen provider must have credentials, or we can't run it.
    turn_cfg = config_for_spec(config, {"provider": blade["provider"], "model": blade.get("model")})
    if not turn_cfg.has_provider_auth():
        msg = (f"no credentials for provider [bold]{blade['provider']}[/bold] — "
               f"run [bold]ronin login {blade['provider']}[/bold] or pick a configured blade")
        console.print(f"[red]✗[/red] {msg}")
        return RouteRun(task_class=task_class, blade=blade, error=msg)

    # Run the task on the chosen blade (read-only ask agent for v1).
    result = run_ask(turn_cfg, task, console=console)

    # Tally cost vs the strong baseline for this single turn.
    base_provider, base_model = baseline_for(config)
    ledger = CostLedger(baseline_provider=base_provider, baseline_model=base_model)
    usage = getattr(result, "usage", None) or {}
    ledger.record(
        turn_cfg.provider, turn_cfg.resolved_model(),
        int(usage.get("input_tokens", 0) or 0),
        int(usage.get("output_tokens", 0) or 0),
        cached_tok=int(usage.get("cache_read_input_tokens", 0) or 0),
    )
    if ledger.turns and ledger.last_baseline > 0:
        console.print(
            f"[#8a8a8a]cost: ${ledger.spent:.4f} · saved ${ledger.saved:.4f} "
            f"vs {base_provider}:{base_model}[/#8a8a8a]"
        )
    elif ledger.saved > 0:
        console.print(f"[#8a8a8a]saved ${ledger.saved:.4f} vs {base_provider}:{base_model}[/#8a8a8a]")

    # Learn from the outcome: record success/failure under this class's tier so the
    # shared per-repo reliability profile improves (and the interactive router
    # benefits too). Best-effort; never fail the command on a write error.
    try:
        stats.record(tier_for_class(task_class), turn_cfg.provider, bool(getattr(result, "success", False)))
        save_stats(root, stats)
    except Exception:  # noqa: BLE001 — learning is best-effort
        pass

    return RouteRun(
        task_class=task_class,
        blade=blade,
        result=result,
        saved=ledger.saved,
        spent=ledger.spent,
    )
