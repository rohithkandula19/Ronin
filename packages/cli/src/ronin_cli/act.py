"""Universal action engine — ``ronin do "<real-world task>"``.

Take on anything a person might ask an errand-runner to do — order food, book a
ticket, make a reservation, sign up for a service — but SAFELY. Three rules,
enforced in code, make this strictly safer than an agent that "just does it":

1. RESEARCH IS GROUNDED. Before acting, ronin researches real options through the
   faithfulness-grounded research path (web search + read the page) and runs the
   answer through the faithfulness harness. If the options are ungrounded (the
   model made up a price / availability), ronin refuses to proceed — it never acts
   on a hallucinated option.

2. EVERY STEP IS APPROVAL-GATED. Each step in the plan is an ``Action`` routed
   through ``ronin_cli.approvals.approve`` — the human OKs every move. Nothing
   external happens without a yes.

3. IT NEVER PAYS. The final, money-moving step of every plan is ``kind="payment"``
   (``reversible=False``, ``external=True``). The approval gate fails closed on
   payments and never auto-approves them, and ``run`` hard-stops at the payment
   step: it hands the user a summary plus the checkout link to finish — and pays.

This module owns three PURE, testable functions and one runner:

- ``classify_task(text) -> str``        the vertical (food / travel / ...).
- ``plan_steps(task, vertical) -> list``ordered ``Action`` dicts; last is payment.
- ``stops_before_payment(steps) -> bool``no auto step ever follows a payment.
- ``run(config, task, *, console)``     ground → plan → gate each step → STOP.

Design notes:
- It depends on ``ronin_cli.approvals`` (the universal approval gate, built in
  parallel) but imports it LAZILY inside ``run`` and degrades gracefully if it is
  absent, so importing this module and the pure functions never require it.
- It uses ronin's provider layer only (via ``research.run_research``) — no vendor
  SDK. Browser driving uses the optional ``ronin-cli[browser]`` extra; without it,
  the reversible steps print prepared manual instructions instead.
"""
from __future__ import annotations

from typing import Any, Optional

# ---------------------------------------------------------------------------
# Verticals + classification (PURE)
# ---------------------------------------------------------------------------

# The verticals ronin understands. "generic" is the catch-all so an unknown task
# still gets a safe, payment-stopping plan rather than a refusal.
VERTICALS = ("food", "travel", "reservation", "shopping", "signup", "generic")

# Keyword → vertical. Order matters: the FIRST vertical (by VERTICALS order) with
# a matching keyword wins, so a task that mentions both "book a table" and "buy"
# classifies as a reservation, not shopping. Matching is plain lowercase
# substring against the task text.
_VERTICAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "food": (
        "order food", "order me", "order a", "pizza", "burger", "sushi",
        "takeout", "take-out", "takeaway", "delivery", "deliver", "doordash",
        "ubereats", "uber eats", "grubhub", "postmates", "meal", "lunch",
        "dinner to", "food",
    ),
    "travel": (
        "flight", "fly ", "flying", "airfare", "airline", "plane ticket",
        "hotel", "motel", "airbnb", "stay in", "train", "rail ", "amtrak",
        "rental car", "rent a car", "bus ticket", "cruise", "trip to",
        "travel", "ticket to", "book a ride",
    ),
    "reservation": (
        "reservation", "reserve", "book a table", "table for", "book a",
        "appointment", "booking", "seats for", "tickets for", "rsvp",
        "schedule a", "haircut", "doctor", "dentist", "tee time",
    ),
    "shopping": (
        "buy", "purchase", "order me a", "add to cart", "checkout", "shop",
        "amazon", "shopping", "get me a", "order the", "groceries",
    ),
    "signup": (
        "sign up", "signup", "sign-up", "register", "registration",
        "create an account", "create account", "subscribe", "enroll",
        "join ", "make an account", "set up an account",
    ),
}


def classify_task(text: str) -> str:
    """Classify a real-world task into a vertical (PURE, no I/O).

    Returns one of :data:`VERTICALS`. Unknown / unmatched tasks fall through to
    ``"generic"`` so they still get a safe, payment-stopping plan. Matching is a
    plain lowercase substring check; the first vertical in :data:`VERTICALS`
    order with any matching keyword wins (so a deterministic tie-break, e.g. a
    "book a table" + "buy" task is a reservation, not shopping).
    """
    low = (text or "").lower()
    if not low.strip():
        return "generic"
    for vertical in VERTICALS:
        if vertical == "generic":
            continue
        for kw in _VERTICAL_KEYWORDS.get(vertical, ()):  # noqa: SIM110 - need the early return
            if kw in low:
                return vertical
    return "generic"


