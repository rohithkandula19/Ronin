"""Explain & convert unix file permissions — `ronin chmod`.

Give it ``644`` or ``rw-r--r--`` and ronin shows both forms plus a plain-English
breakdown for owner / group / others. Pure and unit-tested — no shell-out.
"""
from __future__ import annotations

import re

_PERMS = ["---", "--x", "-w-", "-wx", "r--", "r-x", "rw-", "rwx"]


def octal_to_symbolic(octal: str) -> str | None:
    """'755' -> 'rwxr-xr-x'. Accepts a leading mode digit (e.g. '0755'). Pure."""
    o = str(octal).strip().lstrip("0o") or "0"
    if len(o) == 4:
        o = o[1:]
    if not re.fullmatch(r"[0-7]{3}", o):
        return None
    return "".join(_PERMS[int(d)] for d in o)


def symbolic_to_octal(sym: str) -> str | None:
    """'rwxr-xr-x' -> '755' (ignores a leading file-type char). Pure."""
    s = sym.strip()
    if len(s) == 10:
        s = s[1:]
    if not re.fullmatch(r"[rwx-]{9}", s):
        return None
    digits = []
    for i in range(0, 9, 3):
        g = s[i:i + 3]
        digits.append(str((4 if g[0] == "r" else 0) + (2 if g[1] == "w" else 0) + (1 if g[2] == "x" else 0)))
    return "".join(digits)


def _describe_triplet(sym: str) -> str:
    perms = []
    if sym[0] == "r": perms.append("read")
    if sym[1] == "w": perms.append("write")
    if sym[2] == "x": perms.append("execute")
    return ", ".join(perms) if perms else "no access"


def explain_perm(spec: str) -> dict:
    """Normalize a permission given as octal or symbolic into both forms + a
    per-class English description. Pure."""
    spec = spec.strip()
    if re.fullmatch(r"0?[0-7]{3}", spec) or re.fullmatch(r"[0-7]{4}", spec):
        sym = octal_to_symbolic(spec)
        octal = spec.lstrip("0o").rjust(3, "0")[-3:]
    elif re.fullmatch(r".?[rwx-]{9}", spec):
        sym = spec[-9:]
        octal = symbolic_to_octal(sym)
    else:
        return {"error": f"not a valid permission: '{spec}' (try 644 or rw-r--r--)"}
    if sym is None or octal is None:
        return {"error": f"not a valid permission: '{spec}'"}
    return {
        "octal": octal, "symbolic": sym,
        "owner": _describe_triplet(sym[0:3]),
        "group": _describe_triplet(sym[3:6]),
        "others": _describe_triplet(sym[6:9]),
    }
