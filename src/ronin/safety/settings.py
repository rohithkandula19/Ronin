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

from ..context.compaction import (
    DEFAULT_MAX_RETAINED_CHARS,
    DEFAULT_MAX_RETAINED_PATHS,
)
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


#: Layers that come out of the repository the agent was pointed at, rather than from
#: the person running it. A repo is a thing you clone, and cloning it must not be the
#: same act as trusting it.
REPO_LAYERS: frozenset[str] = frozenset({"project", "local"})

#: The scalars that decide how much the safety layer asks, each ordered
#: most-restrictive first. A :data:`REPO_LAYERS` file may move one of these *down*
#: the ladder — stricter than it found it — and never up.
#:
#: This is the one place layering is not "last wins", and the asymmetry is the point.
#: ``.ronin/settings.json`` is a file committed to a repository, so under plain
#: last-wins a repo containing ``{"yolo": true, "mode": "full"}`` switched off every
#: prompt *and* the whole unconditional deny list — ``rm -rf /`` included — for anyone
#: who opened it, outranking that person's own ``~/.ronin/settings.json``, with no
#: warning. ``cli/wire.py`` already states the principle for Retainer authority: "a
#: rule written into ``.ronin/settings.json`` is a rule the agent running in that
#: workspace could edit". It was simply never extended to these four.
#:
#: Rules are unaffected: a rules list only ever appends, and a rule cannot widen what
#: the deny list refuses. It is the scalars that were dangerous.
PRIVILEGE_LADDERS: Mapping[str, tuple[Any, ...]] = {
    "yolo": (False, True),
    "sandbox": (True, False),
    "mode": (Mode.PLAN, Mode.ASK, Mode.AUTO_EDIT, Mode.FULL),
    "default_decision": (Decision.DENY, Decision.ASK, Decision.ALLOW),
}


def _permissiveness(key: str, value: Any) -> int:
    """Where ``value`` sits on its ladder, or ``-1`` for a value not on one."""
    ladder = PRIVILEGE_LADDERS.get(key, ())
    return ladder.index(value) if value in ladder else -1


def _spell(value: Any) -> str:
    """A value as it is written in the file, not as Python repr()s it.

    ``Mode`` and ``Decision`` are ``StrEnum``, so repr gives ``<Mode.FULL: 'full'>``
    — which is not what anyone typed, and a message that does not quote the file
    back is a message people have to translate before they can act on it.
    """
    return f"{value!s}" if isinstance(value, (Mode, Decision)) else f"{value!r}"


def escalates(key: str, value: Any, current: Any) -> bool:
    """Whether setting ``key`` to ``value`` loosens it compared with ``current``."""
    if key not in PRIVILEGE_LADDERS:
        return False
    return _permissiveness(key, value) > _permissiveness(key, current)