# ---------------------------------------------------------------------------
# Action helpers + planning (PURE)
# ---------------------------------------------------------------------------

# The kinds of step ronin emits. "payment" is special: it is always the final
# step, never reversible, always external, and the approval gate / runner stop
# before it. The others are reversible preparation steps.
KIND_RESEARCH = "research"
KIND_CHOOSE = "choose"
KIND_PREPARE = "prepare"   # fill a cart / form / details up to (not incl.) pay
KIND_REVIEW = "review"
KIND_PAYMENT = "payment"


def _action(
    kind: str,
    summary: str,
    *,
    reversible: bool,
    external: bool,
    cost: float | None = None,
    target: str = "",
    details: str = "",
) -> dict:
    """Build one ``Action`` dict in the shape ``ronin_cli.approvals`` expects.

    Contract (see ``approvals``):
    ``{"kind","summary","reversible":bool,"external":bool,"cost":float|None,
       "target","details"}``.
    """
    return {
        "kind": kind,
        "summary": summary,
        "reversible": bool(reversible),
        "external": bool(external),
        "cost": cost,
        "target": target,
        "details": details,
    }


def payment_step(summary: str, *, cost: float | None = None, target: str = "",
                 details: str = "") -> dict:
    """The terminal, money-moving step. ALWAYS reversible=False, external=True.

    Factored out so every plan ends with an identical, unmistakable payment
    action that the approval gate blocks and the runner stops before. ``cost`` is
    left ``None`` unless a grounded price is known (we never invent a number).
    """
    return _action(
        KIND_PAYMENT, summary,
        reversible=False, external=True, cost=cost, target=target,
        details=details or "ronin stops here — you confirm and pay yourself.",
    )


# Per-vertical preparation steps (everything BEFORE the payment). Each returns a
# list of reversible Action dicts; ``plan_steps`` appends the single payment step
# so the final money-moving action is uniform across verticals.
def _prep_food(task: str) -> list[dict]:
    return [
        _action(KIND_RESEARCH,
                f"Research delivery / takeout options for: {task}",
                reversible=True, external=False,
                target="web", details="Grounded web search for menus, prices, ETA."),
        _action(KIND_CHOOSE, "Choose a place + items from the grounded options",
                reversible=True, external=False, target="plan"),
        _action(KIND_PREPARE, "Fill the cart and delivery details (no payment)",
                reversible=True, external=True, target="browser",
                details="Add items to cart and enter the address; stop before checkout."),
    ]


def _prep_travel(task: str) -> list[dict]:
    return [
        _action(KIND_RESEARCH, f"Research travel options for: {task}",
                reversible=True, external=False, target="web",
                details="Grounded search for flights / hotels / fares + times + price."),
        _action(KIND_CHOOSE, "Choose an itinerary from the grounded options",
                reversible=True, external=False, target="plan"),
        _action(KIND_PREPARE, "Pre-fill passenger / guest details (no payment)",
                reversible=True, external=True, target="browser",
                details="Open the booking page and fill traveler details; stop before pay."),
    ]


def _prep_reservation(task: str) -> list[dict]:
    return [
        _action(KIND_RESEARCH, f"Research availability for: {task}",
                reversible=True, external=False, target="web",
                details="Grounded search for the venue, hours, and open slots."),
        _action(KIND_CHOOSE, "Choose a time / slot from the grounded options",
                reversible=True, external=False, target="plan"),
        _action(KIND_PREPARE, "Fill the reservation form (no deposit / payment)",
                reversible=True, external=True, target="browser",
                details="Enter party size, time, and contact details; stop before any deposit."),
    ]


def _prep_shopping(task: str) -> list[dict]:
    return [
        _action(KIND_RESEARCH, f"Research products for: {task}",
                reversible=True, external=False, target="web",
                details="Grounded search for the item, price, and availability."),
        _action(KIND_CHOOSE, "Choose a product from the grounded options",
                reversible=True, external=False, target="plan"),
        _action(KIND_PREPARE, "Add to cart and fill shipping details (no payment)",
                reversible=True, external=True, target="browser",
                details="Add the item to the cart and enter shipping; stop before checkout."),
    ]


