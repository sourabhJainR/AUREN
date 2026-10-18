"""HTTP and UI regression tests for the live control center."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen, Request

from portable.dashboard_server import _Handler, serve
from portable.engineering_dashboard import EngineeringDashboard
from portable.muxin_session_adapter import MuxinSessionAdapter
from http.server import ThreadingHTTPServer

class ControlCenterApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        dashboard = EngineeringDashboard(root)
        adapter = MuxinSessionAdapter(root, dashboard=dashboard, learning_reader=lambda: [])
        handler = type("TestHandler", (_Handler,), {
            "dashboard": dashboard, "control_center": adapter,
            "ui_root": Path(__file__).resolve().parents[1] / "dashboard",
            "control_center_path": Path(__file__).resolve().parents[1] / "site" / "control-center.html",
        })
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join, 2)

    def test_health_and_live_snapshot_contract(self):
        base = "http://127.0.0.1:%d" % self.server.server_port
        with urlopen(base + "/api/health", timeout=2) as response:
            self.assertEqual(200, response.status)
            self.assertEqual("ok", json.load(response)["status"])
        with urlopen(base + "/api/control-center", timeout=5) as response:
            payload = json.load(response)
        self.assertEqual(1, payload["schema_version"])
        self.assertIn("sessions", payload)
        self.assertIn("learnings", payload)
        self.assertIn("models", payload)
        self.assertIn("routing", payload)
        self.assertIn("resources", payload)
        self.assertEqual([], payload["learnings"])

    def test_route_api_rejects_invalid_payload_without_running_a_model(self):
        base = "http://127.0.0.1:%d" % self.server.server_port
        request = Request(base + "/api/route", data=b'{"task":""}',
                          headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(Exception) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(400, caught.exception.code)

    def test_route_api_rejects_untrusted_browser_origin(self):
        base = "http://127.0.0.1:%d" % self.server.server_port
        request = Request(
            base + "/api/route", data=b'{"task":"private task"}',
            headers={"Content-Type": "application/json", "Origin": "http://attacker.example"},
            method="POST",
        )
        with self.assertRaises(Exception) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(403, caught.exception.code)

    def test_learning_retrieval_failure_is_not_reported_as_empty(self):
        def unavailable():
            raise OSError("memory store unavailable")
        root = Path(self.temp.name)
        self.server.RequestHandlerClass.control_center = MuxinSessionAdapter(
            root, dashboard=EngineeringDashboard(root), learning_reader=unavailable
        )
        base = "http://127.0.0.1:%d" % self.server.server_port
        with urlopen(base + "/api/control-center", timeout=5) as response:
            payload = json.load(response)
        self.assertEqual("unavailable", payload["learning_status"])
        self.assertEqual("OSError", payload["learning_error"])
        self.assertEqual([], payload["learnings"])

    def test_server_refuses_non_loopback_bind(self):
        with self.assertRaises(ValueError):
            serve(project_root=self.temp.name, host="0.0.0.0", port=8765)

    def test_ui_contains_live_endpoint_and_no_fabricated_success_in_live_mapping(self):
        html = (Path(__file__).resolve().parents[1] / "site" / "control-center.html").read_text(encoding="utf-8")
        self.assertIn("/api/control-center", html)
        self.assertIn("applySnapshot", html)
        self.assertIn("no values are inferred as proof", html)
        self.assertIn("No learning attached", html)
        self.assertIn('"unverified"].includes(r.status)', html)
        self.assertIn("Canonical memory unavailable", html)
        self.assertIn('value="unverified"', html)

if __name__ == "__main__":
    unittest.main()
