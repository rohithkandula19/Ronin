"""Pack registry: activation gates and health checks.

The registry is the only supported way for a runtime to obtain a pack.
Activation is gated: a pack that is disabled, unknown, or unhealthy raises
`ActivationError` — the caller never receives a best-effort pack.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .discovery import DiscoveredPack, DiscoveryResult
from .manifest import PackManifest, PackStatus


class ActivationError(RuntimeError):
    """The requested pack cannot be activated. Fail closed."""


@dataclass(frozen=True)
class HealthIssue:
    severity: str  # "error" | "warning"
    message: str


@dataclass
class HealthReport:
    pack_id: str
    issues: list[HealthIssue] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)


class PackRegistry:
    """Holds validated packs and answers activation requests."""

    def __init__(self) -> None:
        self._packs: dict[str, DiscoveredPack] = {}
        self._discovery_errors: dict[str, str] = {}

    # ------------------------------------------------------------- loading
    @classmethod
    def from_discovery(cls, result: DiscoveryResult) -> "PackRegistry":
        reg = cls()
        for pack in result.packs:
            reg.register(pack)
        reg._discovery_errors = dict(result.errors)
        return reg

    def register(self, pack: DiscoveredPack) -> None:
        if pack.manifest.id in self._packs:
            raise ActivationError(f"pack {pack.manifest.id!r} already registered")
        self._packs[pack.manifest.id] = pack

    # ------------------------------------------------------------- queries
    def list_packs(self, *, include_disabled: bool = True) -> list[PackManifest]:
        packs = [p.manifest for p in self._packs.values()]
        if not include_disabled:
            packs = [m for m in packs if m.status is not PackStatus.disabled]
        return sorted(packs, key=lambda m: m.id)

    @property
    def discovery_errors(self) -> dict[str, str]:
        return dict(self._discovery_errors)

    def get(self, pack_id: str) -> PackManifest | None:
        pack = self._packs.get(pack_id)
        return pack.manifest if pack else None

    # ---------------------------------------------------------- activation
    def activate(
        self,
        pack_id: str,
        *,
        role: str,
        country: str,
        language: str,
        known_tools: Iterable[str] | None = None,
        known_eval_suites: Iterable[str] | None = None,
        known_adapters: Iterable[str] | None = None,
    ) -> PackManifest:
        """Return the manifest for a working session, or raise ActivationError.

        Every gate here is deliberate: unknown pack, disabled pack, unsupported
        role/country/language, and (when reference sets are provided) an
        unhealthy pack all refuse activation rather than degrade.
        """
        pack = self._packs.get(pack_id)
        if pack is None:
            raise ActivationError(f"unknown pack {pack_id!r}")
        m = pack.manifest
        if m.status is PackStatus.disabled:
            raise ActivationError(f"pack {pack_id!r} is disabled")
        if role not in m.supported_roles:
            raise ActivationError(
                f"role {role!r} is not supported by {pack_id!r} (roles: {list(m.supported_roles)})"
            )
        if country not in m.supported_countries:
            raise ActivationError(
                f"country {country!r} is not supported by {pack_id!r}"
            )
        if language not in m.supported_languages:
            raise ActivationError(
                f"language {language!r} is not supported by {pack_id!r}"
            )
        if known_tools is not None or known_eval_suites is not None or known_adapters is not None:
            report = self.health_check(
                pack_id,
                known_tools=known_tools or (),
                known_eval_suites=known_eval_suites or (),
                known_adapters=known_adapters or (),
            )
            if not report.healthy:
                errors = "; ".join(i.message for i in report.issues if i.severity == "error")
                raise ActivationError(f"pack {pack_id!r} failed health check: {errors}")
        return m

    # -------------------------------------------------------------- health
    def health_check(
        self,
        pack_id: str,
        *,
        known_tools: Iterable[str],
        known_eval_suites: Iterable[str],
        known_adapters: Iterable[str],
    ) -> HealthReport:
        """Verify every external reference in the manifest actually exists."""
        pack = self._packs.get(pack_id)
        if pack is None:
            return HealthReport(pack_id, [HealthIssue("error", f"unknown pack {pack_id!r}")])
        m = pack.manifest
        report = HealthReport(pack_id)
        tools = set(known_tools)
        suites = set(known_eval_suites)
        adapters = set(known_adapters)

        for tool in m.allowed_tools:
            if tool not in tools:
                report.issues.append(
                    HealthIssue("error", f"allowed tool {tool!r} is not registered")
                )
        for suite in m.required_eval_suites:
            if suite not in suites:
                report.issues.append(
                    HealthIssue("error", f"required eval suite {suite!r} does not exist")
                )
        for policy in m.required_policies:
            policy_file = pack.root / "policies" / f"{policy}.yaml"
            if not policy_file.exists():
                report.issues.append(
                    HealthIssue(
                        "error",
                        f"required policy {policy!r} has no policy file at "
                        f"policies/{policy}.yaml in the pack",
                    )
                )
        if m.default_adapter is not None and m.default_adapter not in adapters:
            report.issues.append(
                HealthIssue(
                    "warning",
                    f"default adapter {m.default_adapter!r} is not registered — "
                    "the base model will be used until it is",
                )
            )
        return report

    # ------------------------------------------------------------- docs
    def pack_doc(self, pack_id: str) -> str:
        """Generate a Markdown summary of a pack from its manifest (nothing invented)."""
        m = self.get(pack_id)
        if m is None:
            raise ActivationError(f"unknown pack {pack_id!r}")
        lines = [
            f"# {m.name} (`{m.id}`)",
            "",
            m.description,
            "",
            f"- version: {m.version}",
            f"- status: {m.status.value}",
            f"- risk level: {m.risk_level.value}",
            f"- roles: {', '.join(m.supported_roles)}",
            f"- countries: {', '.join(m.supported_countries)}",
            f"- languages: {', '.join(m.supported_languages)}",
            f"- default adapter: {m.default_adapter or '(base model)'}",
            f"- allowed model capabilities: {', '.join(m.allowed_model_capabilities)}",
        ]
        if m.allowed_tools:
            lines.append(f"- allowed tools: {', '.join(m.allowed_tools)}")
        if m.blocked_capabilities:
            lines.append(f"- **blocked capabilities**: {', '.join(m.blocked_capabilities)}")
        if m.required_policies:
            lines.append(f"- required policies: {', '.join(m.required_policies)}")
        if m.required_eval_suites:
            lines.append(f"- required eval suites: {', '.join(m.required_eval_suites)}")
        return "\n".join(lines) + "\n"
