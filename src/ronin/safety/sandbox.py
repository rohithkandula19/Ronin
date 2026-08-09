"""Sandboxing: off by default, and never pretending.

When a command runs inside a sandbox the blast radius is the sandbox, so the approval
question stops being interesting — which is why :class:`ronin.safety.policy.PolicyEngine`
auto-approves under an isolating sandbox. That makes the *honesty* of this module the
whole safety argument: if :func:`detect` ever returned something that looked like a
sandbox but was not one, the gate would open and nothing would be behind it.

So there is no fallback. :func:`detect` returns a configured backend or it returns
:class:`Unavailable` carrying the reason and what it looked for. :class:`NoSandbox` is a
real, selectable option — the default — and it reports ``isolates=False``, which is the
one property callers must branch on.

``detect`` takes ``which`` and ``platform`` as parameters rather than reading the
environment, for the usual reason: a test must be able to describe a machine it is not
running on. Argv construction is likewise pure, so the shape of every backend's command
line is tested without any of those binaries being installed.

The three backends, and why each is built the way it is:

* **bubblewrap** (Linux) — ``--ro-bind / /`` first, then a read-write bind of the
  workspace *over* it. Order matters: bwrap applies binds in sequence, so the narrow
  read-write bind has to come second or the read-only root wins.
* **sandbox-exec** (macOS seatbelt) — the profile is passed inline with ``-p`` rather
  than written to a file, so there is no temp file to leak or race.
* **docker** — ``--read-only`` for the image's own filesystem, one writable volume for
  the workspace, ``--network none`` unless the caller asks otherwise, and capabilities
  dropped. Slower to start than the other two, hence last in the preference order.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

#: The policy decision this module implies, spelled out so it is reviewable:
#: inside an isolating sandbox every action is approved, because the thing an approval
#: protects — the user's real filesystem, network and credentials — is not reachable.
#: The exchange is explicit: you get no prompts *because* you gave up reach.
SANDBOX_AUTO_APPROVES = (
    "sandboxed: approved without asking because the command cannot reach anything "
    "outside the sandbox — the workspace is writable, the rest of the filesystem is "
    "read-only, and the network is off unless it was explicitly enabled"
)


@runtime_checkable
class Sandbox(Protocol):
    """A way to run a command with less reach than the agent has.

    ``isolates`` is the only field a caller may branch on for a safety decision, and
    it is why this is a protocol with a truthful ``False`` implementation rather than
    an optional object where ``None`` means "no isolation" — a ``None`` check is easy
    to forget, and forgetting it means auto-approving on a bare machine.
    """

    @property
    def name(self) -> str: ...

    @property
    def isolates(self) -> bool: ...

    def wrap(
        self,
        argv: Sequence[str],
        *,
        workspace: Path,
        network: bool = False,
        writable: Sequence[Path] = (),
    ) -> tuple[str, ...]:
        """The full argv that runs ``argv`` under this sandbox."""
        ...

    def describe(self) -> str:
        """One line a human can read in ``/doctor``."""
        ...


@dataclass(frozen=True, slots=True)
class NoSandbox:
    """The default. Runs the command exactly as given, and says so.

    Deliberately not called ``PassthroughSandbox`` or given a hopeful name: every
    place this appears in a log or a ``/doctor`` line should read as "there is no
    isolation here".
    """

    name: str = "none"

    @property
    def isolates(self) -> bool:
        return False

    def wrap(
        self,
        argv: Sequence[str],
        *,
        workspace: Path,
        network: bool = False,
        writable: Sequence[Path] = (),
    ) -> tuple[str, ...]:
        return tuple(argv)

    def describe(self) -> str:
        return (
            "no sandbox: commands run with the agent's own privileges, so the approval "
            "gate and the deny list are the only things between a command and the machine"
        )


@dataclass(frozen=True, slots=True)
class Unavailable:
    """No backend could be configured, with the reason and what was tried.

    Returned instead of ``NoSandbox`` so a caller that asked for isolation is told it
    did not get any, rather than silently receiving something that does not isolate.
    """

    reason: str
    looked_for: tuple[str, ...] = ()

    @property
    def isolates(self) -> bool:
        return False

    def describe(self) -> str:
        tried = ", ".join(self.looked_for) or "nothing"
        return f"sandbox unavailable: {self.reason} (looked for: {tried})"


@dataclass(frozen=True, slots=True)
class BubblewrapSandbox:
    """``bwrap``: namespaces, no daemon, fast enough to use per command."""

    binary: str = "bwrap"
    name: str = "bubblewrap"

    @property
    def isolates(self) -> bool:
        return True

    def wrap(
        self,
        argv: Sequence[str],
        *,
        workspace: Path,
        network: bool = False,
        writable: Sequence[Path] = (),
    ) -> tuple[str, ...]:
        command = [
            self.binary,
            "--unshare-all",
            *(("--share-net",) if network else ()),
            "--die-with-parent",
            "--new-session",
            # Read-only root first; the writable binds below deliberately land on top.
            "--ro-bind",
            "/",
            "/",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--bind",
            str(workspace),
            str(workspace),
        ]
        for path in writable:
            command += ["--bind", str(path), str(path)]
        command += ["--chdir", str(workspace), "--"]
        command += list(argv)
        return tuple(command)

    def describe(self) -> str:
        return (
            f"bubblewrap ({self.binary}): workspace read-write, rest of the filesystem "
            "read-only, /tmp a fresh tmpfs, network off unless enabled"
        )


@dataclass(frozen=True, slots=True)
class SeatbeltSandbox:
    """macOS ``sandbox-exec`` with an inline profile."""

    binary: str = "/usr/bin/sandbox-exec"
    name: str = "seatbelt"

    @property
    def isolates(self) -> bool:
        return True

    def profile(self, *, workspace: Path, network: bool, writable: Sequence[Path]) -> str:
        """The seatbelt profile. Read is allowed broadly; writes are enumerated.

        Reading is left open because a compiler needs the SDK, the toolchain and half
        of ``/usr``, and enumerating that is a losing game. Writes are what a sandbox
        is for here, and they are a closed list.
        """
        allowed = [workspace, *writable]
        lines = [
            "(version 1)",
            "(allow default)",
            "(deny file-write*)",
            *(f'(allow file-write* (subpath "{path}"))' for path in allowed),
            '(allow file-write* (subpath "/private/tmp"))',
            '(allow file-write* (subpath "/private/var/folders"))',
        ]
        if not network:
            lines.append("(deny network*)")
        return " ".join(lines)

    def wrap(
        self,
        argv: Sequence[str],
        *,
        workspace: Path,
        network: bool = False,
        writable: Sequence[Path] = (),
    ) -> tuple[str, ...]:
        return (
            self.binary,
            "-p",
            self.profile(workspace=workspace, network=network, writable=writable),
            "--",
            *argv,
        )

    def describe(self) -> str:
        return (
            f"seatbelt ({self.binary}): reads allowed, writes confined to the workspace "
            "and temp dirs, network off unless enabled"
        )


@dataclass(frozen=True, slots=True)
class DockerSandbox:
    """A container per command. Strongest isolation here, and the slowest to start."""

    binary: str = "docker"
    image: str = "python:3.11-slim"
    name: str = "docker"

    @property
    def isolates(self) -> bool:
        return True

    def wrap(
        self,
        argv: Sequence[str],
        *,
        workspace: Path,
        network: bool = False,
        writable: Sequence[Path] = (),
    ) -> tuple[str, ...]:
        command = [
            self.binary,
            "run",
            "--rm",
            "-i",
            "--network",
            "bridge" if network else "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp",
            "-v",
            f"{workspace}:{workspace}:rw",
        ]
        for path in writable:
            command += ["-v", f"{path}:{path}:rw"]
        command += ["-w", str(workspace), self.image, *argv]
        return tuple(command)

    def describe(self) -> str:
        return (
            f"docker ({self.image}): container filesystem read-only, workspace mounted "
            "read-write, all capabilities dropped, network off unless enabled"
        )


#: Preference order, cheapest-to-start first. bwrap and seatbelt add milliseconds;
#: docker adds most of a second per command, which is why it is the last resort.
BACKEND_ORDER: tuple[str, ...] = ("bwrap", "sandbox-exec", "docker")


@dataclass(frozen=True, slots=True)
class SandboxRequest:
    """What the caller wants isolated. Kept as a value so it can be logged."""

    workspace: Path
    network: bool = False
    writable: tuple[Path, ...] = field(default_factory=tuple)


def detect(
    *,
    which: Callable[[str], str | None],
    platform: str,
    image: str = DockerSandbox.image,
) -> Sandbox | Unavailable:
    """A configured backend, or :class:`Unavailable` with the reason.

    ``platform`` takes ``sys.platform`` values (``"linux"``, ``"darwin"``, ``"win32"``).
    Never returns :class:`NoSandbox`: a caller that asked to be sandboxed and got a
    non-isolating object back would have no way to tell that from success.
    """
    if platform.startswith("win"):
        return Unavailable(
            reason="no supported sandbox on Windows; run under WSL or in a container",
            looked_for=(),
        )
    if platform == "darwin":
        found = which("sandbox-exec")
        if found:
            return SeatbeltSandbox(binary=found)
    else:
        found = which("bwrap")
        if found:
            return BubblewrapSandbox(binary=found)
    docker = which("docker")
    if docker:
        return DockerSandbox(binary=docker, image=image)
    expected = "sandbox-exec" if platform == "darwin" else "bwrap"
    return Unavailable(
        reason=(
            f"neither {expected} nor docker is installed, so there is nothing to "
            "isolate with — running unsandboxed would be the same as not asking"
        ),
        looked_for=(expected, "docker"),
    )


def describe(sandbox: Sandbox | Unavailable) -> str:
    """One-line status for ``/doctor``, for either arm of the union."""
    return sandbox.describe()


__all__ = [
    "BACKEND_ORDER",
    "SANDBOX_AUTO_APPROVES",
    "BubblewrapSandbox",
    "DockerSandbox",
    "NoSandbox",
    "Sandbox",
    "SandboxRequest",
    "SeatbeltSandbox",
    "Unavailable",
    "describe",
    "detect",
]
