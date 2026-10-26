import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai-harness"))

from extension_policy import ExtensionInvocationDenied, evaluate_invocation, invoke_extension


class ExtensionPolicyTests(unittest.TestCase):
    def metadata(self, **overrides):
        value = {
            "available": True,
            "trust_class": "trusted",
            "requires_approval": True,
            "permissions": ["read-repository"],
            "output_limit_chars": 24,
        }
        value.update(overrides)
        return value

    def test_unverified_extension_is_denied_before_dispatch(self):
        called = []
        with self.assertRaises(ExtensionInvocationDenied):
            invoke_extension(
                self.metadata(trust_class="external-unverified"),
                lambda: called.append("called"),
                trust_granted=True,
                approval_granted=True,
                requested_permissions=["read-repository"],
                artifact_fresh=True,
            )
        self.assertEqual(called, [])

    def test_missing_approval_is_denied_before_dispatch(self):
        called = []
        with self.assertRaises(ExtensionInvocationDenied):
            invoke_extension(
                self.metadata(),
                lambda: called.append("called"),
                trust_granted=True,
                requested_permissions=["read-repository"],
                artifact_fresh=True,
            )
        self.assertEqual(called, [])

    def test_permission_escalation_and_stale_artifact_are_denied(self):
        decision = evaluate_invocation(
            self.metadata(),
            trust_granted=True,
            approval_granted=True,
            requested_permissions=["write-repository"],
            artifact_fresh=False,
        )
        self.assertFalse(decision["allowed"])
        self.assertIn("permission_scope_exceeded", decision["reasons"])
        self.assertIn("freshness_not_verified", decision["reasons"])

    def test_valid_invocation_output_is_bounded(self):
        result = invoke_extension(
            self.metadata(output_limit_chars=12, requires_approval=False),
            lambda: "0123456789abcdefghijklmnop",
            trust_granted=True,
            requested_permissions=["read-repository"],
            artifact_fresh=True,
        )
        self.assertLessEqual(len(result), 12)

    def test_output_limit_must_be_positive(self):
        decision = evaluate_invocation(self.metadata(), requested_output_limit_chars=0)
        self.assertFalse(decision["allowed"])
        self.assertIn("invalid_requested_output_limit", decision["reasons"])


if __name__ == "__main__":
    unittest.main()
