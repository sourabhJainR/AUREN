"""HTTP and UI regression tests for the live control center."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen

from portable.dashboard_server import _Handler
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

    def test_ui_contains_live_endpoint_and_no_fabricated_success_in_live_mapping(self):
        html = (Path(__file__).resolve().parents[1] / "site" / "control-center.html").read_text(encoding="utf-8")
        self.assertIn("/api/control-center", html)
        self.assertIn("applySnapshot", html)
        self.assertIn("No values are inferred as proof", html)
        self.assertIn("No learning attached", html)

if __name__ == "__main__":
    unittest.main()
