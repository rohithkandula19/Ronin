"""Permissions and safety: the gate between a model's intent and the machine.

One idea holds the whole package together: **the gate has to be cheap to say yes to.**
A gate that prompts on ``ls -la`` gets switched off inside a day, and a switched-off gate
protects nothing at all. So the allowlist is broad on purpose, the refusals explain
themselves, and "no" carries feedback the model can act on instead of ending the turn.

Seven modules, in dependency order:

* :mod:`~ronin.safety.command` — parses a command line into segments and resolves each
  segment's real binary. Everything else is built on it, because a regex over a raw
  string cannot answer "what will this actually run": ``echo safe; rm -rf /`` says
  ``echo`` at the front and deletes the filesystem at the back.
* :mod:`~ronin.safety.denylist` — the short list of actions no approval can authorize,
  each with why it is unconditional and what to do instead. Only ``--yolo`` removes it.
* :mod:`~ronin.safety.injection` — all tool output is data; content is flagged, never
  silently stripped; and any call whose arguments quote freshly fetched content
  escalates to ``ask`` regardless of the allowlist.
* :mod:`~ronin.safety.net` — where a fetch may go, and what a URL may say in a log.
  A URL the *model* chose is a request made from inside the network the user trusts,
  so private, loopback and link-local targets are refused in every notation — the
  cloud metadata endpoint is the one that matters — and unclassifiable hosts fail
  closed rather than being handed to a resolver.
* :mod:`~ronin.safety.sandbox` — off by default, and honest: :func:`~ronin.safety.sandbox.detect`
  returns a configured backend or a reason, never a fallback that pretends to isolate.
* :mod:`~ronin.safety.policy` — rules, a documented precedence order, and the four
  answers a human can give (yes once / yes this session / yes and write it down / no
  with feedback). ``PolicyEngine`` satisfies ``ronin.core.protocols.Policy``.
* :mod:`~ronin.safety.settings` — the layered config, with provenance for every
  effective rule and one loud named error per malformed layer.

Depends on ``ronin.core`` and nothing else in Ronin. Subprocesses, git state and the UI
all arrive as injected callables — ``has_checkpoint``, ``Asker``, ``which`` — which is
what lets the whole package be tested offline with no repo, no terminal and no binaries.

Known gaps, named rather than papered over:

* The deny list's path classification uses ``normpath``, not ``resolve``, so it does not
  follow a **symlink**: ``rm -rf ./link`` is judged by the literal ``./link``, not by where
  the link points. This is deliberate, not a hole in the write boundary — the deny list
  analyses bash *command text* and must never touch the disk (see :meth:`Denylist.resolve`).
  The actual file-write confinement is ``ToolContext.resolve`` in the tools layer, which
  *does* resolve symlinks and refuses any target outside the workspace, so ``read``/
  ``write``/``edit`` cannot escape the tree through a symlink.
* Taint tracking is substring matching over fetched spans: it catches a copied span and
  misses a paraphrase. The tradeoff is argued in :mod:`~ronin.safety.injection`.
* Variables other than ``$HOME`` are not expanded, so ``rm -rf "$TARGET"`` is judged on
  the literal text. The deny list therefore cannot see through indirection a shell
  would resolve at runtime.
* The URL check classifies the host as written and does **not** resolve names, so a
  public hostname whose ``A`` record points inward is not caught, and a redirect to
  such an address is followed below this seam. Argued in :mod:`~ronin.safety.net`.
"""

from __future__ import annotations

