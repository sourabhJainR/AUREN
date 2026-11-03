from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from portable.agent_execution_guard import agent_assignment_scope
from portable.agent_profiles import resolve_agent_profile
from portable.execution_controller import ExecutionController
from portable.team_budget_adapters import TeamBudgetAdapterDenied, TeamBudgetAdapterEnforcer
from portable.vibe_coding_host import MCPStdioToolAdapter


def _handler(payload):
    return {"success": True, "detail": "done"}


class _StubMCPAdapter(MCPStdioToolAdapter):
    def __init__(self, *, enforcer=None):
        super().__init__(
            argv=("python", "-c", "pass"),
            tool_name="read_quote",
            team_budget_enforcer=enforcer,
        )
        self.calls = 0

    def _call_untraced(self, request):
        self.calls += 1
        return {"content": [{"type": "text", "text": "quote"}]}


def _team(controller: ExecutionController, root: Path):
    controller.register_handler("team-test", _handler, execution_class="read_only")
    task = controller.submit("team-test", {"work": "research"}, goal="team budget adapter acceptance")
    members = []
    for member_id, profile_id in (
        ("implementer", "backend_implementer"),
        ("qa", "qa_reviewer"),
    ):
        profile = resolve_agent_profile(profile_id, revision=1)
        members.append({
            "member_id": member_id,
            "profile_id": profile_id,
            "profile_revision": 1,
            "profile_digest": profile.digest,
        })
    controller.register_team_members(task.id, "run-acceptance", members)
    for member_id in ("implementer", "qa"):
        controller.transition_team_member(
            task.id, "run-acceptance", member_id,
            expected_state="pending", new_state="assigned",
        )
    controller.transition_team_member(
        task.id, "run-acceptance", "implementer",
        expected_state="assigned", new_state="running",
    )
    return task.id


def _enforcer(controller, task_id, *, key_prefix="adapter"):
    sequence = {"value": 0}

    def resolve_context(request):
        sequence["value"] += 1
        principal = request["principal_id"]
        return {
            "task_id": task_id,
            "run_id": "run-acceptance",
            "member_id": principal,
            "principal_id": principal,
            "operation_key": f"{key_prefix}:{sequence['value']}",
            "tokens": 100,
            "tool_calls": 1,
            "wall_seconds": 30,
        }

    def usage_evidence(_request):
        return {"tokens": 35, "tool_calls": 1, "wall_seconds": 2}

    return TeamBudgetAdapterEnforcer(
        controller,
        context_resolver=resolve_context,
        usage_evidence_resolver=usage_evidence,
    )


def test_provider_and_mcp_tool_calls_share_one_durable_budget(tmp_path: Path):
    controller = ExecutionController(
        tmp_path,
        team_budget_settlement_verifier=lambda receipt: receipt["usage_evidence"],
    )
    task_id = _team(controller, tmp_path)
    enforcer = _enforcer(controller, task_id)

    provider_started = []
    with agent_assignment_scope(
        ("src/app.py",), role_id="BE-01", principal_id="implementer",
    ):
        provider_result = enforcer.execute(
            "provider", "market-data-provider", {"symbol": "MTSI"},
            lambda: (
                provider_started.append(
                    controller.unresolved_team_budget_operations(task_id, "run-acceptance")
                ) or {"status": "ok"}
            ),
        )
        assert provider_result == {"status": "ok"}
        tool = _StubMCPAdapter(enforcer=enforcer)
        tool_result = tool({"symbol": "MTSI"})
        assert tool_result["content"][0]["text"] == "quote"
        assert tool.calls == 1

    assert provider_started and provider_started[0][0]["state"] == "reserved"
    budget = controller.team_budget(task_id, "run-acceptance")
    assert budget["charged_tokens"] == 70
    assert budget["charged_tool_calls"] == 2
    assert budget["charged_wall_seconds"] == 4
    assert budget["reserved_tokens"] == 0
    assert controller.unresolved_team_budget_operations(task_id, "run-acceptance") == ()


def test_adapter_replay_cannot_reinvoke_an_unresolved_operation(tmp_path: Path):
    controller = ExecutionController(
        tmp_path,
        team_budget_settlement_verifier=lambda receipt: receipt["usage_evidence"],
    )
    task = _team(controller, tmp_path)
    enforcer = TeamBudgetAdapterEnforcer(
        controller,
        context_resolver=lambda request: {
            "task_id": task,
            "run_id": "run-acceptance",
            "member_id": request["principal_id"],
            "principal_id": request["principal_id"],
            "operation_key": "same-operation",
            "tokens": 100,
            "tool_calls": 1,
            "wall_seconds": 30,
        },
        usage_evidence_resolver=lambda _request: {
            "tokens": 1, "tool_calls": 1, "wall_seconds": 1,
        },
    )
    calls = []
    with agent_assignment_scope(
        ("src/app.py",), role_id="BE-01", principal_id="implementer",
    ):
        enforcer.execute("provider", "provider-a", {}, lambda: calls.append("first") or {"ok": True})
        with pytest.raises(TeamBudgetAdapterDenied, match="shared team budget denied adapter dispatch"):
            enforcer.execute("provider", "provider-a", {}, lambda: calls.append("replay") or {"ok": True})
    assert calls == ["first"]


