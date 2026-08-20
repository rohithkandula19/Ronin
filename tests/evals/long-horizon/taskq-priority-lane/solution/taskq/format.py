"""Human-readable status lines."""

from __future__ import annotations

from taskq.model import Job


def status_line(job: Job) -> str:
    return "%s %s %s attempts=%d prio=%d" % (
        job.id,
        job.name,
        job.state,
        job.attempts,
        job.priority,
    )


def status_table(jobs: list[Job]) -> list[str]:
    return [status_line(job) for job in jobs]
