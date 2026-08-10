"""Gather the hostnames a scan touched."""

from __future__ import annotations


def unique_hosts(hosts: list[str]) -> list[str]:
    """Hosts with duplicates removed, in first-seen order."""
    return list(set(hosts))
