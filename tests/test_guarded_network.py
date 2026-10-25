from __future__ import annotations

import socket
import unittest
from unittest.mock import Mock, patch

from portable.guarded_network import (
    NetworkPolicyDenied,
    connect_https_pinned,
    guarded_https_get,
    resolve_https_destination,
)


def answer(ip: str):
    return (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443, 0, 0) if ":" in ip else (ip, 443))


class GuardedNetworkTests(unittest.TestCase):
    def resolver_for(self, *ips):
        return lambda host, port, type: [answer(ip) for ip in ips]

    def test_requires_explicit_scope_before_dns_or_connect(self):
        resolver = Mock(side_effect=AssertionError("DNS must not run"))
        connector = Mock(side_effect=AssertionError("socket must not be opened"))
        with self.assertRaisesRegex(NetworkPolicyDenied, "explicit"):
            connect_https_pinned(
                "https://example.com/path",
                allowed_hosts=(),
                resolver=resolver,
                connector=connector,
            )
        resolver.assert_not_called()
        connector.assert_not_called()

    def test_rejects_non_https_credentials_and_host_not_in_allowlist(self):
        resolver = Mock(return_value=[answer("93.184.216.34")])
        for url, hosts in (
            ("http://example.com", ("example.com",)),
            ("https://user:secret@example.com", ("example.com",)),
            ("https://attacker.example/path", ("example.com",)),
        ):
            with self.subTest(url=url):
                with self.assertRaises(NetworkPolicyDenied):
                    resolve_https_destination(url, allowed_hosts=hosts, resolver=resolver)
        resolver.assert_not_called()

    def test_rejects_private_or_mixed_dns_answers_without_explicit_cidr(self):
        for ips in (("127.0.0.1",), ("93.184.216.34", "10.0.0.4"), ("169.254.169.254",)):
            with self.subTest(ips=ips):
                with self.assertRaises(NetworkPolicyDenied):
                    resolve_https_destination(
                        "https://example.com",
                        allowed_hosts=("example.com",),
                        resolver=self.resolver_for(*ips),
                    )

    def test_explicit_cidr_must_contain_every_dns_answer(self):
        with self.assertRaisesRegex(NetworkPolicyDenied, "outside"):
            resolve_https_destination(
                "https://internal.example",
                allowed_hosts=("internal.example",),
                allowed_cidrs=("10.20.0.0/16",),
                resolver=self.resolver_for("10.20.1.2", "10.21.1.2"),
            )
        allowed = resolve_https_destination(
            "https://internal.example",
            allowed_hosts=("internal.example",),
            allowed_cidrs=("10.20.0.0/16",),
            resolver=self.resolver_for("10.20.1.2"),
        )
        self.assertEqual(("10.20.1.2",), tuple(item.address for item in allowed))

    def test_connection_uses_pinned_ip_and_original_hostname_for_tls(self):
        connected = []
        wrapped = []
        class FakeContext:
            def wrap_socket(self, raw, *, server_hostname):
                wrapped.append(server_hostname)
                return "tls-socket"
        def connector(address, *, timeout):
            connected.append((address, timeout))
            return object()
        result = connect_https_pinned(
            "https://example.com/path",
            allowed_hosts=("example.com",),
            resolver=self.resolver_for("93.184.216.34"),
            connector=connector,
            tls_context=FakeContext(),
        )
        self.assertEqual("tls-socket", result)
        self.assertEqual([(("93.184.216.34", 443), 10.0)], connected)
        self.assertEqual(["example.com"], wrapped)

    def test_invalid_dns_resolution_fails_closed(self):
        with self.assertRaisesRegex(NetworkPolicyDenied, "no addresses"):
            resolve_https_destination(
                "https://example.com",
                allowed_hosts=("example.com",),
                resolver=lambda *_args, **_kwargs: [],
            )


    def test_guarded_http_request_rejects_untrusted_host_before_dns_or_connect(self):
        resolver = Mock(side_effect=AssertionError("DNS must not run"))
        connector = Mock(side_effect=AssertionError("socket must not open"))
        with self.assertRaises(NetworkPolicyDenied):
            guarded_https_get(
                "https://evil.example/private",
                allowed_hosts=("api.github.com",),
                resolver=resolver,
                connector=connector,
            )
        resolver.assert_not_called()
        connector.assert_not_called()

    def test_guarded_http_request_rejects_header_injection_before_network(self):
        resolver = Mock(side_effect=AssertionError("DNS must not run"))
        connector = Mock(side_effect=AssertionError("socket must not open"))
        with self.assertRaisesRegex(NetworkPolicyDenied, "header"):
            guarded_https_get(
                "https://api.github.com/repos/owner/repo",
                allowed_hosts=("api.github.com",),
                headers={"Authorization": "Bearer safe\r\nHost: evil.example"},
                resolver=resolver,
                connector=connector,
            )
        resolver.assert_not_called()
        connector.assert_not_called()


    def test_agency_sync_fetch_uses_pinned_upstream_host_scope(self):
        from scripts.sync_agency_agents import fetch
        with patch(
            "scripts.sync_agency_agents.guarded_https_get",
            return_value=(200, b"agent bytes"),
        ) as request:
            self.assertEqual(b"agent bytes", fetch("https://raw.githubusercontent.com/org/repo/main/agent.md"))
        self.assertEqual(
            ("api.github.com", "raw.githubusercontent.com"),
            request.call_args.kwargs["allowed_hosts"],
        )
        with patch(
            "scripts.sync_agency_agents.guarded_https_get",
            return_value=(403, b"denied"),
        ):
            with self.assertRaisesRegex(RuntimeError, "fetch failed"):
                fetch("https://api.github.com/repos/org/repo/tree")


if __name__ == "__main__":
    unittest.main()
