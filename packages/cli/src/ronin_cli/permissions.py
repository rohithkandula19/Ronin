"""Persistent permission rules for the approval gate — `ronin code`.

When you answer **[a]lways** at an approval prompt, ronin remembers it so the
same action runs un-prompted next time — the cure for approval fatigue (the #1
reason people flip ``--yolo`` and lose all protection).

TRUST MODEL (this is security-critical — read before changing):

- **Allow** rules are written ONLY by ronin itself, when you answer ``[a]lways``,
  into your **user-global** config ``~/.ronin/permissions.json``, keyed by the
  project's absolute path. A cloned/hostile repo therefore **cannot ship an
  allow-rule** that auto-approves anything — only you can grant, only for the
  repo you're actually in. The persisted match is glob-*escaped* (an exact
  literal), so approving ``rm -rf build/*`` once does NOT silently allow
  ``rm -rf build/../../etc`` later.

- **Deny** rules are read from BOTH the global store AND the project-local
  ``<root>/.ronin/settings.json``. A deny can only *add* friction (never grant),
  so honoring a repo-committed deny-rule is fail-safe — a team can commit
  "never run rm -rf" and it protects everyone. **Deny always wins over allow**,
  and a deny applies even under ``--yolo`` (a real kill-switch).

A rule is ``{tool, action, match}``: ``tool`` is an exact tool name or ``"*"``;
``action`` is ``"allow"`` or ``"deny"``; ``match`` is an ``fnmatch`` glob tested
against the call's *subject* — the command for ``run_command``, the path for
file tools, else the tool name. NOTE: ``fnmatch`` ``*`` spans ``/`` (so a
hand-written ``src/*`` matches every descendant, not just direct children).
"""
from __future__ import annotations

import fnmatch
import glob as _glob
import json
from dataclasses import dataclass, field
from pathlib import Path

SETTINGS_NAME = "settings.json"
_PERMISSIONS_KEY = "permissions"

# Tools whose match-subject is the command / path rather than the tool name.
_COMMAND_TOOLS = {"run_command", "run_background"}
_PATH_TOOLS = {"write_file", "edit_file", "multi_edit"}


def _global_store_path() -> Path:
    """User-global allow/deny store — ``~/.ronin/permissions.json``. Only ronin
    writes here, so a project directory can't inject allow-rules."""
    return Path.home() / ".ronin" / "permissions.json"


def _project_settings_path(root: str | Path) -> Path:
    return Path(root) / ".ronin" / SETTINGS_NAME


def _repo_key(root: str | Path) -> str:
    return str(Path(root).resolve())


def subject_for(tool: str, args: dict) -> str:
    """The string a rule's ``match`` glob is tested against for this call:
    the command for command tools, the path for ANY tool that takes one (so a
    deny like ``read_file *.env`` actually scopes by path), else the tool name."""
    if tool in _COMMAND_TOOLS:
        return str(args.get("command", ""))
    if "path" in args:
        # normalize so 'src/a.py' and './src/a.py' resolve to the same rule
        # subject (a granted allow shouldn't re-prompt on a cosmetic path form).
        import os
        return os.path.normpath(str(args["path"]))
    return tool  # MCP / plugin / other → match on the tool name itself


@dataclass(frozen=True)
class Rule:
    tool: str
    action: str          # "allow" | "deny"
    match: str = "*"

    def applies(self, tool: str, subject: str) -> bool:
        if self.tool != "*" and self.tool != tool:
            return False
        return fnmatch.fnmatch(subject, self.match)

    def to_dict(self) -> dict:
        return {"tool": self.tool, "action": self.action, "match": self.match}


@dataclass
class PermissionRules:
    """A project's effective allow/deny rules for the approval gate."""

    rules: list[Rule] = field(default_factory=list)

    def deny_reason(self, tool: str, args: dict) -> str | None:
        """The match of the first matching deny-rule, or None. Checked even under
        --yolo so a hand-written deny is a true kill-switch."""
        subject = subject_for(tool, args)
        for r in self.rules:
            if r.action == "deny" and r.applies(tool, subject):
                return r.match
        return None

    def check(self, tool: str, args: dict) -> str:
        """Return ``"allow"``, ``"deny"``, or ``"ask"`` — deny wins over allow."""
        subject = subject_for(tool, args)
        matched = [r for r in self.rules if r.applies(tool, subject)]
        if any(r.action == "deny" for r in matched):
            return "deny"
        if any(r.action == "allow" for r in matched):
            return "allow"
        return "ask"

    def rule_for(self, tool: str, args: dict, action: str = "allow") -> Rule:
        """The rule an ``[a]lways`` answer persists for this call: an EXACT match
        on the command/path (glob metacharacters escaped so the stored pattern is
        a literal), or the tool name for MCP/plugins."""
        subject = subject_for(tool, args)
        if tool in _COMMAND_TOOLS or tool in _PATH_TOOLS:
            # Escape *, ?, [ ] so "rm -rf build/*" can't later fnmatch-match
            # "rm -rf build/../../etc" — the approval was for the literal command.
            return Rule(tool=tool, action=action, match=_glob.escape(subject) if subject else "*")
        return Rule(tool=tool, action=action, match="*")

    def add(self, rule: Rule) -> None:
        if rule not in self.rules:
            self.rules.append(rule)


def _read_global() -> dict:
    path = _global_store_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def _write_global(store: dict) -> None:
    path = _global_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")


def _parse_rules(raw: object) -> list[Rule]:
    out: list[Rule] = []
    if isinstance(raw, list):
        for r in raw:
            if isinstance(r, dict) and r.get("tool") and r.get("action") in ("allow", "deny"):
                out.append(Rule(tool=str(r["tool"]), action=str(r["action"]),
                                match=str(r.get("match", "*"))))
    return out


def load_rules(root: str | Path) -> PermissionRules:
    """Effective rules for ``root``: user-global allow+deny for this repo, plus
    project-local **deny** rules (allow-rules in the project file are ignored —
    only you can grant, via ``[a]lways``)."""
    rules: list[Rule] = []
    # 1) user-global rules the user created for THIS repo (trusted: ronin-written)
    rules.extend(_parse_rules(_read_global().get(_repo_key(root), [])))
    # 2) project-local DENY rules only (fail-safe; committed allow-rules ignored)
    settings = _project_settings_path(root)
    if settings.is_file():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            data = {}
        for r in _parse_rules(data.get(_PERMISSIONS_KEY, []) if isinstance(data, dict) else []):
            if r.action == "deny":
                rules.append(r)
    return PermissionRules(rules=rules)


def add_allow_rule(root: str | Path, rule: Rule) -> None:
    """Persist a user-approved allow-rule into the user-global store for ``root``."""
    store = _read_global()
    bucket = store.setdefault(_repo_key(root), [])
    d = rule.to_dict()
    if d not in bucket:
        bucket.append(d)
    _write_global(store)


def clear_rules(root: str | Path) -> None:
    """Drop all user-created (global) rules for ``root``. Project-committed
    deny-rules in settings.json are left in place (can't be cleared from here)."""
    store = _read_global()
    if store.pop(_repo_key(root), None) is not None:
        _write_global(store)
