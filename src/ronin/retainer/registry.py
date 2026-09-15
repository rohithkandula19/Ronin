"""One file describing one deployment: who serves here, and as what.

A :class:`~ronin.retainer.model.Retainer` is durable and edited by a human, so
something has to read what the human wrote. This is that reader, and it is
deliberately the only one — ``run_summons`` takes a ``Mapping[str, Retainer]``
and does not care where it came from, which is what lets the tests build records
directly and the daemon build them from disk.

Why a file and not a command
----------------------------
``ronin retain add`` would have to write this file anyway, and a wizard that
writes a config is a second place the schema lives. The file is the schema. A
command can come later and produce it.

Why the same rule syntax as ``settings.json``
---------------------------------------------
``grants`` are parsed by :func:`ronin.safety.settings.parse_rule` — the same
parser, the same JSON shape. A Retainer's orders being a *second* permission
language is how the two drift, and a permission language nobody can check
against the one they already know is a permission language people get wrong.

What this file is not
---------------------
It is not a secret store. Tokens are read from the environment at post time (see
:mod:`ronin.retainer.adapters.outbound`), never from here, because a config file
describing a fleet is a file people paste into issues.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ronin.retainer.model import (
    Budgets,
    Capability,
    Channel,
    Deployment,
    Post,
    Retainer,
    StandingOrders,
)
from ronin.retainer.posts import Identity, PostError, workspace_for
from ronin.safety.policy import Decision
from ronin.safety.settings import parse_rule

#: Where the daemon looks when nobody says otherwise. Beside ``settings.json``,
#: because an operator who knows where one lives should not have to ask about the
#: other.
REGISTRY_FILENAME = "retainers.json"

#: Provenance for rules read from this file, so an audit trail can tell an order
#: from a builtin. Matches :data:`ronin.retainer.orders.ORDERS_SOURCE`.
REGISTRY_SOURCE = "standing orders"

#: Where posts are checked out when the file does not say. Under the Ronin home
#: rather than the working directory: a daemon's cwd is not a durable place.
DEFAULT_POSTS_DIRNAME = "posts"


class RegistryError(ValueError):
    """The registry could not be read. Carries where, in the file's own terms."""


@dataclass(frozen=True, slots=True)
class Registry:
    """Everything one ``retain serve`` needs to know before a summons arrives."""

    deployment: Deployment
    retainers: Mapping[str, Retainer]
    posts_root: Path
    identity: Identity

    def __post_init__(self) -> None:
        for key, retainer in self.retainers.items():
            if key != retainer.id:
                raise RegistryError(f"retainer keyed {key!r} but identifies as {retainer.id!r}")

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self.retainers))


