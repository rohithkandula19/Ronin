"""Industry pack manifest schema and fail-closed validation.

The manifest is the single source of truth for what a world is allowed to do.
Validation is strict on the fields that carry safety weight:

- a HIGH-risk pack must declare required policies, required eval suites, and
  an explicit blocked-capabilities list — a high-risk pack with nothing
  forbidden is treated as a configuration error, not a permissive default;
- a STABLE pack must declare required eval suites (stability is an earned
  state backed by evaluations, not a label);
- ids, versions, countries, and languages have strict shapes so downstream
  routing never has to guess.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_LANG_RE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")

# The capability vocabulary routing understands today. A manifest naming an
# unknown capability fails validation — silently ignoring it would let a pack
# believe something is blocked when the runtime has no such notion.
KNOWN_MODEL_CAPABILITIES = frozenset(
    {"text", "vision", "retrieval", "structured_output", "tool_use", "embedding", "code"}
)


class ManifestError(ValueError):
    """A manifest failed validation. The pack must not load."""


class PackStatus(str, Enum):
    draft = "draft"
    beta = "beta"
    stable = "stable"
    disabled = "disabled"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class PackManifest(BaseModel):
    """Declarative definition of an industry world."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str = Field(min_length=1, max_length=80)
    version: str
    status: PackStatus
    risk_level: RiskLevel
    description: str = Field(min_length=1, max_length=500)
    supported_roles: tuple[str, ...] = Field(min_length=1)
    supported_countries: tuple[str, ...] = Field(min_length=1)
    supported_languages: tuple[str, ...] = Field(min_length=1)
    default_adapter: str | None = None
    allowed_model_capabilities: tuple[str, ...] = Field(min_length=1)
    allowed_tools: tuple[str, ...] = ()
    blocked_capabilities: tuple[str, ...] = ()
    required_policies: tuple[str, ...] = ()
    required_eval_suites: tuple[str, ...] = ()

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(f"pack id {v!r} must match {_ID_RE.pattern}")
        return v

    @field_validator("version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not _SEMVER_RE.match(v):
            raise ValueError(f"version {v!r} is not MAJOR.MINOR.PATCH")
        return v

    @field_validator("supported_countries")
    @classmethod
    def _countries(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for c in v:
            if not _COUNTRY_RE.match(c):
                raise ValueError(f"country {c!r} must be an ISO 3166-1 alpha-2 code")
        return v

    @field_validator("supported_languages")
    @classmethod
    def _languages(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for lang in v:
            if not _LANG_RE.match(lang):
                raise ValueError(f"language {lang!r} must look like 'en' or 'en-US'")
        return v

    @field_validator("allowed_model_capabilities")
    @classmethod
    def _capabilities(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(v) - KNOWN_MODEL_CAPABILITIES)
        if unknown:
            raise ValueError(
                f"unknown model capabilities {unknown}; known: {sorted(KNOWN_MODEL_CAPABILITIES)}"
            )
        return v

    @model_validator(mode="after")
    def _safety_invariants(self) -> "PackManifest":
        if self.risk_level is RiskLevel.high:
            missing = [
                name
                for name, value in (
                    ("required_policies", self.required_policies),
                    ("required_eval_suites", self.required_eval_suites),
                    ("blocked_capabilities", self.blocked_capabilities),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    f"high-risk pack {self.id!r} must declare {', '.join(missing)} "
                    "(a high-risk world with no explicit limits is a config error)"
                )
        if self.status is PackStatus.stable and not self.required_eval_suites:
            raise ValueError(
                f"pack {self.id!r} cannot be 'stable' without required_eval_suites — "
                "stability must be backed by evaluations"
            )
        overlap = set(self.blocked_capabilities) & set(self.allowed_model_capabilities)
        if overlap:
            raise ValueError(
                f"pack {self.id!r} both allows and blocks {sorted(overlap)}"
            )
        return self


def parse_manifest(data: dict) -> PackManifest:
    """Validate a parsed YAML mapping into a manifest, or raise ManifestError."""
    if not isinstance(data, dict):
        raise ManifestError("manifest must be a YAML mapping")
    try:
        return PackManifest.model_validate(data)
    except Exception as exc:  # pydantic ValidationError and friends
        raise ManifestError(str(exc)) from exc


def load_manifest(path: str | Path) -> PackManifest:
    """Load and validate manifest.yaml at `path`, or raise ManifestError."""
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"cannot read {p}: {exc}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ManifestError(f"{p} is not valid YAML: {exc}") from exc
    return parse_manifest(data)
