"""Tests for the versioned, data-only extension contract."""
import unittest

from portable.extension_contract import (
    ExtensionContractError,
    ExtensionManifest,
    ExtensionRegistry,
)


class ExtensionContractTests(unittest.TestCase):
    def manifest(self, **overrides):
        values = {
            "name": "local_graph",
            "version": "1.2.3",
            "capabilities": ("ast", "graph"),
            "permissions": (),
            "trust": "native",
        }
        values.update(overrides)
        return ExtensionManifest(**values)

    def test_manifest_digest_is_deterministic_and_permissions_are_explicit(self):
        first = self.manifest()
        second = ExtensionManifest.from_mapping(first.as_dict())
        self.assertEqual(first.digest, second.digest)
        self.assertEqual((), first.permissions)
        self.assertEqual("auren-extension-v1", first.contract_version)

    def test_rejects_wildcards_invalid_versions_and_unknown_fields(self):
        with self.assertRaisesRegex(ExtensionContractError, "forbidden wildcard"):
            self.manifest(capabilities=("*",))
        with self.assertRaisesRegex(ExtensionContractError, "semantic version"):
            self.manifest(version="latest")
        with self.assertRaisesRegex(ExtensionContractError, "unknown manifest fields"):
            ExtensionManifest.from_mapping({
                "name": "provider", "version": "1.0.0", "capabilities": ["graph"],
                "run_shell": True,
            })

    def test_rejects_unbounded_timeout_and_output(self):
        with self.assertRaisesRegex(ExtensionContractError, "timeout_seconds"):
            self.manifest(timeout_seconds=float("inf"))
        with self.assertRaisesRegex(ExtensionContractError, "output_limit_bytes"):
            self.manifest(output_limit_bytes=1_048_577)

    def test_registration_is_idempotent_but_conflicts_fail_closed(self):
        registry = ExtensionRegistry()
        first = self.manifest()
        self.assertEqual(first.digest, registry.register(first))
        self.assertEqual(first.digest, registry.register(first))
        with self.assertRaisesRegex(ExtensionContractError, "different digest"):
            registry.register(self.manifest(version="1.2.4"))

    def test_capability_selection_is_deterministic_and_respects_external_policy(self):
        registry = ExtensionRegistry()
        registry.register(self.manifest(name="external_graph", trust="external"))
        registry.register(self.manifest(name="local_graph", trust="native"))
        registry.register(self.manifest(name="degraded_graph", health="degraded"))
        self.assertEqual(
            ["local_graph", "external_graph"],
            [item.name for item in registry.providers_for("graph")],
        )
        self.assertEqual(
            ["local_graph"],
            [item.name for item in registry.providers_for("graph", allow_external=False)],
        )
        self.assertEqual(3, len(registry.snapshot()))

    def test_manifest_claim_does_not_execute_or_authorize_provider(self):
        registry = ExtensionRegistry()
        registry.register(self.manifest(permissions=("filesystem.read",)))
        self.assertEqual(("filesystem.read",), registry.get("local_graph").permissions)
        self.assertFalse(hasattr(registry.get("local_graph"), "invoke"))


if __name__ == "__main__":
    unittest.main()
