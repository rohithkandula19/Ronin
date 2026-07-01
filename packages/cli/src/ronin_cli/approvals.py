"""Universal approval gate — the safety backbone for every outward move.

This is the single chokepoint the whole agent routes *outward* actions through.
Whatever ronin is about to do to the world — write a file, run a shell command,
send a message, drive a browser, make a booking, move money, call an API — it is
first described as an :class:`Action` and gated here. The user's hard rule:
**every move ronin takes there must be approval.** Nothing reaches the outside
world without first passing a gate that is fail-closed and, for money, never
silently passable.

Posture (mirrors ``permissions.py`` — read before relaxing anything):

- **Fail-closed.** When the shape of an action is unclear, we *escalate*, never
  relax: ``confirm`` is chosen over ``auto`` and ``block`` over ``confirm``. An
  action we cannot understand (not a dict, missing fields, malformed) is treated
  as the most dangerous thing it could be.
- **Payments are never silent.** Anything that moves money is forced to
  ``block`` — it can never be auto-approved, even with ``auto_ok=True``; a human
  is always asked and always warned. This generalizes ``book.py``'s blunt
  pay/submit guard from bookings to *every* kind of action.
- **Default-deny on ambiguity.** An empty answer, an EOF (no TTY / piped stdin),
  or any input error at the prompt denies the action.

THE ACTION SHAPE
================

An ``Action`` is a plain ``dict`` (kept a dict, not a class, so any caller —
edit tool, shell runner, MCP bridge, browser driver — can describe a move
without importing a type)::

    {
        "kind":       str,          # what sort of move: see KINDS below
        "summary":    str,          # human one-liner: "edit app.py", "pay $40"
        "reversible": bool,         # can ronin (or the user) cleanly undo it?
        "external":   bool,         # does it touch the world outside this repo?
        "cost":       float | None, # money it moves/spends, if any (USD)
        "target":     str | None,   # the object acted on: path, URL, recipient
        "details":    dict | None,  # extra structured context (free-form)
    }

Only ``kind`` and ``summary`` really matter for a readable prompt; everything
else defaults conservatively (see :func:`normalize`) so a sparse action is
gated *safely*, not loosely. ``kind`` is open-ended — the set below is the
well-known vocabulary, but an unknown kind is allowed and gated on its flags.

``external`` = "touches the outside world": a network call, a message to a
person, a browser action on a live site, a booking, a payment. A purely local,
in-repo file edit is **not** external. When in doubt, mark it external (it only
adds friction).
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# Well-known action kinds. Open set: an unknown kind is still gated (on its
# reversible/external/payment flags), it just isn't in this list.
KINDS = (
    "edit",       # write/modify a file in the repo (usually local, reversible)
    "shell",      # run a shell command
    "message",    # send a message to a person/channel (email, Slack, SMS, ...)
    "browser",    # drive a real browser against a live site
    "booking",    # reserve/hold something with a buy step (flight, hotel, ...)
    "payment",    # move money: pay, charge, checkout, transfer, send funds
    "api_call",   # call an external API / MCP tool with a side effect
)

# Gate levels, least to most restrictive. Ordering matters: :func:`_escalate`
# uses it to always pick the *more* restrictive of two candidate levels.
AUTO = "auto"        # may run un-prompted when the caller opts in (auto_ok)
CONFIRM = "confirm"  # always ask the human y/N
BLOCK = "block"      # never auto-runnable; human-only, with a money/irreversible warning
LEVELS = (AUTO, CONFIRM, BLOCK)

# Substrings that mark a money-moving / commit step, checked against an action's
# kind + summary + target + details. Generalized from ``book.py``'s
# FORBIDDEN_ACTIONS: matching is a blunt, lowercase substring test, and the list
# is deliberately broad — a false positive only forces a (harmless) extra
# confirmation, a false negative would let money move silently.
PAYMENT_MARKERS = (
    "pay",
    "payment",
    "charge",
    "checkout",
    "check out",
    "purchase",
    "buy",
    "place order",
    "place-order",
    "submit order",
    "submit-order",
    "complete booking",
    "complete order",
    "complete purchase",
    "confirm and pay",
    "book now",
    "transfer funds",
    "send funds",
    "send money",
    "wire ",          # "wire transfer", "wire $40" — trailing space avoids "firewire"
    "invoice",
    "subscribe",
    "billing",
    "refund",
    "payout",
)

# Substrings that mark a clearly destructive / irreversible move regardless of
# the ``reversible`` flag the caller passed. These force ``block`` too: a caller
# that mislabels ``rm -rf /`` as reversible must not be able to auto-run it.
DESTRUCTIVE_MARKERS = (
    "rm -rf",
    "rm -fr",
    "rmdir /s",
    "drop table",
    "drop database",
    "truncate table",
    "delete from",
    "git push --force",
    "git push -f",
    "force-push",
    "force push",
    "format ",        # "format disk" — trailing space avoids "format string"
    "mkfs",
    "dd if=",
    "shred ",
    ":(){",           # fork bomb
    "deltree",
    "del /f",
)


# ---------------------------------------------------------------------------
# Normalization (fail-closed)
# ---------------------------------------------------------------------------


def normalize(action: Any) -> dict:
    """Coerce an arbitrary input into a safe, fully-populated action dict.

    This is where the fail-closed posture lives. Anything that isn't a usable
    mapping — ``None``, a string, a malformed object — becomes an action whose
    flags are the *most cautious* possible (``reversible=False, external=True``),
    so a garbage input is gated as the most dangerous thing it could be rather
    than slipping through as a safe one. Missing individual fields likewise
    default to the cautious value, never the permissive one.
    """
    if not isinstance(action, Mapping):
        # Not even a dict: we cannot reason about it, so assume the worst.
        return {
            "kind": "unknown",
            "summary": _as_str(action) or "unrecognized action",
            "reversible": False,   # assume it cannot be undone
            "external": True,      # assume it touches the world
            "cost": None,
            "target": None,
            "details": None,
        }

    kind = _as_str(action.get("kind")).strip().lower() or "unknown"
    summary = _as_str(action.get("summary")).strip()
    if not summary:
        # No human description given: synthesize a minimal one from kind/target.
        tgt = _as_str(action.get("target")).strip()
        summary = f"{kind} {tgt}".strip() if tgt else kind

    return {
        "kind": kind,
        "summary": summary,
        # Default-cautious: an unspecified action is treated as irreversible and
        # external. A caller must *affirmatively* say reversible=True / not
        # external to earn the gentler gate.
        "reversible": _as_bool(action.get("reversible"), default=False),
        "external": _as_bool(action.get("external"), default=True),
        "cost": _as_cost(action.get("cost")),
        "target": _as_opt_str(action.get("target")),
        "details": dict(action["details"]) if isinstance(action.get("details"), Mapping) else None,
    }


def _as_str(v: Any) -> str:
    return v if isinstance(v, str) else ("" if v is None else str(v))


def _as_opt_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = v if isinstance(v, str) else str(v)
    return s or None


def _as_bool(v: Any, *, default: bool) -> bool:
    """Strict-ish bool: real bools pass through; ``None``/missing -> ``default``;
    a few obvious string spellings are honored; anything else falls back to the
    (cautious) default rather than Python's loose truthiness."""
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    if isinstance(v, str):
        low = v.strip().lower()
        if low in ("true", "yes", "y", "1"):
            return True
        if low in ("false", "no", "n", "0", ""):
            return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return bool(v)
    return default


