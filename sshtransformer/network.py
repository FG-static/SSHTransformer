"""Local network helpers for advertising a host address."""

from __future__ import annotations

import socket
from ipaddress import ip_address


def list_lan_ips() -> list[str]:
    """Return likely LAN IPv4 addresses, excluding loopback and link-local."""
    found: set[str] = set()

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            addr = info[4][0]
            if _is_usable_lan_ip(addr):
                found.add(addr)
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            addr = sock.getsockname()[0]
            if _is_usable_lan_ip(addr):
                found.add(addr)
    except OSError:
        pass

    ordered = sorted(found, key=_prefer_private)
    return ordered


def _is_usable_lan_ip(addr: str) -> bool:
    try:
        ip = ip_address(addr)
    except ValueError:
        return False
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return False
    return ip.version == 4


def _prefer_private(addr: str) -> tuple[int, str]:
    ip = ip_address(addr)
    # Prefer RFC1918 ranges commonly used on home/office LANs.
    if ip.is_private:
        return (0, addr)
    return (1, addr)


def local_identity() -> dict[str, str]:
    import platform

    return {
        "hostname": socket.gethostname(),
        "os": f"macOS {platform.mac_ver()[0]}" if platform.system() == "Darwin" else platform.platform(),
        "system": platform.system(),
    }
