from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai-harness"))

from portable.progress_journal import ProgressJournal
from portable.agent_execution_guard import agent_assignment_scope
from portable.agent_profiles import resolve_agent_profile
from portable.execution_controller import ExecutionController
from portable.team_budget_adapters import TeamBudgetAdapterDenied, TeamBudgetAdapterEnforcer
from portable.crew_adapters import (
    ApprovedTool,
    ApprovedToolRegistry,
    ProviderBackedCrewAdapter,
    SPECIALIST_CREW_IDS,
    build_provider_backed_specialist_crew_adapters,
)
import runtime


def provider_budget_test_handler(payload):
    """Importable durable handler for provider shared-budget acceptance tests."""
    return {"ok": True}


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

    def _budgeted_provider_setup(self, root, *, settlement_verifier=None):
        controller = ExecutionController(
            root,
            team_budget_settlement_verifier=settlement_verifier or (lambda receipt: receipt["usage_evidence"]),
        )
        controller.register_handler(
            "provider-budget-test", provider_budget_test_handler,
            execution_class="read_only",
        )
        task = controller.submit(
            "provider-budget-test", {"work": "provider budget acceptance"},
            goal="verify provider adapter shared budget",
        )
        members = []
        for member_id, profile_id in (
            ("implementer", "backend_implementer"),
            ("qa", "qa_reviewer"),
        ):
            profile = resolve_agent_profile(profile_id, revision=1)
            members.append({
                "member_id": member_id, "profile_id": profile_id,
                "profile_revision": 1, "profile_digest": profile.digest,
            })
        controller.register_team_members(task.id, "provider-budget-run", members)
        for member_id in ("implementer", "qa"):
            controller.transition_team_member(
                task.id, "provider-budget-run", member_id,
                expected_state="pending", new_state="assigned",
            )
        controller.transition_team_member(
            task.id, "provider-budget-run", "implementer",
            expected_state="assigned", new_state="running",
        )

        def context_resolver(request):
            metadata = request["request"]
            return {
                "task_id": task.id,
                "run_id": "provider-budget-run",
                "member_id": request["principal_id"],
                "principal_id": request["principal_id"],
                "operation_key": f"provider:{task.id}:{metadata['crew_id']}",
                "tokens": 100, "tool_calls": 0, "wall_seconds": 30,
            }

        enforcer = TeamBudgetAdapterEnforcer(
            controller,
            context_resolver=context_resolver,
            usage_evidence_resolver=lambda _request: {
                "tokens": 25, "tool_calls": 0, "wall_seconds": 2,
            },
        )
        return controller, task.id, enforcer

    def test_provider_adapter_settles_usage_through_shared_team_ledger(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            controller, task_id, enforcer = self._budgeted_provider_setup(Path(temp))
            calls = []
            response = json.dumps({"tool_calls": [], "proposal": self.proposal()})
            tools = ApprovedToolRegistry([], team_budget_enforcer=enforcer)
            adapter = ProviderBackedCrewAdapter(
                "backend_api", "host-provider", "pinned-v1",
                lambda prompt, timeout: calls.append((prompt, timeout)) or response,
                tools, allowed_tools=(),
            )
            with agent_assignment_scope(
                ("src/app.py",), role_id="BE-01", principal_id="implementer",
            ):
                result = adapter.propose({
                    "task_id": task_id, "crew_id": "backend_api",
                    "task": "Review the provider integration",
                })
            self.assertEqual(result, self.proposal())
            self.assertEqual(len(calls), 1)
            budget = controller.team_budget(task_id, "provider-budget-run")
            self.assertEqual(budget["charged_tokens"], 25)
            self.assertEqual(budget["charged_wall_seconds"], 2)
            self.assertEqual(budget["reserved_tokens"], 0)
            self.assertEqual(controller.unresolved_team_budget_operations(task_id, "provider-budget-run"), ())

    def test_assigned_provider_without_shared_budget_is_denied_before_provider_call(self):
        calls = []
        adapter = ProviderBackedCrewAdapter(
            "backend_api", "host-provider", "pinned-v1",
            lambda _prompt, _timeout: calls.append("called") or "{}",
            ApprovedToolRegistry(), allowed_tools=(),
        )
        with agent_assignment_scope(
            ("src/app.py",), role_id="BE-01", principal_id="implementer",
        ):
            with self.assertRaisesRegex(PermissionError, "requires the host-configured shared team-budget enforcer"):
                adapter.propose({"task_id": "task-1", "crew_id": "backend_api", "task": "safe"})
        self.assertEqual(calls, [])

    def test_provider_usage_settlement_failure_keeps_shared_reservation_held(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            controller, task_id, enforcer = self._budgeted_provider_setup(
                Path(temp), settlement_verifier=lambda _receipt: False,
            )
            calls = []
            response = json.dumps({"tool_calls": [], "proposal": self.proposal()})
            adapter = ProviderBackedCrewAdapter(
                "backend_api", "host-provider", "pinned-v1",
                lambda _prompt, _timeout: calls.append("called") or response,
                ApprovedToolRegistry([], team_budget_enforcer=enforcer), allowed_tools=(),
            )
            with agent_assignment_scope(
                ("src/app.py",), role_id="BE-01", principal_id="implementer",
            ):
                with self.assertRaisesRegex(TeamBudgetAdapterDenied, "reservation remains held"):
                    adapter.propose({
                        "task_id": task_id, "crew_id": "backend_api",
                        "task": "Review the provider integration",
                    })
            self.assertEqual(calls, ["called"])
            unresolved = controller.unresolved_team_budget_operations(task_id, "provider-budget-run")
            self.assertEqual(
                [item["operation_key"] for item in unresolved],
                [f"provider:{task_id}:backend_api"],
            )

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
            "evidence": {"intent_digest": "intent-safe", "task_id": "internal-run-42"},
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
        self.assertEqual(prompt_payload["task_request"]["evidence"], {"intent_digest": "intent-safe"})
        self.assertNotIn("internal-run-42", provider_calls[0][0])
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
                release.wait(2)
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

    def test_shared_budget_enforcer_is_injected_and_bound_into_adapter_identity(self):
        class Enforcer:
            def execute(self, _kind, _name, _request, invoke, **_kwargs):
                return invoke()

        enforcer = Enforcer()
        plain = ProviderBackedCrewAdapter(
            "backend_api", "provider", "v1",
            lambda _prompt, _timeout: "{}",
            ApprovedToolRegistry(), allowed_tools=(),
        )
        budgeted_tools = ApprovedToolRegistry([], team_budget_enforcer=enforcer)
        budgeted = ProviderBackedCrewAdapter(
            "backend_api", "provider", "v1",
            lambda _prompt, _timeout: "{}",
            budgeted_tools, allowed_tools=(),
        )
        self.assertIs(budgeted._team_budget_enforcer, enforcer)
        self.assertNotEqual(plain.configuration_digest, budgeted.configuration_digest)

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

    def test_supervised_registration_uses_controller_owned_progress_journal(self):
        import tempfile
        from types import SimpleNamespace

        runtime._CREW_ADAPTERS.clear()
        try:
            with tempfile.TemporaryDirectory() as temp:
                journal = ProgressJournal(Path(temp) / "progress.jsonl")
                controller = SimpleNamespace(progress_journal=journal)
                tools = ApprovedToolRegistry()
                invokers = {crew: (lambda _prompt, _timeout: "{}") for crew in SPECIALIST_CREW_IDS}
                ids = {crew: "host-provider" for crew in SPECIALIST_CREW_IDS}
                versions = {crew: "pinned-v1" for crew in SPECIALIST_CREW_IDS}
                scopes = {crew: () for crew in SPECIALIST_CREW_IDS}
                adapters = runtime.register_supervised_provider_backed_crew_adapters(
                    controller,
                    provider_invokers=invokers,
                    provider_ids=ids,
                    provider_versions=versions,
                    tools=tools,
                    allowed_tools_by_crew=scopes,
                    replace=True,
                )
                self.assertEqual(set(adapters), set(SPECIALIST_CREW_IDS))
                for adapter in adapters.values():
                    self.assertIs(adapter._progress_callback.__self__, journal)
                    self.assertIs(
                        adapter._progress_callback.__func__,
                        journal.record_provider_event.__func__,
                    )
                with self.assertRaisesRegex(ValueError, "owns provider progress wiring"):
                    runtime.register_supervised_provider_backed_crew_adapters(
                        controller,
                        provider_invokers=invokers,
                        provider_ids=ids,
                        provider_versions=versions,
                        tools=tools,
                        allowed_tools_by_crew=scopes,
                        progress_callbacks_by_crew={crew: (lambda _event: None) for crew in SPECIALIST_CREW_IDS},
                        replace=True,
                    )
        finally:
            runtime._CREW_ADAPTERS.clear()

    def test_runtime_registration_wires_canonical_journal_to_every_specialist_crew(self):
        import tempfile

        runtime._CREW_ADAPTERS.clear()
        try:
            with tempfile.TemporaryDirectory() as temp:
                journal = ProgressJournal(Path(temp) / "progress.jsonl")
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
                    progress_journal=journal,
                    replace=True,
                )
                self.assertEqual(set(adapters), set(SPECIALIST_CREW_IDS))
                for crew in SPECIALIST_CREW_IDS:
                    self.assertIs(adapters[crew]._progress_callback.__self__, journal)
                    self.assertIs(adapters[crew]._progress_callback.__func__,
                                  journal.record_provider_event.__func__)
        finally:
            runtime._CREW_ADAPTERS.clear()

    def test_runtime_registration_wires_host_adapters_into_default_phase_team(self):
        runtime._CREW_ADAPTERS.clear()
        try:
            tools = ApprovedToolRegistry()
            invokers = {crew: (lambda _prompt, _timeout: "{}") for crew in SPECIALIST_CREW_IDS}
            ids = {crew: "host-provider" for crew in SPECIALIST_CREW_IDS}
            versions = {crew: "pinned-v1" for crew in SPECIALIST_CREW_IDS}
            scopes = {crew: () for crew in SPECIALIST_CREW_IDS}
            callbacks = {crew: (lambda _event: None) for crew in SPECIALIST_CREW_IDS}
            adapters = runtime.register_provider_backed_crew_adapters(
                provider_invokers=invokers,
                provider_ids=ids,
                provider_versions=versions,
                tools=tools,
                allowed_tools_by_crew=scopes,
                progress_callbacks_by_crew=callbacks,
                replace=True,
            )
            self.assertEqual(set(runtime._CREW_ADAPTERS), set(SPECIALIST_CREW_IDS))
            self.assertEqual(set(adapters), set(SPECIALIST_CREW_IDS))
            self.assertTrue(all(adapters[crew]._progress_callback is callbacks[crew]
                                for crew in SPECIALIST_CREW_IDS))
        finally:
            runtime._CREW_ADAPTERS.clear()


    def test_runtime_registers_all_six_software_team_personas_with_pinned_host_providers(self):
        from portable.crew_adapters import SOFTWARE_TEAM_PERSONA_IDS

        runtime._CREW_ADAPTERS.clear()
        try:
            tools = ApprovedToolRegistry()
            invokers = {persona: (lambda _prompt, _timeout: "{}") for persona in SOFTWARE_TEAM_PERSONA_IDS}
            provider_ids = {persona: "host-provider" for persona in SOFTWARE_TEAM_PERSONA_IDS}
            provider_versions = {persona: "pinned-model-v1" for persona in SOFTWARE_TEAM_PERSONA_IDS}
            scopes = {persona: () for persona in SOFTWARE_TEAM_PERSONA_IDS}
            callbacks = {persona: (lambda _event: None) for persona in SOFTWARE_TEAM_PERSONA_IDS}
            adapters = runtime.register_software_team_persona_adapters(
                provider_invokers=invokers,
                provider_ids=provider_ids,
                provider_versions=provider_versions,
                tools=tools,
                allowed_tools_by_persona=scopes,
                progress_callbacks_by_persona=callbacks,
                replace=True,
            )
            self.assertEqual(set(adapters), set(SOFTWARE_TEAM_PERSONA_IDS))
            self.assertEqual(set(runtime._CREW_ADAPTERS), set(SOFTWARE_TEAM_PERSONA_IDS))
            for persona, adapter in adapters.items():
                self.assertEqual(adapter.crew_id, persona)
                self.assertEqual(adapter.provider_id, "host-provider")
                self.assertEqual(adapter.provider_version, "pinned-model-v1")
                self.assertEqual(adapter._progress_callback, callbacks[persona])
                self.assertEqual(len(adapter.configuration_digest), 64)
        finally:
            runtime._CREW_ADAPTERS.clear()


    def test_registered_personas_are_consumed_by_the_real_phase_team_factory(self):
        from portable.crew_adapters import SOFTWARE_TEAM_PERSONA_IDS
        from runtime.graph_agent_team import AgentSpec, GraphAgentTeam

        runtime._CREW_ADAPTERS.clear()
        try:
            tools = ApprovedToolRegistry()
            invokers = {persona: (lambda _prompt, _timeout: "{}") for persona in SOFTWARE_TEAM_PERSONA_IDS}
            provider_ids = {persona: "host-provider" for persona in SOFTWARE_TEAM_PERSONA_IDS}
            provider_versions = {persona: "pinned-v1" for persona in SOFTWARE_TEAM_PERSONA_IDS}
            scopes = {persona: () for persona in SOFTWARE_TEAM_PERSONA_IDS}
            runtime.register_software_team_persona_adapters(
                provider_invokers=invokers,
                provider_ids=provider_ids,
                provider_versions=provider_versions,
                tools=tools,
                allowed_tools_by_persona=scopes,
                replace=True,
            )
            team = runtime._phase_team(
                {"risk": "critical", "task": "CEO CTO engineering manager solution architect release operations research evaluation"},
                "execute",
                (AgentSpec, GraphAgentTeam),
            )
            self.assertEqual(set(team.crew_adapters), set(SOFTWARE_TEAM_PERSONA_IDS))
            self.assertTrue(team.require_crew_adapters)
        finally:
            runtime._CREW_ADAPTERS.clear()


    def test_software_team_registration_is_all_or_nothing_and_supervised(self):
        import tempfile
        from types import SimpleNamespace
        from portable.crew_adapters import SOFTWARE_TEAM_PERSONA_IDS

        runtime._CREW_ADAPTERS.clear()
        try:
            tools = ApprovedToolRegistry()
            invokers = {persona: (lambda _prompt, _timeout: "{}") for persona in SOFTWARE_TEAM_PERSONA_IDS}
            provider_ids = {persona: "host-provider" for persona in SOFTWARE_TEAM_PERSONA_IDS}
            provider_versions = {persona: "pinned-v1" for persona in SOFTWARE_TEAM_PERSONA_IDS}
            scopes = {persona: () for persona in SOFTWARE_TEAM_PERSONA_IDS}
            with self.assertRaisesRegex(ValueError, "missing software-team persona IDs"):
                runtime.register_software_team_persona_adapters(
                    provider_invokers={key: value for key, value in invokers.items() if key != SOFTWARE_TEAM_PERSONA_IDS[0]},
                    provider_ids=provider_ids, provider_versions=provider_versions,
                    tools=tools, allowed_tools_by_persona=scopes, replace=True,
                )
            self.assertEqual(runtime._CREW_ADAPTERS, {})
            with self.assertRaisesRegex(TypeError, "canonical ProgressJournal"):
                runtime.register_supervised_software_team_persona_adapters(
                    SimpleNamespace(), provider_invokers=invokers,
                    provider_ids=provider_ids, provider_versions=provider_versions,
                    tools=tools, allowed_tools_by_persona=scopes, replace=True,
                )
            with tempfile.TemporaryDirectory() as temp:
                journal = ProgressJournal(Path(temp) / "progress.jsonl")
                controller = SimpleNamespace(progress_journal=journal)
                adapters = runtime.register_supervised_software_team_persona_adapters(
                    controller, provider_invokers=invokers,
                    provider_ids=provider_ids, provider_versions=provider_versions,
                    tools=tools, allowed_tools_by_persona=scopes, replace=True,
                )
                self.assertEqual(set(adapters), set(SOFTWARE_TEAM_PERSONA_IDS))
                for adapter in adapters.values():
                    self.assertIs(adapter._progress_callback.__self__, journal)
                    self.assertIs(adapter._progress_callback.__func__,
                                  journal.record_provider_event.__func__)
                with self.assertRaisesRegex(ValueError, "owns persona progress wiring"):
                    runtime.register_supervised_software_team_persona_adapters(
                        controller, provider_invokers=invokers,
                        provider_ids=provider_ids, provider_versions=provider_versions,
                        tools=tools, allowed_tools_by_persona=scopes,
                        progress_callbacks_by_persona={persona: (lambda _event: None) for persona in SOFTWARE_TEAM_PERSONA_IDS},
                        replace=True,
                    )
        finally:
            runtime._CREW_ADAPTERS.clear()


if __name__ == "__main__":
    unittest.main()
