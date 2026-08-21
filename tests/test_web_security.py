"""Tests for the web research security module (plan section 21, items 10-11).

No real network: DNS is faked with injectable resolvers.
"""

import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from saya_app.services.web_research import security
from saya_app.services.web_research.security import (
    UrlValidationError,
    check_resolved_addresses,
    is_loopback_text,
    is_url_safe,
    parse_and_validate_url,
)


def fake_resolver_factory(ip_map):
    """Build a getaddrinfo-style resolver keyed by hostname.

    ``ip_map`` maps host -> list of IP strings. Unknown hosts raise gaierror.
    """
    def resolver(host, *args, **kwargs):
        ips = ip_map.get(host)
        if ips is None:
            raise socket.gaierror("fake: unknown host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]
    return resolver


class UrlStructureTests(unittest.TestCase):
    def test_https_public_url_is_safe(self):
        safe, code, info = is_url_safe(
            "https://example.com/path?q=1",
            resolver=fake_resolver_factory({"example.com": ["93.184.216.34"]}),
        )
        self.assertTrue(safe, code)
        self.assertEqual(info["host"], "example.com")

    def test_non_http_scheme_rejected(self):
        for url in ("ftp://example.com", "file:///etc/passwd", "javascript:alert(1)"):
            safe, code, _ = is_url_safe(url)
            self.assertFalse(safe, url)
            self.assertEqual(code, "invalid_scheme")

    def test_userinfo_rejected(self):
        safe, code, _ = is_url_safe("https://user:pass@example.com")
        self.assertFalse(safe)
        self.assertEqual(code, "userinfo_present")

    def test_non_standard_port_rejected(self):
        safe, code, _ = is_url_safe("https://example.com:8080/")
        self.assertFalse(safe)
        self.assertEqual(code, "invalid_port")

    def test_allowed_ports(self):
        for url, host in [
            ("http://example.com/", "example.com"),
            ("http://example.com:80/", "example.com"),
            ("https://example.com:443/", "example.com"),
        ]:
            safe, code, _ = is_url_safe(
                url, resolver=fake_resolver_factory({host: ["93.184.216.34"]})
            )
            self.assertTrue(safe, (url, code))

    def test_localhost_and_subdomains_rejected(self):
        for host in ("localhost", "foo.localhost", "LOCALHOST", "a.b.localhost"):
            safe, code, _ = is_url_safe(f"http://{host}/")
            self.assertFalse(safe, host)
            self.assertEqual(code, "loopback_host", host)

    def test_missing_host_rejected(self):
        safe, code, _ = is_url_safe("https:///path")
        self.assertFalse(safe)
        self.assertEqual(code, "missing_host")

    def test_empty_url_rejected(self):
        safe, code, _ = is_url_safe("")
        self.assertFalse(safe)
        self.assertEqual(code, "empty_url")


class IpAddressTests(unittest.TestCase):
    def test_loopback_ipv4_blocked(self):
        self.assertEqual(check_resolved_addresses(["127.0.0.1"]), "unsafe_address")

    def test_loopback_ipv6_blocked(self):
        self.assertEqual(check_resolved_addresses(["::1"]), "unsafe_address")

    def test_private_ranges_blocked(self):
        for ip in ("10.0.0.1", "172.16.0.1", "172.31.255.254", "192.168.1.1"):
            self.assertEqual(check_resolved_addresses([ip]), "unsafe_address", ip)

    def test_cloud_metadata_blocked(self):
        # 169.254.169.254 is link-local -> must be blocked.
        self.assertEqual(check_resolved_addresses(["169.254.169.254"]), "unsafe_address")

    def test_ipv6_private_blocked(self):
        for ip in ("fc00::1", "fd00::1", "fe80::1"):
            self.assertEqual(check_resolved_addresses([ip]), "unsafe_address", ip)

    def test_multicast_and_unspecified_blocked(self):
        self.assertEqual(check_resolved_addresses(["224.0.0.1"]), "unsafe_address")
        self.assertEqual(check_resolved_addresses(["0.0.0.0"]), "unsafe_address")

    def test_public_addresses_safe(self):
        self.assertEqual(check_resolved_addresses(["93.184.216.34"]), "")
        self.assertEqual(check_resolved_addresses(["8.8.8.8"]), "")

    def test_unparseable_address_rejected(self):
        self.assertEqual(check_resolved_addresses(["not-an-ip"]), "unparseable_address")


class DnsResolutionTests(unittest.TestCase):
    def test_dns_to_internal_blocked(self):
        # Public-looking host resolves to a private IP (DNS rebinding).
        resolver = fake_resolver_factory({"rebind.example.com": ["192.168.0.5"]})
        safe, code, _ = is_url_safe("https://rebind.example.com/", resolver=resolver)
        self.assertFalse(safe)
        self.assertEqual(code, "unsafe_address")

    def test_any_internal_address_blocks_host(self):
        # Mixed public + private: must reject because ANY is unsafe.
        resolver = fake_resolver_factory({"mix.example.com": ["93.184.216.34", "10.1.2.3"]})
        safe, code, _ = is_url_safe("https://mix.example.com/", resolver=resolver)
        self.assertFalse(safe)
        self.assertEqual(code, "unsafe_address")

    def test_dns_failure_blocked(self):
        resolver = fake_resolver_factory({})  # unknown host
        safe, code, _ = is_url_safe("https://nope.invalid/", resolver=resolver)
        self.assertFalse(safe)
        self.assertEqual(code, "dns_resolution_failed")

    def test_ip_literal_internal_blocked(self):
        # Default port so the IP-literal check is reached.
        safe, code, _ = is_url_safe("http://127.0.0.1/")
        self.assertFalse(safe)
        self.assertEqual(code, "unsafe_address")

    def test_internal_ip_with_nonstandard_port_still_rejected(self):
        # Human acceptance case #7: http://127.0.0.1:8000 must be refused.
        # Port 8000 is rejected first; either way access is denied.
        safe, code, _ = is_url_safe("http://127.0.0.1:8000/")
        self.assertFalse(safe)
        self.assertIn(code, ("invalid_port", "unsafe_address"))

    def test_ip_literal_public_allowed(self):
        safe, code, _ = is_url_safe("https://93.184.216.34/")
        self.assertTrue(safe, code)


class HelperTests(unittest.TestCase):
    def test_is_loopback_text(self):
        self.assertTrue(is_loopback_text("localhost"))
        self.assertTrue(is_loopback_text("sub.localhost"))
        self.assertFalse(is_loopback_text("example.com"))

    def test_parse_returns_host_lowercase_scheme(self):
        info = parse_and_validate_url("HTTPS://Example.COM/Path")
        self.assertEqual(info["scheme"], "https")
        self.assertEqual(info["host"], "example.com")


if __name__ == "__main__":
    unittest.main()