def _mapping(value: object, *, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RegistryError(f"{where} must be an object, got {type(value).__name__}")
    return value


def _text(source: Mapping[str, Any], key: str, *, where: str, required: bool = False) -> str:
    value = source.get(key, "")
    if value in ("", None) and not required:
        return ""
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{where}.{key} must be a non-empty string")
    return value


def _names(value: object, *, where: str) -> tuple[str, ...]:
    """A list of strings, refusing the single string that reads like one.

    ``"tools": "bash"`` is a plausible thing to write and would otherwise be
    accepted as the four tools ``b``, ``a``, ``s``, ``h``.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        raise RegistryError(f"{where} must be a list of strings, not one string")
    if not isinstance(value, Sequence):
        raise RegistryError(f"{where} must be a list of strings")
    out: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise RegistryError(f"{where}[{index}] must be a non-empty string")
        out.append(item)
    return tuple(out)


def _enum_set(
    value: object, kind: type[Channel] | type[Capability], *, where: str
) -> frozenset[Any]:
    """Enum members by value, naming the alternatives when one is unknown.

    The alternatives matter more than the refusal: ``"capabilities": ["net"]``
    is a typo whose fix is one word, and an error that does not print
    ``network`` sends the reader to the source.
    """
    known = {member.value: member for member in kind}
    out = set()
    for name in _names(value, where=where):
        member = known.get(name)
        if member is None:
            offered = ", ".join(sorted(known))
            raise RegistryError(f"{where}: unknown {kind.__name__.lower()} {name!r} — {offered}")
        out.add(member)
    return frozenset(out)


def _decision(value: object, *, where: str) -> Decision:
    if value in ("", None):
        return Decision.ASK
    known = {member.value: member for member in Decision}
    if not isinstance(value, str) or value not in known:
        offered = ", ".join(sorted(known))
        raise RegistryError(f"{where} must be one of {offered}")
    return known[value]


def _budgets(value: object, *, where: str) -> Budgets:
    if value is None:
        return Budgets()
    source = _mapping(value, where=where)
    fields = ("iterations", "tokens", "seconds", "notifications")
    unknown = sorted(set(source) - set(fields))
    if unknown:
        raise RegistryError(f"{where}: unknown budget {', '.join(unknown)}")
    numbers: dict[str, int] = {}
    for field_name in fields:
        if field_name not in source:
            continue
        number = source[field_name]
        # bool is an int in Python, and `"iterations": true` should not mean one.
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            raise RegistryError(f"{where}.{field_name} must be a positive integer")
        numbers[field_name] = number
    return Budgets(**numbers)


def _orders(value: object, *, where: str) -> StandingOrders:
    source = _mapping(value, where=where)
    grants = []
    raw_grants = source.get("grants") or []
    if isinstance(raw_grants, str) or not isinstance(raw_grants, Sequence):
        raise RegistryError(f"{where}.grants must be a list of rules")
    for index, entry in enumerate(raw_grants):
        try:
            grants.append(parse_rule(entry, source=REGISTRY_SOURCE))
        except ValueError as error:
            # The index, because "a bad rule somewhere in this file" is not
            # actionable when the file holds a fleet.
            raise RegistryError(f"{where}.grants[{index}]: {error}") from error
    try:
        return StandingOrders(
            brief=_text(source, "brief", where=where),
            tools=frozenset(_names(source.get("tools"), where=f"{where}.tools")),
            grants=tuple(grants),
            default=_decision(source.get("default"), where=f"{where}.default"),
            budgets=_budgets(source.get("budgets"), where=f"{where}.budgets"),
            wants=_enum_set(source.get("wants"), Capability, where=f"{where}.wants"),
        )
    except ValueError as error:
        if isinstance(error, RegistryError):
            raise
        # StandingOrders enforces its own invariants (a blanket allow, say). Its
        # message is the useful one; this only says where.
        raise RegistryError(f"{where}: {error}") from error


def _retainer(value: object, *, where: str, posts_root: Path) -> Retainer:
    source = _mapping(value, where=where)
    identifier = _text(source, "id", where=where, required=True)
    repo = _text(source, "repo", where=where, required=True)
    workspace = _text(source, "workspace", where=where)
    try:
        post = Post(
            repo=repo,
            workspace=(
                Path(workspace).expanduser()
                if workspace
                else workspace_for(posts_root, identifier, repo)
            ),
            branch=_text(source, "branch", where=where),
        )
        return Retainer(
            id=identifier,
            name=_text(source, "name", where=where, required=True),
            post=post,
            orders=_orders(source.get("orders") or {}, where=f"{where}.orders"),
            channels=_enum_set(source.get("channels"), Channel, where=f"{where}.channels"),
            acts_as=_text(source, "acts_as", where=where),
        )
    # `PostError` is a RuntimeError, not a ValueError, so it needs naming: an id
    # that is not a usable directory name and a repo that is not `owner/name`
    # both surface from `workspace_for`, and both are this file's mistake to
    # report rather than a traceback out of the daemon.
    except (ValueError, PostError) as error:
        if isinstance(error, RegistryError):
            raise
        raise RegistryError(f"{where}: {error}") from error


def _deployment(value: object, *, where: str) -> Deployment:
    source = _mapping(value, where=where)
    try:
        return Deployment(
            name=_text(source, "name", where=where, required=True),
            hosted=bool(source.get("hosted", False)),
            capabilities=_enum_set(
                source.get("capabilities"), Capability, where=f"{where}.capabilities"
            ),
        )
    except ValueError as error:
        if isinstance(error, RegistryError):
            raise
        # Deployment refuses `hosted` with the browser capability. That refusal is
        # the point of the record; surfacing it verbatim is the point of this.
        raise RegistryError(f"{where}: {error}") from error


def parse_registry(data: object, *, home: Path) -> Registry:
    """Build a :class:`Registry` from already-decoded JSON.

    Separate from :func:`load_registry` so the whole schema is testable without a
    file, and so a caller holding the document for another reason does not have
    to write it out to read it back.
    """
    document = _mapping(data, where="registry")
    unknown = sorted(set(document) - {"deployment", "retainers", "posts"})
    if unknown:
        # Refusing an unknown key rather than ignoring it: a typo'd "retainer"
        # that is silently dropped is a deployment that starts with nobody in it
        # and no reason given.
        raise RegistryError(f"registry: unknown key {', '.join(unknown)}")

    posts = _mapping(document.get("posts") or {}, where="registry.posts")
    root_text = _text(posts, "root", where="registry.posts")
    posts_root = Path(root_text).expanduser() if root_text else home / DEFAULT_POSTS_DIRNAME

    identity_source = _mapping(posts.get("identity") or {}, where="registry.posts.identity")
    try:
        identity = Identity(
            name=_text(identity_source, "name", where="registry.posts.identity", required=True),
            email=_text(identity_source, "email", where="registry.posts.identity", required=True),
        )
    except ValueError as error:
        if isinstance(error, RegistryError):
            raise
        raise RegistryError(f"registry.posts.identity: {error}") from error

    entries = document.get("retainers")
    if entries is None:
        raise RegistryError("registry: needs a 'retainers' list")
    if isinstance(entries, str) or not isinstance(entries, Sequence):
        raise RegistryError("registry.retainers must be a list")

    records: dict[str, Retainer] = {}
    for index, entry in enumerate(entries):
        retainer = _retainer(entry, where=f"registry.retainers[{index}]", posts_root=posts_root)
        if retainer.id in records:
            # Last-wins would silently drop the first one's orders, and two
            # records claiming one id is a mistake with no safe reading.
            raise RegistryError(f"registry.retainers[{index}]: {retainer.id!r} is already defined")
        records[retainer.id] = retainer

    return Registry(
        deployment=_deployment(document.get("deployment") or {}, where="registry.deployment"),
        retainers=records,
        posts_root=posts_root,
        identity=identity,
    )


def registry_path(home: Path) -> Path:
    return home / REGISTRY_FILENAME


def load_registry(home: Path, *, path: Path | None = None) -> Registry:
    """Read the registry under ``home``, or from ``path`` when one is given.

    A missing file is a :class:`RegistryError` naming where it looked, not an
    empty registry: a daemon that starts with nobody in it looks identical to one
    whose config did not deploy.
    """
    source = path if path is not None else registry_path(home)
    try:
        raw = source.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise RegistryError(f"no retainer registry at {source}") from error
    except OSError as error:
        raise RegistryError(f"could not read {source}: {error}") from error
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RegistryError(f"{source} is not valid JSON: {error}") from error
    return parse_registry(data, home=home)


__all__ = [
    "DEFAULT_POSTS_DIRNAME",
    "REGISTRY_FILENAME",
    "REGISTRY_SOURCE",
    "Registry",
    "RegistryError",
    "load_registry",
    "parse_registry",
    "registry_path",
]
