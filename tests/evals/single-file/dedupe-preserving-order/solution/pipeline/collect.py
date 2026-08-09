"""Gather the hostnames a scan touched."""

from __future__ import annotations


def unique_hosts(hosts: list[str]) -> list[str]:
    """Hosts with duplicates removed, in first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for host in hosts:
        if host not in seen:
            seen.add(host)
            out.append(host)
    return out
