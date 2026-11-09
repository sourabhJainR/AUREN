from __future__ import annotations

import asyncio
import io
import importlib.util
import json
import os
import queue
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
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


@unittest.skipUnless(
    os.name != "nt" and importlib.util.find_spec("uvicorn"),
    "live local host acceptance requires POSIX signal handling and optional uvicorn",
)
class LocalMCPHostProcessTests(unittest.TestCase):
    def test_local_command_starts_both_services_and_shuts_them_down(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "workspace"
            project.mkdir()
            conflicting_package = project / "portable"
            conflicting_package.mkdir()
            (conflicting_package / "__init__.py").write_text(
                "raise RuntimeError('workspace package must not shadow AUREN')\\n",
                encoding="utf-8",
            )
            state_home = root / "auren-home"
            factory = project / "auren_runtime_factory.py"
            factory.write_text(
                "from pathlib import Path\n"
                "from portable.adaptive_runtime import AdaptiveRuntime\n"
                "from portable.orchestration import Graph, Node, NodeKind\n"
                "def create_runtime(*, project_root):\n"
                "    root = Path(project_root)\n"
                "    graph = Graph([Node('local-smoke', NodeKind.DETERMINISTIC, lambda _state: 'ready')])\n"
                "    return AdaptiveRuntime(graph, state_root=root / '.auren-runtime')\n",
                encoding="utf-8",
            )
            repo_root = Path(__file__).resolve().parents[1]
            env = os.environ.copy()
            env["AUREN_HOME"] = str(state_home)
            env.pop("AUREN_MCP_RUNTIME_FACTORY", None)
            process = subprocess.Popen(
                [
                    sys.executable, str(repo_root / "auren_cli.py"), "local",
                    "--project-root", str(project),
                    "--runtime-factory", "auren_runtime_factory:create_runtime",
                ],
                cwd=repo_root, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
            lines: queue.Queue[str] = queue.Queue()

            def collect_output():
                assert process.stdout is not None
                for line in process.stdout:
                    lines.put(line.rstrip("\r\n"))

            reader = threading.Thread(target=collect_output, daemon=True)
            reader.start()
            output = []
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if process.poll() is not None and lines.empty():
                        break
                    try:
                        line = lines.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    output.append(line)
                    if line == "AUREN local services are ready.":
                        break
                self.assertIn("AUREN local services are ready.", output, "\\n".join(output))
                dashboard_line = next(line for line in output if line.startswith("Observation console: "))
                mcp_line = next(line for line in output if line.startswith("MCP endpoint: "))
                dashboard_url = dashboard_line.split(": ", 1)[1]
                mcp_url = mcp_line.split(": ", 1)[1]
                with urllib.request.urlopen(dashboard_url.rsplit("/", 1)[0] + "/api/health", timeout=3) as response:
                    dashboard_health = json.loads(response.read().decode("utf-8"))
                self.assertEqual("ready", dashboard_health["readiness"])
                with urllib.request.urlopen(mcp_url.rsplit("/", 1)[0] + "/readyz", timeout=3) as response:
                    self.assertEqual(200, response.status)

                token = (state_home / "mcp" / "local-operator.token").read_text(encoding="utf-8").strip()
                request_body = json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25", "capabilities": {},
                        "clientInfo": {"name": "local-process-test", "version": "1"},
                    },
                }).encode("utf-8")
                request = urllib.request.Request(
                    mcp_url, data=request_body, method="POST",
                    headers={
                        "Authorization": "Bearer " + token,
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                    },
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    initialized = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(200, response.status)
                    self.assertEqual("2025-11-25", initialized["result"]["protocolVersion"])
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    try:
                        process.wait(timeout=45)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                reader.join(timeout=2)
            self.assertEqual(0, process.returncode, "\\n".join(output))


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

    def test_stdio_proxy_stops_after_an_oversized_frame(self):
        source = io.BytesIO((b"x" * (64 * 1024 + 1)) + b"\n" + b'{"jsonrpc":"2.0","id":9,"method":"ping"}\n')
        target = io.BytesIO()
        with patch("portable.mcp_stdio_proxy.post_message") as post:
            result = run_proxy(
                "http://127.0.0.1:43127/mcp", self.token_path,
                stdin=source, stdout=target, stderr=io.StringIO(),
            )
        self.assertEqual(0, result)
        post.assert_not_called()
        frames = target.getvalue().decode("utf-8").splitlines()
        self.assertEqual(1, len(frames))
        self.assertIn("exceeds the configured byte limit", json.loads(frames[0])["error"]["message"])

    def test_stdio_proxy_preserves_auth_and_negotiated_protocol_version(self):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append({
                    "authorization": self.headers.get("Authorization"),
                    "protocol": self.headers.get("MCP-Protocol-Version"),
                    "request": request,
                })
                if request.get("method") == "initialize":
                    result = {"protocolVersion": "2025-11-25", "capabilities": {}, "serverInfo": {"name": "test", "version": "1"}}
                else:
                    result = {"tools": [{"name": "auren.service_status"}]}
                body = json.dumps({
                    "jsonrpc": "2.0", "id": request.get("id"), "result": result,
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
            initialize = {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                           "clientInfo": {"name": "stdio-test", "version": "1"}},
            }
            tools_list = {"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}}
            source = io.BytesIO(
                (json.dumps(initialize) + "\n" + json.dumps(tools_list) + "\n").encode("utf-8")
            )
            target = io.BytesIO()
            diagnostics = io.StringIO()
            result = run_proxy(
                url, self.token_path, stdin=source, stdout=target, stderr=diagnostics,
            )
            self.assertEqual(0, result)
            token = "Bearer " + self.token_path.read_text(encoding="utf-8").strip()
            self.assertEqual([token, token], [item["authorization"] for item in seen])
            self.assertIsNone(seen[0]["protocol"])
            self.assertEqual("2025-11-25", seen[1]["protocol"])
            responses = [json.loads(line) for line in target.getvalue().decode("utf-8").splitlines()]
            self.assertEqual([1, 7], [item["id"] for item in responses])
            self.assertEqual("auren.service_status", responses[1]["result"]["tools"][0]["name"])
            self.assertEqual("", diagnostics.getvalue())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
