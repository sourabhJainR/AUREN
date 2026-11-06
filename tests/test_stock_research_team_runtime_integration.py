from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from portable.adaptive_runtime import AdaptiveRuntime
from portable.agent_org_runtime import authorize_agent_assignment
from portable.agent_profiles import resolve_agent_profile, resolve_team_template
from portable.durable_approval import DurableApprovalStore
from portable.engineering_context_packet import build_context_packet
from portable.engineering_plan import EngineeringPlan, PlanTask
from portable.engineering_plan_recovery import EngineeringPlanRecoveryBridge
from portable.engineering_plan_store import EngineeringPlanStore
from portable.orchestration import Graph, Node, NodeKind
from portable.runtime_recovery import RuntimeExecutionGuard


WORK_KEY = "AUREN:issue-696:continuous-engineering"
SOURCE_SHA = "453b5e74c4d68c8091daf1334cbfeaf6a6ea8a12"


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _supervisor_handler(_payload):
    return {"success": True, "detail": "canonical stock-research team task accepted", "evidence": ["fixture-supervisor-complete"]}


def _assignment(task_id, run_id, member):
    profile = resolve_agent_profile(member["profile_id"], revision=member["profile_revision"])
    payload = {
        "schema_version": "auren-team-member-assignment-v1",
        "task_id": task_id,
        "run_id": run_id,
        "member_id": member["member_id"],
        "profile_id": profile.profile_id,
        "profile_revision": profile.revision,
        "principal_id": "fixture-principal-" + member["member_id"],
        "role_id": profile.role_id,
        "manager_role_id": profile.manager_role_id,
        "action_class": profile.action_classes[0],
        "scope_digest": _digest({"work_key": WORK_KEY, "member_id": member["member_id"]}),
        "provider_id": "fixture-provider",
        "model_id": "fixture-model",
        "provider_capabilities": list(profile.required_capabilities),
        "provider_preferences": list(profile.provider_preferences),
        "available_tools": list(profile.tool_preferences),
        "provider_readiness_digest": _digest({"provider": "fixture-provider", "profile": profile.digest}),
        "memory_namespace": profile.memory_namespace,
        "memory_isolated": True,
        "workspace_mode": profile.workspace_mode,
        "status": "passed",
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }
    readiness = {
        "profile_digest": member["profile_digest"],
        "provider_id": payload["provider_id"],
        "model_id": payload["model_id"],
        "provider_capabilities": sorted(payload["provider_capabilities"]),
        "provider_preferences": sorted(payload["provider_preferences"]),
        "available_tools": sorted(payload["available_tools"]),
        "provider_readiness_digest": payload["provider_readiness_digest"],
        "memory_namespace": payload["memory_namespace"],
        "memory_isolated": payload["memory_isolated"],
        "workspace_mode": payload["workspace_mode"],
    }
    payload["readiness_digest"] = _digest(readiness)
    payload["assignment_digest"] = _digest(payload)
    payload["signature"] = "fixture-only-not-a-host-signature"
    return payload


def _workspace(task_id, run_id, member, assignment):
    profile = resolve_agent_profile(member["profile_id"], revision=member["profile_revision"])
    payload = {
        "schema_version": "auren-team-member-workspace-v1",
        "task_id": task_id,
        "run_id": run_id,
        "member_id": member["member_id"],
        "profile_id": profile.profile_id,
        "profile_revision": profile.revision,
        "principal_id": assignment["principal_id"],
        "assignment_digest": assignment["assignment_digest"],
        "workspace_id": "fixture-workspace-" + member["member_id"],
        "sandbox_id": "fixture-sandbox-" + member["member_id"],
        "workspace_mode": profile.workspace_mode,
        "isolation_profile": "fixture-contract-only",
        "network_policy": "deny_by_default",
        "secret_policy": "no_inherited_secrets",
        "workspace_digest": _digest({"workspace": member["member_id"], "mode": profile.workspace_mode}),
        "status": "passed",
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }
    payload["evidence_digest"] = _digest(payload)
    payload["signature"] = "fixture-only-not-a-host-signature"
    return payload


class _RecordingBudgetEnforcer:
    def __init__(self):
        self.calls = []

    def execute(self, adapter_kind, adapter_name, request, invoke, **_kwargs):
        self.calls.append((adapter_kind, adapter_name, dict(request)))
        return invoke()


