from __future__ import annotations

import asyncio
import io
import json
import os
import stat
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from portable.adaptive_runtime import AdaptiveRuntime
from portable.local_host import (
    create_local_mcp_service,
    ensure_local_observability_store,
    load_or_create_local_token,
    resolve_runtime_factory,
)
from portable.mcp_stdio_proxy import run_proxy, validate_local_url
from portable.remote_mcp import AurenMCPASGI, AurenMCPService, StaticBearerTokenVerifier
from portable.orchestration import Graph, Node, NodeKind
from portable.dashboard_server import create_server


class LocalMCPHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "workspace"
        self.project.mkdir()
        self.token_path = self.root / "private" / "local-operator.token"

    def tearDown(self):
        self.temp.cleanup()

    def test_local_token_is_stable_and_owner_only(self):
        token, path = load_or_create_local_token(self.token_path)
        self.assertEqual(path, self.token_path)
        self.assertGreaterEqual(len(token), 32)
        self.assertEqual(token, load_or_create_local_token(self.token_path)[0])
        if os.name != "nt":
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            self.assertEqual(0o700, stat.S_IMODE(path.parent.stat().st_mode))

    def test_local_token_rejects_symlinks_and_permissive_files(self):
        self.token_path.parent.mkdir(parents=True)
        source = self.root / "source-token"
        source.write_text("x" * 48, encoding="utf-8")
        try:
            self.token_path.symlink_to(source)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")
        with self.assertRaisesRegex(RuntimeError, "non-symlink"):
            load_or_create_local_token(self.token_path)

        self.token_path.unlink()
        self.token_path.write_text("x" * 48, encoding="utf-8")
        if os.name != "nt":
            self.token_path.chmod(0o644)
            with self.assertRaisesRegex(PermissionError, "readable by group or others"):
                load_or_create_local_token(self.token_path)

    def test_local_mcp_service_uses_loopback_and_workspace_scoped_operator(self):
        runtime = AdaptiveRuntime(
            Graph([Node("local-mcp-test", NodeKind.DETERMINISTIC, lambda _state: "ready")]),
            state_root=self.root / "runtime",
        )
        state_home = self.root / "auren-home"
        with patch("portable.local_host.canonical_auren_home", return_value=state_home):
            service, token_path = create_local_mcp_service(
                runtime, self.project, 43127, token_path=self.token_path,
            )
        try:
            token = token_path.read_text(encoding="utf-8").strip()
            principal = service.token_verifier.verify(token)
            self.assertIsNotNone(principal)
            self.assertEqual("local-operator", principal.subject)
            self.assertEqual((str(self.project.resolve()),), principal.projects)
            self.assertFalse(service.require_https)
            self.assertEqual("http://127.0.0.1:43127/mcp", service.resource_url)
            self.assertEqual(frozenset({("127.0.0.1", 43127)}), service.allowed_hosts)
            tools = {item["name"]: item for item in service.list_tools(principal)}
            self.assertEqual(
                [{"type": "http", "scheme": "bearer"}],
                tools["auren.submit_task"]["_meta"]["securitySchemes"],
            )
        finally:
            service.close()

    def test_disabling_https_is_rejected_for_non_loopback_mcp_resources(self):
        runtime = AdaptiveRuntime(
            Graph([Node("local-mcp-url-test", NodeKind.DETERMINISTIC, lambda _state: "ready")]),
            state_root=self.root / "url-runtime",
        )
        verifier = StaticBearerTokenVerifier([{
            "token": "local-test-token-" + ("x" * 40),
            "subject": "local-operator",
            "scopes": ["auren:tasks:read"],
            "projects": [str(self.project.resolve())],
        }])
        with self.assertRaisesRegex(ValueError, "loopback services"):
            AurenMCPService(
                runtime,
                project_root=self.project,
                token_verifier=verifier,
                resource_url="https://mcp.example.test/mcp",
                allowed_hosts=("mcp.example.test",),
                require_https=False,
                control_store_path=self.root / "remote-control.sqlite3",
            )

    def test_first_run_observability_store_is_created_as_private_empty_journal(self):
        runtime_home = self.root / "runtime-home"
        with patch("portable.local_host.canonical_runtime_home", return_value=runtime_home):
            trace_path = ensure_local_observability_store()
        self.assertEqual(runtime_home / "observability" / "traces.jsonl", trace_path)
        self.assertTrue(trace_path.is_file())
        self.assertEqual(b"", trace_path.read_bytes())
        if os.name != "nt":
            self.assertEqual(0o700, stat.S_IMODE(trace_path.parent.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE(trace_path.stat().st_mode))

    def test_project_local_runtime_factory_is_auto_discovered(self):
        with self.assertRaisesRegex(RuntimeError, "No AUREN runtime factory"):
            resolve_runtime_factory(self.project)
        (self.project / "auren_runtime_factory.py").write_text(
            "def create_runtime(*, project_root):\n    raise NotImplementedError\n",
            encoding="utf-8",
        )
        self.assertEqual(
            "auren_runtime_factory:create_runtime",
            resolve_runtime_factory(self.project),
        )
        self.assertEqual(
            "trusted_runtime:create_runtime",
            resolve_runtime_factory(self.project, "trusted_runtime:create_runtime"),
        )

    def test_local_mcp_asgi_health_initialize_and_host_validation(self):
        runtime = AdaptiveRuntime(
            Graph([Node("local-asgi-test", NodeKind.DETERMINISTIC, lambda _state: "ready")]),
            state_root=self.root / "asgi-runtime",
        )
        state_home = self.root / "auren-home"
        with patch("portable.local_host.canonical_auren_home", return_value=state_home):
            service, token_path = create_local_mcp_service(
                runtime, self.project, 43128, token_path=self.token_path,
            )
        app = AurenMCPASGI(service)
        token = token_path.read_text(encoding="utf-8").strip()

        async def request(method, path, body=None, host="127.0.0.1:43128", auth=None):
            sent = []
            consumed = False
            raw = json.dumps(body).encode("utf-8") if body is not None else b""
            headers = {
                "host": host,
                "accept": "application/json, text/event-stream",
            }
            if body is not None:
                headers["content-type"] = "application/json"
            if auth:
                headers["authorization"] = "Bearer " + auth

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
                "method": method, "path": path, "scheme": "http",
                "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
                "client": ("127.0.0.1", 12345), "server": ("127.0.0.1", 43128),
            }
            await app(scope, receive, send)
            start = next(item for item in sent if item["type"] == "http.response.start")
            payload = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
            return start["status"], json.loads(payload) if payload else None

        try:
            status, health = asyncio.run(request("GET", "/healthz"))
            self.assertEqual(200, status)
            self.assertEqual({"status": "ok"}, health)
            status, initialized = asyncio.run(request(
                "POST", "/mcp",
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                            "clientInfo": {"name": "local-asgi-test", "version": "1"}}},
                auth=token,
            ))
            self.assertEqual(200, status)
            self.assertEqual("2025-11-25", initialized["result"]["protocolVersion"])
            status, _ = asyncio.run(request("GET", "/healthz", host="evil.example:43128"))
            self.assertEqual(400, status)
        finally:
            service.close()

    def test_dashboard_server_selects_an_available_loopback_port(self):
        server = create_server(self.project, "127.0.0.1", 0)
        try:
            self.assertEqual("127.0.0.1", server.server_address[0])
            self.assertGreater(server.server_address[1], 0)
        finally:
            server.server_close()


class LocalMCPStdioProxyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.token_path = self.root / "local.token"
        self.token_path.write_text("local-test-token-" + ("x" * 40), encoding="utf-8")
        if os.name != "nt":
            self.token_path.chmod(0o600)

    def tearDown(self):
        self.temp.cleanup()

    def test_proxy_accepts_only_explicit_loopback_mcp_urls(self):
        self.assertEqual(
            "http://127.0.0.1:43127/mcp",
            validate_local_url("http://127.0.0.1:43127/mcp"),
        )
        for url in (
            "https://127.0.0.1:43127/mcp",
            "http://mcp.example.com:43127/mcp",
            "http://127.0.0.1/mcp",
            "http://127.0.0.1:43127/other",
            "http://user@127.0.0.1:43127/mcp",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_local_url(url)

    def test_stdio_proxy_forwards_authorized_jsonrpc_and_returns_response(self):
        seen = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                seen["authorization"] = self.headers.get("Authorization")
                seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                body = json.dumps({
                    "jsonrpc": "2.0", "id": seen["body"].get("id"),
                    "result": {"tools": [{"name": "auren.service_status"}]},
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/mcp"
            request = {"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}}
            source = io.BytesIO((json.dumps(request) + "\n").encode("utf-8"))
            target = io.BytesIO()
            diagnostics = io.StringIO()
            result = run_proxy(
                url, self.token_path, stdin=source, stdout=target, stderr=diagnostics,
            )
            self.assertEqual(0, result)
            self.assertEqual("Bearer " + self.token_path.read_text(encoding="utf-8").strip(), seen["authorization"])
            response = json.loads(target.getvalue().decode("utf-8"))
            self.assertEqual(7, response["id"])
            self.assertEqual("auren.service_status", response["result"]["tools"][0]["name"])
            self.assertEqual("", diagnostics.getvalue())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
