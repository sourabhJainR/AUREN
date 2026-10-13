import json
import tempfile
import unittest
from pathlib import Path

from portable.adaptive_runtime import AdaptiveRuntime
from portable.agent_capabilities import AutomationScheduler
from portable.chat_capability import CAPABILITY_NAME, descriptor, invoke
from portable.orchestration import Graph


class ChatCapabilityTests(unittest.TestCase):
    def test_descriptor_has_stable_tool_contract(self):
        value = descriptor()
        self.assertEqual(value.name, CAPABILITY_NAME)
        self.assertEqual(value.name, "adaptive_runtime.trigger")
        self.assertEqual(value.input_schema["type"], "object")
        self.assertEqual(value.input_schema["required"], ["task", "project_root"])
        self.assertEqual(value.input_schema["properties"]["priority"]["enum"], ["high", "normal", "low"])
        self.assertEqual(value.output_schema["required"], ["trigger_id", "status", "accepted_at"])

    def test_invoke_returns_durable_receipt_without_waiting_for_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            scheduler = AutomationScheduler(Path(directory) / "runtime.sqlite")
            runtime = AdaptiveRuntime(Graph([]), automation_scheduler=scheduler)
            receipt = invoke(
                runtime,
                {
                    "task": "queue this work",
                    "project_root": directory,
                    "priority": "high",
                    "context": {"source": "chat"},
                    "event_id": "chat-capability-1",
                },
            )
            self.assertEqual(set(receipt), {"trigger_id", "status", "accepted_at"})
            status = runtime.trigger_runtime.get(receipt["trigger_id"])
            self.assertIsNotNone(status)
            self.assertEqual(status.priority, 0)
            # The trigger worker may claim the durable event before this read; both
            # pending and claimed mean invoke returned before task completion.
            self.assertIn(status.status, {"pending", "claimed"})
            scheduler.close()

    def test_invalid_arguments_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            scheduler = AutomationScheduler(Path(directory) / "runtime.sqlite")
            runtime = AdaptiveRuntime(Graph([]), automation_scheduler=scheduler)
            with self.assertRaisesRegex(ValueError, "priority"):
                invoke(runtime, {"task": "x", "project_root": directory, "priority": "urgent"})
            with self.assertRaisesRegex(ValueError, "max_attempts"):
                invoke(runtime, {"task": "x", "project_root": directory, "max_attempts": 0})
            scheduler.close()

    def test_descriptor_is_json_serializable(self):
        value = descriptor()
        encoded = json.dumps({"name": value.name, "input": value.input_schema, "output": value.output_schema})
        self.assertIn("adaptive_runtime.trigger", encoded)


if __name__ == "__main__":
    unittest.main()
