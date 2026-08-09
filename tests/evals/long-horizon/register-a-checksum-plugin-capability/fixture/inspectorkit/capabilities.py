"""The capability catalog: the questions this tool knows how to ask.

A capability is *not* a plugin. It is the pairing of a question with the hook
method that answers it, so that dispatch and the registry can stay generic.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import UnknownCapability


@dataclass(frozen=True)
class Capability:
    #: Name used on the command line, in the report and in ``Plugin.declares``.
    name: str
    #: Method on Plugin that answers it.
    hook: str
    #: Shape of the answer, for the report renderer.
    result_kind: str
    summary: str


CAPABILITIES: dict[str, Capability] = {
    "summary": Capability(
        name="summary",
        hook="summarize",
        result_kind="mapping",
        summary="flat key/value facts about the file",
    ),
    "histogram": Capability(
        name="histogram",
        hook="histogram",
        result_kind="counts",
        summary="a count per key, rendered biggest first",
    ),
    "warnings": Capability(
        name="warnings",
        hook="warnings",
        result_kind="lines",
        summary="things a human should look at",
    ),
}


def capability(name: str) -> Capability:
    try:
        return CAPABILITIES[name]
    except KeyError:
        raise UnknownCapability(
            f"unknown capability {name!r}; the catalog declares {sorted(CAPABILITIES)}"
        ) from None


def hook_for(name: str) -> str:
    return capability(name).hook


def capability_names() -> tuple[str, ...]:
    return tuple(sorted(CAPABILITIES))
