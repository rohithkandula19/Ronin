"""Fail-closed environment validation for production/staging.

`check_environment` returns a list of blocking issues; an empty list means the
environment is safe to start. It refuses the classic foot-guns: default
encryption keys, debug/demo mode in production, unsafe CORS, a local SQLite DB
in production, and unlimited model spend.
"""

from __future__ import annotations

from dataclasses import dataclass

_DEV_KEY_MARKERS = ("dev", "changeme", "placeholder", "default", "insecure", "test")


@dataclass(frozen=True)
class EnvIssue:
    severity: str  # "error" | "warning"
    message: str


def check_environment(env: str, config: dict) -> list[EnvIssue]:
    """Validate a config mapping for the named environment. Fail closed on errors."""
    issues: list[EnvIssue] = []
    is_prod = env in ("production", "staging")

    def err(msg: str) -> None:
        issues.append(EnvIssue("error", msg))

    def warn(msg: str) -> None:
        issues.append(EnvIssue("warning", msg))

    # secrets present
    for key in ("secret_key", "encryption_key"):
        val = str(config.get(key, ""))
        if is_prod and not val:
            err(f"{key} is required in {env}")
        elif is_prod and any(m in val.lower() for m in _DEV_KEY_MARKERS):
            err(f"{key} looks like a development/default value in {env}")

    if is_prod:
        if config.get("debug"):
            err("debug mode must be off in production/staging")
        if config.get("demo_mode"):
            err("demo mode must be off in production/staging")
        cors = config.get("allowed_origins", [])
        if "*" in cors:
            err("CORS allowed_origins must not be '*' in production/staging")
        db = str(config.get("database_url", ""))
        if db.startswith("sqlite") or db.endswith(".db") or not db:
            err("production/staging must use a non-local (PostgreSQL) database_url")
        # cost safety: unlimited spend is never allowed in prod
        limits = config.get("cost_limits", {})
        if not limits or limits.get("platform_monthly_cost") in (None, 0):
            err("production/staging must configure a platform monthly cost ceiling")
        if not config.get("provider_keys") and not config.get("local_only"):
            warn("no provider keys and not local_only — inference will be unavailable")

    # never load prod credentials in local/test
    if env in ("local", "test") and config.get("database_url", "").startswith("postgres") \
            and config.get("allow_remote_db") is not True:
        err(f"{env} must not point at a remote database unless allow_remote_db is set")

    return issues
