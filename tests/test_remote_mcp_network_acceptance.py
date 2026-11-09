from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


class RemoteMCPNetworkAcceptanceTests(unittest.TestCase):
    """Exercise the real Uvicorn/ASGI boundary over loopback HTTP."""

    def setUp(self):
        if importlib.util.find_spec("uvicorn") is None:
            self.skipTest("uvicorn is installed by the runtime-resilience workflow")

        self.temp = tempfile.TemporaryDirectory(prefix="auren-mcp-network-")
        self.root = Path(self.temp.name)
        self.project_root = self.root / "project"
        self.project_root.mkdir()
        self.token = "network-acceptance-token-" + ("z" * 40)
        self.port = self._free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.stderr = (self.root / "server.stderr").open("w+b")

        factory = self.root / "auren_network_runtime_factory.py"
        factory.write_text(
            "from pathlib import Path\n"
            "from portable.adaptive_runtime import AdaptiveRuntime\n"
            "from portable.orchestration import Graph, Node, NodeKind\n"
            "\n"
            "def create_runtime(*, project_root):\n"
            "    root = Path(project_root)\n"
            "    graph = Graph([Node('mcp-network-noop', NodeKind.DETERMINISTIC, lambda _state: 'ready')])\n"
            "    return AdaptiveRuntime(graph, state_root=root / '.auren-network-runtime')\n",
            encoding="utf-8",
        )

        repository_root = Path(__file__).resolve().parents[1]
        environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith("AUREN_MCP_") and key != "AUREN_PROJECT_ROOT"
        }
        environment.update({
            "PYTHONPATH": os.pathsep.join(
                value for value in (str(self.root), str(repository_root), environment.get("PYTHONPATH", ""))
                if value
            ),
            "AUREN_PROJECT_ROOT": str(self.project_root),
            "AUREN_MCP_RUNTIME_FACTORY": "auren_network_runtime_factory:create_runtime",
            "AUREN_MCP_BIND_HOST": "127.0.0.1",
            "AUREN_MCP_PORT": str(self.port),
            "AUREN_MCP_RESOURCE_URL": "https://mcp.example.test/mcp",
            "AUREN_MCP_ALLOWED_HOSTS": "mcp.example.test",
            "AUREN_MCP_ALLOW_STATIC_TOKENS": "1",
            "AUREN_MCP_STATIC_TOKENS": json.dumps([{
                "token": self.token,
                "subject": "network-acceptance-operator",
                "scopes": ["auren:tasks:read"],
                "projects": [str(self.project_root)],
            }]),
            "AUREN_MCP_CONTROL_DB": str(self.root / "operator-control.sqlite3"),
            "AUREN_MCP_FORWARDED_ALLOW_IPS": "127.0.0.1,::1",
        })
        self.process = subprocess.Popen(
            [
                sys.executable, "-m", "portable.remote_mcp_server",
                "--project-root", str(self.project_root),
                "--runtime-factory", "auren_network_runtime_factory:create_runtime",
                "--host", "127.0.0.1", "--port", str(self.port),
            ],
            cwd=repository_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=self.stderr,
        )
        try:
            self._wait_ready()
        except BaseException:
            self.tearDown()
            raise

    def tearDown(self):
        process = getattr(self, "process", None)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        stderr = getattr(self, "stderr", None)
        if stderr is not None:
            stderr.close()
        temp = getattr(self, "temp", None)
        if temp is not None:
            temp.cleanup()

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _request(self, path: str, *, body=None, token: str | None = None, extra_headers=None):
        headers = {
            "Host": "mcp.example.test",
            "X-Forwarded-Proto": "https",
            "Accept": "application/json, text/event-stream",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        headers.update(extra_headers or {})
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers,
            method="POST" if body is not None else "GET",
        )
        with self.opener.open(request, timeout=4) as response:
            return response.status, response.headers, response.read()

    def _wait_ready(self):
        deadline = time.monotonic() + 20
        last_error = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.stderr.flush()
                self.stderr.seek(0)
                detail = self.stderr.read().decode("utf-8", errors="replace")[-4000:]
                self.fail(f"remote MCP server exited before readiness: {detail}")
            try:
                status, _, body = self._request("/readyz")
                if status == 200 and json.loads(body).get("status") == "ok":
                    return
            except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as exc:
                last_error = type(exc).__name__
            time.sleep(0.1)
        self.fail(f"remote MCP server did not become ready within 20 seconds ({last_error})")

    def test_live_http_readiness_oauth_challenge_and_mcp_tool_call(self):
        status, _, body = self._request("/healthz")
        self.assertEqual(200, status)
        self.assertEqual({"status": "ok"}, json.loads(body))

        status, _, body = self._request("/.well-known/oauth-protected-resource/mcp")
        self.assertEqual(200, status)
        metadata = json.loads(body)
        self.assertEqual("https://mcp.example.test/mcp", metadata["resource"])

        status, _, body = self._request("/mcp", body={
            "jsonrpc": "2.0", "id": "unauthorized", "method": "tools/call",
            "params": {"name": "auren.service_status", "arguments": {}},
        })
        self.assertEqual(200, status)
        unauthorized = json.loads(body)["result"]
        self.assertTrue(unauthorized["isError"])
        challenge = unauthorized["_meta"]["mcp/www_authenticate"][0]
        self.assertIn("resource_metadata=", challenge)

        status, headers, body = self._request("/mcp", token=self.token, body={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "auren-network-acceptance", "version": "1"},
            },
        })
        self.assertEqual(200, status)
        self.assertEqual("2025-11-25", json.loads(body)["result"]["protocolVersion"])
        self.assertEqual("2025-11-25", headers.get("mcp-protocol-version"))

        status, _, body = self._request(
            "/mcp", token=self.token,
            extra_headers={"MCP-Protocol-Version": "2025-11-25"},
            body={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        self.assertEqual(200, status)
        tools = {item["name"] for item in json.loads(body)["result"]["tools"]}
        self.assertIn("auren.service_status", tools)
        self.assertIn("auren.list_tasks", tools)
        self.assertNotIn("auren.submit_task", tools)

        status, _, body = self._request(
            "/mcp", token=self.token,
            extra_headers={"MCP-Protocol-Version": "2025-11-25"},
            body={
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "auren.service_status", "arguments": {}},
            },
        )
        self.assertEqual(200, status)
        response = json.loads(body)
        self.assertEqual("2.0", response["jsonrpc"])
        self.assertEqual(3, response["id"])
        self.assertIn("result", response)
        self.assertNotIn("error", response)


if __name__ == "__main__":
    unittest.main()
