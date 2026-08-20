"""Destroy memorizable specifics so the model learns behaviour, not fixtures.

The adapter is supposed to make a small model *ronin-native* — correct tool syntax,
correct gate behaviour, correct recovery, correct planning — and explicitly **not**
to teach it coding knowledge. A 1.5B model shown the same forty fixture paths a few
thousand times learns the paths. So every renameable surface is renamed: directory
segments, file stems, module names, symbol names.

The invariant that makes this safe is **one table, one pass**:

* the substitution table is built once per trajectory, from that trajectory's own
  vocabulary, and then applied to *every* string in it — prompt, assistant prose,
  tool arguments, tool observations;
* it is applied in a single regex pass with the longest alternative first, so a
  replacement can never be re-replaced and no ordering question exists.

Consistency is therefore structural rather than maintained. A rename applied to the
prompt cannot fail to apply to the assistant turn, because both go through the same
call. The alternative — walking the trajectory and renaming each field with its own
logic — is how you teach a model to contradict itself inside one example, so it is
not offered. The invariant is asserted directly in the tests anyway.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from random import Random
from typing import Any

from .trajectory import RecordedCall, RecordedResult, Step, Trajectory

#: Word pools the renamer draws from. Deliberately dull, domain-neutral nouns: a
#: pool of realistic-looking framework names would smuggle in exactly the kind of
#: specific the augmentation exists to remove.
_STEM_POOL: tuple[str, ...] = (
    "alder", "basalt", "cinder", "dovetail", "ember", "flint", "girder", "harrow",
    "indigo", "juniper", "kelvin", "lathe", "marlin", "nutmeg", "onyx", "plinth",
    "quarry", "rivet", "sable", "trellis", "umber", "vellum", "willow", "yarrow",
)
_DIR_POOL: tuple[str, ...] = (
    "core", "lib", "internal", "engine", "runtime", "shared", "domain", "adapters",
    "gateway", "pipeline", "services", "kernel",
)
_SYMBOL_POOL: tuple[str, ...] = (
    "resolve", "compose", "hydrate", "collapse", "reconcile", "dispatch", "anneal",
    "coalesce", "unfurl", "distill", "graft", "quantize",
)

#: Path-shaped values in tool arguments. Restricted to what a repo path looks like
#: so a shell command or a prose sentence is not mistaken for a filename.
_PATH_RE = re.compile(r"(?:[\w.-]+/)*[\w.-]+\.[A-Za-z][A-Za-z0-9]{0,7}")

#: snake_case / lowerCamel identifiers long enough to be a real symbol.
_SYMBOL_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b|\b[a-z]{4,}[A-Z][A-Za-z0-9]*\b")

#: Tokens that look like symbols but are protocol, not fixture. Renaming any of
#: these would rewrite the very behaviour the example is meant to teach — a row
#: whose target calls ``read_flint`` instead of ``read_file`` teaches a tool that
#: does not exist, which is the worst possible outcome of an augmentation pass.
_PROTOCOL_WORDS: frozenset[str] = frozenset(
    {
        "tool_call", "tool_calls", "tool_call_id", "old_string", "new_string",
        "replace_all", "file_path", "read_file", "write_file", "edit_file",
        "multi_edit", "list_files", "search_files", "run_command", "repo_map",
        "run_background", "background_status", "background_logs", "stop_background",
        "bash_output", "todo_write", "web_fetch", "web_search",
    }
)

#: Directory and stem names that are conventions rather than fixtures. Renaming
#: ``tests`` or ``pyproject`` changes what the example is *about*.
_STRUCTURAL_NAMES: frozenset[str] = frozenset(
    {
        "src", "tests", "test", "docs", "scripts", "bin", "build", "dist", "config",
        "pyproject", "setup", "readme", "makefile", "requirements", "init",
        "main", "conftest", "package", "tsconfig", "dockerfile",
    }
)


@dataclass(frozen=True, slots=True)
class Renaming:
    """One trajectory's substitution table, plus the seed that produced it.

    Frozen and seed-stamped so an audit can regenerate the exact perturbation, or
    invert it, from the row's provenance alone.
    """

    seed: int
    table: Mapping[str, str]

    def is_empty(self) -> bool:
        return not self.table

    def apply(self, text: str) -> str:
        """Rewrite ``text`` in one pass. Longest key first, so no key is a prefix
        problem and no substituted value can be substituted again."""
        if not text or not self.table:
            return text
        keys = sorted(self.table, key=len, reverse=True)
        pattern = re.compile("|".join(re.escape(k) for k in keys))
        return pattern.sub(lambda m: self.table[m.group(0)], text)

    def apply_args(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Rewrite string values (and strings nested in lists) — never the keys.

        Keys are the tool's schema: renaming ``path`` to something else produces a
        call the registry rejects, so the table is applied to values only.
        """
        return {k: self._value(v) for k, v in arguments.items()}

    def _value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.apply(value)
        if isinstance(value, list):
            return [self._value(v) for v in value]
        if isinstance(value, dict):
            return {k: self._value(v) for k, v in value.items()}
        return value


