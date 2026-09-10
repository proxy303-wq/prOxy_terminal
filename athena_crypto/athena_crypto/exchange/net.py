"""Network helpers: force IPv4 egress.

Delta's API is dual-stack. On hosts that prefer IPv6 (dual-stack broadband/VPS) the
request goes out over IPv6, so the exchange sees an IPv6 client address. Delta's API
key whitelist is typically IPv4, which makes every private call fail with
ip_not_whitelisted_for_api_key even though the IPv4 address IS whitelisted.

This module forces AF_INET resolution process-wide when enabled, so REST and
WebSocket traffic leaves over the whitelisted IPv4.
"""
import logging
import os
import socket

log = logging.getLogger("athena.net")

_ORIGINAL_GETADDRINFO = socket.getaddrinfo
_PATCHED = False


def ipv4_enabled(default=True):
    val = os.environ.get("DELTA_FORCE_IPV4")
    if val is None:
        return default
    return val.strip().lower() not in ("0", "false", "no", "off")


def force_ipv4(enable=True):
    """Filter DNS results to IPv4 for the whole process (idempotent)."""
    global _PATCHED
    if not enable:
        if _PATCHED:
            socket.getaddrinfo = _ORIGINAL_GETADDRINFO
            _PATCHED = False
        return False
    if _PATCHED:
        return True

    def getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
        results = _ORIGINAL_GETADDRINFO(host, port, family, type, proto, flags)
        v4 = [r for r in results if r[0] == socket.AF_INET]
        if v4:
            return v4
        # no A record: fall back to whatever exists rather than breaking the call
        return results

    socket.getaddrinfo = getaddrinfo_ipv4
    _PATCHED = True
    log.info("IPv4 egress forced (whitelisted address used for exchange calls)")
    return True


def egress_info():
    """Best-effort report of the public addresses this host presents."""
    import urllib.request
    out = {}
    for label, url in (("ipv4", "https://api.ipify.org"), ("ipv6", "https://api6.ipify.org")):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "athena-net"})
            with urllib.request.urlopen(req, timeout=10) as r:
                out[label] = r.read().decode().strip()
        except Exception:
            out[label] = None
    return out
