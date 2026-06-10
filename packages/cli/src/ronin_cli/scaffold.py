"""Generate a new component that fits your project — `ronin scaffold "<what>"`.

ronin detects your stack (and existing conventions), then writes a new component
PLUS its test, matching how the rest of the repo is structured — not generic
boilerplate. The stack detection + prompt are pure and unit-tested; the files are
written through the normal gated, diff-previewed agent.
"""
from __future__ import annotations

from pathlib import Path

# file marker → stack name, checked at the repo root.
_MARKERS: list[tuple[str, str]] = [
    ("pyproject.toml", "python"), ("setup.py", "python"), ("requirements.txt", "python"),
    ("package.json", "node"), ("tsconfig.json", "typescript"),
    ("Cargo.toml", "rust"), ("go.mod", "go"), ("pom.xml", "java"),
    ("build.gradle", "java"), ("Gemfile", "ruby"), ("composer.json", "php"),
    ("pubspec.yaml", "dart"),
]


def detect_stack(root: Path | str) -> list[str]:
    """The project's stack(s) from root marker files, in priority order. Pure-ish."""
    rp = Path(root)
    found: list[str] = []
    for marker, name in _MARKERS:
        if (rp / marker).is_file() and name not in found:
            found.append(name)
    return found or ["unknown"]


def scaffold_prompt(what: str, stack: list[str]) -> str:
    """Build the scaffold instruction, anchored to the detected stack. Pure."""
    stack_str = ", ".join(stack)
    return (
        f"Scaffold this component: {what}\n\n"
        f"This is a {stack_str} project. FIRST look at a couple of existing files "
        "to match the repo's conventions (structure, naming, imports, test layout). "
        "Then create the new component AND a matching test for it, in the right "
        "directories. Keep it minimal but real — no TODO stubs. Run the test to "
        "confirm it passes."
    )
