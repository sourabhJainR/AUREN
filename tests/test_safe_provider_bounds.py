from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai-harness"))

from safe_provider import _bounded_process_output, _provider_total_budget_seconds, _run_resilient
from portable.agent_execution_guard import AgentAssignmentScopeDenied, agent_assignment_scope


class SafeProviderBoundTests(unittest.TestCase):
    def run_command(self, code: str, **kwargs):
        return _bounded_process_output(
            [sys.executable, "-c", code],
            cwd=ROOT, env=dict(os.environ), output_sink=kwargs.pop("output_sink", lambda _chunk: None),
            **kwargs,
        )

    def test_assigned_scope_denies_direct_streaming_provider_before_popen(self):
        with agent_assignment_scope(("allowed.txt",)):
            with patch("safe_provider.subprocess.Popen") as popen:
                with self.assertRaisesRegex(AgentAssignmentScopeDenied, "assigned agent subprocess launch is blocked"):
                    self.run_command("print('must-not-run')", timeout_seconds=2, max_output_bytes=1024)
                popen.assert_not_called()

    def test_output_is_streamed_and_returned_within_limit(self):
        seen = []
        code, output = self.run_command(
            "print('bounded-output')", timeout_seconds=2, max_output_bytes=1024, output_sink=seen.append,
        )
        self.assertEqual(0, code)
        self.assertEqual("bounded-output\n", output)
        self.assertEqual(b"bounded-output\n", b"".join(seen))

    def test_output_overflow_terminates_process_and_fails_closed(self):
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, "byte limit"):
            self.run_command(
                "import sys,time; sys.stdout.write('x' * 1000000); sys.stdout.flush(); time.sleep(10)",
                timeout_seconds=2, max_output_bytes=1024,
            )
        self.assertLess(time.monotonic() - started, 2)

    def test_deadline_terminates_non_cooperative_process(self):
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "deadline"):
            self.run_command("import time; time.sleep(10)", timeout_seconds=0.2, max_output_bytes=1024)
        self.assertLess(time.monotonic() - started, 2)

    def test_parent_exit_does_not_leave_descendant_running(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "descendant-survived.txt"
            child_code = (
                "import pathlib,sys,time; time.sleep(0.7); "
                "pathlib.Path(sys.argv[1]).write_text('survived', encoding='utf-8')"
            )
            parent_code = (
                "import subprocess,sys; subprocess.Popen([sys.executable, '-c', "
                + repr(child_code) + ", " + repr(str(marker))
                + "], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
                "print('parent-complete')"
            )
            code, output = self.run_command(parent_code, timeout_seconds=3, max_output_bytes=1024)
            self.assertEqual(0, code)
            self.assertIn("parent-complete", output)
            time.sleep(0.9)
            self.assertFalse(marker.exists(), "provider descendants must not outlive a completed invocation")



    def test_inherited_supervisor_deadline_caps_provider_budget(self):
        with patch.dict(os.environ, {
            "HARNESS_PROVIDER_TOTAL_TIMEOUT_SECONDS": "30",
            "AUREN_DEADLINE_EPOCH": "103.5",
        }, clear=False):
            self.assertAlmostEqual(3.5, _provider_total_budget_seconds(now_epoch=100.0))
        with patch.dict(os.environ, {
            "HARNESS_PROVIDER_TOTAL_TIMEOUT_SECONDS": "30",
            "AUREN_DEADLINE_EPOCH": "99",
        }, clear=False):
            with self.assertRaisesRegex(TimeoutError, "already expired"):
                _provider_total_budget_seconds(now_epoch=100.0)

    def test_retry_attempts_share_one_total_deadline(self):
        class Feedback:
            def __init__(self):
                self.outcomes = []

            def observe(self, **values):
                self.outcomes.append(values["outcome"])

        feedback = Feedback()
        calls = []

        def transient_attempt(*args, **kwargs):
            calls.append(kwargs["timeout_seconds"])
            time.sleep(0.8)
            return 1, "connection reset"

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "HARNESS_PROVIDER_MAX_RETRIES": "3",
            "HARNESS_PROVIDER_RETRY_DELAY": "0.5",
            "HARNESS_PROVIDER_TIMEOUT_SECONDS": "5",
            "AUREN_HARNESS_PROVIDER_TIMEOUT_SECONDS": "5",
            "HARNESS_PROVIDER_TOTAL_TIMEOUT_SECONDS": "1",
            "AUREN_HARNESS_PROVIDER_TOTAL_TIMEOUT_SECONDS": "1",
            "HARNESS_PROVIDER_MAX_OUTPUT_BYTES": "1024",
            "AUREN_HARNESS_PROVIDER_MAX_OUTPUT_BYTES": "1024",
            "HARNESS_WORKSPACE": directory,
            "AUREN_DEADLINE_EPOCH": "",
        }, clear=False), patch("safe_provider._prepare_prompt", return_value=("bounded prompt", {"digest": "a" * 64})), patch(
            "safe_provider._bounded_process_output", side_effect=transient_attempt
        ):
            result = _run_resilient(["provider-cli"], "prompt", Path(directory), feedback)

        self.assertEqual(124, result)
        self.assertEqual(1, len(calls), "no second provider attempt may start after the shared budget expires")
        self.assertLessEqual(calls[0], 1.0)
        self.assertIn("timeout", feedback.outcomes)

    def test_invalid_process_limits_return_125_without_launching_provider(self):
        class RecordingFeedback:
            def __init__(self):
                self.events = []

            def observe(self, **event):
                self.events.append(event)

        cases = (
            ("HARNESS_PROVIDER_TIMEOUT_SECONDS", "not-a-number"),
            ("HARNESS_PROVIDER_MAX_OUTPUT_BYTES", "not-a-number"),
        )
        for name, value in cases:
            with self.subTest(setting=name), tempfile.TemporaryDirectory() as directory:
                feedback = RecordingFeedback()

                with patch.dict(os.environ, {
                    name: value,
                    f"AUREN_{name}": value,
                }, clear=False), patch("safe_provider._bounded_process_output") as invoke:
                    code = _run_resilient(
                        [sys.executable, "-c", "print('must-not-run')"],
                        "bounded provider configuration regression",
                        Path(directory),
                        feedback,
                    )

                self.assertEqual(125, code)
                invoke.assert_not_called()
                self.assertEqual("output_limit_or_invalid_config", feedback.events[-1]["outcome"])


if __name__ == "__main__":
    unittest.main()