def _vocabulary(trajectory: Trajectory) -> tuple[set[str], set[str], set[str]]:
    """The renameable names in this trajectory: (dir segments, file stems, symbols).

    Drawn from tool arguments and the task prompt only. Tool *observations* are not
    mined for new names — a file listing would contribute half the repo, and every
    name in the table costs a regex alternative on every string in the trajectory.
    """
    dirs: set[str] = set()
    stems: set[str] = set()
    symbols: set[str] = set()

    def scan_path(raw: str) -> None:
        for match in _PATH_RE.finditer(raw):
            parts = match.group(0).split("/")
            for segment in parts[:-1]:
                if segment.isidentifier() and segment not in _STRUCTURAL_NAMES:
                    dirs.add(segment)
            stem = parts[-1].rsplit(".", 1)[0]
            if stem.isidentifier() and stem not in _STRUCTURAL_NAMES:
                stems.add(stem)

    def scan_symbols(raw: str) -> None:
        for match in _SYMBOL_RE.finditer(raw):
            token = match.group(0)
            if token not in _PROTOCOL_WORDS and token not in _STRUCTURAL_NAMES:
                symbols.add(token)

    scan_path(trajectory.prompt)
    scan_symbols(trajectory.prompt)
    for _step, call in trajectory.calls():
        for value in call.arguments.values():
            if isinstance(value, str):
                scan_path(value)
                scan_symbols(value)
    # A stem that is also picked up as a symbol must be renamed as a stem only,
    # or the two tables would disagree about the same token.
    symbols -= stems
    symbols -= dirs
    return dirs, stems, symbols


def _draw(pool: Iterable[str], rng: Random, taken: set[str]) -> str | None:
    """A name from ``pool`` not already used or already present in the trajectory."""
    candidates = [c for c in pool if c not in taken]
    if not candidates:
        return None
    choice = rng.choice(sorted(candidates))
    taken.add(choice)
    return choice


def plan_renaming(trajectory: Trajectory, *, seed: int) -> Renaming:
    """Build the substitution table for one trajectory. Deterministic in ``seed``."""
    rng = Random(seed)
    dirs, stems, symbols = _vocabulary(trajectory)
    taken: set[str] = set(dirs) | set(stems) | set(symbols)
    table: dict[str, str] = {}
    for group, pool in ((dirs, _DIR_POOL), (stems, _STEM_POOL), (symbols, _SYMBOL_POOL)):
        for name in sorted(group):
            replacement = _draw(pool, rng, taken)
            if replacement is not None:
                table[name] = replacement
    return Renaming(seed=seed, table=table)


def augment(trajectory: Trajectory, *, seed: int, run_id_suffix: str = "") -> Trajectory:
    """A perturbed copy of ``trajectory``, renamed consistently throughout.

    The ``run_id`` gains a suffix so the augmented row is never confused with the
    real run it came from; provenance still names the original task and model.
    ``system`` and tool *schemas* are untouched — they are the harness's own text,
    identical at inference, and rewriting them would reintroduce the very
    train/inference prompt divergence this pipeline exists to avoid.
    """
    renaming = plan_renaming(trajectory, seed=seed)
    suffix = run_id_suffix or f"aug{seed}"
    return Trajectory(
        task_id=trajectory.task_id,
        run_id=f"{trajectory.run_id}#{suffix}",
        prompt=renaming.apply(trajectory.prompt),
        model=trajectory.model,
        system=trajectory.system,
        tools=trajectory.tools,
        steps=tuple(_augment_step(step, renaming) for step in trajectory.steps),
        verified=trajectory.verified,
        source=trajectory.source,
    )


def _augment_step(step: Step, renaming: Renaming) -> Step:
    return Step(
        index=step.index,
        text=renaming.apply(step.text),
        calls=tuple(
            RecordedCall(
                id=call.id,
                name=call.name,
                arguments=renaming.apply_args(call.arguments),
            )
            for call in step.calls
        ),
        results=tuple(
            RecordedResult(
                call_id=result.call_id,
                name=result.name,
                ok=result.ok,
                content=renaming.apply(result.content),
                error=renaming.apply(result.error),
            )
            for result in step.results
        ),
    )


__all__ = ["Renaming", "augment", "plan_renaming"]
