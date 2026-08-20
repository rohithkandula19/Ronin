"""Layered permission config, with provenance and no silent failures.

Later layers override earlier ones::

    builtin defaults
      < ~/.ronin/settings.json                (the user, across all projects)
      < ./.ronin/settings.json               (the project, committed)
      < ./.ronin/settings.local.json         (this checkout only, gitignored)
      < --flags                              (this invocation only)

Merge semantics, and why they are not uniform
---------------------------------------------

**Scalars override. Rule lists append.**

Scalars override because that is what a scalar means: if the project says
``mode: auto_edit`` and you pass ``--mode ask``, you meant ask.

Rule lists append because the alternative is a live grenade. If a later layer *replaced*
the rule list, then a project ``settings.json`` containing one convenience rule would
silently discard every builtin rule beneath it — including the tool-wide ``ask`` that is
the shell's floor. The failure would be invisible: nothing errors, the file looks right,
and the gate is simply weaker than it reads. Appending means a layer can only ever *add*
a rule; to defeat an earlier rule you write a more specific one, which is visible in
``/doctor`` and attributable to a file.

Provenance is a feature, not diagnostics
----------------------------------------

Every effective rule carries the layer it came from, and :meth:`Settings.provenance`
prints the whole picture. A user who cannot see which file granted a permission cannot
revoke it, and a user who cannot revoke a permission deletes the config and runs with the
gate off. So "which file did this come from" has to be answerable in one command.

One malformed layer does not take down the session
--------------------------------------------------

A JSON syntax error in ``settings.local.json`` must not stop the agent from starting —
that turns a typo into an outage, and an outage into ``--yolo``. Instead the layer is
skipped, the error is recorded in :attr:`Settings.errors` as a named, loud problem, and
the remaining layers load. Same for one bad rule inside an otherwise fine file: that
rule is dropped, the rest of the file still applies, and the error says which index and
why. Unknown keys are errors too — a silently ignored typo is a permission the user
believes they granted.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.types import Mode
from .injection import MIN_TAINT_SPAN
from .policy import (
    AnyUse,
    CommandRegex,
    Decision,
    Exact,
    Matcher,
    PathGlob,
    Rule,
    RuleSet,
    builtin_rules,
)

#: The layer names, in application order. ``flags`` is last because a flag is the most
#: specific statement of intent there is: someone typed it just now.
LAYER_NAMES: tuple[str, ...] = ("builtin", "user", "project", "local", "flags")

USER_SETTINGS = Path(".ronin") / "settings.json"
PROJECT_SETTINGS = Path(".ronin") / "settings.json"
LOCAL_SETTINGS = Path(".ronin") / "settings.local.json"

#: Scalar keys, with the type each must parse to. Anything else in a file is an error,
#: because a silently ignored key is a permission the user thinks they granted.
SCALAR_KEYS: Mapping[str, str] = {
    "mode": "mode",
    "sandbox": "bool",
    "yolo": "bool",
    "protected_branches": "strings",
    "default_decision": "decision",
    "taint_min_span": "int",
    "max_retained_paths": "count_or_null",
    "max_retained_chars": "count_or_null",
    "compaction_escalate": "bool",
}

RULES_KEY = "rules"


@dataclass(frozen=True, slots=True)
class LayerError:
    """A problem in one layer, named loudly enough to fix."""

    layer: str
    path: Path | None
    message: str

    def __str__(self) -> str:
        where = str(self.path) if self.path is not None else f"--{self.layer}"
        return f"{self.layer} layer ({where}): {self.message}"


@dataclass(frozen=True, slots=True)
class Layer:
    """One layer's contribution, kept separately so provenance can be reported."""

    name: str
    path: Path | None
    present: bool
    rules: tuple[Rule, ...] = ()
    scalars: Mapping[str, Any] = field(default_factory=dict)
    errors: tuple[LayerError, ...] = ()

    def describe(self) -> str:
        where = str(self.path) if self.path is not None else "(no file)"
        if self.errors:
            problems = "; ".join(error.message for error in self.errors)
            return f"{self.name:8} {where}  PROBLEM: {problems}"
        if not self.present:
            return f"{self.name:8} {where}  (absent)"
        scalars = ", ".join(f"{key}={value!r}" for key, value in sorted(self.scalars.items()))
        detail = f"{len(self.rules)} rule(s)"
        return f"{self.name:8} {where}  {detail}{'  ' + scalars if scalars else ''}"


