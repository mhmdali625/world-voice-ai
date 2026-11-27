"""
Which address should the phone use? (home Wi-Fi, university Wi-Fi,
phone hotspot, Tailscale)

The laptop's IPv4 addresses are listed with a hint, printed when the
server starts and served at GET /network for the dashboard, so nobody
has to run ipconfig. The phone takes the address in its settings
(Server address), no rebuild needed.
"""

import ipaddress
import socket

from fastapi import APIRouter

import config

router = APIRouter(tags=["Network"])
PORT = 8000


def _kind(address: str) -> str:
    ip = ipaddress.ip_address(address)
    if ip in ipaddress.ip_network("100.64.0.0/10"):
        return "Tailscale (works across different networks)"
    if address.startswith(("192.168.43.", "172.20.10.", "192.168.137.")) or \
            ip in ipaddress.ip_network("10.0.0.0/8") and address.startswith("10.") and address.split(".")[1] in ("42",):
        return "phone hotspot"
    if ip.is_private:
        return "local network (Wi-Fi / Ethernet)"
    return "public address"


def local_addresses() -> list[dict]:
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    primary = None
    try:                                    # the address of the real network (default route)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            primary = s.getsockname()[0]
            found.add(primary)
    except OSError:
        pass
    rows = []
    for address in sorted(found, key=lambda a: (a != primary, a)):
        if address.startswith(("127.", "169.254.")):
            continue
        kind = _kind(address)
        if address != primary and address.endswith(".1") and "Tailscale" not in kind:
            kind = "probably a virtual adapter (WSL / VM) - not for the phone"
        rows.append({"address": address, "url": f"http://{address}:{PORT}", "kind": kind,
                     "recommended": address == primary})
    return rows


def startup_banner() -> str:
    lines = ["[NETWORK] On the phone, Settings > Server address:"]
    for row in local_addresses():
        lines.append(f"[NETWORK]   {row['url']:28s} {row['kind']}{'  <- use this' if row['recommended'] else ''}")
    lines.append(f"[NETWORK] Access code: {'required (WV_ACCESS_TOKEN)' if config.ACCESS_TOKEN else 'off'}")
    return "\n".join(lines)


@router.get("/network")
async def network():
    return {"addresses": local_addresses(), "port": PORT, "access_code_required": bool(config.ACCESS_TOKEN)}
