"""Regression tests for default harness task-aware memory routing."""
from dataclasses import dataclass
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai-harness"))

from runtime import _build_task_context_prompt


@dataclass
class RoutingReceipt:
    task_type: str
    query_digest: str
    memory_ids: tuple[str, ...]
    memory_omitted: int
    budget_chars: int


class DefaultHarnessTaskContextRoutingTests(unittest.TestCase):
    def test_default_prompt_builder_uses_supervisor_identity_and_redacted_receipt(self):
        observed = {}

        def router(task_id, task, context):
            observed.update(task_id=task_id, task=task, context=context)
            return {
                "recall": SimpleNamespace(text="[MEMORY id=m1]rollback policy[/MEMORY]"),
                "receipt": RoutingReceipt("coding", "digest-only", ("m1",), 1, 4200),
            }

        prompt = _build_task_context_prompt(
            "base phase prompt",
            router=router,
            task_id="durable-run-17",
            task="implement task-aware memory routing",
        )

        self.assertEqual(observed["task_id"], "durable-run-17")
        self.assertEqual(observed["task"], "implement task-aware memory routing")
        self.assertEqual(observed["context"], {})
        self.assertIn("rollback policy", prompt)
        self.assertIn('"query_digest": "digest-only"', prompt)
        self.assertNotIn("implement task-aware memory routing", prompt.split("Task routing receipt", 1)[-1])

    def test_missing_receipt_fails_before_agent_prompt_is_returned(self):
        with self.assertRaisesRegex(ValueError, "structured routing receipt"):
            _build_task_context_prompt(
                "base",
                router=lambda *_: {"recall": SimpleNamespace(text="bounded memory")},
                task_id="durable-run-18",
                task="review",
            )

    def test_missing_supervisor_identity_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "supervisor-issued task ID"):
            _build_task_context_prompt(
                "base",
                router=lambda *_: {},
                task_id=" ",
                task="review",
            )


if __name__ == "__main__":
    unittest.main()
