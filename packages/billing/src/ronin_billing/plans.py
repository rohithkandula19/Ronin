"""Plans, a plan catalog, and a fail-closed entitlements checker.

A :class:`Plan` bundles a price (integer minor units / cents), a set of boolean
``features`` and a dict of numeric ``limits`` (e.g. ``max_seats``,
``monthly_request_quota``, ``storage_bytes``). Limits use the meter name as the
key so invoice computation can look up an included allowance directly.

The :class:`Entitlements` checker resolves a plan (by slug via a
:class:`PlanCatalog`, or a :class:`Plan` object directly) and answers two
questions — is a feature allowed, and is a value within a limit — failing
closed: an unknown plan or unknown limit key raises rather than defaulting to
"allow".
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import UnknownEntitlement, UnknownPlan

#: Sentinel limit value meaning "no ceiling" — an explicitly declared, known
#: value (not the same as an undeclared/unknown key, which fails closed).
UNLIMITED = -1


class Plan(BaseModel):
    """A billing plan: price + entitlements. Immutable value object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str
    display: str
    monthly_price_cents: int = Field(ge=0)
    limits: dict[str, int] = Field(default_factory=dict)
    features: frozenset[str] = Field(default_factory=frozenset)

    @field_validator("limits")
    @classmethod
    def _validate_limits(cls, value: dict[str, int]) -> dict[str, int]:
        for key, limit in value.items():
            if limit < 0 and limit != UNLIMITED:
                raise ValueError(f"limit {key!r} must be >= 0 or UNLIMITED ({UNLIMITED})")
        return value

    def included_for(self, meter: str) -> int | None:
        """Included allowance for a meter, ``UNLIMITED``, or ``None`` if undeclared."""
        return self.limits.get(meter)


class PlanCatalog:
    """In-memory registry of plans keyed by slug. Injectable persistence."""

    def __init__(self, plans: list[Plan] | None = None) -> None:
        self._plans: dict[str, Plan] = {}
        for plan in plans or []:
            self.add(plan)

    def add(self, plan: Plan) -> None:
        if plan.slug in self._plans:
            raise ValueError(f"duplicate plan slug: {plan.slug!r}")
        self._plans[plan.slug] = plan

    def get(self, slug: str) -> Plan:
        """Return the plan for ``slug`` or raise :class:`UnknownPlan` (fail-closed)."""
        try:
            return self._plans[slug]
        except KeyError:
            raise UnknownPlan(f"no such plan: {slug!r}") from None

    def slugs(self) -> list[str]:
        return sorted(self._plans)


class Entitlements:
    """Fail-closed entitlement checker over a :class:`PlanCatalog`."""

    def __init__(self, catalog: PlanCatalog | None = None) -> None:
        self._catalog = catalog or PlanCatalog()

    def _resolve(self, plan: str | Plan) -> Plan:
        if isinstance(plan, Plan):
            return plan
        return self._catalog.get(plan)

    def allows(self, plan: str | Plan, feature: str) -> bool:
        """True iff ``plan`` grants ``feature``. Unknown plan raises; missing feature is False."""
        return feature in self._resolve(plan).features

    def within_limit(self, plan: str | Plan, key: str, current_value: int) -> bool:
        """True iff ``current_value`` is within the plan's limit for ``key``.

        Fail-closed: an unknown plan raises :class:`UnknownPlan` and an
        undeclared limit key raises :class:`UnknownEntitlement` — we never
        assume an unstated limit is unlimited.
        """
        if current_value < 0:
            raise ValueError("current_value must be >= 0")
        resolved = self._resolve(plan)
        if key not in resolved.limits:
            raise UnknownEntitlement(f"plan {resolved.slug!r} declares no limit {key!r}")
        limit = resolved.limits[key]
        if limit == UNLIMITED:
            return True
        return current_value <= limit


__all__ = ["UNLIMITED", "Entitlements", "Plan", "PlanCatalog"]
