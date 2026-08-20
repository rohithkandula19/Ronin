"""What models, servers, and tools this machine actually has — probed offline.

The first-run wizard and ``ronin doctor`` both need to answer the same three
questions before they can say anything useful: is there a provider key in the
environment, is a local model server actually listening, and is ripgrep on ``PATH``.
None of those answers should come from a global — a wizard that reads the real
``os.environ`` cannot be tested on a machine that happens to have ``ANTHROPIC_API_KEY``
set, and a probe that opens a real socket cannot be tested at all without a server to
open it against.

So every probe is an **injected callable** and this module is otherwise pure:

* ``env`` is a ``Mapping[str, str]`` passed in, never ``os.environ`` read from inside;
* ``probe`` is ``Callable[[str, int], bool]`` — the real one is :func:`real_probe`, a
  short-timeout ``socket.create_connection``, and it is the single impure function
  here. Tests pass a fake prober and never touch the network;
* ``which`` defaults to :func:`shutil.which` but is overridable, so ripgrep detection
  is hermetic too.

:func:`detect` folds the three into one :class:`Detection`. The caller (the wizard,
``doctor``, or ``main``) decides what to do with it; this module only reports.
"""

from __future__ import annotations

import shutil
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass

#: Provider name → the environment variable that holds its API key. These are the
#: hosted providers Ronin knows how to reach; the presence of the variable is all
#: this layer checks — whether the key is *valid* is a question only a request can
#: answer, and a diagnostic must not make one. ``gemini`` and ``cerebras`` speak an
#: OpenAI-compatible wire protocol (the wizard writes them as ``openai-compatible``
#: with an explicit ``base_url``); the rest have a first-class adapter.
PROVIDER_KEYS: Mapping[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


@dataclass(frozen=True, slots=True)
class LocalServer:
    """A local model server Ronin can reach, and where it listens by default.

    ``base_url`` is the OpenAI-compatible endpoint the provider adapter uses; it is the
    same string as ``ronin.providers.openai_compat.KNOWN_BASE_URLS`` gives each of these
    names, kept here so the wizard can write a working config without importing the
    provider layer.
    """

    name: str
    host: str
    port: int
    base_url: str


#: Known local model servers and their default ports, in the order the wizard prefers
#: them. Ollama first: it is the zero-config default a "clean container with only
#: ollama" ships, and the case the first-run wizard is measured against.
LOCAL_SERVERS: Mapping[str, LocalServer] = {
    "ollama": LocalServer("ollama", "localhost", 11434, "http://localhost:11434/v1"),
    "lmstudio": LocalServer("lmstudio", "localhost", 1234, "http://localhost:1234/v1"),
    "vllm": LocalServer("vllm", "localhost", 8000, "http://localhost:8000/v1"),
}

#: The ripgrep binary. The grep/glob/ls tools shell out to this exact name
#: (``ronin.tools.search``), so this is what detection looks for.
RIPGREP_BINARY = "rg"


@dataclass(frozen=True, slots=True)
class Detection:
    """What was found on this machine: keys, reachable local servers, and ripgrep.

    Pure data, so a test can assert on it and the wizard and ``doctor`` can both read
    the same value rather than each probing separately and disagreeing.
    """

    keys: tuple[str, ...] = ()
    local_servers: tuple[str, ...] = ()
    ripgrep: bool = False

    def has_any_model(self) -> bool:
        """Whether *some* model is reachable — a provider key or a live local server.

        The one question the wizard and ``doctor`` most need answered: with neither, no
        session can start, and that is the failure a first run has to name.
        """
        return bool(self.keys or self.local_servers)


def detect(
    *,
    env: Mapping[str, str],
    probe: Callable[[str, int], bool],
    which: Callable[[str], str | None] = shutil.which,
) -> Detection:
    """Run the three probes and fold them into a :class:`Detection`.

    Everything impure is injected: ``env`` is the environment as a mapping, ``probe``
    answers "is something listening at ``host:port``?", and ``which`` finds a binary.
    The real ``probe`` is :func:`real_probe`; tests pass a fake and stay offline.

    An empty or whitespace-only value counts as *no key*: an exported-but-blank
    ``ANTHROPIC_API_KEY=`` is the shape a shell profile leaves behind, and treating it
    as present is how a first run confidently writes a config that 401s on turn one.
    """
    keys = tuple(name for name, var in PROVIDER_KEYS.items() if env.get(var, "").strip())
    servers = tuple(
        server.name for server in LOCAL_SERVERS.values() if probe(server.host, server.port)
    )
    return Detection(keys=keys, local_servers=servers, ripgrep=which(RIPGREP_BINARY) is not None)


def real_probe(host: str, port: int, *, timeout: float = 0.3) -> bool:
    """Is something accepting connections at ``host:port``? The one impure function.

    A short timeout on purpose: a first run probes three dead ports on the common
    machine that has no local server, and three multi-second stalls is a wizard people
    ``Ctrl-C`` out of. ``0.3s`` is long enough for a loopback connection that will
    succeed and short enough that three failures are imperceptible.

    Never called from a test — the whole point of the ``probe`` seam is that tests pass
    a fake prober, so this connects to nothing when the suite runs.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        # ConnectionRefusedError, timeout, DNS failure — every "not reachable" reason
        # is an OSError, and none of them is an error *here*: an absent server is a
        # fact to report, not an exception to raise.
        return False


__all__ = [
    "LOCAL_SERVERS",
    "PROVIDER_KEYS",
    "RIPGREP_BINARY",
    "Detection",
    "LocalServer",
    "detect",
    "real_probe",
]