def _prep_signup(task: str) -> list[dict]:
    return [
        _action(KIND_RESEARCH, f"Research the service / plan for: {task}",
                reversible=True, external=False, target="web",
                details="Grounded search for the signup page and the plan terms."),
        _action(KIND_CHOOSE, "Choose the plan / tier from the grounded options",
                reversible=True, external=False, target="plan"),
        _action(KIND_PREPARE, "Fill the signup form (no paid upgrade / billing)",
                reversible=True, external=True, target="browser",
                details="Enter account details on the free step; stop before any paid plan."),
    ]


def _prep_generic(task: str) -> list[dict]:
    return [
        _action(KIND_RESEARCH, f"Research how to do: {task}",
                reversible=True, external=False, target="web",
                details="Grounded web search for the right site and the steps."),
        _action(KIND_CHOOSE, "Choose the best path from the grounded options",
                reversible=True, external=False, target="plan"),
        _action(KIND_PREPARE, "Prepare the steps / form up to any payment",
                reversible=True, external=True, target="browser",
                details="Drive the page up to but not including any money-moving step."),
    ]


_PREP_BUILDERS = {
    "food": _prep_food,
    "travel": _prep_travel,
    "reservation": _prep_reservation,
    "shopping": _prep_shopping,
    "signup": _prep_signup,
    "generic": _prep_generic,
}

# A short, vertical-appropriate label for the terminal payment step.
_PAYMENT_LABEL = {
    "food": "Place the order and pay",
    "travel": "Confirm the booking and pay",
    "reservation": "Confirm the reservation (any deposit / payment)",
    "shopping": "Check out and pay",
    "signup": "Start a paid plan / enter billing",
    "generic": "Complete the final money-moving step",
}


def plan_steps(task: str, vertical: str) -> list[dict]:
    """Build the ordered list of ``Action`` dicts for ``task`` (PURE, no I/O).

    The plan is: a few reversible preparation steps (research → choose →
    prepare/fill) followed by exactly ONE terminal payment step. By construction
    the LAST step is always ``kind="payment"`` with ``reversible=False`` and
    ``external=True``, so it can never auto-run — the approval gate blocks it and
    the runner stops before it. An unknown ``vertical`` is treated as "generic".

    e.g. food → [research options, choose, fill cart, **payment(blocked)**].
    """
    task = (task or "").strip()
    builder = _PREP_BUILDERS.get(vertical, _prep_generic)
    steps: list[dict] = builder(task)
    # The final, uniform, money-moving step. Always last; never reversible.
    steps.append(payment_step(
        _PAYMENT_LABEL.get(vertical, _PAYMENT_LABEL["generic"]),
        target="checkout",
    ))
    return steps


def is_payment_step(step: dict) -> bool:
    """True if ``step`` is the terminal money-moving action.

    Mirrors ``approvals.is_payment`` locally so the pure planning code does not
    need the approvals module imported. A step is a payment if it is tagged
    ``kind="payment"`` OR is an irreversible external step that moves money — we
    treat ``kind=="payment"`` as the authoritative marker the planner emits.
    """
    if not isinstance(step, dict):
        return False
    if (step.get("kind") or "").strip().lower() == KIND_PAYMENT:
        return True
    # Defensive: an irreversible, external step carrying a cost is money-moving.
    return (
        step.get("reversible") is False
        and step.get("external") is True
        and step.get("cost") is not None
    )


def stops_before_payment(steps: list[dict]) -> bool:
    """Assert the plan never auto-executes anything AFTER a payment (PURE).

    The safety invariant: once a payment step appears, no further step may run
    automatically. We enforce the strongest possible form — there is nothing
    after the payment at all (the payment is the terminal action). Returns True
    when the plan is safe (no step follows the first payment), False otherwise.
    An empty plan, or one with no payment, is trivially safe (True).
    """
    if not steps:
        return True
    first_payment: int | None = None
    for i, step in enumerate(steps):
        if is_payment_step(step):
            first_payment = i
            break
    if first_payment is None:
        return True  # no payment at all — nothing to stop before.
    # Safe iff the payment is the last step: nothing executes after it.
    return first_payment == len(steps) - 1


# ---------------------------------------------------------------------------
# Runner — ground → plan → gate each step → STOP before payment
# ---------------------------------------------------------------------------

