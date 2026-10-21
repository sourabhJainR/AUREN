"""Public SDK contract tests."""
from __future__ import annotations

import tempfile
import unittest

from portable.sdk import AurenClient, SDK_API_VERSION


def sdk_test_handler(payload):
    return {"accepted": True, "value": payload.get("value")}


class AurenClientTests(unittest.TestCase):
    def test_api_version_and_canonical_durable_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = AurenClient(tmp, dashboard_enabled=False)
            try:
                self.assertEqual("v1", SDK_API_VERSION)
                client.register_handler(
                    "sdk-test",
                    sdk_test_handler,
                    execution_class="read_only",
                )
                task = client.submit("sdk-test", {"value": "bounded"})
                persisted = client.task(task.id)
                self.assertIsNotNone(persisted)
                self.assertEqual(task.id, persisted.id)
                self.assertEqual("pending", persisted.state)
                progress = client.progress(task.id)
                self.assertEqual("queued", progress.state)
            finally:
                client.close()

    def test_unknown_execution_class_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = AurenClient(tmp, dashboard_enabled=False)
            try:
                with self.assertRaisesRegex(ValueError, "execution_class"):
                    client.register_handler(
                        "sdk-test", sdk_test_handler, execution_class="untrusted"
                    )
            finally:
                client.close()

    def test_dashboard_is_optional_and_observational(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = AurenClient(tmp, dashboard_enabled=False)
            try:
                with self.assertRaisesRegex(RuntimeError, "disabled"):
                    client.dashboard_snapshot()
            finally:
                client.close()

    def test_memory_facade_uses_shared_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = AurenClient(tmp, dashboard_enabled=False)
            try:
                client.remember("repo-a", "semantic", "keep verified decisions")
                records = client.search_memory("repo-a", "verified decisions")
                self.assertTrue(records)
                self.assertIn("verified decisions", records[0].text)
            finally:
                client.close()


if __name__ == "__main__":
    unittest.main()
