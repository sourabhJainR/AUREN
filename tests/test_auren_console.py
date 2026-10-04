import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from portable.auren_console import (
    _serve_once,
    collect_snapshot,
    render_dashboard,
    render_status,
)


class TestConsoleSnapshot(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / ".auren"

    def tearDown(self):
        self.temp.cleanup()

    def test_empty_home_is_healthy_enough_to_render(self):
        snapshot = collect_snapshot(self.home)
        self.assertEqual(snapshot.installation["status"], "not_installed")
        self.assertEqual(snapshot.runs["count"], 0)
        self.assertEqual(snapshot.learning["status"], "unknown")
        self.assertIn("Overview", render_dashboard(snapshot))

    def test_secret_like_trace_metadata_is_redacted(self):
        trace_dir = self.home / "observability"
        trace_dir.mkdir(parents=True)
        (trace_dir / "traces.jsonl").write_text(
            json.dumps(
                {
                    "trace_id": "t1",
                    "status": "ok",
                    "metadata": {"api_key": "secret", "provider": "codex"},
                    "spans": [],
                    "scores": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        snapshot = collect_snapshot(self.home)
        self.assertEqual(snapshot.runs["recent"][0]["metadata"]["api_key"], "[REDACTED]")
        self.assertEqual(snapshot.runs["recent"][0]["metadata"]["provider"], "codex")

    def test_malformed_sources_do_not_break_snapshot(self):
        self.home.mkdir(parents=True)
        (self.home / "active.json").write_text("{broken", encoding="utf-8")
        (self.home / "observability").mkdir()
        (self.home / "observability" / "traces.jsonl").write_text("not-json\n", encoding="utf-8")
        snapshot = collect_snapshot(self.home)
        self.assertEqual(snapshot.installation["status"], "unknown")
        self.assertEqual(snapshot.runs["status"], "unknown")

    def test_status_json_has_stable_top_level_keys(self):
        payload = json.loads(render_status(collect_snapshot(self.home), as_json=True))
        self.assertEqual(
            set(payload),
            {"generated_at", "installation", "runs", "learning", "recovery", "health", "capabilities"},
        )

    def test_dashboard_contains_human_facing_sections(self):
        html = render_dashboard(collect_snapshot(self.home))
        self.assertIn("AUREN Console", html)
        self.assertIn("Overview", html)
        self.assertIn("Runs", html)
        self.assertIn("Learning", html)
        self.assertIn("Recovery", html)


class TestConsoleHTTP(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / ".auren"
        self.server, self.url = _serve_once("127.0.0.1", 0, lambda: collect_snapshot(self.home))
        import threading

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.temp.cleanup()

    def test_server_binds_loopback(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertTrue(self.url.startswith("http://127.0.0.1:"))

    def test_dashboard_and_api_are_read_only_get_surfaces(self):
        with urlopen(self.url, timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("text/html", response.headers["Content-Type"])
        with urlopen(self.url + "api/status", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
            self.assertIn("health", payload)
        with self.assertRaises(HTTPError) as ctx:
            request = Request(self.url, method="POST")
            urlopen(request, timeout=2)
        self.assertEqual(ctx.exception.code, 405)
