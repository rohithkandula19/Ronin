"""Non-executing manifest reader for project-local Python plugins.

Plugin modules are executable code, so the host must inspect their declared
capabilities before importing them. ``PLUGIN`` is a literal top-level dictionary
read with :mod:`ast`, never by executing the plugin.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CAPABILITIES = ("network", "filesystem", "subprocess", "payment")
HIGH_RISK_CAPABILITIES = frozenset({"subprocess", "payment"})


class ManifestError(ValueError):
    """A plugin manifest is malformed or cannot be read safely."""


@dataclass(frozen=True)
class PluginManifest:
    """A validated, host-readable plugin declaration."""

    name: str | None
    version: str | None
    description: str | None
    capabilities: tuple[str, ...]
    declared: bool

    @property
    def high_risk_capabilities(self) -> tuple[str, ...]:
        return tuple(c for c in self.capabilities if c in HIGH_RISK_CAPABILITIES)


_DEFAULT = PluginManifest(
    name=None,
    version=None,
    description=None,
    # Pre-manifest plugins are fail-closed: they cannot omit a declaration to
    # claim a less dangerous posture.
    capabilities=CAPABILITIES,
    declared=False,
)


def read_manifest(path: Path | str) -> PluginManifest:
    """Read literal ``PLUGIN = {...}`` metadata without importing ``path``."""
    p = Path(path)
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
    except OSError as exc:
        raise ManifestError(f"could not read plugin: {exc}") from exc
    except SyntaxError as exc:
        raise ManifestError(f"invalid Python: {exc.msg} (line {exc.lineno})") from exc

    value: ast.expr | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == "PLUGIN" for target in node.targets):
                value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "PLUGIN":
            value = node.value

    if value is None:
        return _DEFAULT
    try:
        raw = ast.literal_eval(value)
    except (ValueError, TypeError) as exc:
        raise ManifestError("PLUGIN must be a literal dictionary") from exc
    if not isinstance(raw, dict):
        raise ManifestError("PLUGIN must be a dictionary")
    return _validate(raw)


def _validate(raw: dict[Any, Any]) -> PluginManifest:
    for key in ("name", "version", "description"):
        value = raw.get(key)
        if value is not None and not isinstance(value, str):
            raise ManifestError(f"PLUGIN.{key} must be a string")

    supplied = raw.get("capabilities", CAPABILITIES)
    if not isinstance(supplied, (list, tuple)) or not all(isinstance(item, str) for item in supplied):
        raise ManifestError("PLUGIN.capabilities must be a list of strings")
    capabilities = tuple(supplied)
    if len(set(capabilities)) != len(capabilities):
        raise ManifestError("PLUGIN.capabilities must not contain duplicates")
    unknown = sorted(set(capabilities) - set(CAPABILITIES))
    if unknown:
        raise ManifestError("PLUGIN.capabilities contains unsupported value(s): " + ", ".join(unknown))

    return PluginManifest(
        name=raw.get("name"),
        version=raw.get("version"),
        description=raw.get("description"),
        capabilities=capabilities,
        declared=True,
    )


def capability_label(manifest: PluginManifest | None) -> str:
    """A concise, non-secret capability label for tables and approval prompts."""
    if manifest is None:
        return "unavailable"
    values = ", ".join(manifest.capabilities) or "none"
    return values if manifest.declared else f"{values} (undeclared)"
