import json
import unittest

from scripts.remote_mcp_acceptance import AcceptanceFailure, run_acceptance


ENDPOINT = "https://mcp.example.test/mcp"
TOKEN = "test-access-token-with-sufficient-length"
ISSUER = "https://identity.example.test/"
ALLOWED_HOSTS = ("mcp.example.test", "identity.example.test")


class RemoteMCPAcceptanceTests(unittest.TestCase):
    def _transport(
        self, *, omit_tool=None, health_status=200, readiness_status=200,
        issuer=ISSUER, omit_profile_marker=False, omit_challenge=False,
        profile_ids=None, bad_pkce=False, omit_refresh=False,
    ):
        calls = []
        profile_call_count = 0

        def transport(url, **kwargs):
            nonlocal profile_call_count
            calls.append((url, kwargs))
            if kwargs["method"] == "GET" and url.endswith("/healthz"):
                return health_status, b'{"status":"ok"}'
            if kwargs["method"] == "GET" and url.endswith("/readyz"):
                return readiness_status, b'{"status":"ok"}'
            if kwargs["method"] == "GET" and "oauth-protected-resource" in url:
                return 200, json.dumps({
                    "resource": ENDPOINT,
                    "authorization_servers": [issuer],
                }).encode()
            if kwargs["method"] == "GET" and "oauth-authorization-server" in url:
                metadata = {
                    "issuer": issuer,
                    "authorization_endpoint": issuer.rstrip("/") + "/authorize",
                    "token_endpoint": issuer.rstrip("/") + "/token",
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code"] if omit_refresh else ["authorization_code", "refresh_token"],
                    "code_challenge_methods_supported": [] if bad_pkce else ["S256"],
                    "token_endpoint_auth_methods_supported": ["none"],
                    "client_id_metadata_document_supported": True,
                    "scopes_supported": ["openid"] if omit_refresh else ["openid", "offline_access"],
                }
                return 200, json.dumps(metadata).encode()

            request = json.loads(kwargs["body"].decode())
            if request["method"] == "server/discover":
                result = {
                    "resultType": "complete",
                    "supportedVersions": ["2026-07-28", "2025-11-25"],
                    "ttlMs": 0, "cacheScope": "private",
                    "_meta": {"io.modelcontextprotocol/serverInfo": {
                        "name": "auren-operator-mcp", "version": "1.1.0",
                    }},
                }
            elif request["method"] == "initialize":
                result = {
                    "protocolVersion": "2025-11-25",
                    "serverInfo": {"name": "auren-operator-mcp", "version": "1.1.0"},
                }
            elif request["method"] == "tools/list":
                names = [
                    "auren.get_profile", "auren.service_status", "auren.list_tasks",
                    "auren.get_task", "auren.get_evidence", "auren.list_approvals",
                    "auren.get_approval", "auren.submit_task", "auren.decide_approval",
                ]
                if omit_tool:
                    names.remove(omit_tool)
                tools = []
                for name in names:
                    tool = {"name": name, "_meta": {}}
                    if name == "auren.get_profile":
                        if not omit_profile_marker:
                            tool["_meta"]["openai/profile"] = True
                        tool["_meta"]["securitySchemes"] = [
                            {"type": "oauth2", "scopes": ["auren:tasks:read"]},
                        ]
                        tool["securitySchemes"] = [
                            {"type": "oauth2", "scopes": ["auren:tasks:read"]},
                        ]
                        tool["outputSchema"] = {
                            "type": "object",
                            "properties": {"id": {
                                "type": "string", "minLength": 1, "pattern": r"\S",
                            }},
                            "required": ["id"],
                        }
                    elif name in {
                        "auren.service_status", "auren.list_tasks", "auren.get_task",
                    }:
                        tool["_meta"]["securitySchemes"] = [
                            {"type": "oauth2", "scopes": ["auren:tasks:read"]},
                        ]
                    elif name == "auren.get_evidence":
                        tool["_meta"]["securitySchemes"] = [
                            {"type": "oauth2", "scopes": ["auren:evidence:read"]},
                        ]
                    elif name in {"auren.list_approvals", "auren.get_approval"}:
                        tool["_meta"]["securitySchemes"] = [
                            {"type": "oauth2", "scopes": ["auren:approvals:read"]},
                        ]
                    elif name == "auren.submit_task":
                        tool["_meta"]["securitySchemes"] = [
                            {"type": "oauth2", "scopes": ["auren:tasks:write"]},
                        ]
                    elif name == "auren.decide_approval":
                        tool["_meta"]["securitySchemes"] = [
                            {"type": "oauth2", "scopes": ["auren:approvals:decide"]},
                        ]
                    tools.append(tool)
                result = {"tools": tools}
                if kwargs["headers"]["mcp-protocol-version"] == "2026-07-28":
                    result.update({"resultType": "complete", "ttlMs": 0, "cacheScope": "private"})
            elif request["method"] == "tools/call":
                if "authorization" not in kwargs["headers"]:
                    auth_meta = {} if omit_challenge else {
                        "mcp/www_authenticate": [
                            'Bearer resource_metadata="https://mcp.example.test/.well-known/oauth-protected-resource/mcp", error="invalid_token", error_description="Sign in required"',
                        ],
                    }
                    result = {
                        "isError": True,
                        "_meta": auth_meta,
                        "content": [{"type": "text", "text": "Authentication required"}],
                    }
                else:
                    profile_call_count += 1
                    if profile_ids:
                        profile_id = profile_ids[min(profile_call_count - 1, len(profile_ids) - 1)]
                    else:
                        profile_id = "auren_profile_stable"
                    profile = {"id": profile_id, "name": "AUREN"}
                    result = {
                        "isError": False,
                        "structuredContent": profile,
                        "content": [{"type": "text", "text": json.dumps(profile)}],
                    }
            else:
                raise AssertionError("acceptance probe attempted a mutating method")

            return 200, json.dumps({
                "jsonrpc": "2.0", "id": request["id"], "result": result,
            }).encode()

        return transport, calls

    def test_acceptance_checks_oauth_chatgpt_metadata_profile_and_read_only_discovery(self):
        transport, calls = self._transport()
        result = run_acceptance(
            ENDPOINT, TOKEN, allowed_hosts=ALLOWED_HOSTS,
            expected_issuer=ISSUER, transport=transport,
        )
        self.assertEqual("accepted", result["status"])
        self.assertEqual(9, result["tool_count"])
        self.assertEqual(0, result["live_task_mutations"])
        self.assertTrue(result["oauth_discovery_verified"])
        self.assertTrue(result["profile_identity_verified"])
        self.assertTrue(result["tool_level_oauth_challenge_verified"])
        self.assertTrue(result["write_tools_verified"])
        self.assertEqual(11, len(calls))
        self.assertEqual(
            ("identity.example.test",),
            next(kwargs["allowed_hosts"] for url, kwargs in calls if "oauth-authorization-server" in url),
        )
        self.assertTrue(all(
            kwargs["allowed_hosts"] == (("identity.example.test",) if "oauth-authorization-server" in url else ("mcp.example.test",))
            for url, kwargs in calls
        ))
        post_calls = [call for call in calls if call[1]["method"] == "POST"]
        self.assertEqual(
            ["server/discover", "tools/list", "initialize", "tools/list", "tools/call", "tools/call", "tools/call"],
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
        unauthenticated = [
            call for call in post_calls if "authorization" not in call[1]["headers"]
        ]
        self.assertEqual(1, len(unauthenticated))

    def test_endpoint_must_be_https_and_explicitly_allowlisted(self):
        with self.assertRaisesRegex(ValueError, "allowlisted HTTPS"):
            run_acceptance(
                "http://mcp.example.test/mcp", TOKEN,
                allowed_hosts=ALLOWED_HOSTS, transport=lambda *a, **k: (200, b"{}"),
            )
        with self.assertRaisesRegex(ValueError, "allowlisted HTTPS"):
            run_acceptance(
                ENDPOINT, TOKEN, allowed_hosts=("other.example.test",),
                transport=lambda *a, **k: (200, b"{}"),
            )

    def test_non_200_health_fails(self):
        transport, _ = self._transport(health_status=503)
        with self.assertRaisesRegex(AcceptanceFailure, "HTTP 503"):
            run_acceptance(ENDPOINT, TOKEN, allowed_hosts=ALLOWED_HOSTS, transport=transport)

    def test_non_200_readiness_fails(self):
        transport, calls = self._transport(readiness_status=503)
        with self.assertRaisesRegex(AcceptanceFailure, "HTTP 503"):
            run_acceptance(ENDPOINT, TOKEN, allowed_hosts=ALLOWED_HOSTS, transport=transport)
        self.assertEqual(2, len(calls))
        self.assertTrue(calls[-1][0].endswith("/readyz"))

    def test_unready_service_fails_even_when_liveness_is_ok(self):
        transport, _ = self._transport()
        original = transport
        def unready(url, **kwargs):
            if url.endswith("/readyz"):
                return 200, b'{"status":"not_ready"}'
            return original(url, **kwargs)
        with self.assertRaisesRegex(AcceptanceFailure, "readiness check"):
            run_acceptance(ENDPOINT, TOKEN, allowed_hosts=ALLOWED_HOSTS, transport=unready)

    def test_missing_read_only_tool_fails_without_mutating_server(self):
        transport, calls = self._transport(omit_tool="auren.get_evidence")
        with self.assertRaisesRegex(AcceptanceFailure, "auren.get_evidence"):
            run_acceptance(ENDPOINT, TOKEN, allowed_hosts=ALLOWED_HOSTS, transport=transport)
        methods = [
            json.loads(kwargs["body"].decode())["method"]
            for _, kwargs in calls if kwargs["method"] == "POST"
        ]
        self.assertEqual(["server/discover", "tools/list"], methods)

    def test_oauth_issuer_host_must_be_explicitly_allowlisted(self):
        transport, _ = self._transport()
        with self.assertRaisesRegex(AcceptanceFailure, "explicit outbound allowlist"):
            run_acceptance(
                ENDPOINT, TOKEN, allowed_hosts=("mcp.example.test",),
                expected_issuer=ISSUER, transport=transport,
            )

    def test_exact_expected_issuer_is_required(self):
        transport, _ = self._transport(issuer="https://identity.example.test")
        with self.assertRaisesRegex(AcceptanceFailure, "exactly match"):
            run_acceptance(
                ENDPOINT, TOKEN, allowed_hosts=ALLOWED_HOSTS,
                expected_issuer=ISSUER, transport=transport,
            )

    def test_oauth_provider_must_advertise_pkce_s256_and_refresh(self):
        transport, _ = self._transport(bad_pkce=True)
        with self.assertRaisesRegex(AcceptanceFailure, "PKCE S256"):
            run_acceptance(ENDPOINT, TOKEN, allowed_hosts=ALLOWED_HOSTS, transport=transport)
        transport, _ = self._transport(omit_refresh=True)
        with self.assertRaisesRegex(AcceptanceFailure, "offline_access"):
            run_acceptance(ENDPOINT, TOKEN, allowed_hosts=ALLOWED_HOSTS, transport=transport)

    def test_profile_metadata_challenge_and_stable_identity_are_required(self):
        transport, _ = self._transport(omit_profile_marker=True)
        with self.assertRaisesRegex(AcceptanceFailure, "get_profile"):
            run_acceptance(ENDPOINT, TOKEN, allowed_hosts=ALLOWED_HOSTS, transport=transport)
        transport, _ = self._transport(omit_challenge=True)
        with self.assertRaisesRegex(AcceptanceFailure, "OAuth challenge"):
            run_acceptance(ENDPOINT, TOKEN, allowed_hosts=ALLOWED_HOSTS, transport=transport)
        transport, _ = self._transport(profile_ids=["auren_profile_first", "auren_profile_second"])
        with self.assertRaisesRegex(AcceptanceFailure, "identity changed"):
            run_acceptance(ENDPOINT, TOKEN, allowed_hosts=ALLOWED_HOSTS, transport=transport)

    def test_write_scopes_are_required_only_when_requested(self):
        transport, _ = self._transport(omit_tool="auren.submit_task")
        result = run_acceptance(ENDPOINT, TOKEN, allowed_hosts=ALLOWED_HOSTS, transport=transport)
        self.assertFalse(result["write_tools_verified"])
        transport, _ = self._transport(omit_tool="auren.decide_approval")
        with self.assertRaisesRegex(AcceptanceFailure, "write tools"):
            run_acceptance(
                ENDPOINT, TOKEN, allowed_hosts=ALLOWED_HOSTS,
                require_write=True, transport=transport,
            )

    def test_missing_or_invalid_token_fails_before_network(self):
        calls = []
        with self.assertRaisesRegex(ValueError, "access token"):
            run_acceptance(
                ENDPOINT, "short", allowed_hosts=ALLOWED_HOSTS,
                transport=lambda *a, **k: calls.append((a, k)) or (200, b"{}"),
            )
        self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
