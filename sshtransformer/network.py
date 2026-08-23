"""Local network helpers for advertising a host address."""

from __future__ import annotations

import platform
import re
import socket
import subprocess
from ipaddress import ip_address

# Virtual / tunnel / container NICs — not useful for LAN peer pairing.
_SKIP_IFACE_PREFIXES = (
    "lo",
    "lo0",
    "utun",
    "tun",
    "tap",
    "awdl",
    "llw",
    "bridge",
    "docker",
    "br-",
    "veth",
    "virbr",
    "vmnet",
    "vnic",
    "gif",
    "stf",
    "ap",
    "ipsec",
    "wg",
    "cni",
    "flannel",
    "kube",
    "nerdctl",
)


def list_lan_ips() -> list[str]:
    """Return LAN IPv4 addresses, default-route NIC first (like Linux `hostname -I`)."""
    iface_ips = _iface_ipv4_map()
    usable: list[tuple[str, str]] = []  # (iface, ip)
    for iface, ip in iface_ips:
        if _skip_iface(iface):
            continue
        if _is_usable_lan_ip(ip):
            usable.append((iface, ip))

    if not usable:
        # Last resort: UDP trick, still filtered.
        fallback = _udp_outbound_ip()
        if fallback and _is_usable_lan_ip(fallback):
            return [fallback]
        return []

    preferred_iface = _default_route_iface()
    ordered: list[str] = []
    seen: set[str] = set()

    if preferred_iface:
        for iface, ip in usable:
            if iface == preferred_iface and ip not in seen:
                ordered.append(ip)
                seen.add(ip)

    for iface, ip in usable:
        if ip not in seen:
            ordered.append(ip)
            seen.add(ip)

    return ordered


def _skip_iface(name: str) -> bool:
    n = name.lower().rstrip(":")
    return any(n == p or n.startswith(p) for p in _SKIP_IFACE_PREFIXES)


def _is_usable_lan_ip(addr: str) -> bool:
    try:
        ip = ip_address(addr)
    except ValueError:
        return False
    if ip.version != 4:
        return False
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return False
    # CGNAT / some corp tunnels look "public" but sit on utun — already filtered by iface.
    return True


def _iface_ipv4_map() -> list[tuple[str, str]]:
    system = platform.system()
    if system == "Linux":
        return _linux_iface_ips()
    if system == "Darwin":
        return _darwin_iface_ips()
    return _darwin_iface_ips()  # ifconfig-style fallback


def _linux_iface_ips() -> list[tuple[str, str]]:
    """Prefer `hostname -I` order; attach iface names via `ip -o -4 addr` when possible."""
    by_ip: dict[str, str] = {}
    try:
        out = subprocess.check_output(
            ["ip", "-o", "-4", "addr", "show", "scope", "global"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            # 2: eth0    inet 10.0.0.5/24 ...
            parts = line.split()
            if len(parts) >= 4 and parts[2] == "inet":
                iface = parts[1]
                ip = parts[3].split("/")[0]
                by_ip[ip] = iface
    except (OSError, subprocess.CalledProcessError):
        pass

    ordered: list[tuple[str, str]] = []
    try:
        out = subprocess.check_output(
            ["hostname", "-I"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for ip in out.split():
            if _is_usable_lan_ip(ip):
                ordered.append((by_ip.get(ip, "unknown"), ip))
        if ordered:
            return ordered
    except (OSError, subprocess.CalledProcessError):
        pass

    return [(iface, ip) for ip, iface in by_ip.items()]


def _darwin_iface_ips() -> list[tuple[str, str]]:
    """Parse `ifconfig` — Mac equivalent of collecting host IPs."""
    try:
        out = subprocess.check_output(["ifconfig"], text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return []

    results: list[tuple[str, str]] = []
    iface = ""
    for line in out.splitlines():
        if line and not line[0].isspace():
            iface = line.split(":", 1)[0]
            continue
        match = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)\b", line)
        if match and iface:
            results.append((iface, match.group(1)))
    return results


def _default_route_iface() -> str | None:
    system = platform.system()
    try:
        if system == "Darwin":
            out = subprocess.check_output(
                ["route", "-n", "get", "default"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("interface:"):
                    return line.split(":", 1)[1].strip()
        elif system == "Linux":
            out = subprocess.check_output(
                ["ip", "-4", "route", "show", "default"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            # default via 10.0.0.1 dev eth0 ...
            match = re.search(r"\bdev\s+(\S+)", out)
            if match:
                return match.group(1)
    except (OSError, subprocess.CalledProcessError):
        return None
    return None


def _udp_outbound_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def local_identity() -> dict[str, str]:
    return {
        "hostname": socket.gethostname(),
        "os": (
            f"macOS {platform.mac_ver()[0]}"
            if platform.system() == "Darwin"
            else platform.platform()
        ),
        "system": platform.system(),
    }
