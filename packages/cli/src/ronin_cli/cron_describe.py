"""Explain a cron expression in plain English — `ronin cron`.

Reading ``*/15 9-17 * * 1-5`` at a glance is miserable. This turns a standard
5-field cron expression into a sentence ("Every 15 minutes, between 09:00 and
17:59, Monday through Friday"). Pure and unit-tested — no network, no LLM.
"""
from __future__ import annotations

_DOW = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
_MONTH = ["", "January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
_ALIASES = {
    "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}


def _list_phrase(items: list[str], names=None) -> str:
    if names:
        items = [names[int(i)] for i in items]
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _field(spec: str, unit: str, names=None) -> str | None:
    """Describe one field as a phrase, or None if it's '*' (no constraint)."""
    if spec == "*":
        return None
    if spec.startswith("*/"):
        return f"every {spec[2:]} {unit}s"
    if "-" in spec and "," not in spec:
        a, b = spec.split("-", 1)
        if names:
            return f"{names[int(a)]} through {names[int(b)]}"
        return f"{unit}s {a} through {b}"
    parts = spec.split(",")
    return f"{unit} " + _list_phrase(parts, names) if not names else _list_phrase(parts, names)


def _time_phrase(mn: str, hr: str) -> str:
    if mn == "*" and hr == "*":
        return "Every minute"
    if mn.startswith("*/") and hr == "*":
        return f"Every {mn[2:]} minutes"
    if mn.isdigit() and hr.isdigit():
        return f"At {int(hr):02d}:{int(mn):02d}"
    # single minute past several explicit hours -> "At 09:00 and 17:00"
    if mn.isdigit() and ("," in hr or "-" in hr) and all(p.isdigit() for p in hr.replace("-", ",").split(",")):
        if "-" in hr:
            a, b = hr.split("-")
            return f"At minute {int(mn)} past every hour from {int(a):02d} to {int(b):02d}"
        return "At " + _list_phrase([f"{int(h):02d}:{int(mn):02d}" for h in hr.split(",")])
    seg: list[str] = []
    if mn == "*":
        seg.append("every minute")
    elif mn.startswith("*/"):
        seg.append(f"every {mn[2:]} minutes")
    elif "-" in mn:
        a, b = mn.split("-"); seg.append(f"at minutes {a} through {b}")
    elif "," in mn:
        seg.append("at minutes " + _list_phrase(mn.split(",")))
    else:
        seg.append(f"at minute {int(mn)}")
    if hr != "*":
        if hr.startswith("*/"):
            seg.append(f"of every {hr[2:]} hours")
        elif "-" in hr and "," not in hr:
            a, b = hr.split("-"); seg.append(f"between {int(a):02d}:00 and {int(b):02d}:59")
        elif "," in hr:
            seg.append("during hours " + _list_phrase(hr.split(",")))
        else:
            seg.append(f"of hour {int(hr)}")
    return ", ".join(seg).capitalize()


def describe_cron(expr: str) -> str:
    """Plain-English description of a 5-field cron expression (or @alias). Pure."""
    expr = _ALIASES.get(expr.strip(), expr.strip())
    parts = expr.split()
    if len(parts) != 5:
        return "invalid cron expression (expected 5 fields or a @alias)"
    mn, hr, dom, mon, dow = parts

    clauses = [_time_phrase(mn, hr)]
    dm = _field(dom, "day-of-month")
    if dm:
        clauses.append("on " + dm)
    mo = _field(mon, "month", _MONTH)
    if mo:
        clauses.append("in " + mo)
    dw = _field(dow, "day", _DOW)
    if dw:
        clauses.append(dw if "through" in dw else "on " + dw)

    return ", ".join(clauses) + "."
