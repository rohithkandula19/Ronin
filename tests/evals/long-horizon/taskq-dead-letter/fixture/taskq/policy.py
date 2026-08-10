"""Retry policy, built from a parsed config."""

from __future__ import annotations

from dataclasses import dataclass

from taskq.errors import ConfigError
from taskq.model import Job


@dataclass(frozen=True, slots=True)
class Policy:
    max_attempts: int = 3

    @classmethod
    def from_config(cls, config: dict[str, str]) -> Policy:
        raw = config.get("max_attempts", "3")
        try:
            max_attempts = int(raw)
        except ValueError as exc:
            raise ConfigError("max_attempts must be an integer: %r" % raw) from exc
        if max_attempts < 1:
            raise ConfigError("max_attempts must be at least 1")
        return cls(max_attempts=max_attempts)

    def should_retry(self, job: Job) -> bool:
        """True while the job has attempts left."""
        return job.attempts < self.max_attempts