# Spelled out once so the promise is identical everywhere it is printed.
NEVER_PAYS_LINE = "ronin never pays — you confirm and pay yourself."


def _ground_research(config: Any, task: str, vertical: str) -> tuple[str, bool, str]:
    """Run the GROUNDED research path and judge whether the options are trustworthy.

    Returns ``(answer, grounded, verdict)``:
    - ``answer``   the research text (real options + sources), or an error line.
    - ``grounded`` True only when we are confident enough to proceed.
    - ``verdict``  a one-line faithfulness summary for display.

    Grounding uses the same faithfulness harness the rest of ronin uses: we score
    the research answer against the sources the search/fetch tools actually
    returned. If the harness says the answer is ungrounded (a made-up price /
    availability), we refuse to proceed. If we cannot judge (the offline / raw
    fallback path, which returns no agent trace), we are conservative and still
    surface the answer but mark it ungrounded so the run does NOT auto-act on it.
    """
    from .research import run_research

    question = (
        f"Find real, current options for this task and cite sources: {task}. "
        f"List each option with its name, price, and a link. "
        f"This is a {vertical} task."
    )

    # Capture the agent's trace (the sources it actually read) so the faithfulness
    # harness can ground the answer against them. ``run_research`` takes an
    # injected runner; the default runner discards the trace, so we build a
    # trace-capturing runner around the same read-only code agent. If that path
    # is unavailable we fall back to run_research's own degradation.
    trace: list[Any] = []
    answer = ""
    try:
        from .code_mode import run_code_agent
        from .research import RESEARCH_SYSTEM

        res = run_code_agent(
            config, question, root=".", console=None, yolo=True,
            read_only=True, include_image_tool=False, base_system=RESEARCH_SYSTEM,
        )
        answer = (res.output or res.error or "").strip()
        trace = list(getattr(res, "trace", []) or [])
    except Exception:  # noqa: BLE001 - degrade to the plain research path
        answer = run_research(question, config=config, root=".")
        trace = []

    if not answer:
        return ("(research returned nothing)", False, "no research output")

    # Score the answer against the sources the agent observed.
    try:
        from .faithfulness_hook import evaluate_answer_from_trace

        outcome = evaluate_answer_from_trace(
            answer, trace, mode="gate", config=config,
        )
        verdict = outcome.summary
        # Proceed only when the harness is positive: not ungrounded AND not an
        # abstain. An abstain (e.g. no sources captured on the fallback path)
        # means "can't vouch for these options" → do not auto-act.
        grounded = not (outcome.ungrounded or outcome.abstain)
        return (answer, grounded, verdict)
    except Exception as e:  # noqa: BLE001 - harness unavailable → be conservative
        return (answer, False, f"faithfulness check unavailable: {e}")


def _approve(step: dict, *, console: Any) -> bool:
    """Route one step through the universal approval gate, failing CLOSED.

    Imports ``ronin_cli.approvals`` lazily so this module (and its pure helpers)
    never hard-depend on it. If the gate is somehow unavailable, we fail closed:
    a payment is always denied, and any other external step is denied too (only
    a non-external, reversible local step is allowed through without the gate).
    """
    try:
        from . import approvals
    except Exception:  # noqa: BLE001 - gate missing → fail closed
        if is_payment_step(step):
            return False
        # Allow only clearly-internal, reversible work without the real gate.
        return bool(step.get("reversible")) and not step.get("external")
    return bool(approvals.approve(step, console=console))


def _drive_browser_or_print(step: dict, *, console: Any) -> None:
    """Execute a reversible PREPARE step: drive the browser if the extra is
    installed, else print the prepared manual instructions. NEVER pays.

    This only ever navigates / reads / fills — it does not click a pay/submit
    control. The payment step is handled separately (and stopped before).
    """
    details = step.get("details") or step.get("summary") or ""
    try:
        from .browser_tools import browser_tools_available, install_hint
    except Exception:  # noqa: BLE001
        browser_tools_available = lambda: False  # noqa: E731
        install_hint = lambda: "browser extra not available"  # noqa: E731

    if step.get("target") == "browser" and browser_tools_available():
        # The browser extra is present. We deliberately do not auto-fill unknown
        # forms here (no grounded URL/selectors in this generic path); we tell the
        # user exactly what was prepared. Real per-site driving would route every
        # intended action through the approval gate first — and never a pay click.
        console.print(f"   [dim]browser ready — prepared:[/dim] {details}")
    else:
        console.print(f"   [dim]manual step:[/dim] {details}")
        if step.get("target") == "browser":
            console.print(f"   [dim]{install_hint().splitlines()[0]}[/dim]")