def _as_cost(v: Any) -> Optional[float]:
    """A non-negative float, or ``None``. A malformed cost becomes ``None``
    (unknown), not ``0`` — 'unknown cost' must not read as 'free'."""
    if v is None or isinstance(v, bool):
        return None
    try:
        c = float(v)
    except (TypeError, ValueError):
        return None
    if c != c:  # NaN
        return None
    return abs(c)


# ---------------------------------------------------------------------------
# Pure classification — testable, no I/O
# ---------------------------------------------------------------------------


def _haystack(action: dict) -> str:
    """All the human-facing text of an action, lowercased, for marker scans:
    kind + summary + target + a flat dump of details values."""
    parts = [action.get("kind", ""), action.get("summary", ""), action.get("target", "") or ""]
    details = action.get("details")
    if isinstance(details, Mapping):
        for k, val in details.items():
            parts.append(_as_str(k))
            parts.append(_as_str(val))
    return " ".join(p for p in parts if p).lower()


def is_payment(action: Any) -> bool:
    """True if this action moves money.

    PURE. An action is a payment when its kind is ``"payment"``, when it carries
    a positive ``cost``, or when any money-moving keyword (see
    :data:`PAYMENT_MARKERS`) appears in its kind / summary / target / details.
    Deliberately broad: a false positive just forces an extra confirmation, a
    false negative could move money silently.
    """
    a = normalize(action)
    if a["kind"] == "payment":
        return True
    cost = a["cost"]
    if cost is not None and cost > 0:
        return True
    hay = _haystack(a)
    return any(marker in hay for marker in PAYMENT_MARKERS)


