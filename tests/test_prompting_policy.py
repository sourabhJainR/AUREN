#!/usr/bin/env python3
import unittest

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".ai-harness"))

from runtime.prompting_policy import assess_prompt, compose, quality_dict


class PromptingPolicyTests(unittest.TestCase):
    def test_complete_job_is_not_interviewed(self):
        prompt = """# Task\nImplement the requested API change.\n\n## Context\nExisting REST service and tests.\n\n## Why\nClients need the new field for compatibility.\n\n## Exit Criteria\nAPI and tests pass; no unrelated behavior changes.\n\n## Guardrails\nPreserve backward compatibility.\n\n## Response Contract\nReport changed files and verification evidence concisely.\n"""
        quality = assess_prompt(prompt)
        self.assertTrue(quality.complete)
        self.assertFalse(quality.needs_interview)

    def test_missing_why_or_done_requests_interview(self):
        quality = assess_prompt("## Task\nImplement feature X.\n\n## Context\nExisting service.")
        self.assertTrue(quality.needs_interview)

    def test_compose_adds_reason_done_and_concise_output_rules(self):
        result = compose("## Task\nFix the bug.\n\n## Context\nService and tests.")
        self.assertIn("## Why this matters", result)
        self.assertIn("## What done looks like", result)
        self.assertIn("## Response contract", result)
        self.assertIn("## Verification economy", result)
        self.assertIn("## Communication and data format", result)
        self.assertIn("Prefer TOON when the receiver supports it", result)
        self.assertIn("Keep JSON when an API", result)
        self.assertIn("A skipped check is not a pass", result)
        self.assertIn("CLARIFICATION_NEEDED", result)

    def test_quality_dict_is_serializable_shape(self):
        result = quality_dict("## Task\nDo it")
        self.assertEqual(set(result), {
            "has_task", "has_context", "has_why", "has_exit_criteria",
            "has_guardrails", "has_response_contract", "needs_interview", "complete",
        })


if __name__ == "__main__":
    unittest.main()
