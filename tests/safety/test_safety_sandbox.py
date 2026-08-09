"""Sandbox: argv construction, and the refusal to pretend.

``which`` and ``platform`` are injected, so every backend's command line is checked on a
machine that has none of the binaries installed — which is the only way this can be a CI
gate rather than a manual check on someone's laptop.

The honesty tests carry more weight than the argv tests. The policy engine auto-approves
under an isolating sandbox, so anything that reported isolation it did not have would
open the gate onto nothing.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from ronin.safety.sandbox import (
    DEFAULT_IMAGE,
    SANDBOX_AUTO_APPROVES,
    BubblewrapSandbox,
    DockerSandbox,
    NoSandbox,
    Sandbox,
    SeatbeltSandbox,
    Unavailable,
    describe,
    detect,
)

WORKSPACE = Path("/work/repo")


def which_finds(*names: str) -> Callable[[str], str | None]:
    """A ``shutil.which`` stand-in describing a machine we are not running on."""
    available = {name: f"/usr/bin/{name}" for name in names}
    return available.get


# --------------------------------------------------------------------------- #
# detect() is honest
# --------------------------------------------------------------------------- #


def test_bubblewrap_is_preferred_on_linux() -> None:
    backend = detect(which=which_finds("bwrap", "docker"), platform="linux")
    assert isinstance(backend, BubblewrapSandbox)
    assert backend.binary == "/usr/bin/bwrap"


def test_seatbelt_is_preferred_on_macos() -> None:
    backend = detect(which=which_finds("sandbox-exec", "docker"), platform="darwin")
    assert isinstance(backend, SeatbeltSandbox)


def test_bubblewrap_is_not_looked_for_on_macos() -> None:
    backend = detect(which=which_finds("bwrap"), platform="darwin")
    assert isinstance(backend, Unavailable)


def test_docker_is_the_fallback_when_the_native_backend_is_missing() -> None:
    backend = detect(which=which_finds("docker"), platform="linux")
    assert isinstance(backend, DockerSandbox)
    assert backend.image == DEFAULT_IMAGE


def test_a_bare_machine_gets_a_reason_not_a_fake_sandbox() -> None:
    """The single most important behaviour in this module: never a silent fallback."""
    backend = detect(which=which_finds(), platform="linux")
    assert isinstance(backend, Unavailable)
    assert backend.isolates is False
    assert "bwrap" in backend.looked_for and "docker" in backend.looked_for
    assert "nothing to isolate with" in backend.reason
    assert not isinstance(backend, NoSandbox)


def test_windows_is_told_what_to_do_instead() -> None:
    backend = detect(which=which_finds("docker"), platform="win32")
    assert isinstance(backend, Unavailable)
    assert "WSL" in backend.reason


def test_a_custom_image_is_honoured() -> None:
    backend = detect(which=which_finds("docker"), platform="linux", image="ronin/ci:1.2")
    assert isinstance(backend, DockerSandbox)
    assert backend.image == "ronin/ci:1.2"


def test_describe_works_for_both_arms_of_the_union() -> None:
    assert "unavailable" in describe(detect(which=which_finds(), platform="linux"))
    assert "bubblewrap" in describe(detect(which=which_finds("bwrap"), platform="linux"))


# --------------------------------------------------------------------------- #
# NoSandbox
# --------------------------------------------------------------------------- #


def test_the_default_provides_no_isolation_and_says_so_plainly() -> None:
    sandbox = NoSandbox()
    assert sandbox.isolates is False
    assert sandbox.wrap(("ls", "-la"), workspace=WORKSPACE) == ("ls", "-la")
    assert "no sandbox" in sandbox.describe()
    assert "no isolation" in sandbox.describe() or "own privileges" in sandbox.describe()


@pytest.mark.parametrize(
    "sandbox",
    [NoSandbox(), BubblewrapSandbox(), SeatbeltSandbox(), DockerSandbox()],
)
def test_every_backend_satisfies_the_protocol(sandbox: Sandbox) -> None:
    assert isinstance(sandbox, Sandbox)
    assert sandbox.name
    assert sandbox.describe()


# --------------------------------------------------------------------------- #
# Argv construction
# --------------------------------------------------------------------------- #


def argv(sandbox: Sandbox, **kwargs: object) -> Sequence[str]:
    return sandbox.wrap(("pytest", "-q"), workspace=WORKSPACE, **kwargs)  # type: ignore[arg-type]


def test_bubblewrap_binds_the_root_read_only_before_the_workspace_read_write() -> None:
    """Order matters: bwrap applies binds in sequence, so a read-write bind placed
    before the read-only root would be overwritten by it."""
    command = list(argv(BubblewrapSandbox()))
    assert command[:2] == ["bwrap", "--unshare-all"]
    ro = command.index("--ro-bind")
    rw = command.index("--bind")
    assert ro < rw, "the narrow read-write bind must land on top of the read-only root"
    assert command[rw : rw + 3] == ["--bind", "/work/repo", "/work/repo"]
    assert command[-2:] == ["pytest", "-q"]
    assert "--" in command


def test_bubblewrap_leaves_the_network_off_by_default() -> None:
    assert "--share-net" not in argv(BubblewrapSandbox())
    assert "--share-net" in argv(BubblewrapSandbox(), network=True)


def test_bubblewrap_gives_the_command_a_fresh_tmpfs_and_its_own_session() -> None:
    command = argv(BubblewrapSandbox())
    assert "--tmpfs" in command
    assert "--new-session" in command
    assert "--die-with-parent" in command


def test_extra_writable_paths_are_bound_read_write() -> None:
    command = list(argv(BubblewrapSandbox(), writable=[Path("/cache/uv")]))
    assert command[command.index("/cache/uv") - 1] == "--bind"


def test_the_seatbelt_profile_denies_writes_then_allows_the_workspace() -> None:
    profile = SeatbeltSandbox().profile(workspace=WORKSPACE, network=False, writable=())
    assert profile.index("(deny file-write*)") < profile.index('(subpath "/work/repo")')
    assert "(deny network*)" in profile


def test_the_seatbelt_profile_is_passed_inline_not_through_a_temp_file() -> None:
    command = list(argv(SeatbeltSandbox()))
    assert command[0].endswith("sandbox-exec")
    assert command[1] == "-p"
    assert command[2].startswith("(version 1)")
    assert command[-2:] == ["pytest", "-q"]


def test_the_seatbelt_profile_allows_the_network_only_when_asked() -> None:
    assert "(deny network*)" not in SeatbeltSandbox().profile(
        workspace=WORKSPACE, network=True, writable=()
    )


def test_docker_mounts_only_the_workspace_and_drops_capabilities() -> None:
    command = list(argv(DockerSandbox()))
    assert command[:4] == ["docker", "run", "--rm", "-i"]
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--security-opt") + 1] == "no-new-privileges"
    assert "/work/repo:/work/repo:rw" in command
    assert command[-3:] == [DEFAULT_IMAGE, "pytest", "-q"]


def test_docker_uses_a_bridge_network_when_the_network_is_enabled() -> None:
    command = list(argv(DockerSandbox(), network=True))
    assert command[command.index("--network") + 1] == "bridge"


# --------------------------------------------------------------------------- #
# The policy this module implies
# --------------------------------------------------------------------------- #


def test_the_auto_approval_rationale_is_stated_as_a_reviewable_constant() -> None:
    """The engine hands this to the model and the log verbatim, so the trade — no
    prompts *because* no reach — is on the record rather than implicit."""
    assert "cannot reach anything" in SANDBOX_AUTO_APPROVES
    assert "read-only" in SANDBOX_AUTO_APPROVES
    assert "network is off" in SANDBOX_AUTO_APPROVES
