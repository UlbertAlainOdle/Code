"""SSRF protection for the web research pipeline (plan section 12.1 & 12.2).

Responsibilities:
- URL structure validation: scheme, userinfo, port, host presence, localhost.
- DNS resolution of all A/AAAA records for the host.
- IP category checks: reject loopback / private / link-local / multicast /
  reserved / unspecified addresses.

The DNS resolver is injectable so tests can simulate internal addresses
without touching the real network. No real internet access happens here.
"""

import ipaddress
import socket
from urllib.parse import urlsplit


# Schemes considered safe to fetch.
ALLOWED_SCHEMES = ("http", "https")
# Ports that may appear in the URL. Unspecified is always allowed.
ALLOWED_PORTS = (80, 443)


class UrlValidationError(Exception):
    """Raised when a URL is not safe to fetch."""


def _is_loopback_text(host: str) -> bool:
    """True for ``localhost`` and its subdomains (e.g. ``foo.localhost``)."""
    host_lower = host.lower().rstrip(".")
    if host_lower == "localhost":
        return True
    if host_lower.endswith(".localhost"):
        return True
    return False


def parse_and_validate_url(url: str) -> dict:
    """Validate the URL structure (no DNS yet).

    Returns a dict with ``scheme``, ``host``, ``port``, ``path`` etc.
    Raises :class:`UrlValidationError` on any structural problem.
    """
    if not isinstance(url, str) or not url.strip():
        raise UrlValidationError("empty_url")

    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UrlValidationError("invalid_scheme")

    host = parts.hostname
    if not host:
        raise UrlValidationError("missing_host")

    # Reject userinfo (user:pass@) explicitly.
    # urlsplit keeps userinfo in ``parts.netloc``; hostname strips it.
    netloc = parts.netloc or ""
    if "@" in netloc:
        raise UrlValidationError("userinfo_present")

    if _is_loopback_text(host):
        raise UrlValidationError("loopback_host")

    # Port handling: explicit ports must be in the allowlist. Unspecified ok.
    port = parts.port  # None when not present; raises ValueError if invalid.
    if port is not None and port not in ALLOWED_PORTS:
        raise UrlValidationError("invalid_port")

    return {
        "scheme": scheme,
        "host": host,
        "port": port,
        "path": parts.path or "/",
        "query": parts.query,
        "netloc": parts.netloc,
    }


def _ip_is_unsafe(ip: ipaddress._BaseAddress) -> bool:
    """Return True if the address falls into a forbidden category."""
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def check_resolved_addresses(addresses) -> str:
    """Given an iterable of IP strings, return an error code or ``""`` if safe.

    If ANY resolved address is unsafe, the whole host is rejected. This is the
    core SSRF defense against DNS-rebinding / mixed public+private records.
    """
    unsafe = []
    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            # Unparseable address is treated as unsafe rather than skipped.
            return "unparseable_address"
        if _ip_is_unsafe(ip):
            unsafe.append(str(ip))
    if unsafe:
        return "unsafe_address"
    return ""


def resolve_host(host: str, resolver=None):
    """Resolve a host to a list of IP strings using the injectable resolver.

    ``resolver`` defaults to :func:`socket.getaddrinfo`. It must return the
    same 5-tuple shape. Returns a list of unique IP address strings.
    """
    getaddrinfo = resolver or socket.getaddrinfo
    try:
        infos = getaddrinfo(host, None)
    except socket.gaierror as exc:
        # NXDOMAIN / resolution failure.
        raise UrlValidationError("dns_resolution_failed") from exc

    ips = []
    seen = set()
    for info in infos:
        # info: (family, type, proto, canonname, sockaddr)
        sockaddr = info[4]
        ip = sockaddr[0]
        if ip not in seen:
            seen.add(ip)
            ips.append(ip)
    if not ips:
        raise UrlValidationError("dns_no_addresses")
    return ips


def is_url_safe(url: str, resolver=None) -> tuple:
    """Full safety check: URL structure + DNS + IP categories.

    Returns ``(True, "", info)`` when safe, ``(False, code, info)`` otherwise.
    ``info`` is the parsed URL dict (possibly partial on failure). ``code`` is
    a short stable error string, never a verbose message that could leak data.
    """
    try:
        info = parse_and_validate_url(url)
    except UrlValidationError as exc:
        return False, exc.args[0] if exc.args else "invalid_url", {}

    host = info["host"]
    try:
        # Skip DNS for IP literals: validate them directly. This also catches
        # obfuscated forms that urlsplit still surfaces as hostnames.
        try:
            ip_obj = ipaddress.ip_address(host)
            code = check_resolved_addresses([str(ip_obj)])
            if code:
                return False, code, info
            return True, "", info
        except ValueError:
            pass

        ips = resolve_host(host, resolver=resolver)
        code = check_resolved_addresses(ips)
        if code:
            return False, code, info
        info["resolved_ips"] = ips
        return True, "", info
    except UrlValidationError as exc:
        return False, exc.args[0] if exc.args else "dns_error", info


def is_loopback_text(host: str) -> bool:
    """Public alias for the localhost text check (used by tests)."""
    return _is_loopback_text(host)
