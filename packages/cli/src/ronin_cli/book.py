"""Prepare-and-confirm bookings - `ronin book "<request>"`.

Research a booking (flight, hotel, restaurant, ticket, anything with a buy step)
and PREPARE it up to the payment step, then STOP. The user always confirms and
pays themselves. This is a hard rule, enforced in code below and stated in the
docs: this command never submits a payment or final purchase.

What it does:

1. Reads a natural-language request.
2. Gathers candidate options into a structured summary (option, price, link,
   key details) using web search (free, no key; optional and lazy).
3. Optionally drives the browser to PRE-FILL a form up to but NOT including
   payment (the browser extra is optional and lazy; if absent we fall back to
   search plus manual steps).
4. Returns the summary plus a next-step link for the user to confirm and pay.

The payment guard:

``FORBIDDEN_ACTIONS`` lists the words for any final pay/submit/purchase step.
``assert_not_payment`` raises ``PaymentBlocked`` if any code path tries to take
such an action. The browser pre-fill path runs every intended action through
this guard BEFORE doing it, so a "click Pay" can never reach the browser. Tests
assert no forbidden action is ever taken.

Everything is offline-safe by default: ``prepare_booking`` takes ``search_fn``
and ``browser_prefill_fn`` as injectable callables, both optional. With neither,
you still get a structured prepare-only result and the manual next steps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

# Words that mark a final money-moving / commit step. If any prepared browser
# action contains one of these, we refuse to take it. Keep this list blunt and
# obvious; matching is a plain lowercase substring check.
FORBIDDEN_ACTIONS = (
    "pay",
    "payment",
    "purchase",
    "buy",
    "checkout",
    "place order",
    "place-order",
    "submit order",
    "complete booking",
    "complete order",
    "confirm and pay",
    "book now",
    "charge",
)


class PaymentBlocked(Exception):
    """Raised when code tries to take a pay/submit/purchase action. Hard stop."""


def is_payment_action(action: str) -> bool:
    """True if ``action`` describes a final pay/submit/purchase step."""
    low = (action or "").lower()
    return any(word in low for word in FORBIDDEN_ACTIONS)


def assert_not_payment(action: str) -> None:
    """Guard. Raise ``PaymentBlocked`` if ``action`` is a payment/submit step.

    Call this before performing ANY prepared action. This is the enforcement
    point for the hard rule: ronin book never pays.
    """
    if is_payment_action(action):
        raise PaymentBlocked(
            f"refusing to take a payment/submit action: {action!r}. "
            "ronin book prepares only; you confirm and pay yourself."
        )


@dataclass
class BookingOption:
    """One candidate the user can pick from."""

    option: str                       # short label, e.g. "United UA123 nonstop"
    price: str = ""                   # as text, e.g. "$248" (no currency math)
    link: str = ""                    # where the user goes to confirm and pay
    details: str = ""                 # key details (times, stops, address, ...)

    def as_dict(self) -> dict:
        return {"option": self.option, "price": self.price,
                "link": self.link, "details": self.details}


@dataclass
class PrepareResult:
    """The prepare-only result handed back to the user. Never a confirmation."""

    request: str
    options: list[BookingOption] = field(default_factory=list)
    next_step_link: str = ""          # the link to confirm and pay
    manual_steps: list[str] = field(default_factory=list)
    browser_prefilled: bool = False   # did we pre-fill a form (never paid)?
    notes: list[str] = field(default_factory=list)
    # Always true: this result is prepare-only and stops before payment.
    prepared_only: bool = True
    paid: bool = False                # always False; never flipped to True

    def as_dict(self) -> dict:
        return {
            "request": self.request,
            "options": [o.as_dict() for o in self.options],
            "next_step_link": self.next_step_link,
            "manual_steps": list(self.manual_steps),
            "browser_prefilled": self.browser_prefilled,
            "notes": list(self.notes),
            "prepared_only": self.prepared_only,
            "paid": self.paid,
        }


# Type aliases for the injected, optional helpers.
SearchFn = Callable[[str, int], str]                  # (query, max_results) -> text
BrowserPrefillFn = Callable[[str, "PrepareResult"], bool]  # (request, result) -> prefilled?


def _parse_search_results(text: str) -> list[BookingOption]:
    """Turn ``web_search`` output into options. Best-effort, never raises.

    web_search returns blocks like::

        1. Title of result
           https://example.com/path
           snippet text...

    We pull the title (option), the first URL (link), and the snippet (details).
    Price is left blank unless an obvious price token appears in the snippet.
    """
    import re

    options: list[BookingOption] = []
    if not text or text.lower().startswith(("error", "no results")):
        return options

    price_re = re.compile(r"(?:[$£€]\s?\d[\d,]*(?:\.\d{2})?|\b\d+\s?(?:usd|eur|gbp)\b)",
                          re.IGNORECASE)
    url_re = re.compile(r"https?://\S+")
    # Split into numbered blocks: "1. ...", "2. ...".
    blocks = re.split(r"\n(?=\s*\d+\.\s)", text)
    for block in blocks:
        m = re.match(r"\s*\d+\.\s*(.+)", block)
        if not m:
            continue
        title = m.group(1).strip()
        rest = block[m.end():]
        url_m = url_re.search(rest)
        link = url_m.group(0).rstrip(".,)") if url_m else ""
        # details: the lines after the url, collapsed.
        details = " ".join(
            line.strip() for line in rest.splitlines()
            if line.strip() and not url_re.search(line)
        ).strip()
        price_m = price_re.search(title + " " + details)
        price = price_m.group(0).strip() if price_m else ""
        options.append(BookingOption(option=title[:120], price=price,
                                     link=link, details=details[:240]))
    return options


def _default_manual_steps(request: str, has_options: bool) -> list[str]:
    """The manual path the user follows to finish (confirm and pay themselves)."""
    steps = [
        "Open the link for the option you want.",
        "Review the details (dates, times, price, cancellation policy).",
        "Enter your traveler / contact details if the form is not pre-filled.",
        "At the payment step, enter your own payment details and submit.",
        "ronin stops before this step: you confirm and pay yourself.",
    ]
    if not has_options:
        steps.insert(0, "Search did not return options; search manually for "
                        f"'{request}' and compare a few results.")
    return steps


def prepare_booking(
    request: str,
    *,
    search_fn: Optional[SearchFn] = None,
    browser_prefill_fn: Optional[BrowserPrefillFn] = None,
    max_results: int = 5,
) -> PrepareResult:
    """Research and PREPARE a booking, stopping before payment.

    ``search_fn``           optional ``(query, max_results) -> text`` (e.g.
                            ``web_tools.web_search``). When None, no web search
                            is done; you still get a prepare-only result.
    ``browser_prefill_fn``  optional ``(request, result) -> bool`` that drives
                            the browser to pre-fill a form up to but NOT
                            including payment, returning whether it pre-filled.
                            When None (browser extra absent), we fall back to
                            search plus manual steps.

    The function never takes a payment action. The browser pre-fill callable is
    expected to route its intended actions through ``assert_not_payment``; if it
    raises ``PaymentBlocked``, we record that and return a prepare-only result
    anyway (we never pay).
    """
    request = (request or "").strip()
    result = PrepareResult(request=request)

    # 1) Research: gather candidate options via the optional search function.
    if search_fn is not None and request:
        try:
            raw = search_fn(request, max_results)
        except Exception as e:  # noqa: BLE001 - search is best-effort
            result.notes.append(f"search failed: {e}")
        else:
            result.options = _parse_search_results(raw)[:max_results]
            if not result.options:
                result.notes.append("search returned no usable options.")
    elif search_fn is None:
        result.notes.append("web search not used (offline / not provided).")

    # next-step link: the first option's link, if any.
    for o in result.options:
        if o.link:
            result.next_step_link = o.link
            break

    # 2) Optional: drive the browser to pre-fill a form, never past payment.
    if browser_prefill_fn is not None:
        try:
            prefilled = browser_prefill_fn(request, result)
            result.browser_prefilled = bool(prefilled)
            if result.browser_prefilled:
                result.notes.append(
                    "browser pre-filled a form up to the payment step "
                    "(stopped before pay)."
                )
        except PaymentBlocked as e:
            # The guard fired: some action tried to pay. We never pay. Record it.
            result.notes.append(f"blocked a payment action during pre-fill: {e}")
        except Exception as e:  # noqa: BLE001 - browser is best-effort
            result.notes.append(f"browser pre-fill unavailable: {e}")
    else:
        result.notes.append(
            "browser extra not installed; prepared a summary instead. "
            "Enable with: pip install 'ronin-cli[browser]'"
        )

    # 3) Manual next steps so the user can finish (confirm and pay themselves).
    result.manual_steps = _default_manual_steps(request, bool(result.options))

    # Invariant: this is always prepare-only and never paid.
    result.prepared_only = True
    result.paid = False
    return result