def test_assigned_mcp_call_without_budget_mediation_is_denied_before_launch(tmp_path: Path):
    adapter = _StubMCPAdapter()
    with agent_assignment_scope(
        ("src/app.py",), role_id="BE-01", principal_id="implementer",
    ):
        with pytest.raises(PermissionError, match="requires the host-configured shared team-budget enforcer"):
            adapter({"symbol": "MTSI"})
    assert adapter.calls == 0


def test_usage_settlement_failure_keeps_reservation_and_blocks_success(tmp_path: Path):
    controller = ExecutionController(
        tmp_path,
        team_budget_settlement_verifier=lambda _receipt: False,
    )
    task = _team(controller, tmp_path)
    enforcer = TeamBudgetAdapterEnforcer(
        controller,
        context_resolver=lambda request: {
            "task_id": task,
            "run_id": "run-acceptance",
            "member_id": request["principal_id"],
            "principal_id": request["principal_id"],
            "operation_key": "unverified-usage",
            "tokens": 100,
            "tool_calls": 1,
            "wall_seconds": 30,
        },
        usage_evidence_resolver=lambda _request: {
            "tokens": 1, "tool_calls": 1, "wall_seconds": 1,
        },
    )
    calls = []
    with agent_assignment_scope(
        ("src/app.py",), role_id="BE-01", principal_id="implementer",
    ):
        with pytest.raises(TeamBudgetAdapterDenied, match="reservation remains held"):
            enforcer.execute("tool", "untrusted-tool", {}, lambda: calls.append("called") or {"ok": True})
    assert calls == ["called"]
    unresolved = controller.unresolved_team_budget_operations(task, "run-acceptance")
    assert [item["operation_key"] for item in unresolved] == ["unverified-usage"]


def test_release_readiness_blocks_unknown_provider_outcome(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    task_id = _team(controller, tmp_path)
    controller.reserve_team_budget(
        task_id, "run-acceptance", "implementer", "unknown-provider-result",
        tokens=100, tool_calls=1, wall_seconds=30,
    )
    controller.transition_team_member(
        task_id, "run-acceptance", "implementer",
        expected_state="running", new_state="completed",
    )
    controller.transition_team_member(
        task_id, "run-acceptance", "qa",
        expected_state="assigned", new_state="running",
    )
    controller.transition_team_member(
        task_id, "run-acceptance", "qa",
        expected_state="running", new_state="completed",
    )
    controller.run_once(task_id)
    readiness = controller.team_delivery_release_readiness(task_id, "run-acceptance")
    assert readiness["delivery_ready"] is False
    assert readiness["release_ready"] is False
    assert "team_budget_operations_unresolved" in readiness["blocking_reasons"]
    assert readiness["unresolved_operations"] == ["unknown-provider-result"]


def test_delivery_readiness_requires_completed_team_and_verified_independent_approval(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    task_id = _team(controller, tmp_path)
    controller.transition_team_member(
        task_id, "run-acceptance", "implementer",
        expected_state="running", new_state="completed",
    )
    controller.transition_team_member(
        task_id, "run-acceptance", "qa",
        expected_state="assigned", new_state="running",
    )
    controller.transition_team_member(
        task_id, "run-acceptance", "qa",
        expected_state="running", new_state="completed",
    )
    controller.run_once(task_id)
    blocked = controller.team_delivery_release_readiness(task_id, "run-acceptance")
    assert blocked["delivery_ready"] is True
    assert blocked["release_ready"] is False
    assert blocked["release_authorized"] is False
    assert "independent_approval_not_verified" in blocked["blocking_reasons"]

    task = controller.task(task_id)
    payload = dict(task.payload)
    payload["auren_dispatch"] = {
        "approval_evidence": {"reviewer_principal_ids": ["independent-reviewer"]},
        "assignment_evidence": {"principal_id": "implementer"},
    }
    with sqlite3.connect(controller.db_path) as db:
        db.execute("UPDATE tasks SET payload=? WHERE id=?", (
            json.dumps(payload, sort_keys=True, separators=(",", ":")), task_id,
        ))
    controller._trusted_plan_approval_verifier = lambda _metadata, _evidence: True
    accepted = controller.team_delivery_release_readiness(task_id, "run-acceptance")
    assert accepted["delivery_ready"] is True
    assert accepted["release_ready"] is True
    assert accepted["release_authorized"] is False
