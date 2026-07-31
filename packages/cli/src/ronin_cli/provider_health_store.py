"""Durable, observation-only provider health and cooling-down policy.

This store never performs hidden pings or declares a provider reachable. It
records outcomes already observed during agent work and temporarily penalises a
provider after failures so routing has a durable operational signal.
"""
from __future__ import annotations

import datetime as _datetime
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
_PATH = ".ronin/provider-health.json"
_BASE_COOLDOWN_SECONDS = 30
_MAX_COOLDOWN_SECONDS = 3600
_LOCK = threading.RLock()


def _now() -> float:
    return _datetime.datetime.now(_datetime.timezone.utc).timestamp()


class ProviderHealthStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.path = self.root / _PATH

    def observe(self, observations: Mapping[str, Mapping[str, Any]], *, now: float | None = None) -> None:
        """Merge one orchestration's actual provider outcomes into local health."""
        timestamp = _now() if now is None else float(now)
        with _LOCK:
            data = self._load()
            providers = data["providers"]
            for label, raw in observations.items():
                if not isinstance(label, str) or not isinstance(raw, Mapping):
                    continue
                attempts = _count(raw.get("attempts"))
                failures = min(_count(raw.get("failures")), attempts)
                if not attempts:
                    continue
                entry = providers.setdefault(label, _empty())
                entry["attempts"] += attempts
                entry["failures"] += failures
                entry["successes"] += attempts - failures
                roles = {str(role) for role in entry.get("roles", [])}
                roles.update(str(role) for role in raw.get("roles", []) if isinstance(role, str))
                entry["roles"] = sorted(roles)
                if failures:
                    entry["consecutive_failures"] += failures
                    entry["last_failure"] = timestamp
                    if raw.get("last_error"):
                        entry["last_error"] = str(raw["last_error"])[:500]
                    cooldown = min(
                        _MAX_COOLDOWN_SECONDS,
                        _BASE_COOLDOWN_SECONDS * (2 ** min(entry["consecutive_failures"] - 1, 6)),
                    )
                    entry["cooldown_until"] = timestamp + cooldown
                # Aggregated subtask outcomes do not preserve the exact order of
                # a mixed success/failure wave. Keep its cooldown conservatively;
                # only a later all-success observation proves recovery.
                if attempts and not failures:
                    entry["consecutive_failures"] = 0
                    entry["last_success"] = timestamp
                    entry["cooldown_until"] = 0.0
            data["updated"] = timestamp
            self._save(data)

    def view(self, *, now: float | None = None) -> dict[str, dict[str, Any]]:
        timestamp = _now() if now is None else float(now)
        data = self._load()
        result: dict[str, dict[str, Any]] = {}
        for label, raw in data["providers"].items():
            if not isinstance(label, str) or not isinstance(raw, dict):
                continue
            cooldown_until = _number(raw.get("cooldown_until"))
            cooling = cooldown_until > timestamp
            failures = _count(raw.get("failures"))
            successes = _count(raw.get("successes"))
            consecutive = _count(raw.get("consecutive_failures"))
            status = "cooling-down" if cooling else ("healthy" if successes and not consecutive else "degraded")
            result[label] = {
                "attempts": _count(raw.get("attempts")),
                "failures": failures,
                "successes": successes,
                "roles": list(raw.get("roles", [])),
                "last_error": raw.get("last_error"),
                "last_success": _number(raw.get("last_success")) or None,
                "last_failure": _number(raw.get("last_failure")) or None,
                "cooldown_until": cooldown_until or None,
                "status": status,
            }
        return result

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"schema_version": SCHEMA_VERSION, "updated": 0.0, "providers": {}}
        if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION or not isinstance(raw.get("providers"), dict):
            return {"schema_version": SCHEMA_VERSION, "updated": 0.0, "providers": {}}
        return raw

    def _save(self, data: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".provider-health-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, sort_keys=True, indent=2)
                handle.write("\n")
            os.replace(name, self.path)
        except OSError:
            try:
                os.unlink(name)
            except OSError:
                pass
            raise


def _empty() -> dict[str, Any]:
    return {
        "attempts": 0, "failures": 0, "successes": 0, "roles": [],
        "consecutive_failures": 0, "last_error": None, "last_success": 0.0,
        "last_failure": 0.0, "cooldown_until": 0.0,
    }


def _count(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _number(value: object) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0
