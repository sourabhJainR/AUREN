from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai-harness"))

from portable.progress_journal import ProgressJournal
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

    def test_hung_provider_is_bounded_even_if_invoker_ignores_timeout(self):
        from threading import Event
        import time

        started = Event()
        release = Event()
        calls = []

        def hung_provider(prompt, timeout_seconds):
            calls.append(timeout_seconds)
            started.set()
            release.wait(2)
            return "{}"

        adapter = ProviderBackedCrewAdapter(
            "backend_api", "host-provider", "pinned-v1", hung_provider,
            ApprovedToolRegistry(), allowed_tools=(), timeout_seconds=1,
        )
        before = time.monotonic()
        try:
            with self.assertRaisesRegex(TimeoutError, "exceeded its bounded wait"):
                adapter.propose({"crew_id": "backend_api", "task": "test"})
            self.assertTrue(started.is_set())
            self.assertLess(time.monotonic() - before, 1.8)
            self.assertEqual(calls, [1])
        finally:
            release.set()


    def test_provider_lifecycle_is_written_to_canonical_journal_without_payload_leakage(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            journal = ProgressJournal(Path(temp) / "progress.jsonl")
            response = json.dumps({"tool_calls": [], "proposal": self.proposal()})
            adapter = ProviderBackedCrewAdapter(
                "backend_api", "host-provider", "pinned-v1",
                lambda _prompt, _timeout: response,
                ApprovedToolRegistry(), allowed_tools=(),
                progress_callback=journal.record_provider_event,
            )
            request = {"task_id": "supervisor-task-17", "crew_id": "backend_api",
                       "task": "safe fixture", "secret": "must-not-be-recorded"}
            adapter.propose(request)
            snapshot = journal.snapshot("supervisor-task-17")
            self.assertEqual(snapshot["event_count"], 2)
            self.assertEqual(snapshot["stage"], "provider:host-provider")
            self.assertEqual(snapshot["reported_state"], "running")
            raw = journal.path.read_text(encoding="utf-8")
            self.assertNotIn("must-not-be-recorded", raw)
            self.assertNotIn('"secret"', raw)
            self.assertNotIn("supervisor-task-17", json.loads(
                next(line for line in raw.splitlines() if '"provider_call_started"' in line)
            ).get("message", ""))

    def test_provider_timeout_is_durable_blocked_progress(self):
        import tempfile
        import time
        from threading import Event

        started, release = Event(), Event()
        with tempfile.TemporaryDirectory() as temp:
            journal = ProgressJournal(Path(temp) / "progress.jsonl")

            def hung_provider(_prompt, _timeout):
                started.set()
                release.wait(1)
                return json.dumps({"tool_calls": [], "proposal": self.proposal()})

            adapter = ProviderBackedCrewAdapter(
                "backend_api", "host-provider", "pinned-v1", hung_provider,
                ApprovedToolRegistry(), allowed_tools=(), timeout_seconds=1,
                progress_callback=journal.record_provider_event,
            )
            before = time.monotonic()
            try:
                with self.assertRaisesRegex(TimeoutError, "bounded wait"):
                    adapter.propose({"task_id": "timeout-task", "crew_id": "backend_api", "task": "safe"})
                self.assertTrue(started.is_set())
                self.assertLess(time.monotonic() - before, 1.8)
                snapshot = journal.snapshot("timeout-task")
                self.assertEqual(snapshot["reported_state"], "blocked")
                self.assertIn("Reconcile before retry", snapshot["message"])
                self.assertEqual(snapshot["event_count"], 2)
            finally:
                release.set()

    def test_provider_progress_requires_supervisor_task_identity(self):
        adapter = ProviderBackedCrewAdapter(
            "backend_api", "host-provider", "pinned-v1",
            lambda _prompt, _timeout: json.dumps({"tool_calls": [], "proposal": self.proposal()}),
            ApprovedToolRegistry(), allowed_tools=(),
            progress_callback=lambda _event: None,
        )
        with self.assertRaisesRegex(ValueError, "supervisor-issued task_id"):
            adapter.propose({"crew_id": "backend_api", "task": "safe"})

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
