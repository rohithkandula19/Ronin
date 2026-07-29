"""Pluggable, sandboxed execution backends for the coding agent's shell.

By default ronin runs ``run_command`` straight on the user's host via
``subprocess`` (see ``code_tools.run_command``). That's convenient but it means
a model-issued ``rm -rf`` or ``curl … | sh`` hits the *real* machine. This
module adds the missing safety valve other agents (Hermes/OpenClaw) already
have: the same shell command can instead be dispatched into an isolated
**Docker container** or a **remote SSH host**, so the agent's commands run
somewhere disposable and the host filesystem stays private.

The design is a clean split between *pure* and *impure*:

- ``wrap_command(cmd, backend) -> list[str]`` and ``parse_backend(spec) -> dict``
  are PURE and deterministic — no subprocess, no I/O, fully unit-testable
  without Docker/SSH/network. They decide *how* a command would be launched.
- ``run_in_backend(...)`` is the ONLY impure piece — it takes the argv that
  ``wrap_command`` produced and actually runs it through ``subprocess``.

Backend spec shapes
-------------------
A "backend" is a plain ``dict`` (JSON/TOML-friendly, so it can live in config
later). Three types are understood:

``{"type": "local"}``
    Run on the host, exactly like today: ``sh -lc "<cmd>"``. This is the
    default and is a **no-op change** — nothing about existing behavior moves
    unless a non-local backend is explicitly opted into.

``{"type": "docker", "container": "<name-or-id>"}``  (running container)
``{"type": "docker", "image": "<image>"}``           (ephemeral container)
    Run inside Docker. With ``container`` we ``docker exec`` into an
    already-running container; with ``image`` we ``docker run --rm`` a throwaway
    one. Optional keys:
      - ``"workdir"``: ``-w <dir>`` working directory inside the container.
      - ``"user"``:    ``-u <user>`` user to run as inside the container.

``{"type": "ssh", "host": "user@host"}``
    Run on a remote host over SSH. Optional keys:
      - ``"port"``: int/str → adds ``-p <port>``.
      - ``"options"``: list[str] of extra raw ``ssh`` flags
        (e.g. ``["-i", "/path/key"]``) inserted verbatim before the host.

SAFETY NOTES (read before changing)
-----------------------------------
* **Host filesystem is private by default for docker/ssh.** A Docker container
  sees only what its image ships plus whatever volumes were mounted when it was
  created; an SSH host sees only its own disk. ronin does NOT add any bind mount
  or file sync here — so unless the *user* explicitly mounted the project into
  the container (``docker run -v $PWD:/work …``) or arranged a synced remote
  checkout, the agent literally cannot touch the host's files. That isolation is
  the whole point. ``wrap_command`` never injects ``-v``/mounts; mounting stays
  an explicit, user-controlled act.
* **Default stays ``local``.** ``parse_backend("local")`` and an absent backend
  both mean "run on the host" — opting into isolation is deliberate, never
  silent. Wiring a ``--backend`` flag through ``code_tools.run_command`` does not
  change behavior until a user passes a non-local value.
* **No shell-injection via the spec.** The command string is always passed as a
  *single argv element* to ``sh -lc`` (local/docker) or as ``ssh``'s single
  remote-command argument — it is never concatenated into a shell string here,
  so the spec can't be used to splice extra commands around the payload. The
  ``ssh`` host field is additionally validated (``_validate_ssh_host``) to a
  conservative ``[user@]host`` character set, rejecting spaces, shell
  metacharacters, and anything starting with ``-`` (which could otherwise be
  read as an ``ssh`` option rather than a host).
* The gating/approval model is unchanged and orthogonal: ``run_command`` is a
  SENSITIVE tool and is still gated by ``permissions.py`` regardless of which
  backend ultimately executes the approved command.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
import tempfile

__all__ = [
    "DEFAULT_BACKEND",
    "parse_backend",
    "wrap_command",
    "run_in_backend",
]

# The backend used when nothing is specified. Behavior-preserving: host shell.
DEFAULT_BACKEND: dict = {"type": "local"}

# Conservative allowlist for an SSH ``[user@]host`` token. Hostnames/IPv4 use
# alphanumerics, dots, and hyphens; an optional ``user@`` prefix uses the usual
# login-name characters. Deliberately strict — no spaces, no shell metacharacters
# (; | & $ ` etc.), so the host field cannot smuggle a second command. IPv6 in
# brackets and ``ssh://`` URLs are intentionally out of scope here; pass exotic
# targets via an SSH ``~/.ssh/config`` Host alias instead.
_SSH_HOST_RE = re.compile(r"^(?:[A-Za-z0-9._-]+@)?[A-Za-z0-9._-]+$")


def _host_shell(cmd: str) -> list[str]:
    """Return the native host-shell argv without changing container semantics."""
    if platform.system() == "Windows":
        # ``cmd.exe`` is present on supported Windows hosts. Keep the command as
        # one argv element, just as the POSIX wrapper does, so backend metadata
        # cannot splice flags or a second command around it.
        return ["cmd.exe", "/d", "/s", "/c", cmd]
    return ["sh", "-lc", cmd]


def _validate_ssh_host(host: str) -> str:
    """Return ``host`` if it is a safe ``[user@]host`` token, else raise.

    Guards against command injection through the host field (the one piece of a
    backend spec that ends up adjacent to argv we don't fully control) and
    against a leading ``-`` being misread by ``ssh`` as an option.
    """
    if not isinstance(host, str) or not host:
        raise ValueError("ssh backend requires a non-empty 'host'")
    if host.startswith("-"):
        # Would be parsed by ssh as a flag, not a destination.
        raise ValueError(f"unsafe ssh host {host!r}: must not start with '-'")
    if not _SSH_HOST_RE.match(host):
        raise ValueError(
            f"unsafe ssh host {host!r}: expected '[user@]hostname' with only "
            "letters, digits, '.', '-', '_' (no spaces or shell metacharacters)"
        )
    return host


def wrap_command(cmd: str, backend: dict) -> list[str]:
    """Return the argv that runs shell command ``cmd`` under ``backend``. PURE.

    Deterministic and side-effect-free: given the same inputs it always returns
    the same argv, and it touches no subprocess, filesystem, or network. This is
    the single source of truth for *how* each backend launches a command; the
    only impure caller is ``run_in_backend``.

    See the module docstring for the accepted ``backend`` spec shapes. ``cmd`` is
    always carried as one element so it cannot break out of the wrapper.
    """
    if not isinstance(backend, dict):
        raise ValueError(f"backend must be a dict, got {type(backend).__name__}")

    btype = backend.get("type", "local")

    if btype == "local":
        # Native host shell. The command remains one argv element on both POSIX
        # and Windows; Docker continues to use its container's POSIX shell.
        return _host_shell(cmd)

    if btype == "docker":
        container = backend.get("container")
        image = backend.get("image")
        if not container and not image:
            raise ValueError("docker backend requires 'container' or 'image'")
        if container and image:
            raise ValueError("docker backend: set 'container' OR 'image', not both")

        argv: list[str]
        if container:
            argv = ["docker", "exec"]
        else:
            # Ephemeral, auto-removed container from an image.
            argv = ["docker", "run", "--rm"]

        # Optional working dir / user, valid for both exec and run.
        workdir = backend.get("workdir")
        if workdir:
            argv += ["-w", str(workdir)]
        user = backend.get("user")
        if user:
            argv += ["-u", str(user)]

        argv.append(str(container) if container else str(image))
        argv += ["sh", "-lc", cmd]
        return argv

    if btype == "ssh":
        host = _validate_ssh_host(backend.get("host", ""))
        argv = ["ssh"]
        port = backend.get("port")
        if port is not None and port != "":
            argv += ["-p", str(port)]
        options = backend.get("options")
        if options:
            if not isinstance(options, (list, tuple)):
                raise ValueError("ssh backend 'options' must be a list of strings")
            argv += [str(o) for o in options]
        # ssh runs everything after the host as the remote command. Passing the
        # whole command as one argv element means the remote login shell receives
        # it verbatim; we do NOT wrap it in another sh -c (ssh's remote shell
        # already interprets it).
        argv += [host, cmd]
        return argv

    if btype == "seatbelt":
        # macOS-native OS sandbox via Seatbelt (sandbox-exec). Confines the
        # command's WRITES to the workspace + temp (+ cache dirs), denies
        # everything else by default. Reads/process/network stay allowed so real
        # build/test commands run. See :func:`seatbelt_profile`.
        if platform.system() != "Darwin":
            raise ValueError(
                "seatbelt backend requires macOS (Seatbelt / sandbox-exec); "
                "on Linux use a container/landlock backend"
            )
        workspace = backend.get("workspace") or os.getcwd()
        profile = seatbelt_profile(
            workspace,
            allow_network=bool(backend.get("allow_network", True)),
            extra_writes=backend.get("extra_writes"),
        )
        # -p passes the profile inline (no temp file → wrap_command stays pure);
        # the command is one argv element so it can't break out of the wrapper.
        return ["sandbox-exec", "-p", profile, "/bin/sh", "-c", cmd]

    raise ValueError(
        f"unknown backend type {btype!r}: expected 'local', 'docker', 'ssh', or 'seatbelt'"
    )


def _sb_quote(path: str) -> str:
    """Escape a path for a double-quoted Seatbelt string literal."""
    return path.replace("\\", "\\\\").replace('"', '\\"')


def seatbelt_profile(
    workspace: str,
    *,
    allow_network: bool = True,
    extra_writes: list[str] | None = None,
) -> str:
    """A macOS Seatbelt (.sb) profile that CONFINES WRITES to ``workspace`` + the
    per-user temp dir + standard cache dirs, and denies everything else. PURE.

    Design: ``(deny default)`` then re-allow only what a real coding command needs
    — process fork/exec, reading anywhere (compilers read system libs; reads are
    low-risk), and WRITING only under the workspace, ``$TMPDIR`` and package-manager
    caches (so ``uv`` / ``pip`` / ``npm`` still work). The result blocks the moves
    that matter: writing to ``$HOME`` (``~/.ssh``, dotfiles), ``/etc``, ``/usr``,
    or any other project. ``allow_network=False`` also cuts the network.

    Returned as a single line so it can be passed inline via ``sandbox-exec -p``.

    LIMITATION (stated honestly, not overclaimed): this profile confines WRITES and
    process damage. It does NOT confine READS — a sandboxed command can still read
    files (``~/.ssh``, ``~/.aws``) — and by default the network is open, so a
    malicious command could read a secret and exfiltrate it. Use
    ``allow_network=False`` (``RONIN_BACKEND=seatbelt:no-network``) to also cut the
    network. Full read-confinement is not offered because it breaks ordinary tools
    that read system/config files; the guarantee here is "can't damage your machine
    or files outside the project", not "can't see anything".
    """
    ws = os.path.realpath(workspace)
    # A sandbox rooted at "/" would allow writes EVERYWHERE — a silent no-op sandbox,
    # worse than none because the user believes they are contained. Refuse it
    # (fail-closed → the selection turns this into a refusal, never a host run).
    if ws in ("/", ""):
        raise ValueError(
            "refusing a seatbelt sandbox rooted at '/': it would allow writes "
            "everywhere. cd into a specific project directory."
        )
    home = os.path.expanduser("~")
    writes = [
        ws,
        os.path.realpath(tempfile.gettempdir()),
        os.path.join(home, ".cache"),
        os.path.join(home, "Library", "Caches"),
        os.path.join(home, ".npm"),
        os.path.join(home, ".cargo", "registry"),
    ]
    for p in extra_writes or []:
        writes.append(os.path.realpath(p))
    seen: set[str] = set()
    uniq = [p for p in writes if p and not (p in seen or seen.add(p))]
    write_rules = " ".join(f'(subpath "{_sb_quote(p)}")' for p in uniq)
    net = "(allow network*)" if allow_network else "(deny network*)"
    return (
        "(version 1) (deny default) (allow process*) (allow file-read*) "
        "(allow file-ioctl) (allow sysctl-read) (allow mach*) (allow ipc-posix*) "
        "(allow signal (target self)) "
        f"(allow file-write* {write_rules}) "
        '(allow file-write-data (literal "/dev/null") (literal "/dev/tty") '
        '(literal "/dev/stdout") (literal "/dev/stderr") (literal "/dev/dtracehelper") '
        '(literal "/dev/random") (literal "/dev/urandom")) '
        f"{net}"
    )


def parse_backend(spec: str) -> dict:
    """Parse a ``--backend`` string into a backend dict. PURE.

    Accepted forms::

        "local"                  -> {"type": "local"}
        "docker:<container>"     -> {"type": "docker", "container": "<container>"}
        "ssh:user@host"          -> {"type": "ssh", "host": "user@host"}
        "ssh:user@host:2222"     -> {"type": "ssh", "host": "user@host", "port": 2222}

    A bare ``""`` / ``"local"`` yields the default local backend. The
    ``docker:`` form maps to a *running container* by name/id (use a dict spec
    directly if you want the ``image`` / ephemeral form). Unknown types raise a
    clear ``ValueError`` so a typo fails loudly instead of silently running on
    the host.
    """
    if spec is None:
        raise ValueError("backend spec must be a string, got None")
    spec = spec.strip()
    if spec == "" or spec == "local":
        return {"type": "local"}

    head, sep, rest = spec.partition(":")
    head = head.strip().lower()

    if head == "local":
        return {"type": "local"}

    if head == "seatbelt":
        # "seatbelt" (network allowed) or "seatbelt:no-network" / ":strict" (cut net).
        opt = rest.strip().lower()
        allow_net = opt not in ("no-network", "no-net", "nonet", "strict")
        return {"type": "seatbelt", "allow_network": allow_net}

    if head == "docker":
        container = rest.strip()
        if not container:
            raise ValueError(
                "docker backend spec needs a container name: 'docker:<container>'"
            )
        return {"type": "docker", "container": container}

    if head == "ssh":
        target = rest.strip()
        if not target:
            raise ValueError("ssh backend spec needs a host: 'ssh:user@host[:port]'")
        # Split a trailing ``:port`` off the right. We only treat the LAST
        # ``:NNN`` segment as a port, and only when it is all digits — so
        # ``ssh:user@host`` (no port) and ``ssh:user@host:2222`` both parse, and
        # a stray colon in a username doesn't get mistaken for a port.
        host = target
        port: int | None = None
        h, psep, maybe_port = target.rpartition(":")
        if psep and maybe_port.isdigit() and h:
            host = h
            port = int(maybe_port)
        out: dict = {"type": "ssh", "host": _validate_ssh_host(host)}
        if port is not None:
            out["port"] = port
        return out

    raise ValueError(
        f"unknown backend {head!r} in spec {spec!r}: expected 'local', "
        "'docker:<container>', or 'ssh:user@host[:port]'"
    )


def run_in_backend(
    cmd: str,
    backend: dict,
    *,
    cwd: str | None = None,
    timeout: int = 120,
) -> tuple[int, str, str]:
    """Execute ``cmd`` under ``backend`` and return ``(exit_code, stdout, stderr)``.

    The ONLY impure function here: it builds the argv with the pure
    ``wrap_command`` and runs it via ``subprocess.run``. Output is captured as
    text. ``cwd`` is the *local* working directory the launcher process runs in
    (it sets where ``docker``/``ssh``/``sh`` are invoked from on the host; for
    docker/ssh the *remote* working directory is controlled by the backend's
    ``workdir`` / the remote default, not by ``cwd``). ``timeout`` is in seconds.

    A non-zero exit from the command surfaces in the returned code (no raise). A
    timeout raises ``subprocess.TimeoutExpired``, matching ``subprocess`` norms,
    so callers can distinguish "command failed" from "command hung".
    """
    # Seatbelt confines writes to a workspace; default it to where we run (cwd),
    # i.e. the project root, so the profile is generated for the right directory.
    if backend.get("type") == "seatbelt" and not backend.get("workspace"):
        backend = {**backend, "workspace": cwd or os.getcwd()}
    argv = wrap_command(cmd, backend)
    proc = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, (proc.stdout or ""), (proc.stderr or "")


def requested_backend() -> str | None:
    """The sandbox backend requested via ``RONIN_BACKEND``, or ``None`` for host.

    Unset / empty / ``"local"`` means "run on the host on purpose"; any other value
    is a request to CONTAIN, and a failure to honor it must fail CLOSED — see
    :func:`sandbox_refusal`."""
    import os
    spec = os.environ.get("RONIN_BACKEND", "").strip()
    return spec if spec and spec.lower() != "local" else None


def sandbox_refusal(spec: str, reason: str) -> str:
    """The message a tool returns when a requested sandbox cannot run a command.

    Ronin refuses rather than falling back to the host: a command the user asked to
    contain must never silently execute on their real machine (Phase A: 'never
    silently execute outside the requested sandbox')."""
    return (
        f"blocked: RONIN_BACKEND requested the sandbox {spec!r}, but it could not "
        f"run this command ({reason}). Refusing to run on the host — a command you "
        "asked to contain must not silently escape to your real machine. Fix the "
        "backend (is Docker / the container running?), or unset RONIN_BACKEND to run "
        "on the host on purpose."
    )
