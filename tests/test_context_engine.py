"""Tests for deterministic IO-aware context selection and session ownership."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / ".ai-harness" / "context_engine.py"
LAUNCHER = ROOT / ".ai-harness" / "run.py"


class ContextEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("context_engine", MODULE)
        cls.module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(cls.module)

    def test_context_respects_budget(self):
        result = self.module.select_context(
            "authentication export",
            "# Repository Context\n" + "- src/authentication/export/service.py\n" * 200,
            "lesson authentication export security\n" * 50,
            "review export should preserve authorization\n" * 50,
            budget_chars=2000,
        )
        total = sum(len(value) for key, value in result.items() if key in {"repository", "memory", "history"})
        self.assertLessEqual(total, 2000)

    def test_strategy_is_explicit(self):
        result = self.module.select_context("feature", "repo", "memory", "history", 1000)
        self.assertIn("stable-prefix", result["strategy"])

    def test_history_compaction_preserves_high_value_evidence(self):
        history = "\n".join(
            [
                "[execute] exploratory note about an unrelated module",
                "[execute] Evidence: tenant filter is applied in service.py:42",
                "[execute] Decision: preserve legacy null behavior",
                "[execute] noisy repeated model narration",
                "[validate] Verification: regression tests passed",
                "[review] Risk: downstream consumer may depend on ordering",
                "[review] Next: inspect the consumer before finalizing",
            ]
        )
        result = self.module.compact_history(history, "tenant filter legacy regression", 280)
        self.assertIn("Evidence:", result)
        self.assertIn("Verification:", result)
        self.assertLessEqual(len(result), 280)

    def test_context_metrics_report_approximate_tokens_and_preserved_failure_signals(self):
        history = "\n".join([
            "ERROR: provider timeout; traceback in adapter.py:42",
            "Security denied: write scope exceeded and blocked by policy",
            "Evidence: regression verification passed; acceptance criteria satisfied",
            *[f"routine progress note {index}" for index in range(60)],
        ])
        result = self.module.select_context(
            "provider timeout security regression",
            "repository files: portable/adapter.py\n" * 3,
            "memory: preserve authorization and error handling",
            history,
            budget_chars=4000,
        )
        metrics = result["history_compression"]
        self.assertIn("approx_input_tokens", metrics)
        self.assertIn("approx_output_tokens", metrics)
        self.assertIn("not provider token usage", metrics["token_estimate_method"])
        self.assertIn("error", metrics["retained_signals"])
        self.assertIn("timeout", metrics["retained_signals"])
        self.assertIn("security", metrics["retained_signals"])
        self.assertIn("denied", metrics["retained_signals"])
        self.assertEqual([], metrics["missed_signals"])
        self.assertLessEqual(metrics["output_chars"], metrics["input_chars"])

    def test_context_benchmark_records_cost_and_signal_metrics(self):
        report = self.module.benchmark_context_selection(
            "security timeout",
            "repo " * 2000,
            "memory " * 1000,
            "ERROR: timeout; security denied\n" + ("routine note\n" * 400),
            budget_chars=4000,
            provider_token_usage={"input_tokens": 123, "output_tokens": 45},
        )
        self.assertGreater(report["raw_chars"], report["bounded_chars"])
        self.assertGreaterEqual(report["latency_ms"], 0)
        self.assertEqual({"input_tokens": 123, "output_tokens": 45}, report["provider_token_usage"])
        self.assertIn("timeout", report["retained_signals"])
        self.assertIn("security", report["retained_signals"])
        self.assertIsInstance(report["missed_signals"], list)
        with self.assertRaisesRegex(ValueError, "token usage"):
            self.module.benchmark_context_selection(
                "task", "repo", "memory", "history",
                provider_token_usage={"input_tokens": True, "output_tokens": 1},
            )

    def test_empty_history_is_empty(self):
        self.assertEqual(self.module.compact_history("", "anything", 100), "")

    def test_launcher_exists_as_public_entrypoint(self):
        self.assertTrue(LAUNCHER.exists())

    def test_session_allocator_is_idempotent(self):
        spec = importlib.util.spec_from_file_location("run_launcher", LAUNCHER)
        launcher = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(launcher)
        expected = Path(tempfile.gettempdir()) / "ai-harness-test-session"
        original = launcher._original_make_run_dir
        try:
            launcher._session_dir = None
            launcher._original_make_run_dir = lambda: expected
            first = launcher.session_make_run_dir()
            second = launcher.session_make_run_dir()
            self.assertEqual(first, expected)
            self.assertEqual(first, second)
        finally:
            launcher._original_make_run_dir = original
            launcher._session_dir = None


if __name__ == "__main__":
    unittest.main()
