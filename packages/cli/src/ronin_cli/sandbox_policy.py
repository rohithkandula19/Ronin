"""Inspectable execution-containment policy.

This is deliberately a reporting layer, not a second command executor. Actual
containment remains owned by ``backends.run_in_backend`` and its fail-closed
callers. Keeping the policy view pure makes it usable by the CLI, tests, and a
future dashboard without accidentally changing execution behavior.
"""
from __future__ import annotations

import platform
import shutil
from dataclasses import dataclass
from typing import Callable

from .backends import parse_backend


@dataclass(frozen=True)
class SandboxPolicyStatus:
    requested: str | None
    mode: str
    status: str
    detail: str
    fail_closed: bool


def inspect_sandbox_policy(
    spec: str | None,
    *,
    system: str | None = None,
    executable: Callable[[str], str | None] = shutil.which,
) -> SandboxPolicyStatus:
    """Describe the requested backend without probing a container or network.

    The result distinguishes an intentional local host run from an unavailable
    requested sandbox. The latter remains ``blocked`` because normal tool calls
    refuse instead of silently falling back to the host.
    """
    if spec is None or not spec.strip() or spec.strip().lower() == "local":
        return SandboxPolicyStatus(
            requested=None, mode="local", status="host",
            detail="commands run on the host after normal approval gates", fail_closed=False,
        )
    try:
        backend = parse_backend(spec)
    except ValueError as exc:
        return SandboxPolicyStatus(
            requested=spec, mode="invalid", status="blocked", detail=str(exc), fail_closed=True,
        )
    mode = str(backend["type"])
    host_system = system or platform.system()
    if mode == "docker":
        available = executable("docker") is not None
        return SandboxPolicyStatus(
            requested=spec, mode=mode, status="configured" if available else "blocked",
            detail=("Docker command is available; backend failures still refuse host execution"
                    if available else "Docker is unavailable; requested commands will be refused"),
            fail_closed=True,
        )
    if mode == "seatbelt":
        available = host_system == "Darwin" and executable("sandbox-exec") is not None
        return SandboxPolicyStatus(
            requested=spec, mode=mode, status="configured" if available else "blocked",
            detail=("macOS Seatbelt confines writes to the workspace"
                    if available else "Seatbelt is unavailable on this host; requested commands will be refused"),
            fail_closed=True,
        )
    if mode == "ssh":
        return SandboxPolicyStatus(
            requested=spec, mode=mode, status="configured",
            detail="SSH target syntax is valid; connection is attempted only when a command is approved",
            fail_closed=True,
        )
    return SandboxPolicyStatus(
        requested=spec, mode=mode, status="host", detail="local host execution", fail_closed=False,
    )
