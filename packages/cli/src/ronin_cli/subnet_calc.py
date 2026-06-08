"""CIDR / subnet calculator — `ronin subnet`.

`ronin subnet 192.168.1.0/24` reports the network & broadcast addresses, netmask,
usable host range and address count. Uses stdlib ``ipaddress`` (IPv4 + IPv6),
offline. The computation is pure and unit-tested.
"""
from __future__ import annotations

import ipaddress


def subnet_info(cidr: str) -> dict:
    """Subnet facts for a CIDR (or bare IP). Pure (stdlib only)."""
    try:
        net = ipaddress.ip_network(cidr.strip(), strict=False)
    except ValueError as e:
        return {"error": str(e)}

    # DON'T materialize net.hosts() — an IPv6 /64 has 2**64 hosts, which made
    # `list(net.hosts())` hang for years and froze pytest on CI. The values
    # below are computed arithmetically from the network's int range.
    n_addr = net.num_addresses
    if net.version == 4:
        # RFC 3021: /31 has 2 usable hosts; /32 has 1. Otherwise exclude the
        # network and broadcast addresses.
        if net.prefixlen >= 31:
            usable = n_addr
            first = net.network_address
            last = net.broadcast_address
        else:
            usable = n_addr - 2
            first = net.network_address + 1
            last = net.broadcast_address - 1
    else:  # IPv6 — no broadcast; per RFC 4291, Subnet-Router anycast is the
        # network address. /127 and /128 are special-cased per RFC 6164.
        if net.prefixlen >= 127:
            usable = n_addr
            first = net.network_address
            last = net.network_address + (n_addr - 1)
        else:
            usable = n_addr - 1
            first = net.network_address + 1
            last = net.network_address + (n_addr - 1)

    info = {
        "cidr": str(net),
        "version": net.version,
        "network": str(net.network_address),
        "netmask": str(net.netmask),
        "prefix": net.prefixlen,
        "num_addresses": n_addr,
        "first_host": str(first) if usable > 0 else None,
        "last_host": str(last) if usable > 0 else None,
        "usable_hosts": usable,
        "is_private": net.is_private,
    }
    if net.version == 4:
        info["broadcast"] = str(net.broadcast_address)
        info["wildcard"] = str(net.hostmask)
    return info
