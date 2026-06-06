"""Tests for the subnet/CIDR calculator."""
from __future__ import annotations

from ronin_cli.subnet_calc import subnet_info


def test_ipv4_24() -> None:
    i = subnet_info("192.168.1.0/24")
    assert i["network"] == "192.168.1.0"
    assert i["broadcast"] == "192.168.1.255"
    assert i["netmask"] == "255.255.255.0"
    assert i["usable_hosts"] == 254
    assert i["first_host"] == "192.168.1.1" and i["last_host"] == "192.168.1.254"
    assert i["is_private"] is True


def test_host_bits_ignored_strict_false() -> None:
    # a host address with a prefix still resolves to its network
    assert subnet_info("10.0.5.37/16")["network"] == "10.0.0.0"


def test_ipv6() -> None:
    i = subnet_info("2001:db8::/64")
    assert i["version"] == 6 and i["prefix"] == 64


def test_invalid() -> None:
    assert "error" in subnet_info("not-a-subnet")
