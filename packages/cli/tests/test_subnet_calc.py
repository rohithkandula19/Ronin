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


def test_ipv6_64_does_not_enumerate_2pow64_hosts() -> None:
    # Regression: `list(net.hosts())` on a /64 hangs for ~years on CI and
    # froze PR #1's pytest at 91%. Confirm the call returns instantly and
    # reports the correct usable_hosts (2**64 - 1, excluding subnet-router
    # anycast) without trying to iterate them.
    i = subnet_info("2001:db8::/64")
    assert i["usable_hosts"] == (1 << 64) - 1
    assert i["first_host"] == "2001:db8::1"
    assert i["last_host"] == "2001:db8::ffff:ffff:ffff:ffff"


def test_ipv4_31_rfc3021() -> None:
    # RFC 3021: a /31 has 2 usable hosts (point-to-point links).
    i = subnet_info("10.0.0.0/31")
    assert i["usable_hosts"] == 2
    assert i["first_host"] == "10.0.0.0" and i["last_host"] == "10.0.0.1"


def test_invalid() -> None:
    assert "error" in subnet_info("not-a-subnet")
