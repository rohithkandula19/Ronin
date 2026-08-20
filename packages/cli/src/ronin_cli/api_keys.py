"""Project-bound Ronin API keys for local gateways, CI, and remote workers.

Raw keys are returned exactly once and never written to disk. The local store
contains a per-key salt plus SHA-256 digest, capability scopes, resource policy,
usage counters, and an audit log that deliberately excludes raw key material.
Provider credentials are not part of this system; callers bring those through
Ronin's existing configuration and vault paths.
"""
from __future__ import annotations

import datetime as _datetime
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCOPES = frozenset({
    "read", "mission:read", "mission:run", "proposal:read",
    "approval:request", "worker:dispatch",
})
_SCHEMA_VERSION = 1
_LOCK = threading.RLock()


def _now() -> _datetime.datetime:
    return _datetime.datetime.now(_datetime.timezone.utc)


def _stamp(value: _datetime.datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="seconds")


def _project_id(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()


def _digest(salt: str, raw: str) -> str:
    return hashlib.sha256(f"{salt}:{raw}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApiKeyPolicy:
    requests_per_minute: int = 60
    max_tokens: int = 100_000
    max_cost_usd: float = 25.0
    max_concurrency: int = 1

    def validate(self) -> None:
        if not 1 <= self.requests_per_minute <= 100_000:
            raise ValueError("requests_per_minute must be between 1 and 100000")
        if not 1 <= self.max_tokens <= 10_000_000:
            raise ValueError("max_tokens must be between 1 and 10000000")
        if not 0.0 <= self.max_cost_usd <= 1_000_000.0:
            raise ValueError("max_cost_usd must be between 0 and 1000000")
        if not 1 <= self.max_concurrency <= 32:
            raise ValueError("max_concurrency must be between 1 and 32")

    def as_dict(self) -> dict[str, Any]:
        return {
            "requests_per_minute": self.requests_per_minute,
            "max_tokens": self.max_tokens,
            "max_cost_usd": self.max_cost_usd,
            "max_concurrency": self.max_concurrency,
        }


@dataclass(frozen=True)
class ApiKeyRecord:
    id: str
    name: str
    prefix: str
    scopes: tuple[str, ...]
    project_id: str
    policy: ApiKeyPolicy
    created_at: str
    expires_at: str | None
    revoked_at: str | None
    last_used_at: str | None
    usage: dict[str, Any]

    @property
    def active(self) -> bool:
        return self.revoked_at is None and not _expired(self.expires_at)


@dataclass(frozen=True)
class IssuedApiKey:
    record: ApiKeyRecord
    token: str


@dataclass(frozen=True)
class ApiKeyLease:
    key_id: str
    lease_id: str


class ApiKeyError(ValueError):
    """Authentication, policy, or lifecycle rejection without leaking a key."""


class ApiKeyStore:
    """Atomic project-local store; access is denied outside the issuing project."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.path = self.root / ".ronin" / "api-keys.json"
        self.audit_path = self.root / ".ronin" / "api-key-audit.jsonl"

    def create(
        self,
        name: str,
        *,
        scopes: Iterable[str],
        policy: ApiKeyPolicy | None = None,
        expires_at: str | None = None,
    ) -> IssuedApiKey:
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 120:
            raise ValueError("key name must be between 1 and 120 characters")
        clean_scopes = _scopes(scopes)
        selected_policy = policy or ApiKeyPolicy()
        selected_policy.validate()
        expiry = _expiry(expires_at)
        raw = "rnk_live_" + secrets.token_urlsafe(32)
        key_id = f"key-{uuid.uuid4().hex[:16]}"
        salt = secrets.token_hex(16)
        record = {
            "id": key_id,
            "name": clean_name,
            "prefix": raw[:16],
            "scopes": list(clean_scopes),
            "project_id": _project_id(self.root),
            "policy": selected_policy.as_dict(),
            "created_at": _stamp(),
            "expires_at": expiry,
            "revoked_at": None,
            "last_used_at": None,
            "salt": salt,
            "digest": _digest(salt, raw),
            "usage": _empty_usage(),
        }
        with _LOCK:
            data = self._load()
            data["keys"].append(record)
            self._save(data)
            self._audit(key_id, "created", scopes=clean_scopes)
        return IssuedApiKey(_record(record), raw)

    def list(self, *, include_revoked: bool = False) -> tuple[ApiKeyRecord, ...]:
        rows = [_record(row) for row in self._load()["keys"]]
        if not include_revoked:
            rows = [row for row in rows if row.revoked_at is None]
        return tuple(sorted(rows, key=lambda row: (row.created_at, row.id), reverse=True))

    def revoke(self, key_id: str) -> ApiKeyRecord:
        with _LOCK:
            data, row = self._row_for(key_id)
            if row["revoked_at"] is None:
                row["revoked_at"] = _stamp()
                self._save(data)
                self._audit(key_id, "revoked")
            return _record(row)

    def rotate(self, key_id: str, *, expires_at: str | None = None) -> IssuedApiKey:
        old = self.revoke(key_id)
        return self.create(old.name, scopes=old.scopes, policy=old.policy, expires_at=expires_at or old.expires_at)

    def audit(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        """Return safe audit metadata, newest first, without any key material."""
        try:
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ()
        events: list[dict[str, Any]] = []
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and {"at", "key_id", "action"} <= set(event):
                events.append({key: event.get(key, "") for key in ("at", "key_id", "action", "scope", "scopes")})
        return tuple(reversed(events[-max(1, limit):]))

    def authorize(self, token: str | None, *, scope: str) -> ApiKeyRecord:
        if scope not in SCOPES:
            raise ValueError("unknown API key scope")
        raw = (token or "").strip()
        if not raw.startswith("rnk_live_"):
            raise ApiKeyError("invalid Ronin API key")
        with _LOCK:
            data = self._load()
            for row in data["keys"]:
                if not isinstance(row, dict) or row.get("project_id") != _project_id(self.root):
                    continue
                salt, digest = row.get("salt"), row.get("digest")
                if not isinstance(salt, str) or not isinstance(digest, str):
                    continue
                if hmac.compare_digest(_digest(salt, raw), digest):
                    return self._authorize_row(data, row, scope)
        raise ApiKeyError("invalid Ronin API key")

    def reserve(self, token: str | None, *, scope: str, now: _datetime.datetime | None = None) -> ApiKeyLease:
        timestamp = now or _now()
        with _LOCK:
            data, row = self._authenticated_row(token, scope)
            usage = _usage(row)
            minute = timestamp.strftime("%Y%m%d%H%M")
            if usage["minute"] != minute:
                usage["minute"] = minute
                usage["requests_in_minute"] = 0
            policy = _policy(row)
            if usage["requests_in_minute"] >= policy.requests_per_minute:
                self._audit(row["id"], "rate_limited", scope=scope)
                raise ApiKeyError("API key rate limit exceeded")
            if usage["active_requests"] >= policy.max_concurrency:
                self._audit(row["id"], "concurrency_limited", scope=scope)
                raise ApiKeyError("API key concurrency limit exceeded")
            lease_id = f"lease-{uuid.uuid4().hex}"
            usage["requests_in_minute"] += 1
            usage["active_requests"] += 1
            usage["leases"].append(lease_id)
            row["usage"] = usage
            row["last_used_at"] = _stamp(timestamp)
            self._save(data)
            self._audit(row["id"], "reserved", scope=scope)
            return ApiKeyLease(row["id"], lease_id)

    def settle(self, lease: ApiKeyLease, *, tokens: int = 0, cost_usd: float = 0.0) -> ApiKeyRecord:
        if tokens < 0 or cost_usd < 0:
            raise ValueError("usage must not be negative")
        with _LOCK:
            data, row = self._row_for(lease.key_id)
            usage = _usage(row)
            if lease.lease_id not in usage["leases"]:
                raise ApiKeyError("API key lease is invalid or already settled")
            policy = _policy(row)
            if usage["tokens"] + tokens > policy.max_tokens:
                _release(usage, lease.lease_id)
                row["usage"] = usage
                self._save(data)
                self._audit(row["id"], "token_budget_exceeded")
                raise ApiKeyError("API key token budget exceeded")
            if usage["cost_usd"] + cost_usd > policy.max_cost_usd:
                _release(usage, lease.lease_id)
                row["usage"] = usage
                self._save(data)
                self._audit(row["id"], "cost_budget_exceeded")
                raise ApiKeyError("API key cost budget exceeded")
            usage["tokens"] += int(tokens)
            usage["cost_usd"] += float(cost_usd)
            _release(usage, lease.lease_id)
            row["usage"] = usage
            row["last_used_at"] = _stamp()
            self._save(data)
            self._audit(row["id"], "settled")
            return _record(row)

    def _authenticated_row(self, token: str | None, scope: str) -> tuple[dict[str, Any], dict[str, Any]]:
        raw = (token or "").strip()
        if not raw.startswith("rnk_live_"):
            raise ApiKeyError("invalid Ronin API key")
        data = self._load()
        for row in data["keys"]:
            if not isinstance(row, dict) or row.get("project_id") != _project_id(self.root):
                continue
            salt, digest = row.get("salt"), row.get("digest")
            if isinstance(salt, str) and isinstance(digest, str) and hmac.compare_digest(_digest(salt, raw), digest):
                self._authorize_row(data, row, scope)
                return data, row
        raise ApiKeyError("invalid Ronin API key")

    def _authorize_row(self, data: dict[str, Any], row: dict[str, Any], scope: str) -> ApiKeyRecord:
        if row.get("revoked_at") or _expired(row.get("expires_at")):
            self._audit(str(row.get("id", "unknown")), "rejected", scope=scope)
            raise ApiKeyError("Ronin API key is inactive")
        scopes = row.get("scopes", [])
        if scope not in scopes:
            self._audit(str(row.get("id", "unknown")), "scope_denied", scope=scope)
            raise ApiKeyError("Ronin API key lacks the required scope")
        row["last_used_at"] = _stamp()
        self._save(data)
        self._audit(str(row["id"]), "authorized", scope=scope)
        return _record(row)

    def _row_for(self, key_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        data = self._load()
        for row in data["keys"]:
            if isinstance(row, dict) and row.get("id") == key_id:
                return data, row
        raise ValueError("Ronin API key was not found")

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"schema_version": _SCHEMA_VERSION, "keys": []}
        if data.get("schema_version") != _SCHEMA_VERSION or not isinstance(data.get("keys"), list):
            return {"schema_version": _SCHEMA_VERSION, "keys": []}
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".api-keys-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(name, self.path)
        except OSError:
            try:
                os.unlink(name)
            except OSError:
                pass
            raise

    def _audit(self, key_id: str, action: str, *, scope: str = "", scopes: Iterable[str] = ()) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "at": _stamp(), "key_id": key_id, "action": action,
            "scope": scope, "scopes": list(scopes),
        }
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _scopes(scopes: Iterable[str]) -> tuple[str, ...]:
    selected = tuple(sorted({str(scope).strip() for scope in scopes if str(scope).strip()}))
    if not selected:
        raise ValueError("at least one API key scope is required")
    unknown = set(selected) - SCOPES
    if unknown:
        raise ValueError("unknown API key scope(s): " + ", ".join(sorted(unknown)))
    return selected


def _expiry(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = _datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("expires_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("expires_at must include a timezone")
    if parsed <= _now():
        raise ValueError("expires_at must be in the future")
    return _stamp(parsed.astimezone(_datetime.timezone.utc))


def _expired(value: object) -> bool:
    if not value:
        return False
    try:
        return _datetime.datetime.fromisoformat(str(value)).astimezone(_datetime.timezone.utc) <= _now()
    except ValueError:
        return True


def _empty_usage() -> dict[str, Any]:
    return {"minute": "", "requests_in_minute": 0, "active_requests": 0, "leases": [], "tokens": 0, "cost_usd": 0.0}


def _usage(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("usage") if isinstance(row.get("usage"), dict) else {}
    usage = _empty_usage()
    usage.update({key: raw.get(key, usage[key]) for key in usage})
    usage["requests_in_minute"] = max(0, int(usage["requests_in_minute"] or 0))
    usage["active_requests"] = max(0, int(usage["active_requests"] or 0))
    usage["tokens"] = max(0, int(usage["tokens"] or 0))
    usage["cost_usd"] = max(0.0, float(usage["cost_usd"] or 0.0))
    usage["leases"] = [item for item in usage["leases"] if isinstance(item, str)] if isinstance(usage["leases"], list) else []
    return usage


def _release(usage: dict[str, Any], lease_id: str) -> None:
    usage["leases"] = [item for item in usage["leases"] if item != lease_id]
    usage["active_requests"] = max(0, usage["active_requests"] - 1)


def _policy(row: dict[str, Any]) -> ApiKeyPolicy:
    raw = row.get("policy") if isinstance(row.get("policy"), dict) else {}
    try:
        policy = ApiKeyPolicy(
            requests_per_minute=int(raw.get("requests_per_minute", 60)),
            max_tokens=int(raw.get("max_tokens", 100_000)),
            max_cost_usd=float(raw.get("max_cost_usd", 25.0)),
            max_concurrency=int(raw.get("max_concurrency", 1)),
        )
        policy.validate()
        return policy
    except (TypeError, ValueError) as exc:
        raise ApiKeyError("stored API key policy is invalid") from exc


def _record(row: dict[str, Any]) -> ApiKeyRecord:
    return ApiKeyRecord(
        id=str(row["id"]), name=str(row["name"]), prefix=str(row["prefix"]),
        scopes=tuple(row.get("scopes", [])), project_id=str(row["project_id"]),
        policy=_policy(row), created_at=str(row["created_at"]),
        expires_at=row.get("expires_at"), revoked_at=row.get("revoked_at"),
        last_used_at=row.get("last_used_at"), usage=_usage(row),
    )
