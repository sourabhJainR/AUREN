from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from portable.adaptive_runtime import AdaptiveRuntime
from portable.adaptive_trigger import TriggerReceipt
from portable.orchestration import Graph
from portable.remote_mcp import (
    AurenMCPASGI,
    AurenMCPService,
    MCPPrincipal,
    OAuthIntrospectionVerifier,
    StaticBearerTokenVerifier,
)
from portable.runtime_recovery import RuntimeExecutionGuard


class RemoteMCPServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = AdaptiveRuntime(Graph([]), state_root=self.root / "runtime")
        self.token = "test-remote-mcp-token-" + ("x" * 40)
        self.principal = MCPPrincipal(
            "operator-1",
            frozenset({
                "auren:tasks:read", "auren:tasks:write", "auren:tasks:cancel",
                "auren:tasks:resume", "auren:evidence:read",
                "auren:approvals:read", "auren:approvals:decide", "auren:admin",
            }),
            roles=("release-approver",),
            projects=(str(self.root),),
        )
        verifier = StaticBearerTokenVerifier([{
            "token": self.token, "subject": self.principal.subject,
            "scopes": sorted(self.principal.scopes), "roles": list(self.principal.roles),
            "projects": list(self.principal.projects),
        }])
        self.service = AurenMCPService(
            self.runtime, project_root=self.root, token_verifier=verifier,
            resource_url="https://mcp.example.test/mcp",
            authorization_server_url="https://identity.example.test",
            allowed_origins=("https://chat.example.test",),
            allowed_hosts=("mcp.example.test",), require_https=True,
            control_store_path=self.root / "operator-control.sqlite3",
        )

    def tearDown(self):
        self.service.close()
        self.runtime = None
        self.temp.cleanup()

    def _enqueue(self, task, project_root, context, *, priority, event_id, max_attempts, fire_and_forget):
        event = self.runtime.trigger_runtime.emit(
            "adaptive_runtime",
            {
                "task": task, "project_root": str(project_root),
                "context": dict(context), "priority": priority,
            },
            event_id=event_id, max_attempts=max_attempts,
            priority={"high": 0, "normal": 1, "low": 2}[priority],
        )
        return TriggerReceipt(event.event_id, event.status, event.created_at)

    def test_operator_tool_catalog_is_scope_filtered(self):
        read_only = MCPPrincipal("reader", frozenset({"auren:tasks:read"}), projects=(str(self.root),))
        names = {tool["name"] for tool in self.service.list_tools(read_only)}
        self.assertIn("auren.list_tasks", names)
        self.assertIn("auren.service_status", names)
        self.assertNotIn("auren.submit_task", names)
        self.assertNotIn("auren.decide_approval", names)
        self.assertNotIn("auren.pause_admissions", names)

    def test_submit_is_bound_to_server_workspace_and_idempotent(self):
        with patch.object(self.service.trigger, "trigger_adaptive_runtime", side_effect=self._enqueue) as enqueue:
            first = self.service.call_tool(
                "auren.submit_task",
                {"task": "Implement a safe feature", "idempotency_key": "request-0001",
                 "context": {"acceptance_criteria": ["tests pass"]}},
                self.principal,
            )
            second = self.service.call_tool(
                "auren.submit_task",
                {"task": "Implement a safe feature", "idempotency_key": "request-0001",
                 "context": {"acceptance_criteria": ["tests pass"]}},
                self.principal,
            )
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual("accepted", first["status"])
        self.assertFalse(first["completion_verified"])
        self.assertEqual(2, enqueue.call_count)
        event = self.runtime.trigger_runtime.get_event(first["task_id"])
        self.assertEqual(str(self.root), event.payload["project_root"])
        self.assertEqual(self.service._principal_key(self.principal), event.payload["context"]["_auren_mcp_subject"])
        self.assertNotIn("operator-1", event.payload["context"]["_auren_mcp_subject"])

    def test_submit_rejects_host_controlled_context_fields(self):
        with self.assertRaisesRegex(ValueError, "host-controlled"):
            self.service.call_tool(
                "auren.submit_task",
                {"task": "bad", "context": {"project_root": "/other"}},
                self.principal,
            )

    def test_pause_and_resume_admissions_are_durable_and_hash_chained(self):
        paused = self.service.call_tool(
            "auren.pause_admissions", {"reason": "maintenance window"}, self.principal,
        )
        self.assertTrue(paused["admissions_paused"])
        self.assertTrue(paused["audit_chain_valid"])
        with self.assertRaisesRegex(Exception, "admissions are paused"):
            self.service.call_tool("auren.submit_task", {"task": "blocked"}, self.principal)
        resumed = self.service.call_tool(
            "auren.resume_admissions", {"reason": "maintenance completed"}, self.principal,
        )
        self.assertFalse(resumed["admissions_paused"])
        self.assertTrue(self.service.verify_control_audit())

    def test_operator_control_audit_tampering_blocks_new_admissions(self):
        with sqlite3.connect(self.service.control_db) as db:
            db.execute("DROP TRIGGER immutable_service_control_event_update")
            db.execute(
                "UPDATE service_control_events SET event_json=? WHERE sequence=0",
                ('{"sequence":0,"type":"tampered"}',),
            )
        self.assertFalse(self.service.verify_control_audit())
        with self.assertRaisesRegex(Exception, "audit is inconsistent"):
            self.service.call_tool("auren.submit_task", {"task": "must not enter"}, self.principal)

    def test_cancel_pending_trigger_and_live_runtime_guard(self):
        with patch.object(self.service.trigger, "trigger_adaptive_runtime", side_effect=self._enqueue):
            receipt = self.service.call_tool(
                "auren.submit_task", {"task": "pending work"}, self.principal,
            )
        cancelled = self.service.call_tool(
            "auren.cancel_task", {"task_id": receipt["task_id"], "reason": "operator stop"},
            self.principal,
        )
        self.assertTrue(cancelled["cancellation_requested"])
        self.assertEqual("cancelled", cancelled["status"])

        with patch.object(self.service.trigger, "trigger_adaptive_runtime", side_effect=self._enqueue):
            active = self.service.call_tool(
                "auren.submit_task", {"task": "active work"}, self.principal,
            )
        claim = self.runtime.trigger_runtime.claim(active["task_id"])
        self.assertIsNotNone(claim)
        guard = RuntimeExecutionGuard(max_steps=10, max_attempts=3)
        self.runtime._active_runtime_guards["llm-chat:" + active["task_id"]] = guard
        requested = self.service.call_tool(
            "auren.cancel_task", {"task_id": active["task_id"], "reason": "cancel active"},
            self.principal,
        )
        self.assertTrue(requested["cancellation_requested"])
        self.assertEqual("cancellation_requested", requested["status"])
        self.assertTrue(guard.cancellation_event.is_set())

    def test_profile_identity_is_stable_across_calls(self):
        first = self.service.call_tool("auren.get_profile", {}, self.principal)
        second = self.service.call_tool("auren.get_profile", {}, self.principal)
        self.assertEqual(first, second)
        self.assertTrue(first["id"].startswith("auren_"))
        self.assertNotIn(self.principal.subject, first["id"])

    def test_tool_schemas_are_enforced_server_side(self):
        with self.assertRaisesRegex(ValueError, "unsupported field"):
            self.service.call_tool(
                "auren.get_task", {"task_id": "task-1", "include_prompt": True},
                self.principal,
            )

    def test_status_and_evidence_are_read_only(self):
        self.service.start()
        status = self.service.call_tool("auren.service_status", {}, self.principal)
        self.assertEqual("ok", status["status"])
        self.assertTrue(status["admissions"]["audit_chain_valid"])
        self.assertIn("queue", status)
        names = {tool["name"] for tool in self.service.list_tools(self.principal)}
        self.assertIn("auren.get_evidence", names)

    def test_static_tokens_reject_weak_or_unknown_scopes(self):
        with self.assertRaisesRegex(ValueError, "at least 32"):
            StaticBearerTokenVerifier([{"token": "short", "subject": "x", "scopes": []}])
        with self.assertRaisesRegex(ValueError, "scopes are invalid"):
            StaticBearerTokenVerifier([{
                "token": "x" * 40, "subject": "x", "scopes": ["admin"],
            }])


