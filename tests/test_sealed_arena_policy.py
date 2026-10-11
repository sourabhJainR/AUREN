from __future__ import annotations
import json
import unittest
from portable.sealed_arena_policy import PolicyCaseInvocation, PolicyCaseOutput

class SealedArenaPolicyTests(unittest.TestCase):
    def test_invocation_excludes_evaluator_metadata(self):
        payload = json.loads(PolicyCaseInvocation("classify", {"text": "hello"}, {"locale": "en"}).to_bytes())
        self.assertEqual(set(payload), {"protocol_version", "task", "input", "environment"})
        self.assertNotIn("case_id", payload)
        self.assertNotIn("holdout", payload)
        self.assertNotIn("manifest", payload)

    def test_nested_evaluator_fields_are_rejected(self):
        for payload in ({"case_id": "x"}, {"input": {"oracle_answer": "gold"}}, {"env": [{"receipt_key": "secret"}]}):
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "evaluator-only"):
                PolicyCaseInvocation("task", payload, {})

    def test_policy_output_is_untrusted_and_content_addressed(self):
        output = PolicyCaseOutput.from_untrusted({"prediction": True})
        self.assertEqual(len(output.output_digest), 64)

    def test_policy_output_cannot_claim_evaluator_authority(self):
        with self.assertRaisesRegex(ValueError, "evaluator-only"):
            PolicyCaseOutput.from_untrusted({"receipt_key": "fake"})

    def test_answer_aliases_are_rejected_recursively(self):
        forbidden_payloads = (
            {"answer": "gold"},
            {"input": {"expected_answer": "gold"}},
            {"environment": [{"expected-output": "gold"}]},
            {"task": {"reference answer": "gold"}},
            {"nested": {"solution": "gold"}},
            {"nested": {"gold_label": "gold"}},
            {"nested": {"oracle_output": "gold"}},
        )
        for payload in forbidden_payloads:
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "evaluator-only"):
                PolicyCaseOutput.from_untrusted(payload)

if __name__ == "__main__": unittest.main()
