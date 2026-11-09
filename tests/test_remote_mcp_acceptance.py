import json
import unittest

from scripts.remote_mcp_acceptance import AcceptanceFailure, run_acceptance


ENDPOINT = "https://mcp.example.test/mcp"
TOKEN = "test-access-token-with-sufficient-length"


class RemoteMCPAcceptanceTests(unittest.TestCase):
    def _transport(self, *, omit_tool=None, health_status=200):
        calls = []

        def transport(url, **kwargs):
            calls.append((url, kwargs))
            if kwargs["method"] == "GET" and url.endswith("/healthz"):
                return health_status, b'{"status":"ok"}'
            if kwargs["method"] == "GET" and "oauth-protected-resource" in url:
                return 200, json.dumps({
                    "resource": ENDPOINT,
                    "authorization_servers": ["https://identity.example.test"],
                }).encode()
            request = json.loads(kwargs["body"].decode())
            if request["method"] == "server/discover":
                return 200, json.dumps({
                    "jsonrpc": "2.0", "id": request["id"],
                    "result": {
                        "resultType": "complete",
                        "supportedVersions": ["2026-07-28", "2025-11-25"],
                        "ttlMs": 0, "cacheScope": "private",
                        "_meta": {"io.modelcontextprotocol/serverInfo": {
                            "name": "auren-operator-mcp", "version": "1.1.0",
                        }},
                    },
                }).encode()
            if request["method"] == "initialize":
                return 200, json.dumps({
                    "jsonrpc": "2.0", "id": request["id"],
                    "result": {
                        "protocolVersion": "2025-11-25",
                        "serverInfo": {"name": "auren-operator-mcp", "version": "1.1.0"},
                    },
                }).encode()
            if request["method"] == "tools/list":
                names = [
                    "auren.get_profile", "auren.service_status", "auren.list_tasks",
                    "auren.get_task", "auren.get_evidence",
                ]
                if omit_tool:
                    names.remove(omit_tool)
                result = {"tools": [{"name": name} for name in names]}
                if kwargs["headers"]["mcp-protocol-version"] == "2026-07-28":
                    result.update({"resultType": "complete", "ttlMs": 0, "cacheScope": "private"})
                return 200, json.dumps({
                    "jsonrpc": "2.0", "id": request["id"], "result": result,
                }).encode()
            raise AssertionError("acceptance probe attempted a mutating method")

        return transport, calls

    def test_acceptance_checks_health_metadata_and_read_only_tool_discovery(self):
        transport, calls = self._transport()
        result = run_acceptance(
            ENDPOINT, TOKEN, allowed_hosts=("mcp.example.test",), transport=transport,
        )
        self.assertEqual("accepted", result["status"])
        self.assertEqual(5, result["tool_count"])
        self.assertEqual(0, result["live_task_mutations"])
        self.assertEqual(6, len(calls))
        self.assertTrue(all(call[1]["allowed_hosts"] == ("mcp.example.test",) for call in calls))
        post_calls = [call for call in calls if call[1]["method"] == "POST"]
        self.assertEqual(
            ["server/discover", "tools/list", "initialize", "tools/list"],
            [json.loads(call[1]["body"].decode())["method"] for call in post_calls],
        )
        modern_calls = [
            call for call in post_calls
            if call[1]["headers"]["mcp-protocol-version"] == "2026-07-28"
        ]
        self.assertEqual(["server/discover", "tools/list"], [
            json.loads(call[1]["body"].decode())["method"] for call in modern_calls
        ])
        self.assertEqual("server/discover", modern_calls[0][1]["headers"]["mcp-method"])
        self.assertTrue(all(call[1]["headers"]["authorization"] == "Bearer " + TOKEN for call in calls))

    def test_endpoint_must_be_https_and_explicitly_allowlisted(self):
        with self.assertRaisesRegex(ValueError, "allowlisted HTTPS"):
            run_acceptance(
                "http://mcp.example.test/mcp", TOKEN,
                allowed_hosts=("mcp.example.test",), transport=lambda *a, **k: (200, b"{}"),
            )
        with self.assertRaisesRegex(ValueError, "allowlisted HTTPS"):
            run_acceptance(
                ENDPOINT, TOKEN, allowed_hosts=("other.example.test",),
                transport=lambda *a, **k: (200, b"{}"),
            )

    def test_non_200_health_fails(self):
        transport, _ = self._transport(health_status=503)
        with self.assertRaisesRegex(AcceptanceFailure, "HTTP 503"):
            run_acceptance(ENDPOINT, TOKEN, allowed_hosts=("mcp.example.test",), transport=transport)

    def test_missing_read_only_tool_fails_without_mutating_server(self):
        transport, calls = self._transport(omit_tool="auren.get_evidence")
        with self.assertRaisesRegex(AcceptanceFailure, "auren.get_evidence"):
            run_acceptance(ENDPOINT, TOKEN, allowed_hosts=("mcp.example.test",), transport=transport)
        methods = [
            json.loads(kwargs["body"].decode())["method"]
            for _, kwargs in calls if kwargs["method"] == "POST"
        ]
        self.assertEqual(["server/discover", "tools/list"], methods)

    def test_missing_or_invalid_token_fails_before_network(self):
        calls = []
        with self.assertRaisesRegex(ValueError, "access token"):
            run_acceptance(
                ENDPOINT, "short", allowed_hosts=("mcp.example.test",),
                transport=lambda *a, **k: calls.append((a, k)) or (200, b"{}"),
            )
        self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
