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
            "artifact_fresh": True,
            "manifest_valid": True,
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

    def test_invalid_manifest_is_denied_before_dispatch(self):
        called = []
        with self.assertRaises(ExtensionInvocationDenied):
            invoke_extension(
                self.metadata(manifest_valid=False),
                lambda: called.append("called"),
                trust_granted=True,
                approval_granted=True,
                requested_permissions=["read-repository"],
                artifact_fresh=True,
            )
        self.assertEqual([], called)
        decision = evaluate_invocation(self.metadata(manifest_valid=False))
        self.assertIn("extension_manifest_invalid", decision["reasons"])

    def test_malformed_permission_scope_is_denied_before_dispatch(self):
        called = []
        with self.assertRaises(ExtensionInvocationDenied):
            invoke_extension(
                self.metadata(permissions=[{"write": True}]),
                lambda: called.append("called"),
                trust_granted=True,
                approval_granted=True,
                requested_permissions=["read-repository"],
                artifact_fresh=True,
            )
        self.assertEqual([], called)

    def test_output_limit_must_be_positive(self):
        decision = evaluate_invocation(self.metadata(), requested_output_limit_chars=0)
        self.assertFalse(decision["allowed"])
        self.assertIn("invalid_requested_output_limit", decision["reasons"])



    def test_missing_policy_and_unscoped_provider_are_denied_before_dispatch(self):
        called = []
        with self.assertRaises(ExtensionInvocationDenied):
            invoke_extension(
                None,
                lambda: called.append("called"),
                trust_granted=True,
                approval_granted=True,
                requested_permissions=["read-repository"],
                artifact_fresh=True,
                provider_id="provider-a",
                require_provider_scope=True,
            )
        self.assertEqual([], called)

        decision = evaluate_invocation(
            self.metadata(provider_ids=["provider-b"]),
            trust_granted=True,
            approval_granted=True,
            requested_permissions=["read-repository"],
            artifact_fresh=True,
            provider_id="provider-a",
            require_provider_scope=True,
        )
        self.assertFalse(decision["allowed"])
        self.assertIn("provider_scope_not_authorized", decision["reasons"])

    def test_artifact_freshness_must_match_manifest_not_only_caller_claim(self):
        decision = evaluate_invocation(
            self.metadata(artifact_fresh=False),
            trust_granted=True,
            approval_granted=True,
            requested_permissions=["read-repository"],
            artifact_fresh=True,
        )
        self.assertFalse(decision["allowed"])
        self.assertIn("freshness_not_verified", decision["reasons"])


    def test_direct_tool_registry_denies_missing_extension_policy_before_handler(self):
        from portable.agency_agent_capabilities import AllowlistedToolPolicy, ToolRegistry, ToolSpec

        called = []
        registry = ToolRegistry([
            ToolSpec(
                "optional-extension", "test", "execution",
                handler=lambda: called.append("called") or "unsafe",
                is_extension=True,
            )
        ])
        with self.assertRaises(ExtensionInvocationDenied):
            registry.execute(
                "optional-extension", {}, AllowlistedToolPolicy(allowed_tools=frozenset({"optional-extension"}))
            )
        self.assertEqual([], called)

    def test_direct_tool_registry_enforces_provider_scope_and_output_limit(self):
        from portable.agency_agent_capabilities import AllowlistedToolPolicy, ToolRegistry, ToolSpec

        metadata = {
            "available": True, "manifest_valid": True, "trust_class": "trusted",
            "requires_approval": False, "permissions": ["read-repository"],
            "provider_ids": ["provider-a"], "artifact_fresh": True, "output_limit_chars": 6,
        }
        tool = ToolSpec(
            "extension-tool", "test", "execution", handler=lambda: "0123456789",
            is_extension=True, extension_metadata=metadata, extension_trust_granted=True,
            extension_permissions=("read-repository",), extension_artifact_fresh=True,
            extension_provider_id="provider-a",
        )
        registry = ToolRegistry([tool])
        policy = AllowlistedToolPolicy(allowed_tools=frozenset({"extension-tool"}))
        self.assertLessEqual(len(registry.execute("extension-tool", {}, policy)), 6)
        denied = ToolSpec(
            **{**tool.__dict__, "extension_provider_id": "provider-b"}
        )
        with self.assertRaisesRegex(PermissionError, "provider_scope_not_authorized"):
            ToolRegistry([denied]).execute("extension-tool", {}, policy)


    def test_side_effect_and_external_data_policy_cannot_disable_approval(self):
        for metadata in (
            self.metadata(requires_approval=False, side_effects=["write-repository"]),
            self.metadata(requires_approval=False, privacy_class="external"),
        ):
            with self.subTest(metadata=metadata):
                decision = evaluate_invocation(
                    metadata,
                    trust_granted=True,
                    requested_permissions=["read-repository"],
                    artifact_fresh=True,
                )
                self.assertFalse(decision["allowed"])
                self.assertTrue(
                    {"side_effect_approval_required", "external_data_approval_required"} & set(decision["reasons"])
                )

if __name__ == "__main__":
    unittest.main()