def is_destructive(action: Any) -> bool:
    """True if this action is clearly, irrecoverably destructive (``rm -rf``,
    ``drop table``, force-push, disk format, ...). PURE. Forces ``block`` even if
    the caller (wrongly) marked it reversible."""
    return any(marker in _haystack(normalize(action)) for marker in DESTRUCTIVE_MARKERS)


def is_destructive_command(command: str) -> bool:
    """True if a raw shell command string matches a destructive marker. Pure —
    the destructive floor's check for ``run_command``."""
    c = (command or "").lower()
    return any(marker in c for marker in DESTRUCTIVE_MARKERS)


def _escalate(a: str, b: str) -> str:
    """Return the more restrictive of two gate levels (block > confirm > auto)."""
    return a if LEVELS.index(a) >= LEVELS.index(b) else b


def gate_level(action: Any) -> str:
    """Classify how an action must be gated: ``"auto"`` | ``"confirm"`` | ``"block"``.

    PURE. No prompting, no I/O — just the policy. The rules, in order of
    precedence (most restrictive wins; ties escalate):

    - **block** — the action moves money (:func:`is_payment`) OR is clearly
      destructive (:func:`is_destructive`). Never auto-runnable; a human must
      explicitly approve, and is warned. This is the hard floor.
    - **auto** — reversible AND not external AND not a payment. A purely local,
      undoable, in-repo move (e.g. editing a tracked file) that the caller may
      run un-prompted if it has opted into ``auto_ok``.
    - **confirm** — everything else: external OR irreversible (but not a money /
      destructive move). Always asked.

    Fail-closed: an unrecognizable action normalizes to
    ``reversible=False, external=True`` and therefore lands at ``confirm`` at the
    gentlest, ``block`` if it trips a marker — never ``auto``.
    """
    a = normalize(action)

    # 1) Hard floor: money or clear destruction can never be auto-run.
    if is_payment(a) or is_destructive(a):
        return BLOCK

    reversible = a["reversible"]
    external = a["external"]

    # 2) The only path to auto: reversible, internal, non-payment.
    if reversible and not external:
        level = AUTO
    else:
        # 3) External or irreversible -> confirm.
        level = CONFIRM

    # Belt-and-suspenders: never *lower* below what the flags imply. (No-op given
    # the branches above, but keeps the "escalate, never relax" invariant explicit
    # so future edits to the branch logic can't accidentally relax a gate.)
    floor = CONFIRM if (external or not reversible) else AUTO
    return _escalate(level, floor)


def describe(action: Any) -> str:
    """A clear one-line description of what ronin wants to do.

    Shape::

        ronin wants to: <summary>  [external · costs $X · irreversible]

    The trailing bracket lists only the tags that apply (external, cost,
    irreversible, and — for the strongest signal — "MOVES MONEY" /
    "DESTRUCTIVE"). With no tags (a plain internal reversible move) the bracket
    is omitted. PURE.
    """
    a = normalize(action)
    summary = a["summary"] or a["kind"]
    tags: list[str] = []

    if is_payment(a):
        tags.append("MOVES MONEY")
    if is_destructive(a):
        tags.append("DESTRUCTIVE")
    if a["external"]:
        tags.append("external")
    cost = a["cost"]
    if cost is not None and cost > 0:
        tags.append(f"costs ${cost:g}")
    if not a["reversible"]:
        tags.append("irreversible")

    line = f"ronin wants to: {summary}"
    if tags:
        line += "  [" + " · ".join(tags) + "]"
    return line


