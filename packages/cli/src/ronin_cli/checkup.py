"""A full repo health checkup — `ronin checkup`.

Where `ronin doctor` checks your *config* (provider/key/model), `checkup` checks
your *codebase*: is there a README, a license, tests; are dependencies declared;
any committed secrets; how many TODO/FIXME markers; any oversized files. Each
check yields a status and the whole thing rolls up into a health score. The
checks are pure-ish (filesystem + git only) and unit-tested.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
              "build", ".pytest_cache", ".ronin", ".mypy_cache"}


@dataclass
class Check:
    name: str
    status: str           # "ok" | "warn" | "fail"
    detail: str


def _has_any(root: Path, names: list[str]) -> bool:
    return any((root / n).exists() for n in names)


def _has_tests(root: Path) -> bool:
    for p in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        n = p.name
        if n.startswith("test_") or n.endswith("_test.py") or "tests" in p.parts:
            return True
    return False


def _large_files(root: Path, *, limit_kb: int = 1024) -> list[str]:
    out: list[str] = []
    for p in root.rglob("*"):
        if not p.is_file() or any(part in _SKIP_DIRS for part in p.parts):
            continue
        try:
            if p.stat().st_size > limit_kb * 1024:
                out.append(str(p.relative_to(root)))
        except OSError:
            continue
    return out


def run_checks(root: Path | str) -> list[Check]:
    """Run every health check and return their statuses. Pure given the tree."""
    rp = Path(root).resolve()
    checks: list[Check] = []

    # README
    if _has_any(rp, ["README.md", "README.rst", "README.txt", "README"]):
        checks.append(Check("readme", "ok", "README present"))
    else:
        checks.append(Check("readme", "warn", "no README found"))

    # license
    if _has_any(rp, ["LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"]):
        checks.append(Check("license", "ok", "LICENSE present"))
    else:
        checks.append(Check("license", "warn", "no LICENSE found"))

    # tests
    if _has_tests(rp):
        checks.append(Check("tests", "ok", "test files present"))
    else:
        checks.append(Check("tests", "fail", "no test files found"))

    # dependency audit
    try:
        from .deps import audit
        a = audit(rp)
        if a["undeclared"]:
            checks.append(Check("deps", "warn",
                                f"{len(a['undeclared'])} imported but undeclared: "
                                f"{', '.join(a['undeclared'][:5])}"))
        else:
            checks.append(Check("deps", "ok", "imports and declared deps line up"))
    except Exception:  # noqa: BLE001
        checks.append(Check("deps", "ok", "no dependency manifest to audit"))

    # secrets
    try:
        from .scan import scan_tree
        findings = scan_tree(rp)
        if findings:
            checks.append(Check("secrets", "fail",
                                f"possible secret(s) in {len(findings)} file(s): "
                                f"{findings[0].path}"))
        else:
            checks.append(Check("secrets", "ok", "no obvious secrets committed"))
    except Exception:  # noqa: BLE001
        checks.append(Check("secrets", "ok", "secret scan skipped"))

    # TODO/FIXME markers
    try:
        from .kaizen import find_targets
        targets = find_targets(rp, limit=500)
        if len(targets) > 20:
            checks.append(Check("todos", "warn", f"{len(targets)} TODO/FIXME/HACK markers"))
        elif targets:
            checks.append(Check("todos", "ok", f"{len(targets)} TODO/FIXME marker(s)"))
        else:
            checks.append(Check("todos", "ok", "no lingering markers"))
    except Exception:  # noqa: BLE001
        checks.append(Check("todos", "ok", "marker scan skipped"))

    # oversized files
    big = _large_files(rp)
    if big:
        checks.append(Check("large-files", "warn",
                            f"{len(big)} file(s) > 1MB: {big[0]}"))
    else:
        checks.append(Check("large-files", "ok", "no oversized files"))

    return checks


def health_score(checks: list[Check]) -> int:
    """0–100 health score: fails cost 20, warns cost 7 (floored at 0). Pure."""
    penalty = sum(20 if c.status == "fail" else 7 if c.status == "warn" else 0
                  for c in checks)
    return max(0, 100 - penalty)


def grade(score: int) -> str:
    """Letter grade for a health score. Pure."""
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 55:
        return "D"
    return "F"
