from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai-harness"))

from portable.crew_adapters import (
    ApprovedTool,
    ApprovedToolRegistry,
    ProviderBackedCrewAdapter,
    SPECIALIST_CREW_IDS,
    build_provider_backed_specialist_crew_adapters,
)
import runtime


class ProviderBackedCrewAdapterTests(unittest.TestCase):
    def proposal(self):
        return {
            "schema_version": "auren-crew-proposal-v1",
            "recommendation": "Inspect the endpoint contract.",
            "artifacts": [{"name": "api-contract", "content": "contract reviewed"}],
            "evidence_refs": ["provider:run-1"],
            "uncertainty": 0.1,
            "blockers": [],
            "acceptance_check_results": [{"check": "contract", "passed": True}],
        }

    def test_provider_response_uses_allowlisted_read_only_tools_and_strict_schema(self):
        tool_calls = []
        provider_calls = []

        def lookup(arguments):
            tool_calls.append(dict(arguments))
            return {"ticket": arguments["ticket"], "status": "open"}

        tools = ApprovedToolRegistry([
            ApprovedTool("lookup_ticket", lookup, "Read one ticket by ID."),
        ])
        response = json.dumps({
            "tool_calls": [{"name": "lookup_ticket", "arguments": {"ticket": "AUREN-438"}}],
            "proposal": self.proposal(),
        })

        def provider(prompt, timeout_seconds):
            provider_calls.append((prompt, timeout_seconds))
            return response

        adapter = ProviderBackedCrewAdapter(
            "backend_api", "host-provider", "model-2026-10",
            provider, tools, allowed_tools=("lookup_ticket",), timeout_seconds=15,
        )
        request = {
            "crew_id": "backend_api",
            "specialty": "backend API engineering",
            "task": "Review endpoint contract",
            "graph_digest": "graph-1",
            "crew_plan_digest": "plan-1",
            "secret": "must-not-cross-provider-boundary",
        }
        proposal = adapter.propose(request)
        self.assertEqual(proposal, self.proposal())
        self.assertEqual(tool_calls, [{"ticket": "AUREN-438"}])
        self.assertEqual(provider_calls[0][1], 15)
        self.assertNotIn("must-not-cross-provider-boundary", provider_calls[0][0])
        prompt_payload = json.loads(provider_calls[0][0].split("\n", 1)[1])
        self.assertEqual(prompt_payload["task_request"]["task"], "Review endpoint contract")
        self.assertEqual(prompt_payload["approved_tools"][0]["name"], "lookup_ticket")
        self.assertEqual(adapter.implementation_version, "host-provider@model-2026-10")
        self.assertEqual(len(adapter.configuration_digest), 64)

    def test_provider_response_rejects_duplicate_keys_and_unapproved_tools(self):
        tools = ApprovedToolRegistry([
            ApprovedTool("read", lambda _args: {"ok": True}, "Read-only test tool."),
        ])
        duplicate = ProviderBackedCrewAdapter(
            "backend_api", "provider", "v1",
            lambda _prompt, _timeout: '{"tool_calls":[],"tool_calls":[],"proposal":{}}',
            tools, allowed_tools=("read",),
        )
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            duplicate.propose({"crew_id": "backend_api", "task": "test"})

        bad_tool = ProviderBackedCrewAdapter(
            "backend_api", "provider", "v1",
            lambda _prompt, _timeout: json.dumps({
                "tool_calls": [{"name": "write", "arguments": {}}],
                "proposal": self.proposal(),
            }),
            tools, allowed_tools=("read",),
        )
        with self.assertRaisesRegex(PermissionError, "not approved"):
            bad_tool.propose({"crew_id": "backend_api", "task": "test"})

    def test_builder_requires_all_pinned_provider_and_tool_scope_registrations(self):
        tools = ApprovedToolRegistry()
        invokers = {crew: (lambda _prompt, _timeout: "{}") for crew in SPECIALIST_CREW_IDS}
        ids = {crew: "host-provider" for crew in SPECIALIST_CREW_IDS}
        versions = {crew: "pinned-v1" for crew in SPECIALIST_CREW_IDS}
        scopes = {crew: () for crew in SPECIALIST_CREW_IDS}
        adapters = build_provider_backed_specialist_crew_adapters(
            provider_invokers=invokers,
            provider_ids=ids,
            provider_versions=versions,
            tools=tools,
            allowed_tools_by_crew=scopes,
        )
        self.assertEqual(set(adapters), set(SPECIALIST_CREW_IDS))
        self.assertTrue(all(adapter.configuration_digest for adapter in adapters.values()))
        with self.assertRaisesRegex(ValueError, "missing specialist crew IDs"):
            build_provider_backed_specialist_crew_adapters(
                provider_invokers={},
                provider_ids=ids,
                provider_versions=versions,
                tools=tools,
                allowed_tools_by_crew=scopes,
            )

    def test_runtime_registration_wires_host_adapters_into_default_phase_team(self):
        runtime._CREW_ADAPTERS.clear()
        try:
            tools = ApprovedToolRegistry()
            invokers = {crew: (lambda _prompt, _timeout: "{}") for crew in SPECIALIST_CREW_IDS}
            ids = {crew: "host-provider" for crew in SPECIALIST_CREW_IDS}
            versions = {crew: "pinned-v1" for crew in SPECIALIST_CREW_IDS}
            scopes = {crew: () for crew in SPECIALIST_CREW_IDS}
            adapters = runtime.register_provider_backed_crew_adapters(
                provider_invokers=invokers,
                provider_ids=ids,
                provider_versions=versions,
                tools=tools,
                allowed_tools_by_crew=scopes,
                replace=True,
            )
            self.assertEqual(set(runtime._CREW_ADAPTERS), set(SPECIALIST_CREW_IDS))
            self.assertEqual(set(adapters), set(SPECIALIST_CREW_IDS))
        finally:
            runtime._CREW_ADAPTERS.clear()


if __name__ == "__main__":
    unittest.main()