from .command import (
    CODE_INTERPRETERS,
    EVAL_BINARIES,
    FETCH_BINARIES,
    SHELL_EXECUTORS,
    WRAPPERS,
    Hazard,
    HazardCode,
    Origin,
    Redirect,
    Segment,
    Severity,
    hazards,
    parse_command,
    resolve_binary,
    worst_severity,
)
from .denylist import (
    DENY_REASONS,
    SECRET_DIRECTORIES,
    WRITE_BINARIES,
    DenyCode,
    DenyHit,
    Denylist,
    DenyReason,
)
from .injection import (
    CLOSE_MARKER,
    MIN_TAINT_SPAN,
    OPEN_MARKER,
    STANDING_INSTRUCTION,
    InjectionFinding,
    InjectionKind,
    ScanResult,
    TaintHit,
    TaintTracker,
    scan,
    wrap_and_scan,
    wrap_untrusted,
)
from .net import (
    ALLOWED_SCHEMES,
    BLOCKED_NAMES,
    BLOCKED_SUFFIXES,
    REDACTED,
    UrlNotAllowed,
    address_reason,
    check_url,
    host_reason,
    parse_address,
    redact_url,
    split_host,
)
from .policy import (
    BUILTIN_SOURCE,
    COMMAND_ARGUMENT,
    HAZARD_FLOOR,
    PATH_ARGUMENTS,
    Answer,
    AnyUse,
    Asker,
    AuditEntry,
    CommandRegex,
    Decision,
    Exact,
    Matcher,
    MatchTarget,
    Outcome,
    PathGlob,
    PolicyEngine,
    Resolution,
    Rule,
    RuleSet,
    UnattendedAsker,
    Verdict,
    builtin_rules,
    builtin_ruleset,
    glob_to_regex,
    most_restrictive,
)
from .sandbox import (
    SANDBOX_AUTO_APPROVES,
    BubblewrapSandbox,
    DockerSandbox,
    NoSandbox,
    Sandbox,
    SeatbeltSandbox,
    Unavailable,
    detect,
)
from .settings import (
    LAYER_NAMES,
    LOCAL_SETTINGS,
    PROJECT_SETTINGS,
    USER_SETTINGS,
    Layer,
    LayerError,
    Settings,
    load_settings,
    parse_rule,
)

__all__ = [
    "ALLOWED_SCHEMES",
    "BLOCKED_NAMES",
    "BLOCKED_SUFFIXES",
    "BUILTIN_SOURCE",
    "CLOSE_MARKER",
    "CODE_INTERPRETERS",
    "COMMAND_ARGUMENT",
    "DENY_REASONS",
    "EVAL_BINARIES",
    "FETCH_BINARIES",
    "HAZARD_FLOOR",
    "LAYER_NAMES",
    "LOCAL_SETTINGS",
    "MIN_TAINT_SPAN",
    "OPEN_MARKER",
    "PATH_ARGUMENTS",
    "PROJECT_SETTINGS",
    "REDACTED",
    "SANDBOX_AUTO_APPROVES",
    "SECRET_DIRECTORIES",
    "SHELL_EXECUTORS",
    "STANDING_INSTRUCTION",
    "USER_SETTINGS",
    "WRAPPERS",
    "WRITE_BINARIES",
    "Answer",
    "AnyUse",
    "Asker",
    "AuditEntry",
    "BubblewrapSandbox",
    "CommandRegex",
    "Decision",
    "DenyCode",
    "DenyHit",
    "DenyReason",
    "Denylist",
    "DockerSandbox",
    "Exact",
    "Hazard",
    "HazardCode",
    "InjectionFinding",
    "InjectionKind",
    "Layer",
    "LayerError",
    "MatchTarget",
    "Matcher",
    "NoSandbox",
    "Origin",
    "Outcome",
    "PathGlob",
    "PolicyEngine",
    "Redirect",
    "Resolution",
    "Rule",
    "RuleSet",
    "Sandbox",
    "ScanResult",
    "SeatbeltSandbox",
    "Segment",
    "Settings",
    "Severity",
    "TaintHit",
    "TaintTracker",
    "UnattendedAsker",
    "Unavailable",
    "UrlNotAllowed",
    "Verdict",
    "address_reason",
    "builtin_rules",
    "builtin_ruleset",
    "check_url",
    "detect",
    "glob_to_regex",
    "hazards",
    "host_reason",
    "load_settings",
    "most_restrictive",
    "parse_address",
    "parse_command",
    "parse_rule",
    "redact_url",
    "resolve_binary",
    "scan",
    "split_host",
    "worst_severity",
    "wrap_and_scan",
    "wrap_untrusted",
]