class OAuthIntrospectionVerifierTests(unittest.TestCase):
    class Response:
        status = 200

        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps(self.payload).encode("utf-8")

    def setUp(self):
        self.verifier = OAuthIntrospectionVerifier(
            endpoint="https://identity.example.test/oauth/introspect",
            client_id="auren-resource", client_secret="client-secret",
            audience="https://mcp.example.test/mcp",
            issuer="https://identity.example.test/",
        )

    def test_valid_introspection_binds_issuer_audience_expiry_and_scopes(self):
        payload = {
            "active": True, "iss": "https://identity.example.test",
            "sub": "user-123", "aud": "https://mcp.example.test/mcp",
            "exp": time.time() + 300, "nbf": time.time() - 1,
            "scope": "openid auren:tasks:read auren:tasks:write",
            "roles": ["engineer"], "projects": ["/workspace/main"],
        }
        with patch("portable.remote_mcp.urllib.request.urlopen", return_value=self.Response(payload)):
            principal = self.verifier.verify("valid-access-token")
        self.assertIsNotNone(principal)
        self.assertEqual("user-123", principal.subject)
        self.assertEqual("https://identity.example.test", principal.issuer)
        self.assertEqual(
            frozenset({"auren:tasks:read", "auren:tasks:write"}), principal.scopes,
        )
        self.assertEqual(("engineer",), principal.roles)

    def test_wrong_issuer_audience_or_expiry_is_rejected(self):
        cases = [
            {"iss": "https://attacker.example", "aud": "https://mcp.example.test/mcp", "exp": time.time() + 300},
            {"iss": "https://identity.example.test", "aud": "https://other.example/mcp", "exp": time.time() + 300},
            {"iss": "https://identity.example.test", "aud": "https://mcp.example.test/mcp", "exp": time.time() - 1},
        ]
        for index, claims in enumerate(cases):
            with self.subTest(claims=claims):
                payload = {
                    "active": True, "sub": "user-123", "scope": "auren:tasks:read",
                    **claims,
                }
                with patch("portable.remote_mcp.urllib.request.urlopen", return_value=self.Response(payload)):
                    self.assertIsNone(self.verifier.verify(f"invalid-access-token-{index}"))


class RemoteMCPASGITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = AdaptiveRuntime(Graph([]), state_root=self.root / "runtime")
        self.token = "asgi-remote-mcp-token-" + ("y" * 40)
        verifier = StaticBearerTokenVerifier([{
            "token": self.token, "subject": "operator",
            "scopes": ["auren:tasks:read"], "projects": [str(self.root)],
        }])
        service = AurenMCPService(
            self.runtime, project_root=self.root, token_verifier=verifier,
            resource_url="https://mcp.example.test/mcp",
            authorization_server_url="https://identity.example.test",
            allowed_origins=("https://chat.example.test",),
            allowed_hosts=("mcp.example.test",), require_https=True,
            control_store_path=self.root / "operator-control.sqlite3",
        )
        self.app = AurenMCPASGI(service)
        self.service = service

    def tearDown(self):
        self.service.close()
        self.runtime = None
        self.temp.cleanup()

    def request(self, method="POST", path="/mcp", body=None, extra_headers=None, scheme="https"):
        headers = {
            "host": "mcp.example.test",
            "authorization": "Bearer " + self.token,
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        }
        headers.update(extra_headers or {})
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        sent = []
        consumed = False

        async def receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": raw, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "path": path, "scheme": scheme,
            "headers": [(k.lower().encode("latin1"), v.encode("latin1")) for k, v in headers.items()],
            "client": ("127.0.0.1", 12345), "server": ("mcp.example.test", 443),
        }
        asyncio.run(self.app(scope, receive, send))
        start = next(item for item in sent if item["type"] == "http.response.start")
        response_body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
        return start["status"], dict(start["headers"]), response_body

    def test_initialize_and_tool_discovery_follow_streamable_http(self):
        status, headers, body = self.request(body={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
        })
        self.assertEqual(200, status)
        response = json.loads(body)
        self.assertEqual("2025-11-25", response["result"]["protocolVersion"])
        self.assertEqual("2025-11-25", headers[b"mcp-protocol-version"])

        status, _, body = self.request(body={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                                       extra_headers={"mcp-protocol-version": "2025-11-25"})
        self.assertEqual(200, status)
        discovered = {tool["name"]: tool for tool in json.loads(body)["result"]["tools"]}
        self.assertIn("auren.list_tasks", discovered)
        self.assertIn("auren.get_profile", discovered)
        self.assertEqual(
            [{"type": "oauth2", "scopes": ["auren:tasks:read"]}],
            discovered["auren.list_tasks"]["_meta"]["securitySchemes"],
        )

    def test_invalid_origin_and_host_are_rejected(self):
        status, _, _ = self.request(
            body={"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
            extra_headers={"origin": "https://attacker.example"},
        )
        self.assertEqual(403, status)
        status, _, _ = self.request(
            body={"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
            extra_headers={"host": "evil.example"},
        )
        self.assertEqual(400, status)

    def test_chatgpt_tool_level_oauth_challenge_is_returned_without_a_token(self):
        status, headers, body = self.request(
            body={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "auren.service_status", "arguments": {}},
            },
            extra_headers={"authorization": ""},
        )
        self.assertEqual(200, status)
        response = json.loads(body)
        result = response["result"]
        self.assertTrue(result["isError"])
        challenge = result["_meta"]["mcp/www_authenticate"][0]
        self.assertIn("resource_metadata=", challenge)
        self.assertIn("error=", challenge)
        self.assertIn(b"www-authenticate", headers)

    def test_insufficient_scope_returns_oauth_tool_challenge(self):
        status, _, body = self.request(
            body={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "auren.submit_task", "arguments": {"task": "not authorized"}},
            },
        )
        self.assertEqual(200, status)
        result = json.loads(body)["result"]
        self.assertTrue(result["isError"])
        challenge = result["_meta"]["mcp/www_authenticate"][0]
        self.assertIn('error="insufficient_scope"', challenge)

    def test_protected_resources_require_authentication(self):
        status, headers, _ = self.request(
            body={"jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}},
            extra_headers={"authorization": ""},
        )
        self.assertEqual(401, status)
        self.assertIn(b"resource_metadata=", headers[b"www-authenticate"])

    def test_unauthenticated_discovery_advertises_per_tool_oauth_scopes(self):
        status, _, body = self.request(
            body={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            extra_headers={"authorization": ""},
        )
        self.assertEqual(200, status)
        tools = {tool["name"]: tool for tool in json.loads(body)["result"]["tools"]}
        self.assertIn("auren.submit_task", tools)
        self.assertEqual(
            [{"type": "oauth2", "scopes": ["auren:tasks:write"]}],
            tools["auren.submit_task"]["_meta"]["securitySchemes"],
        )
        self.assertTrue(tools["auren.get_profile"]["_meta"]["openai/profile"])

    def test_transport_methods_and_https_are_enforced(self):
        status, headers, _ = self.request(method="GET")
        self.assertEqual(405, status)
        self.assertEqual(b"POST", headers[b"allow"])
        status, _, _ = self.request(
            body={"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
            scheme="http",
        )
        self.assertEqual(400, status)

    def test_notification_returns_202_without_json_body(self):
        status, _, body = self.request(
            body={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            extra_headers={"mcp-protocol-version": "2025-11-25"},
        )
        self.assertEqual(202, status)
        self.assertEqual(b"", body)

    def test_readiness_requires_the_durable_dispatcher(self):
        status, _, body = self.request(method="GET", path="/readyz", body=None)
        self.assertEqual(503, status)
        self.service.start()
        status, _, body = self.request(method="GET", path="/readyz", body=None)
        self.assertEqual(200, status)
        self.assertEqual({"status": "ok"}, json.loads(body))

    def test_protected_resource_metadata_is_public_but_origin_checked(self):
        status, _, body = self.request(
            method="GET", path="/.well-known/oauth-protected-resource", body=None,
        )
        self.assertEqual(200, status)
        metadata = json.loads(body)
        self.assertEqual("https://mcp.example.test/mcp", metadata["resource"])
        self.assertEqual(["https://identity.example.test"], metadata["authorization_servers"])


if __name__ == "__main__":
    unittest.main()
