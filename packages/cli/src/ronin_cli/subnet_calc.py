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
    hosts = list(net.hosts())
    info = {
        "cidr": str(net),
        "version": net.version,
        "network": str(net.network_address),
        "netmask": str(net.netmask),
        "prefix": net.prefixlen,
        "num_addresses": net.num_addresses,
        "first_host": str(hosts[0]) if hosts else None,
        "last_host": str(hosts[-1]) if hosts else None,
        "usable_hosts": len(hosts),
        "is_private": net.is_private,
    }
    if net.version == 4:
        info["broadcast"] = str(net.broadcast_address)
        info["wildcard"] = str(net.hostmask)
    return info