@dataclass(frozen=True, slots=True)
class Settings:
    """The resolved configuration, plus how it got that way."""

    workspace_root: Path
    home: Path
    mode: Mode = Mode.ASK
    sandbox: bool = False
    yolo: bool = False
    protected_branches: frozenset[str] = frozenset({"main", "master", "trunk"})
    default_decision: Decision = Decision.ASK
    taint_min_span: int = MIN_TAINT_SPAN
    #: Compaction retention ceilings. ``None`` means no ceiling, which is the default
    #: and is load-bearing — see ``CompactionPolicy.max_retained_paths``. Settable
    #: because compaction *reports* bounding them as the remedy when retained results
    #: alone exceed the trigger, and advice the user cannot act on is not advice.
    max_retained_paths: int | None = None
    max_retained_chars: int | None = None
    #: Let compaction surrender older retained file context by itself rather than
    #: reporting that it cannot fit. Off by default: see ``CompactionPolicy``.
    compaction_escalate: bool = False
    rules: tuple[Rule, ...] = ()
    layers: tuple[Layer, ...] = ()
    errors: tuple[LayerError, ...] = ()
    scalar_sources: Mapping[str, str] = field(default_factory=dict)

    def ruleset(self) -> RuleSet:
        return RuleSet(rules=self.rules, default=self.default_decision)

    @property
    def healthy(self) -> bool:
        """Whether every layer loaded cleanly. ``/doctor`` leads with this."""
        return not self.errors

    def rules_from(self, layer: str) -> tuple[Rule, ...]:
        """Every effective rule contributed by ``layer``."""
        return tuple(rule for rule in self.rules if rule.source == layer)

    def source_of(self, key: str) -> str:
        """Which layer set a scalar. ``"builtin"`` when nothing overrode it."""
        return self.scalar_sources.get(key, "builtin")

    def provenance(self) -> tuple[str, ...]:
        """The report ``/doctor`` prints: every layer, then every scalar's origin."""
        lines = [layer.describe() for layer in self.layers]
        lines.append("")
        for key in SCALAR_KEYS:
            lines.append(f"{key} = {getattr(self, key)!r}  (from {self.source_of(key)})")
        if self.errors:
            lines.append("")
            lines.extend(f"PROBLEM: {error}" for error in self.errors)
        return tuple(lines)


def load_settings(
    *,
    home: Path,
    cwd: Path,
    flags: Mapping[str, Any] | None = None,
    builtin: Sequence[Rule] | None = None,
) -> Settings:
    """Resolve every layer into one :class:`Settings`.

    ``home`` and ``cwd`` are required parameters rather than discovered from the
    process, so a test can describe a machine without touching the developer's real
    ``~/.ronin`` — and so two sessions in different directories cannot leak config into
    each other.
    """
    base_rules = tuple(builtin) if builtin is not None else builtin_rules()
    layers: list[Layer] = [Layer(name="builtin", path=None, present=True, rules=base_rules)]
    layers.append(_file_layer("user", home / USER_SETTINGS))
    layers.append(_file_layer("project", cwd / PROJECT_SETTINGS))
    layers.append(_file_layer("local", cwd / LOCAL_SETTINGS))
    layers.append(_mapping_layer("flags", None, flags or {}))

    rules: list[Rule] = []
    scalars: dict[str, Any] = {}
    sources: dict[str, str] = {}
    errors: list[LayerError] = []
    for layer in layers:
        rules.extend(layer.rules)
        errors.extend(layer.errors)
        for key, value in layer.scalars.items():
            scalars[key] = value
            sources[key] = layer.name

    defaults = Settings(workspace_root=cwd, home=home)
    return Settings(
        workspace_root=cwd,
        home=home,
        mode=scalars.get("mode", defaults.mode),
        sandbox=scalars.get("sandbox", defaults.sandbox),
        yolo=scalars.get("yolo", defaults.yolo),
        protected_branches=scalars.get("protected_branches", defaults.protected_branches),
        default_decision=scalars.get("default_decision", defaults.default_decision),
        taint_min_span=scalars.get("taint_min_span", defaults.taint_min_span),
        max_retained_paths=scalars.get("max_retained_paths", defaults.max_retained_paths),
        max_retained_chars=scalars.get("max_retained_chars", defaults.max_retained_chars),
        compaction_escalate=scalars.get("compaction_escalate", defaults.compaction_escalate),
        rules=tuple(rules),
        layers=tuple(layers),
        errors=tuple(errors),
        scalar_sources=sources,
    )