class StockResearchTeamRuntimeIntegrationTests(unittest.TestCase):
    def test_stock_research_team_runs_through_adaptive_runtime_and_keeps_release_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path = Path(temporary)
            enforcer = _RecordingBudgetEnforcer()
            runtime = AdaptiveRuntime(
                Graph([Node("team-runtime", NodeKind.DETERMINISTIC, lambda _state: {"ready": True})]),
                state_root=tmp_path,
                team_budget_enforcer_factory=lambda controller: enforcer,
            )
            controller = runtime.execution_controller
            controller.register_handler("stock-team-supervisor", _supervisor_handler, execution_class="read_only", agent_action_class="run_tests")

            state_root = tmp_path / "canonical"
            store = EngineeringPlanStore(state_root / "engineering-plans.sqlite3")
            recovery = EngineeringPlanRecoveryBridge(store=store, ledger=runtime.recovery_ledger)
            plan_task_id = "stock_research_team_delivery"
            plan = EngineeringPlan(
                run_id=WORK_KEY,
                goal="Exercise the stock-research delivery team through AUREN's canonical runtime",
                requirements=(
                    "Form the versioned stock-research delivery team.",
                    "Bind each persona to a host-authorized principal and a separate workspace receipt.",
                    "Dispatch members in dependency order through the shared team budget boundary.",
                    "Keep release blocked without independently verified approval evidence.",
                ),
                tasks=(
                    PlanTask(
                        task_id=plan_task_id,
                        title="Run the stock-research team integration contract",
                        acceptance_criteria=(
                            "All stock-research template members complete through AdaptiveRuntime.",
                            "Every member has a distinct principal, assignment and workspace binding.",
                            "The shared budget enforcer is passed to every worker dispatch.",
                            "A second dispatch call does not repeat completed member work.",
                            "Delivery readiness does not authorize release without independent approval.",
                        ),
                        rollback_plan=(
                            "Stop the fixture team dispatch, preserve the durable member and receipt records, "
                            "and keep all external provider and release gates blocked."
                        ),
                        risk="high",
                        owner_role_id="QA-01",
                        manager_role_id="EM-ENG-01",
                    ),
                ),
                source_revision=SOURCE_SHA,
                policy_digest="b" * 64,
                budget={"attempts": 1, "dispatches": 1},
            )
            run = recovery.start(plan)
            run = recovery.transition_task(WORK_KEY, plan_task_id, "ready", reason="bounded stock-team integration contract is prepared")
            run = recovery.transition_task(WORK_KEY, plan_task_id, "running", reason="canonical trusted dispatch is being exercised")

            guard = RuntimeExecutionGuard(max_steps=16, max_attempts=8, deadline_seconds=300)
            guard_policy_path = controller.root / ".auren" / "guard_policy.json"
            guard_policy_path.parent.mkdir(parents=True, exist_ok=True)
            guard_policy_path.write_text(
                json.dumps({"schema_version": "auren-guard-v1", "default_effect": "allow", "rules": []}),
                encoding="utf-8",
            )
            approval_store = DurableApprovalStore(controller)

            def assignment_resolver(context):
                return authorize_agent_assignment(
                    role_id="QA-01",
                    manager_id="EM-ENG-01",
                    action_class="run_tests",
                    scope=("stock-research-team",),
                    principal_id="fixture-supervisor-principal",
                    authorizer=lambda request: (
                        request.get("principal_id") == "fixture-supervisor-principal"
                        and request.get("role_id") == "QA-01"
                        and request.get("manager_id") == "EM-ENG-01"
                        and request.get("action_class") == "run_tests"
                        and request.get("scope") == ("stock-research-team",)
                        and request.get("work_key") == context["dispatch_key"]
                    ),
                    policy_path=guard_policy_path,
                    work_key=context["dispatch_key"],
                    task_payload=context["payload"],
                    deadline_seconds=context.get("deadline_seconds"),
                )

            runtime.configure_trusted_plan_dispatch(
                store=store,
                recovery_bridge=recovery,
                approval_store=approval_store,
                budget_resolver=lambda _run, _task, _handler: {
                    "budget_id": "stock-team:" + WORK_KEY,
                    "guard": guard,
                    "checkpoint_digest": guard.snapshot()["digest"],
                    "policy_digest": plan.policy_digest,
                },
                approval_resolver=lambda _run, _task, _handler: None,
                assignment_resolver=assignment_resolver,
            )
            packet = build_context_packet(
                plan=plan,
                run=run,
                task_id=plan_task_id,
                policy_constraints=(
                    "Use the canonical stock-research team template.",
                    "Treat fixture receipts as contract evidence only, not provider, licensing or production isolation proof.",
                    "Do not authorize release or learning promotion from this test.",
                ),
                budget_remaining={"attempts": 1, "dispatches": 1},
                next_safe_action="Dispatch the versioned stock-research team through AdaptiveRuntime.",
            )
            supervisor_task = runtime.dispatch_engineering_plan_task(
                run_id=WORK_KEY,
                task_id=plan_task_id,
                dispatch_key=WORK_KEY,
                handler="stock-team-supervisor",
                packet=packet,
                payload={"application": "TradingDYOR", "resource": "stock-research-team"},
                deadline_seconds=300,
            )
            controller.run_once(supervisor_task.id)
            assert controller.task(supervisor_task.id).state == "completed"

            members = runtime.register_team_template(
                supervisor_task.id, WORK_KEY, "stock_research_delivery", revision=1,
            )
            template = resolve_team_template("stock_research_delivery", revision=1)
            assert {item["member_id"] for item in members} == {item["member_id"] for item in template["members"]}
            assignments = {}
            for member in members:
                assignment = _assignment(supervisor_task.id, WORK_KEY, member)
                assignments[member["member_id"]] = assignment
                controller.bind_team_member_assignment(
                    supervisor_task.id, WORK_KEY, member["member_id"],
                    receipt=assignment,
                    verify_assignment=lambda record: record.get("signature") == "fixture-only-not-a-host-signature",
                )
                controller.bind_team_member_workspace(
                    supervisor_task.id, WORK_KEY, member["member_id"],
                    receipt=_workspace(supervisor_task.id, WORK_KEY, member, assignment),
                    verify_receipt=lambda record: (
                        record.get("signature") == "fixture-only-not-a-host-signature"
                        and record.get("network_policy") == "deny_by_default"
                        and record.get("secret_policy") == "no_inherited_secrets"
                    ),
                )

            dispatch_order = []

            def dispatch_member(envelope):
                member_id = envelope["member_id"]
                dispatch_order.append(member_id)
                assert envelope["workspace"]["workspace_id"] == "fixture-workspace-" + member_id
                assert envelope["workspace"]["network_policy"] == "deny_by_default"
                assert envelope["workspace"]["secret_policy"] == "no_inherited_secrets"
                assert envelope["team_budget_enforcer"] is enforcer
                operation = enforcer.execute(
                    "tool", "fixture-persona-worker",
                    {"member_id": member_id, "work_key": WORK_KEY},
                    lambda: {"member_id": member_id, "completed": True},
                )
                assert operation == {"member_id": member_id, "completed": True}
                profile = envelope["profile"]
                assignment = envelope["assignment"]
                workspace = envelope["workspace"]
                receipt = {
                    "schema_version": "auren-team-member-execution-v1",
                    "task_id": envelope["task_id"],
                    "run_id": envelope["run_id"],
                    "member_id": member_id,
                    "profile_id": profile["profile_id"],
                    "profile_revision": profile["revision"],
                    "principal_id": assignment["principal_id"],
                    "assignment_digest": assignment["assignment_digest"],
                    "workspace_id": workspace["workspace_id"],
                    "workspace_digest": workspace["workspace_digest"],
                    "provider_evidence_digest": assignment["provider_readiness_digest"],
                    "budget_evidence_digest": _digest({"work_key": WORK_KEY, "member_id": member_id}),
                    "status": "completed",
                    "output_digest": _digest(operation),
                    "issued_at": datetime.now(timezone.utc).isoformat(),
                }
                receipt["evidence_digest"] = _digest(receipt)
                receipt["signature"] = "fixture-only-not-a-host-signature"
                return receipt

            runtime.configure_team_persona_dispatch(
                dispatch_member=dispatch_member,
                verify_assignment_current=lambda record: record.get("signature") == "fixture-only-not-a-host-signature",
                verify_workspace_current=lambda record: (
                    record.get("signature") == "fixture-only-not-a-host-signature"
                    and record.get("network_policy") == "deny_by_default"
                    and record.get("secret_policy") == "no_inherited_secrets"
                ),
                verify_provider_readiness=lambda profile, assignment: (
                    set(profile["required_capabilities"]).issubset(set(assignment["provider_capabilities"]))
                    and set(profile["provider_preferences"]).intersection(set(assignment["provider_preferences"]))
                    and set(profile["tool_preferences"]).issubset(set(assignment["available_tools"]))
                    and assignment["provider_id"] == "fixture-provider"
                    and bool(assignment["provider_readiness_digest"])
                ),
                verify_execution_receipt=lambda record: record.get("signature") == "fixture-only-not-a-host-signature",
            )

            completed = runtime.dispatch_team_template(supervisor_task.id, WORK_KEY)
            assert [item["member_id"] for item in template["members"]] == dispatch_order
            assert all(item["state"] == "completed" for item in completed)
            assert len({item["assignment"]["principal_id"] for item in completed}) == len(completed)
            assert len({item["workspace"]["workspace_id"] for item in completed}) == len(completed)
            assert len(enforcer.calls) == len(template["members"])

            # A repeated supervisor invocation returns the durable terminal team without
            # repeating any worker or provider/tool operation.
            runtime.dispatch_team_template(supervisor_task.id, WORK_KEY)
            assert len(enforcer.calls) == len(template["members"])

            readiness = controller.team_delivery_release_readiness(supervisor_task.id, WORK_KEY)
            assert readiness["delivery_ready"] is True
            assert readiness["release_ready"] is False
            assert readiness["release_authorized"] is False
            assert "independent_approval_not_verified" in readiness["blocking_reasons"]
            assert all(
                readiness["member_states"][item["member_id"]] == "completed"
                for item in template["members"]
            )

            # Fixture callbacks are not production receipts. This test proves the
            # composition path and fail-closed release boundary, not provider, license,
            # host isolation, independent reviewer, deployment or release outcomes.
