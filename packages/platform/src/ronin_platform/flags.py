"""Feature flags: global/env/user/org/plan/country + percentage rollout, audited."""

from __future__ import annotations

import hashlib
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class FlagScope(str, Enum):
    global_ = "global"
    environment = "environment"
    user = "user"
    org = "org"
    plan = "plan"
    country = "country"


class Flag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    enabled: bool = False           # global default
    environments: tuple[str, ...] = ()   # if set, only these envs are on
    users: tuple[str, ...] = ()          # explicit allow
    orgs: tuple[str, ...] = ()
    plans: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    percentage: int = 0             # 0-100 deterministic rollout by user id
    security_sensitive: bool = False  # flags that must NEVER gate safety are barred


class FlagStore:
    def __init__(self) -> None:
        self._flags: dict[str, Flag] = {}
        self.audit: list[dict] = []

    def set(self, flag: Flag, *, actor: str = "", at: str = "") -> None:
        if flag.security_sensitive:
            raise ValueError(
                f"flag {flag.key!r} marked security_sensitive: safety decisions must "
                "not be gated by feature flags"
            )
        self._flags[flag.key] = flag
        self.audit.append({"key": flag.key, "actor": actor, "at": at,
                           "enabled": flag.enabled, "percentage": flag.percentage})

    def is_enabled(
        self, key: str, *, environment: str = "", user_id: str = "",
        org_id: str = "", plan: str = "", country: str = "",
    ) -> bool:
        flag = self._flags.get(key)
        if flag is None:
            return False  # unknown flag = off (fail closed)
        if flag.environments and environment not in flag.environments:
            return False
        if flag.users and user_id in flag.users:
            return True
        if flag.orgs and org_id in flag.orgs:
            return True
        if flag.plans and plan in flag.plans:
            return True
        if flag.countries and country in flag.countries:
            return True
        if flag.percentage > 0 and user_id:
            bucket = int(hashlib.sha256(f"{key}:{user_id}".encode()).hexdigest(), 16) % 100
            if bucket < flag.percentage:
                return True
        # explicit-target flags (users/orgs/plans/countries set) require a match;
        # a pure on/off flag falls back to its global `enabled`.
        has_targets = bool(flag.users or flag.orgs or flag.plans or flag.countries or flag.percentage)
        return flag.enabled and not has_targets