# ---------------------------------------------------------------------------
# The one impure entry point: ask the human.
# ---------------------------------------------------------------------------


def approve(action: Any, *, console: Any = None, auto_ok: bool = False) -> bool:
    """Gate an action, returning whether it may proceed.

    This is the ONLY function here that talks to a human. It classifies via the
    pure :func:`gate_level`, renders :func:`describe`, and then:

    - ``auto``    : returns ``True`` *without asking* iff ``auto_ok`` is set;
                    otherwise asks (so a caller that hasn't opted into auto still
                    gets a confirmation rather than a silent action).
    - ``confirm`` : ALWAYS asks the human y/N.
    - ``block``   : can NEVER be auto-approved. Always asks, regardless of
                    ``auto_ok``, and prints a loud warning that this moves money
                    or is irreversible before asking.

    The prompt is **default-deny**: an empty answer, EOF (no interactive stdin),
    or any read error returns ``False``. Only an explicit yes proceeds.

    ``console`` is an optional Rich console used for nicer rendering; everything
    degrades to plain ``print``/``input`` without it, so this works headless.
    """
    a = normalize(action)
    level = gate_level(a)
    description = describe(a)

    # auto + caller opted in: the single path that proceeds without a prompt.
    if level == AUTO and auto_ok:
        return True

    _render_request(description, level, console)

    # Block-level actions get a loud, explicit money/irreversible warning and can
    # never be silently approved — auto_ok is ignored here by construction (we
    # already returned above only for AUTO).
    if level == BLOCK:
        _render_block_warning(a, console)

    answer = _ask_yes_no(level, console)
    return answer is True


# --- rendering / prompting helpers (impure, isolated) ----------------------


def _render_request(description: str, level: str, console: Any) -> None:
    """Show the action and its gate level. Rich panel if a console is given,
    else plain text. Never raises (rendering must not break the gate)."""
    if console is None:
        print(description)
        return
    try:
        from rich.panel import Panel
        style = {AUTO: "cyan", CONFIRM: "yellow", BLOCK: "red"}.get(level, "yellow")
        title = f"approval · {level}"
        console.print(Panel(description, title=title, border_style=style, padding=(0, 2)))
    except Exception:  # noqa: BLE001 - degrade to plain text, never crash the gate
        try:
            console.print(description)
        except Exception:  # noqa: BLE001
            print(description)


def _render_block_warning(action: dict, console: Any) -> None:
    """The loud warning printed before a block-level prompt. States plainly that
    this moves money and/or is irreversible — the user must opt in by hand."""
    reasons: list[str] = []
    if is_payment(action):
        cost = action.get("cost")
        if isinstance(cost, (int, float)) and cost and cost > 0:
            reasons.append(f"this MOVES MONEY (${float(cost):g})")
        else:
            reasons.append("this MOVES MONEY")
    if is_destructive(action):
        reasons.append("this is DESTRUCTIVE / irreversible")
    if not reasons:
        # Reached block without a payment/destructive reason should be impossible,
        # but stay loud rather than silent if policy ever changes.
        reasons.append("this is an irreversible / high-risk action")
    msg = ("BLOCKED BY DEFAULT: " + "; ".join(reasons)
           + ". ronin will NOT do this on its own — approve by hand only if you "
             "are sure.")
    if console is None:
        print(msg)
        return
    try:
        console.print(f"[bold red]{msg}[/bold red]")
    except Exception:  # noqa: BLE001
        print(msg)


def _ask_yes_no(level: str, console: Any) -> Optional[bool]:
    """Prompt y/N and return True/False. DEFAULT-DENY: empty input, EOF (no TTY),
    or any read error -> ``False``. Returns ``None`` only as an internal 'errored'
    sentinel that the caller treats as deny."""
    prompt = "Proceed? [y/N] " if level != BLOCK else "Type 'y' to allow this anyway [y/N] "
    try:
        if console is not None and hasattr(console, "input"):
            raw = console.input(prompt)
        else:
            raw = input(prompt)
    except (EOFError, KeyboardInterrupt):
        # No interactive stdin, or the user interrupted: deny.
        return False
    except Exception:  # noqa: BLE001 - any prompt failure denies
        return False
    ans = (raw or "").strip().lower()
    if ans in ("y", "yes"):
        return True
    return False
