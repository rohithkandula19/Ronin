"""Print the allow-list."""

from __future__ import annotations

from ports.parse import parse_ports

ALLOW = "80, 443, 8080,"


def main() -> None:
    print(",".join(str(port) for port in parse_ports(ALLOW)))