# --------------------------------------------------------------------------- #
# Parsing one layer
# --------------------------------------------------------------------------- #


def _file_layer(name: str, path: Path) -> Layer:
    """Read and parse one settings file. Never raises."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Layer(name=name, path=path, present=False)
    except OSError as exc:
        return Layer(
            name=name,
            path=path,
            present=True,
            errors=(
                LayerError(
                    layer=name,
                    path=path,
                    message=f"could not be read ({exc.strerror or exc}); this layer was skipped",
                ),
            ),
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return Layer(
            name=name,
            path=path,
            present=True,
            errors=(
                LayerError(
                    layer=name,
                    path=path,
                    message=(
                        f"is not valid JSON (line {exc.lineno}, column {exc.colno}: "
                        f"{exc.msg}); this layer was skipped and the others still apply"
                    ),
                ),
            ),
        )
    if not isinstance(data, dict):
        return Layer(
            name=name,
            path=path,
            present=True,
            errors=(
                LayerError(
                    layer=name,
                    path=path,
                    message=(
                        f"must contain a JSON object, found {type(data).__name__}; "
                        "this layer was skipped"
                    ),
                ),
            ),
        )
    return _mapping_layer(name, path, data)


def _mapping_layer(name: str, path: Path | None, data: Mapping[str, Any]) -> Layer:
    errors: list[LayerError] = []
    scalars: dict[str, Any] = {}
    for key, value in data.items():
        if key == RULES_KEY:
            continue
        if key not in SCALAR_KEYS:
            errors.append(
                LayerError(
                    layer=name,
                    path=path,
                    message=(
                        f"unknown setting {key!r} was ignored. Known settings: "
                        f"{', '.join(sorted(SCALAR_KEYS))}, {RULES_KEY}"
                    ),
                )
            )
            continue
        try:
            scalars[key] = _coerce(key, value)
        except ValueError as exc:
            errors.append(LayerError(layer=name, path=path, message=str(exc)))

    rules: list[Rule] = []
    raw_rules = data.get(RULES_KEY, [])
    if not isinstance(raw_rules, list):
        errors.append(
            LayerError(
                layer=name,
                path=path,
                message=f"{RULES_KEY!r} must be a list; the whole list was ignored",
            )
        )
        raw_rules = []
    for index, entry in enumerate(raw_rules):
        try:
            rules.append(parse_rule(entry, source=name))
        except ValueError as exc:
            errors.append(
                LayerError(
                    layer=name,
                    path=path,
                    message=f"rule {index} was dropped: {exc}",
                )
            )
    return Layer(
        name=name,
        path=path,
        present=bool(data),
        rules=tuple(rules),
        scalars=scalars,
        errors=tuple(errors),
    )


def _coerce(key: str, value: object) -> object:
    kind = SCALAR_KEYS[key]
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{key!r} must be true or false, found {value!r}")
        return value
    if kind == "int":
        if not isinstance(value, int) or isinstance(value, bool) or value < 4:
            raise ValueError(f"{key!r} must be an integer >= 4, found {value!r}")
        return value
    if kind == "count_or_null":
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(
                f"{key!r} must be a positive integer, or null for no limit, found {value!r}"
            )
        return value
    if kind == "mode":
        legal = ", ".join(mode.value for mode in Mode)
        if not isinstance(value, str) or value not in {mode.value for mode in Mode}:
            raise ValueError(f"{key!r} must be one of {legal}, found {value!r}")
        return Mode(value)
    if kind == "decision":
        legal = ", ".join(decision.value for decision in Decision)
        if not isinstance(value, str) or value not in {d.value for d in Decision}:
            raise ValueError(f"{key!r} must be one of {legal}, found {value!r}")
        return Decision(value)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key!r} must be a list of strings, found {value!r}")
    return frozenset(value)


#: JSON shorthand: a rule may name its matcher inline instead of nesting ``match``.
_SHORTHAND: Mapping[str, str] = {"command": "regex", "path": "path", "exact": "exact"}


def parse_rule(entry: object, *, source: str) -> Rule:
    """One rule from its JSON form. Raises ``ValueError`` with a fixable message.

    Two spellings are accepted, because the nested one is precise and the flat one is
    what people actually type::

        {"tool": "bash", "decision": "allow", "match": {"kind": "regex", "pattern": "^pytest"}}
        {"tool": "bash", "decision": "allow", "command": "^pytest"}
    """
    if not isinstance(entry, Mapping):
        raise ValueError(f"must be an object, found {type(entry).__name__}")
    tool = entry.get("tool", "*")
    if not isinstance(tool, str) or not tool:
        raise ValueError(f"'tool' must be a non-empty string, found {tool!r}")
    raw_decision = entry.get("decision")
    if not isinstance(raw_decision, str) or raw_decision not in {d.value for d in Decision}:
        legal = ", ".join(d.value for d in Decision)
        raise ValueError(f"'decision' must be one of {legal}, found {raw_decision!r}")
    decision = Decision(raw_decision)
    reason = entry.get("reason", "")
    if not isinstance(reason, str):
        raise ValueError(f"'reason' must be a string, found {reason!r}")
    always_ask = entry.get("always_ask", entry.get("unwaivable", False))
    if not isinstance(always_ask, bool):
        raise ValueError(f"'always_ask' must be true or false, found {always_ask!r}")
    return Rule(
        tool=tool,
        matcher=_parse_matcher(entry),
        decision=decision,
        source=source,
        reason=reason,
        unwaivable=always_ask,
    )


def _parse_matcher(entry: Mapping[str, Any]) -> Matcher:
    match = entry.get("match")
    if match is None:
        for key, kind in _SHORTHAND.items():
            if key in entry:
                value = entry[key]
                if key == "exact":
                    return _build_matcher({"kind": "exact", **_as_mapping(value)})
                return _build_matcher({"kind": kind, "pattern": value})
        return AnyUse()
    if not isinstance(match, Mapping):
        raise ValueError(f"'match' must be an object, found {type(match).__name__}")
    return _build_matcher(match)


def _as_mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"'exact' must be an object with 'argument' and 'value', found {value!r}")
    return value


def _build_matcher(match: Mapping[str, Any]) -> Matcher:
    kind = match.get("kind", "tool")
    if kind == "tool":
        return AnyUse()
    if kind == "exact":
        argument = match.get("argument", "command")
        value = match.get("value")
        if not isinstance(argument, str) or not isinstance(value, str):
            raise ValueError("an 'exact' match needs string 'argument' and 'value'")
        return Exact(argument=argument, value=value)
    if kind == "path":
        pattern = match.get("pattern")
        argument = match.get("argument", "*")
        if not isinstance(pattern, str) or not isinstance(argument, str):
            raise ValueError("a 'path' match needs a string 'pattern'")
        return PathGlob(pattern=pattern, argument=argument)
    if kind == "regex":
        pattern = match.get("pattern")
        if not isinstance(pattern, str):
            raise ValueError("a 'regex' match needs a string 'pattern'")
        try:
            return CommandRegex(pattern=pattern)
        except Exception as exc:  # re.error, and anything a future re raises
            raise ValueError(f"'pattern' is not a valid regex: {exc}") from None
    legal = "tool, exact, path, regex"
    raise ValueError(f"unknown match kind {kind!r}; expected one of {legal}")


__all__ = [
    "LAYER_NAMES",
    "LOCAL_SETTINGS",
    "PROJECT_SETTINGS",
    "RULES_KEY",
    "SCALAR_KEYS",
    "USER_SETTINGS",
    "Layer",
    "LayerError",
    "Settings",
    "load_settings",
    "parse_rule",
]
