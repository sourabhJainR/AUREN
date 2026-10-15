from __future__ import annotations

import unittest

from portable.crew_adapters import ApprovedToolRegistry, ProviderBackedCrewAdapter
from portable.production_crew_registry import ProductionCrewAdapterRegistry


def provider_response(prompt: str, timeout: int) -> str:
    return '{"tool_calls":[],"proposal":{"schema_version":"auren-crew-proposal-v1","recommendation":"review","artifacts":[],"evidence_refs":[],"uncertainty":[],"blockers":[],"acceptance_check_results":[]}}'


class ProductionCrewRegistryTests(unittest.TestCase):
    def make_adapter(self, crew_id="qa"):
        return ProviderBackedCrewAdapter(
            crew_id=crew_id, provider_id="provider", provider_version="2026-10-01",
            invoke_provider=provider_response, tools=ApprovedToolRegistry(),
            allowed_tools=(), timeout_seconds=5,
        )

    def test_requires_provider_backed_pinned_registration_and_freeze(self):
        registry = ProductionCrewAdapterRegistry(required_crew_ids=("qa",))
        registry.register(self.make_adapter())
        digest = registry.freeze()
        self.assertEqual(len(digest), 64)
        self.assertEqual(registry.get("qa").crew_id, "qa")
        with self.assertRaises(RuntimeError):
            registry.register(self.make_adapter("api"))

    def test_missing_required_adapter_fails_closed(self):
        registry = ProductionCrewAdapterRegistry(required_crew_ids=("qa", "api"))
        registry.register(self.make_adapter())
        with self.assertRaisesRegex(RuntimeError, "api"):
            registry.freeze()

    def test_duplicate_and_unregistered_dispatch_rejected(self):
        registry = ProductionCrewAdapterRegistry()
        registry.register(self.make_adapter())
        with self.assertRaisesRegex(ValueError, "duplicate"):
            registry.register(self.make_adapter())
        registry.freeze()
        with self.assertRaises(PermissionError):
            registry.get("api")


if __name__ == "__main__":
    unittest.main()