@dataclass(frozen=True, slots=True)
class LayerError:
    """A problem in one layer, named loudly enough to fix."""

    layer: str
    path: Path | None
    message: str
    skipped: bool = True
    """Whether the whole layer was dropped, or only the one thing named.

    A malformed layer is skipped entirely, so its rules stop applying and saying so
    is the useful half of the message. A refused privilege escalation is narrower —
    that one scalar did not take effect and everything else in the file still does.
    Reporting both the same way would tell a user their rules were gone when they
    were not, which is the kind of wrong that gets a config deleted.
    """

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
    ignored: bool = False
    """Whether the layer was deliberately not read, as opposed to not being there.

    The two look identical in a report that only knows "absent", and they are not the
    same fact: under ``--restricted`` the file is sitting on disk where the user can
    see it, and a report that calls it absent is one they stop believing the moment
    they run ``ls``. Somebody auditing a locked-down session needs to read this line
    and learn that a settings file exists and did not apply.
    """

    def describe(self) -> str:
        where = str(self.path) if self.path is not None else "(no file)"
        if self.errors:
            problems = "; ".join(error.message for error in self.errors)
            return f"{self.name:8} {where}  PROBLEM: {problems}"
        if self.ignored:
            return f"{self.name:8} {where}  (ignored — restricted mode reads no settings files)"
        if not self.present:
            return f"{self.name:8} {where}  (absent)"
        scalars = ", ".join(f"{key}={_spell(value)}" for key, value in sorted(self.scalars.items()))
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
    max_retained_paths: int | None = DEFAULT_MAX_RETAINED_PATHS
    max_retained_chars: int | None = DEFAULT_MAX_RETAINED_CHARS
    #: Let compaction surrender older retained file context by itself rather than
    #: reporting that it cannot fit. Off by default: see ``CompactionPolicy``.
    compaction_escalate: bool = True
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
            lines.append(f"{key} = {_spell(getattr(self, key))}  (from {self.source_of(key)})")
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
    ignore_files: bool = False,
) -> Settings:
    """Resolve every layer into one :class:`Settings`.

    ``home`` and ``cwd`` are required parameters rather than discovered from the
    process, so a test can describe a machine without touching the developer's real
    ``~/.ronin`` — and so two sessions in different directories cannot leak config into
    each other.

    ``ignore_files`` drops every file layer, leaving the builtins and whatever was
    typed on the command line. It is what ``--restricted`` is made of, and it is a
    parameter rather than a setting for the reason the restriction exists: a profile
    you can hand to an auditor is not one the workspace can edit. The layers are still
    *listed* in the result, marked ignored rather than absent, because a report that
    silently omits a file the user can see on disk is a report they stop believing.
    """
    base_rules = tuple(builtin) if builtin is not None else builtin_rules()
    layers: list[Layer] = [Layer(name="builtin", path=None, present=True, rules=base_rules)]
    files = (
        ("user", home / USER_SETTINGS),
        ("project", cwd / PROJECT_SETTINGS),
        ("local", cwd / LOCAL_SETTINGS),
    )
    for name, path in files:
        layers.append(
            Layer(name=name, path=path, present=False, ignored=True)
            if ignore_files
            else _file_layer(name, path)
        )
    layers.append(_mapping_layer("flags", None, flags or {}))

    rules: list[Rule] = []
    scalars: dict[str, Any] = {}
    sources: dict[str, str] = {}
    errors: list[LayerError] = []
    defaults = Settings(workspace_root=cwd, home=home)
    for layer in layers:
        rules.extend(layer.rules)
        errors.extend(layer.errors)
        for key, value in layer.scalars.items():
            if layer.name in REPO_LAYERS:
                current = scalars.get(key, getattr(defaults, key, None))
                if escalates(key, value, current):
                    # Reported rather than dropped: a silently ignored key is a
                    # permission the user thinks they granted, and a silently
                    # *honoured* one here was a permission they never did.
                    errors.append(
                        LayerError(
                            layer=layer.name,
                            path=layer.path,
                            message=(
                                f"refused {key} = {_spell(value)}: a file in the "
                                f"repository cannot loosen {key} past {_spell(current)}, "
                                "which came from a layer you control. Set it in your "
                                f"own ~/{USER_SETTINGS} or pass the flag, if you mean it"
                            ),
                            skipped=False,
                        )
                    )
                    continue
            scalars[key] = value
            sources[key] = layer.name

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

#: Which keys each match kind actually reads.
#:
#: The point is the refusal, not the documentation. ``kind`` defaults to ``tool``
#: and ``tool`` means :class:`~ronin.safety.policy.AnyUse` — the *broadest*
#: matcher there is — so a ``match`` object whose keys are all ignored does not
#: fail, it silently widens. ``{"tool": "bash", "decision": "allow", "match":
#: {"command": "^pytest"}}`` is a plausible blend of the two documented spellings
#: and used to parse as *allow every bash command*. A permission rule that fails
#: open is worse than one that will not load.
_MATCH_KEYS: Mapping[str, frozenset[str]] = {
    "tool": frozenset({"kind"}),
    "exact": frozenset({"kind", "argument", "value"}),
    "path": frozenset({"kind", "pattern", "argument"}),
    "regex": frozenset({"kind", "pattern"}),
}


def _stray_keys_message(kind: str, stray: Sequence[str], match: Mapping[str, Any]) -> str:
    """Why the rule was refused, in terms of the fix rather than the parser."""
    listed = ", ".join(repr(key) for key in stray)
    shorthand = [key for key in stray if key in _SHORTHAND]
    if shorthand and "kind" not in match:
        key = shorthand[0]
        return (
            f"a 'match' with no 'kind' means kind 'tool', which matches *every* use "
            f"of the tool — it does not read {listed}. Write {key!r} at the top level "
            f"beside 'tool' and 'decision', or give 'match' an explicit 'kind'"
        )
    return f"a 'match' of kind {kind!r} does not read {listed}"


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
    # Kind first, then stray keys: an unknown kind makes "which keys are legal"
    # unanswerable, and reporting the stray key instead would name the wrong fix.
    allowed = _MATCH_KEYS.get(kind) if isinstance(kind, str) else None
    if allowed is None:
        legal = ", ".join(_MATCH_KEYS)
        raise ValueError(f"unknown match kind {kind!r}; expected one of {legal}")
    stray = sorted(set(match) - allowed)
    if stray:
        raise ValueError(_stray_keys_message(kind, stray, match))
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
    # Unreachable while `_MATCH_KEYS` and the branches above agree about the
    # kinds. If a kind is ever added to the table and not built here, say so
    # instead of falling off the end and returning None.
    raise ValueError(f"match kind {kind!r} is known but not built — this is a bug")


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