def run(config: Any, task: str, *, console: Any) -> None:
    """Take on ``task`` safely: ground the research, plan, gate every step, STOP
    before payment.

    Flow:
      1. GROUND — research real options through the grounded research path and
         run the faithfulness harness. Refuse to proceed on ungrounded options.
      2. PLAN — classify the task and build the ordered ``Action`` plan whose
         final step is always a blocked payment.
      3. GATE — call ``approvals.approve`` on EVERY step; the human OKs each move.
      4. EXECUTE the reversible steps (drive the browser if installed, else print
         prepared manual steps), then HARD-STOP at the payment step and hand back
         a summary + the checkout link for the user to finish — and pay.

    Prints a clear "ronin never pays — you confirm and pay yourself." line and,
    by safety invariant, never executes anything after the payment step.
    """
    task = (task or "").strip()
    if not task:
        console.print("[yellow]Tell me what to do, e.g.[/yellow] "
                      'ronin do "order me a pizza"')
        return

    vertical = classify_task(task)
    console.print(f"[bold #7aa2f7]🤖 ronin do:[/bold #7aa2f7] {task}  "
                  f"[dim]({vertical})[/dim]")
    console.print(f"[#e0af68]{NEVER_PAYS_LINE}[/#e0af68]\n")

    # 1) GROUND the research — never act on a hallucinated option.
    console.print("[dim]Researching real options (grounded)…[/dim]")
    answer, grounded, verdict = _ground_research(config, task, vertical)
    console.print(answer.strip() or "(no options found)")
    console.print(f"[dim]grounding: {verdict}[/dim]\n")
    if not grounded:
        console.print(
            "[bold red]Refusing to act on ungrounded options.[/bold red] The "
            "research could not be verified against real sources (possible "
            "made-up price / availability). Review the options above and run a "
            "more specific request, or proceed manually."
        )
        console.print(f"\n[bold #e0af68]{NEVER_PAYS_LINE}[/bold #e0af68]")
        return

    # 2) PLAN — the final step is always a blocked payment.
    steps = plan_steps(task, vertical)
    # Belt-and-suspenders: never run a plan that could act after a payment.
    if not stops_before_payment(steps):  # pragma: no cover - invariant guard
        console.print("[bold red]Internal safety check failed: plan would act "
                      "after payment. Aborting.[/bold red]")
        return

    # 3 + 4) GATE every step; execute reversible ones; STOP at payment.
    console.print("[bold]Plan (you approve every step):[/bold]")
    for idx, step in enumerate(steps, 1):
        console.print(f"[bold]{idx}/{len(steps)}[/bold] {step['summary']}")

        if is_payment_step(step):
            # The hard stop. Ask for approval (the gate will BLOCK / never
            # auto-approve a payment), then hand the user the checkout to finish.
            _approve(step, console=console)  # renders the block; result ignored
            link = _checkout_link(answer)
            console.print(
                "\n[bold #e0af68]── STOP: payment step ──[/bold #e0af68]\n"
                f"[bold]{NEVER_PAYS_LINE}[/bold]"
            )
            if link:
                console.print(f"[bold]Finish here (you confirm and pay):[/bold] "
                              f"[#7dcfff]{link}[/#7dcfff]")
            else:
                console.print("[dim]Open the chosen option above and complete the "
                              "payment yourself.[/dim]")
            return  # nothing after the payment — invariant upheld at runtime.

        # Reversible step: gate it, then execute it if approved.
        if not _approve(step, console=console):
            console.print("[yellow]Stopped: you declined this step.[/yellow]")
            console.print(f"\n[bold #e0af68]{NEVER_PAYS_LINE}[/bold #e0af68]")
            return
        _drive_browser_or_print(step, console=console)

    # Unreachable in normal flow (the payment step returns above), but safe.
    console.print(f"\n[bold #e0af68]{NEVER_PAYS_LINE}[/bold #e0af68]")


def _checkout_link(research_answer: str) -> str:
    """Best-effort: pull the first URL out of the grounded research answer so the
    user has a direct link to finish + pay. Returns "" if none is found."""
    import re

    m = re.search(r"https?://\S+", research_answer or "")
    return m.group(0).rstrip(").,]\"'") if m else ""
