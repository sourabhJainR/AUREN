from pathlib import Path
import os
import sqlite3
import subprocess
from threading import Event
import sys

from portable.execution_controller import ExecutionController


def durable_handler(payload):
    return {"success": True, "detail": "done", "evidence": [payload["id"]]}


def test_handler_registration_submit_and_resume(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller.register_handler("tests.durable_handler", durable_handler, execution_class="read_only")
    task = controller.submit("tests.durable_handler", {"id": "e1"})
    score = controller.run_once(task.id)
    assert score is not None
    assert controller.task(task.id).state == "completed"


def test_session_mapping_survives_new_controller(tmp_path: Path):
    first = ExecutionController(tmp_path)
    work_id = first.work_unit_for_session("session-1", "long task")
    second = ExecutionController(tmp_path)
    assert second.work_unit_for_session("session-1", "long task") == work_id


def ci_gate_handler(payload):
    # Deliberately return task-controlled data to prove it is not trusted as CI evidence.
    return payload["result"]


def trusted_ci_success(_policy):
    return {
        "head_sha": "trusted-head",
        "status": "completed",
        "conclusion": "success",
        "passed_checks": ["tests", "lint"],
        "skipped_checks": [],
    }


def trusted_ci_stale(_policy):
    return {
        "head_sha": "old-head",
        "status": "completed",
        "conclusion": "success",
        "passed_checks": ["tests", "lint"],
        "skipped_checks": [],
    }


def trusted_ci_skipped(_policy):
    return {
        "head_sha": "trusted-head",
        "status": "completed",
        "conclusion": "success",
        "passed_checks": ["tests"],
        "skipped_checks": ["lint"],
    }


def trusted_ci_missing(_policy):
    return None


def register_protected_handler(controller, provider=trusted_ci_success):
    controller.register_handler("tests.ci_provider", provider, execution_class="context_provider")
    controller.register_handler(
        "tests.ci_gate_handler",
        ci_gate_handler,
        ci_gate_policy={
            "expected_head_sha": "trusted-head",
            "required_checks": ["tests", "lint"],
            "evidence_handler": "tests.ci_provider",
        },
        execution_class="ci_gated",
    )


def test_task_payload_cannot_disable_gate_or_forge_ci_evidence(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    register_protected_handler(controller, trusted_ci_stale)
    task = controller.submit(
        "tests.ci_gate_handler",
        {
            "require_ci_gate": False,
            "expected_head_sha": "attacker-selected-head",
            "required_checks": [],
            "result": {
                "success": True,
                "ci_gate": {
                    "head_sha": "trusted-head",
                    "status": "completed",
                    "conclusion": "success",
                    "passed_checks": ["tests", "lint"],
                    "skipped_checks": [],
                },
            },
        },
    )
    score = controller.run_once(task.id)
    assert score is not None
    assert controller.task(task.id).state == "failed"


def test_missing_trusted_ci_evidence_fails_even_if_task_claims_success(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    register_protected_handler(controller, trusted_ci_missing)
    task = controller.submit(
        "tests.ci_gate_handler",
        {
            "require_ci_gate": False,
            "result": {
                "success": True,
                "ci_gate": {
                    "head_sha": "trusted-head",
                    "status": "completed",
                    "conclusion": "success",
                    "passed_checks": ["tests", "lint"],
                    "skipped_checks": [],
                },
            },
        },
    )
    controller.run_once(task.id)
    assert controller.task(task.id).state == "failed"


def test_trusted_ci_provider_rejects_skipped_required_check(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    register_protected_handler(controller, trusted_ci_skipped)
    task = controller.submit("tests.ci_gate_handler", {"result": {"success": True}})
    controller.run_once(task.id)
    assert controller.task(task.id).state == "failed"


def test_ci_gate_uses_trusted_sha_and_required_checks_not_task_overrides(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    register_protected_handler(controller)
    task = controller.submit(
        "tests.ci_gate_handler",
        {
            "expected_head_sha": "wrong-head",
            "required_checks": ["only-one-check"],
            "result": {
                "success": True,
                "ci_gate": {
                    "head_sha": "wrong-head",
                    "status": "completed",
                    "conclusion": "success",
                    "passed_checks": ["only-one-check"],
                    "skipped_checks": [],
                },
            },
        },
    )
    controller.run_once(task.id)
    assert controller.task(task.id).state == "completed"


def test_reregistering_handler_without_policy_preserves_existing_gate(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    register_protected_handler(controller, trusted_ci_stale)
    controller.register_handler("tests.ci_gate_handler", ci_gate_handler, execution_class="ci_gated")
    task = controller.submit(
        "tests.ci_gate_handler",
        {"require_ci_gate": False, "result": {"success": True}},
    )
    controller.run_once(task.id)
    assert controller.task(task.id).state == "failed"


def test_reregistering_handler_with_empty_policy_preserves_existing_gate(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    register_protected_handler(controller, trusted_ci_stale)
    controller.register_handler("tests.ci_gate_handler", ci_gate_handler, ci_gate_policy={}, execution_class="ci_gated")
    task = controller.submit(
        "tests.ci_gate_handler",
        {"require_ci_gate": False, "result": {"success": True}},
    )
    controller.run_once(task.id)
    assert controller.task(task.id).state == "failed"


def test_ci_gate_policy_requires_expected_sha_checks_and_provider(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller.register_handler("tests.ci_provider", trusted_ci_success, execution_class="context_provider")

    invalid_policies = [
        (
            {"required_checks": ["tests"], "evidence_handler": "tests.ci_provider"},
            "expected_head_sha",
        ),
        (
            {
                "expected_head_sha": "trusted-head",
                "required_checks": [],
                "evidence_handler": "tests.ci_provider",
            },
            "required_checks",
        ),
        (
            {"expected_head_sha": "trusted-head", "required_checks": ["tests"]},
            "evidence_handler",
        ),
    ]
    for policy, expected_message in invalid_policies:
        try:
            controller.register_handler(
                "tests.ci_gate_handler", ci_gate_handler, ci_gate_policy=policy, execution_class="ci_gated",
            )
        except ValueError as error:
            assert expected_message in str(error)
        else:
            raise AssertionError(f"invalid CI policy should reject {expected_message}")


def trusted_missing_promotion_context(_context):
    return {
        "expected_repository": "owner/project",
        "expected_head": "b" * 40,
        "decision_record": None,
        "authorized_approvers": [],
        "known_evidence_ids": [],
    }


def trusted_malformed_promotion_context(_context):
    return {
        "expected_repository": "owner/project",
        "expected_head": "b" * 40,
        "decision_record": {"schema_version": "1.0"},
        "authorized_approvers": "not-a-trusted-approver-list",
        "known_evidence_ids": [],
    }


def protected_side_effect_handler(payload):
    Path(payload["side_effect_path"]).write_text("side-effect", encoding="utf-8")
    return {"success": True}

def trusted_ci_for_promotion(_policy):
    return {
        "head_sha": "b" * 40,
        "status": "completed",
        "conclusion": "success",
        "passed_checks": ["tests"],
        "skipped_checks": [],
    }


def test_promotion_gate_rejects_missing_trusted_record_before_handler_side_effect(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller.register_handler("tests.promotion_context", trusted_missing_promotion_context, execution_class="context_provider")
    controller.register_handler("tests.promotion_ci", trusted_ci_for_promotion, execution_class="context_provider")
    controller.register_handler(
        "tests.merge_handler",
        protected_side_effect_handler,
        promotion_gate_policy={"context_handler": "tests.promotion_context"},
        ci_gate_policy={"expected_head_sha": "b" * 40, "required_checks": ["tests"], "evidence_handler": "tests.promotion_ci"},
        execution_class="consequential",
    )
    side_effect_path = tmp_path / "merge-ran.txt"
    task = controller.submit(
        "tests.merge_handler",
        {"side_effect_path": str(side_effect_path), "decision_record": {"self_authorized": True}},
    )

    controller.run_once(task.id)

    assert controller.task(task.id).state == "failed"
    assert not side_effect_path.exists()


def test_promotion_gate_rejects_malformed_trusted_context_before_handler_side_effect(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller.register_handler("tests.promotion_context", trusted_malformed_promotion_context, execution_class="context_provider")
    controller.register_handler("tests.promotion_ci", trusted_ci_for_promotion, execution_class="context_provider")
    controller.register_handler(
        "tests.publish_handler",
        protected_side_effect_handler,
        promotion_gate_policy={"context_handler": "tests.promotion_context"},
        ci_gate_policy={"expected_head_sha": "b" * 40, "required_checks": ["tests"], "evidence_handler": "tests.promotion_ci"},
        execution_class="consequential",
    )
    side_effect_path = tmp_path / "publish-ran.txt"
    task = controller.submit(
        "tests.publish_handler",
        {
            "side_effect_path": str(side_effect_path),
            "authorized_approvers": ["attacker"],
            "decision_record": {"promotion": {"approved_by": "attacker"}},
        },
    )

    controller.run_once(task.id)

    assert controller.task(task.id).state == "failed"
    assert not side_effect_path.exists()


def _supervisor_checkpoint_envelope(task_id: str, intent: str = "durable crew task") -> dict:
    import hashlib
    from portable.crew_adapters import GraphHandoffController, TaskRetryBudget

    budget = TaskRetryBudget(max_retries=3)
    return {
        "schema_version": "auren-task-handoff-envelope-v1",
        "handoff_checkpoint": GraphHandoffController(retry_budget=budget).snapshot(),
        "retry_budget": budget.snapshot(),
        "task_id": task_id,
        "intent_digest": hashlib.sha256(intent.encode("utf-8")).hexdigest(),
        "crew_plan_digest": hashlib.sha256(b"stable crew plan").hexdigest(),
    }


def test_supervisor_checkpoint_round_trips_and_detects_tampering(tmp_path: Path):
    import json
    import sqlite3
    import pytest

    controller = ExecutionController(tmp_path)
    envelope = _supervisor_checkpoint_envelope("task-checkpoint")
    controller.save_graph_checkpoint("task-checkpoint", envelope)
    assert controller.load_graph_checkpoint("task-checkpoint") == envelope
    controller.save_graph_checkpoint("task-checkpoint", envelope)
    with sqlite3.connect(controller.evidence_capture.path) as db:
        evidence = db.execute(
            "SELECT event_type, artifact_digest FROM engineering_events "
            "WHERE event_type='crew_handoff_checkpoint_persisted'"
        ).fetchall()
    assert len(evidence) == 1
    assert evidence[0][0] == "crew_handoff_checkpoint_persisted"
    assert len(evidence[0][1]) == 64

    encoded = json.dumps({**envelope, "intent_digest": "f" * 64}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with sqlite3.connect(controller.db_path) as db:
        db.execute(
            "UPDATE graph_checkpoints SET envelope_json=? WHERE task_id=?",
            (encoded, "task-checkpoint"),
        )
    with pytest.raises(ValueError, match="digest mismatch"):
        controller.load_graph_checkpoint("task-checkpoint")
    with pytest.raises(ValueError, match="digest mismatch"):
        controller.save_graph_checkpoint("task-checkpoint", envelope)


def test_execute_graph_owns_durable_checkpoint_and_ignores_caller_checkpoint(tmp_path: Path, monkeypatch):
    from portable.state_graph import GraphAgentTeam

    controller = ExecutionController(tmp_path)
    team = GraphAgentTeam(graph=None)
    seen = []

    def fake_run(
        instance, task_id, intent, context=None, *,
        handoff_checkpoint_writer=None, require_durable_handoffs=None,
    ):
        state = dict(context or {})
        seen.append((state, require_durable_handoffs, handoff_checkpoint_writer))
        envelope = state.get("auren_supervisor_checkpoint_envelope")
        resumed = envelope is not None
        if envelope is None:
            envelope = _supervisor_checkpoint_envelope(task_id, intent)
        handoff_checkpoint_writer(task_id, envelope)
        return {"resumed_from_supervisor": resumed}

    monkeypatch.setattr(GraphAgentTeam, "run", fake_run)
    first = controller.execute_graph(
        team,
        "task-checkpoint",
        "durable crew task",
        {"auren_supervisor_checkpoint_envelope": {"forged": True},
         "auren_crew_handoff_checkpoint_data": {"forged": True},
         "auren_retry_budget": {"forged": True}},
    )
    assert first == {"resumed_from_supervisor": False}
    assert "auren_supervisor_checkpoint_envelope" not in seen[0][0]
    assert "auren_crew_handoff_checkpoint_data" not in seen[0][0]
    assert "auren_retry_budget" not in seen[0][0]
    assert seen[0][1] is True
    assert callable(seen[0][2])
    assert controller.load_graph_checkpoint("task-checkpoint") is not None
    assert team.handoff_checkpoint_writer is None
    assert team.require_durable_handoffs is False

    second = controller.execute_graph(team, "task-checkpoint", "durable crew task", {})
    assert second == {"resumed_from_supervisor": True}
    assert seen[1][0]["auren_supervisor_checkpoint_envelope"] == controller.load_graph_checkpoint("task-checkpoint")


def test_graph_team_rejects_checkpoint_for_stale_intent_or_crew_plan():
    import hashlib
    import pytest
    from portable.state_graph import GraphAgentTeam

    class StubGraph:
        def digest(self):
            return "a" * 64

        def run(self, task_id, intent, state):
            return state

    team = GraphAgentTeam(graph=StubGraph())
    intent = "durable crew task"
    plan = team.plan_crews(intent)
    envelope = _supervisor_checkpoint_envelope("task-checkpoint", intent)
    envelope["intent_digest"] = hashlib.sha256(b"different intent").hexdigest()
    envelope["crew_plan_digest"] = plan.digest
    with pytest.raises(ValueError, match="intent digest"):
        team.run("task-checkpoint", intent, {"auren_supervisor_checkpoint_envelope": envelope})

    envelope = _supervisor_checkpoint_envelope("task-checkpoint", intent)
    envelope["intent_digest"] = plan.intent_digest
    envelope["crew_plan_digest"] = "f" * 64
    with pytest.raises(ValueError, match="crew-plan digest"):
        team.run("task-checkpoint", intent, {"auren_supervisor_checkpoint_envelope": envelope})


def test_provider_resume_uses_canonical_graph_checkpoint_and_evidence(tmp_path: Path):
    import sqlite3
    from portable.crew_adapters import TaskRetryBudget
    from portable.resumable_provider import ResumableProviderExecutor
    from portable.state_graph import GraphAgentTeam

    class ProviderCheckpointGraph:
        def digest(self):
            return "a" * 64

        def run(self, task_id, intent, state):
            budget = TaskRetryBudget.resume(state["auren_retry_budget"])
            executor = ResumableProviderExecutor(
                task_id=task_id,
                stage_id="proposal",
                providers={
                    "primary": lambda request, key: (_ for _ in ()).throw(ConnectionResetError("reset")),
                    "backup": lambda request, key: {"proposal": "ok"},
                },
                provider_versions={"primary": "v1", "backup": "v2"},
                preferred_provider="primary",
                retry_budget=budget,
                persist=state["auren_provider_resume_persist"],
            )
            self.result = executor.run({"intent": intent})
            return self.result

    controller = ExecutionController(tmp_path)
    graph = ProviderCheckpointGraph()
    result = controller.execute_graph(
        GraphAgentTeam(graph=graph),
        "provider-task",
        "resume provider work",
        {"auren_provider_resume_state": {"forged": True}},
    )
    assert result == {"proposal": "ok"}
    envelope = controller.load_graph_checkpoint("provider-task")
    assert envelope is not None
    provider_state = envelope["provider_resume_state"]
    assert provider_state["task_id"] == "provider-task"
    assert provider_state["status"] == "completed"
    assert envelope["retry_budget"]["used_retries"] == 1
    assert envelope["handoff_checkpoint"]["retry_budget"]["digest"] == envelope["retry_budget"]["digest"]
    assert len(provider_state["digest"]) == 64
    assert "forged" not in provider_state
    with sqlite3.connect(controller.evidence_capture.path) as db:
        rows = db.execute(
            "SELECT metadata_json FROM engineering_events "
            "WHERE event_type='crew_handoff_checkpoint_persisted'"
        ).fetchall()
    assert rows
    assert any(provider_state["digest"] in row[0] for row in rows)


def test_provider_resume_checkpoint_tampering_fails_closed(tmp_path: Path):
    import hashlib
    import pytest
    from portable.crew_adapters import GraphHandoffController, TaskRetryBudget

    controller = ExecutionController(tmp_path)
    envelope = _supervisor_checkpoint_envelope("provider-tamper")
    provider_payload = {
        "schema_version": "auren-provider-resume-v1",
        "task_id": "provider-tamper",
        "stage_id": "proposal",
        "preferred_provider": "p",
        "attempted_providers": [],
        "deferred_until": {},
        "status": "ready",
        "result_digest": None,
        "retry_budget": envelope["retry_budget"],
        "provider_versions": {"p": "1"},
        "provider_configuration_digest": hashlib.sha256(
            b'{"preferred":"p","versions":{"p":"1"}}'
        ).hexdigest(),
    }
    provider_payload["digest"] = hashlib.sha256(
        __import__("json").dumps(
            provider_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    envelope["provider_resume_state"] = provider_payload
    controller.save_graph_checkpoint("provider-tamper", envelope)
    loaded = controller.load_graph_checkpoint("provider-tamper")
    assert loaded["provider_resume_state"]["digest"] == provider_payload["digest"]

    tampered = dict(loaded)
    tampered_provider = dict(tampered["provider_resume_state"])
    tampered_provider["status"] = "completed"
    tampered["provider_resume_state"] = tampered_provider
    with pytest.raises(ValueError, match="provider resume checkpoint digest mismatch"):
        controller.save_graph_checkpoint("provider-tamper", tampered)


def test_rate_limited_provider_resumes_after_reset_from_supervisor_checkpoint(tmp_path: Path):
    from datetime import datetime, timezone
    from portable.crew_adapters import TaskRetryBudget
    from portable.resumable_provider import ProviderCallError, ResumableProviderExecutor
    from portable.state_graph import GraphAgentTeam

    reset_at = "2026-10-15T12:01:00+00:00"

    class RateLimitGraph:
        def __init__(self, resumed):
            self.resumed = resumed

        def digest(self):
            return "c" * 64

        def run(self, task_id, intent, state):
            budget = TaskRetryBudget.resume(state["auren_retry_budget"])
            def limited(_request, _key):
                raise ProviderCallError("rate limited", status_code=429, reset_at=reset_at)
            providers = (
                {"a": limited, "b": limited}
                if not self.resumed
                else {"a": limited, "b": lambda request, key: {"resumed": True}}
            )
            executor = ResumableProviderExecutor(
                task_id=task_id,
                stage_id="long-operation",
                providers=providers,
                provider_versions={"a": "v1", "b": "v2"},
                preferred_provider="a" if not self.resumed else "b",
                retry_budget=budget,
                persist=state["auren_provider_resume_persist"],
                checkpoint=state.get("auren_provider_resume_state"),
            )
            return executor.run(
                {"intent": intent},
                now=datetime(2026, 10, 15, 12, 0 if not self.resumed else 1, tzinfo=timezone.utc),
            )

    controller = ExecutionController(tmp_path)
    first = controller.execute_graph(
        GraphAgentTeam(graph=RateLimitGraph(False)), "rate-limited-task", "long operation"
    )
    assert first["status"] == "deferred"
    saved = controller.load_graph_checkpoint("rate-limited-task")
    assert saved["provider_resume_state"]["status"] == "deferred"
    assert saved["retry_budget"]["used_retries"] == 1

    second = controller.execute_graph(
        GraphAgentTeam(graph=RateLimitGraph(True)), "rate-limited-task", "long operation"
    )
    assert second == {"resumed": True}
    final = controller.load_graph_checkpoint("rate-limited-task")
    assert final["provider_resume_state"]["status"] == "completed"
    assert final["retry_budget"]["used_retries"] == 1


def crash_once_handler(payload):
    """Exit the worker process on its first real invocation, then recover."""
    marker = Path(payload["marker"])
    if not marker.exists():
        marker.write_text("started", encoding="utf-8")
        os._exit(73)
    return {"success": True, "detail": "recovered after process death", "evidence": ["process-restart"]}


def test_real_process_death_resumes_running_handler(tmp_path: Path):
    root = tmp_path / "state"
    marker = tmp_path / "handler-started"
    task_id_file = tmp_path / "task-id"
    script = (
        "from pathlib import Path; "
        "from portable.execution_controller import ExecutionController; "
        "from test_execution_controller import crash_once_handler; "
        f"root=Path({str(root)!r}); marker={str(marker)!r}; task_file=Path({str(task_id_file)!r}); "
        "c=ExecutionController(root); "
        "c.register_handler('tests.crash_once_handler', crash_once_handler, execution_class='read_only'); "
        "t=c.submit('tests.crash_once_handler', {'marker': marker}); "
        "task_file.write_text(t.id, encoding='utf-8'); c.run_once(t.id)"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent), env.get("PYTHONPATH", "")])
    )
    child = subprocess.run([sys.executable, "-c", script], env=env, timeout=30, check=False)
    assert child.returncode == 73
    assert marker.exists()
    task_id = task_id_file.read_text(encoding="utf-8")

    # A killed process cannot renew its lease. Expire it explicitly to model
    # the host lease-reaper boundary without sleeping for the five-minute TTL.
    controller = ExecutionController(root)
    with sqlite3.connect(controller.db_path) as db:
        db.execute("UPDATE tasks SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?", (task_id,))
    scores = controller.resume_pending()
    assert scores
    assert controller.task(task_id).state == "completed"
    assert controller.task(task_id).attempts == 2



def test_resume_pending_continues_independent_work_around_live_lease(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller.register_handler("tests.durable_handler", durable_handler, execution_class="read_only")
    blocked = controller.submit("tests.durable_handler", {"id": "held"})
    independent = controller.submit("tests.durable_handler", {"id": "independent"})

    # Simulate another live worker owning the first task. Its lease must not be
    # stolen, but it must not prevent unrelated pending work from progressing.
    claimed = controller._claim(blocked.id)
    assert claimed is not None
    with sqlite3.connect(controller.db_path) as db:
        db.execute("UPDATE tasks SET updated_at='2000-01-01T00:00:00+00:00' WHERE id=?", (blocked.id,))

    # The recovery batch limit applies to eligible work. The oldest row is
    # intentionally the still-live lease, which must not starve this task.
    scores = controller.resume_pending(limit=1)

    assert len(scores) == 1
    assert controller.task(blocked.id).state == "running"
    assert controller.task(independent.id).state == "completed"


def test_supervisor_checkpoint_rejects_stale_handoff_and_retry_state(tmp_path: Path):
    import pytest
    from portable.crew_adapters import GraphHandoffController, TaskRetryBudget

    controller = ExecutionController(tmp_path)
    stale = _supervisor_checkpoint_envelope("monotonic-task")
    budget = TaskRetryBudget(max_retries=3)
    queue = GraphHandoffController(max_attempts=3, retry_budget=budget)
    item = queue.enqueue(
        source_crew="frontend_ui",
        target_crew="qa_reviewer",
        task="review accessible UI",
        source_plan_digest=stale["crew_plan_digest"],
        allowed_handoffs=("qa_reviewer",),
        selected_crews=("frontend_ui", "qa_reviewer"),
    )
    assert item is not None
    stale["retry_budget"] = budget.snapshot()
    stale["handoff_checkpoint"] = queue.snapshot()
    controller.save_graph_checkpoint("monotonic-task", stale)

    claimed = queue.claim_next(graph_node_active=True)
    assert claimed is not None
    receipt_payload = {
        "schema_version": "auren-handoff-review-receipt-v1",
        "reviewer_id": "independent-qa",
        "reviewer_version": "test-v1",
        "status": "pass",
        "evidence_refs": ["test://review/monotonic-task"],
        "reason": "Review passed in the test harness.",
    }
    import hashlib
    import json
    receipt_payload["digest"] = hashlib.sha256(
        json.dumps(receipt_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    queue.mark_completed(claimed, independent_review_passed=True, review_receipt=receipt_payload)
    completed = dict(stale)
    completed["retry_budget"] = budget.snapshot()
    completed["handoff_checkpoint"] = queue.snapshot()
    controller.save_graph_checkpoint("monotonic-task", completed)

    with pytest.raises(ValueError, match="terminal handoff decision"):
        controller.save_graph_checkpoint("monotonic-task", stale)
    loaded = controller.load_graph_checkpoint("monotonic-task")
    assert loaded is not None
    assert loaded["handoff_checkpoint"]["completed_ids"] == [item.handoff_id]
    assert loaded["handoff_checkpoint"]["completion_receipts"][item.handoff_id]["status"] == "pass"


def test_supervisor_checkpoint_rejects_retry_budget_refund(tmp_path: Path):
    import pytest
    from portable.crew_adapters import TaskRetryBudget

    controller = ExecutionController(tmp_path)
    initial = _supervisor_checkpoint_envelope("retry-refund-task")
    controller.save_graph_checkpoint("retry-refund-task", initial)
    budget = TaskRetryBudget(max_retries=3)
    assert budget.consume(component="crew_handoff", operation_key="handoff:stable:retry:1")
    advanced = _supervisor_checkpoint_envelope("retry-refund-task")
    advanced["retry_budget"] = budget.snapshot()
    advanced["handoff_checkpoint"] = {
        **advanced["handoff_checkpoint"],
        "retry_budget": budget.snapshot(),
    }
    payload = {key: value for key, value in advanced["handoff_checkpoint"].items() if key != "digest"}
    import hashlib
    import json
    advanced["handoff_checkpoint"]["digest"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    controller.save_graph_checkpoint("retry-refund-task", advanced)
    with pytest.raises(ValueError, match="refund or rewrite consumed retry budget"):
        controller.save_graph_checkpoint("retry-refund-task", initial)


def test_handoff_queue_survives_supervisor_restart_without_duplicate_dispatch(tmp_path: Path):
    from portable.crew_adapters import GraphHandoffController, TaskRetryBudget

    first = ExecutionController(tmp_path)
    envelope = _supervisor_checkpoint_envelope("handoff-restart-task", "restart handoff")
    budget = TaskRetryBudget.resume(envelope["retry_budget"])
    handoffs = GraphHandoffController(
        max_attempts=3,
        retry_budget=budget,
        persist=lambda snapshot: first.save_graph_checkpoint(
            "handoff-restart-task",
            {
                **envelope,
                "handoff_checkpoint": dict(snapshot),
                "retry_budget": budget.snapshot(),
            },
        ),
        require_persistence=True,
    )
    queued = handoffs.enqueue(
        source_crew="frontend_ui",
        target_crew="qa_reviewer",
        task="review once",
        source_plan_digest=envelope["crew_plan_digest"],
        allowed_handoffs=("qa_reviewer",),
        selected_crews=("frontend_ui", "qa_reviewer"),
    )
    assert queued is not None

    # Simulate process exit after a durable claim but before review completion.
    claimed = handoffs.claim_next(graph_node_active=True)
    assert claimed is not None
    second_process = ExecutionController(tmp_path)
    saved = second_process.load_graph_checkpoint("handoff-restart-task")
    assert saved is not None
    resumed_budget = TaskRetryBudget.resume(saved["retry_budget"])
    resumed = GraphHandoffController.resume(
        saved["handoff_checkpoint"],
        retry_budget=resumed_budget,
        persist=lambda snapshot: second_process.save_graph_checkpoint(
            "handoff-restart-task",
            {
                **saved,
                "handoff_checkpoint": dict(snapshot),
                "retry_budget": resumed_budget.snapshot(),
            },
        ),
        require_persistence=True,
    )
    # The interrupted in-flight claim is requeued under its original idempotency key.
    retry = resumed.claim_next(graph_node_active=True)
    assert retry is not None
    assert retry.handoff_id == queued.handoff_id
    duplicate = resumed.enqueue(
        source_crew="frontend_ui",
        target_crew="qa_reviewer",
        task="review once",
        source_plan_digest=envelope["crew_plan_digest"],
        allowed_handoffs=("qa_reviewer",),
        selected_crews=("frontend_ui", "qa_reviewer"),
    )
    assert duplicate is not None
    assert duplicate.handoff_id == queued.handoff_id
    assert len(resumed.snapshot()["queued"]) == 0
    assert len(resumed.snapshot()["in_flight"]) == 1


def test_exhausted_handoff_retry_budget_is_terminal_and_cannot_be_resurrected(tmp_path: Path):
    import pytest
    from portable.crew_adapters import GraphHandoffController, TaskRetryBudget

    controller = ExecutionController(tmp_path)
    envelope = _supervisor_checkpoint_envelope("exhausted-handoff-task")
    budget = TaskRetryBudget(max_retries=0)
    queue = GraphHandoffController(max_attempts=3, retry_budget=budget)
    item = queue.enqueue(
        source_crew="frontend_ui",
        target_crew="qa_reviewer",
        task="bounded retry",
        source_plan_digest=envelope["crew_plan_digest"],
        allowed_handoffs=("qa_reviewer",),
        selected_crews=("frontend_ui", "qa_reviewer"),
    )
    assert item is not None
    envelope["retry_budget"] = budget.snapshot()
    envelope["handoff_checkpoint"] = queue.snapshot()
    controller.save_graph_checkpoint("exhausted-handoff-task", envelope)

    claimed = queue.claim_next(graph_node_active=True)
    assert claimed is not None
    assert queue.retry(claimed, retryable=True) is None
    terminal = dict(envelope)
    terminal["retry_budget"] = budget.snapshot()
    terminal["handoff_checkpoint"] = queue.snapshot()
    controller.save_graph_checkpoint("exhausted-handoff-task", terminal)

    with pytest.raises(ValueError, match="resurrect a terminal handoff"):
        controller.save_graph_checkpoint("exhausted-handoff-task", envelope)
    saved = controller.load_graph_checkpoint("exhausted-handoff-task")
    assert saved is not None
    assert saved["handoff_checkpoint"]["queued"] == []
    assert saved["handoff_checkpoint"]["in_flight"] == []
    assert saved["retry_budget"]["remaining"] == 0


def test_real_graph_team_handoff_resumes_through_supervisor_after_two_interruptions(tmp_path: Path):
    import pytest
    from portable.state_graph import GraphAgentTeam

    intent = "Build a frontend UI and complete accessibility QA review"
    handoff_task = "review accessible UI implementation"
    interruptions = []

    class InterruptibleGraph:
        states = ()

        def __init__(self, phase):
            self.phase = phase

        def digest(self):
            return "a" * 64

        def run(self, task_id, goal, state):
            if self.phase == "enqueue":
                plan = state["auren_crew_plan"]
                selected = {item["crew_id"] for item in plan["assignments"]}
                assert {"frontend_ui", "qa_reviewer"}.issubset(selected)
                first = state["auren_crew_handoff_enqueue"](
                    "frontend_ui", "qa_reviewer", handoff_task
                )
                duplicate = state["auren_crew_handoff_enqueue"](
                    "frontend_ui", "qa_reviewer", handoff_task
                )
                assert duplicate["handoff_id"] == first["handoff_id"]
                interruptions.append(first["handoff_id"])
                raise RuntimeError("simulated process interruption after durable enqueue")
            record = state["auren_crew_handoff_claim"]()
            assert record is not None
            if self.phase == "claim":
                interruptions.append(record["handoff_id"])
                raise RuntimeError("simulated process interruption after durable claim")
            state["auren_crew_handoff_complete"](record)
            return {"completed": True, "handoff_id": record["handoff_id"]}

    def independent_review(_handoff):
        return {
            "schema_version": "auren-handoff-review-receipt-v1",
            "reviewer_id": "integration-qa",
            "reviewer_version": "v1",
            "status": "pass",
            "evidence_refs": ["test://supervisor/independent-review"],
            "reason": "Independent review passed in the integration test.",
        }

    def make_team(phase):
        return GraphAgentTeam(
            graph=InterruptibleGraph(phase),
            independent_handoff_reviewer=independent_review,
            independent_handoff_reviewer_id="integration-qa",
            independent_handoff_reviewer_version="v1",
        )

    first_process = ExecutionController(tmp_path)
    with pytest.raises(RuntimeError, match="after durable enqueue"):
        first_process.execute_graph(make_team("enqueue"), "real-handoff-task", intent)

    second_process = ExecutionController(tmp_path)
    saved_after_enqueue = second_process.load_graph_checkpoint("real-handoff-task")
    assert saved_after_enqueue is not None
    assert len(saved_after_enqueue["handoff_checkpoint"]["queued"]) == 1
    with pytest.raises(RuntimeError, match="after durable claim"):
        second_process.execute_graph(make_team("claim"), "real-handoff-task", intent)

    third_process = ExecutionController(tmp_path)
    saved_after_claim = third_process.load_graph_checkpoint("real-handoff-task")
    assert saved_after_claim is not None
    assert len(saved_after_claim["handoff_checkpoint"]["in_flight"]) == 1
    result = third_process.execute_graph(make_team("complete"), "real-handoff-task", intent)

    assert result["completed"] is True
    assert interruptions[0] == interruptions[1] == result["handoff_id"]
    final = third_process.load_graph_checkpoint("real-handoff-task")
    assert final is not None
    assert final["handoff_checkpoint"]["completed_ids"] == [result["handoff_id"]]
    assert final["handoff_checkpoint"]["completion_receipts"][result["handoff_id"]]["reviewer_id"] == "integration-qa"
    assert final["retry_budget"]["digest"] == final["handoff_checkpoint"]["retry_budget"]["digest"]


def test_supervised_cross_crew_handoff_is_revision_indexed_and_not_replayed(tmp_path: Path, monkeypatch):
    import hashlib
    import subprocess
    from portable.state_graph import GraphAgentTeam

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True, capture_output=True, text=True, timeout=5,
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "auren-test@example.invalid")
    git("config", "user.name", "AUREN integration test")
    (tmp_path / "seed.txt").write_text("checkpoint identity fixture", encoding="utf-8")
    git("add", "seed.txt")
    git("commit", "-m", "seed supervised handoff fixture", "-q")
    head_sha = git("rev-parse", "HEAD").lower()
    git("remote", "add", "origin", "https://github.com/acme/auren-fixture.git")
    monkeypatch.delenv("AUREN_REPOSITORY_SLUG", raising=False)  # Exercise origin-URL slug resolution.

    class Adapter:
        def __init__(self, crew_id):
            self.crew_id = crew_id
            self.implementation_version = "integration-test-v1"
            self.calls = 0
            self.configuration_digest = hashlib.sha256(crew_id.encode()).hexdigest()

        def propose(self, request):
            self.calls += 1
            checks = {
                name: {"status": "pass", "evidence_refs": [f"test:{self.crew_id}:{index}"]}
                for index, name in enumerate(request["acceptance_checks"])
            }
            proposal = {
                "schema_version": "auren-crew-proposal-v1",
                "recommendation": f"{self.crew_id} proposal",
                "artifacts": [f"proposal:{self.crew_id}"],
                "evidence_refs": [f"test:evidence:{self.crew_id}"],
                "uncertainty": [],
                "blockers": [],
                "acceptance_check_results": checks,
            }
            if self.crew_id == "backend_api":
                proposal["handoff_requests"] = [{
                    "target_crew": "qa_reviewer",
                    "task": "Independently verify the backend API proposal",
                }]
            return proposal

    source = Adapter("backend_api")
    target = Adapter("qa_reviewer")

    class HandoffGraph:
        def digest(self):
            return "a" * 64

        def run(self, task_id, intent, state):
            source_record = state["auren_crew_dispatch"](
                "backend_api", "Implement the backend API", {"evidence_refs": ["test:source"]}
            )
            request = source_record["proposal"]["handoff_requests"][0]
            queued = state["auren_crew_handoff_enqueue"](
                "backend_api", request["target_crew"], request["task"]
            )
            if queued["status"] != "queued":
                return {"handoff_status": queued["status"], "handoff_id": queued["handoff_id"]}
            claimed = state["auren_crew_handoff_claim"]()
            assert claimed is not None
            state["auren_crew_dispatch"](
                claimed["target_crew"], claimed["task"], {"handoff_id": claimed["handoff_id"]}
            )
            state["auren_crew_handoff_complete"](claimed)
            return {"handoff_status": "completed", "handoff_id": claimed["handoff_id"]}

    def reviewer(handoff):
        return {
            "schema_version": "auren-handoff-review-receipt-v1",
            "reviewer_id": "independent-qa-fixture",
            "reviewer_version": "qa-v1",
            "status": "pass",
            "evidence_refs": ["test:qa:reviewed-proposal", "test:qa:acceptance"],
            "reason": "fixture independently checked proposal evidence and acceptance",
        }

    def new_team():
        return GraphAgentTeam(
            graph=HandoffGraph(),
            agents={"backend_api": source, "qa_reviewer": target},
            independent_handoff_reviewer=reviewer,
            independent_handoff_reviewer_id="independent-qa-fixture",
            independent_handoff_reviewer_version="qa-v1",
        )

    first_controller = ExecutionController(tmp_path)
    first = first_controller.execute_graph(
        new_team(), "cross-crew-task", "backend API implementation with QA review", {}
    )
    assert first["handoff_status"] == "completed"
    first_envelope = first_controller.load_graph_checkpoint("cross-crew-task")
    assert first_envelope is not None
    assert first_envelope["repository"] == "acme/auren-fixture"
    assert first_envelope["head_sha"] == head_sha
    receipt_map = first_envelope["handoff_checkpoint"]["completion_receipts"]
    assert len(receipt_map) == 1
    assert receipt_map[next(iter(receipt_map))]["reviewer_id"] == "independent-qa-fixture"
    assert first_envelope["retry_budget"]["digest"] == first_envelope["handoff_checkpoint"]["retry_budget"]["digest"]

    evidence_ids = first_controller.evidence_ledger.known_evidence_ids(
        repository="acme/auren-fixture", head_sha=head_sha
    )
    assert any(item.startswith("crew-checkpoint:") for item in evidence_ids)

    # A new supervisor instance proves the persisted terminal receipt is restored;
    # the source proposal can replay, but the target's external work must not repeat.
    second_controller = ExecutionController(tmp_path)
    second = second_controller.execute_graph(
        new_team(), "cross-crew-task", "backend API implementation with QA review", {}
    )
    assert second["handoff_status"] == "already_completed"
    assert source.calls == 2
    assert target.calls == 1
    resumed = second_controller.load_graph_checkpoint("cross-crew-task")
    assert resumed is not None
    assert resumed["handoff_checkpoint"]["completed_ids"] == first_envelope["handoff_checkpoint"]["completed_ids"]


def test_session_work_unit_creation_is_idempotent_if_mapping_write_is_lost(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    first = controller.work_unit_for_session("session-retry", "long task")
    with sqlite3.connect(controller.db_path) as db:
        db.execute("DELETE FROM sessions WHERE session_id=?", ("session-retry",))

    restarted = ExecutionController(tmp_path)
    second = restarted.work_unit_for_session("session-retry", "long task")
    assert second == first
    with sqlite3.connect(restarted.company.db_path) as db:
        rows = db.execute(
            "SELECT id FROM work_units WHERE work_key LIKE ?",
            ("execution-session:session-retry:%:initial",),
        ).fetchall()
    assert [row[0] for row in rows] == [first]


def test_terminal_session_does_not_implicitly_retry_and_new_goal_gets_one_generation(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    first = controller.work_unit_for_session("session-generation", "long task")
    controller.company.finish(first, success=False, reason="no-progress gate stopped", evidence=("test:failure",))

    restarted = ExecutionController(tmp_path)
    same_goal = restarted.work_unit_for_session("session-generation", "long task")
    assert same_goal == first
    assert restarted.company._state(first)[0] == "failed"

    second = restarted.work_unit_for_session("session-generation", "different explicit goal")
    again = restarted.work_unit_for_session("session-generation", "different explicit goal")
    assert second != first
    assert again == second
    assert restarted.company._state(second)[0] == "running"



def test_run_continuously_drains_work_and_stops_after_bounded_idle_cycles(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller.register_handler("tests.continuous_handler", durable_handler, execution_class="read_only")
    first = controller.submit("tests.continuous_handler", {"id": "continuous-1"})
    second = controller.submit("tests.continuous_handler", {"id": "continuous-2"})

    report = controller.run_continuously(
        batch_size=1, idle_sleep_seconds=0.001, max_cycles=10, max_idle_cycles=2,
    )

    assert report.stop_reason == "idle_budget_exhausted"
    assert report.processed_work_units == 2
    assert report.completed_tasks == 2
    assert report.failed_tasks == 0
    assert len(report.trust_scores) == 2
    assert report.cycles == 4
    assert report.idle_cycles == 2
    assert controller.task(first.id).state == "completed"
    assert controller.task(second.id).state == "completed"


def test_run_continuously_observes_preexisting_cancellation(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    stop = Event()
    stop.set()
    report = controller.run_continuously(stop_event=stop, max_cycles=5)
    assert report.stop_reason == "cancelled"
    assert report.cycles == 0
    assert report.processed_work_units == 0


def test_run_continuously_never_calls_idle_as_success(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    report = controller.run_continuously(
        idle_sleep_seconds=0, max_cycles=20, max_idle_cycles=2,
    )
    assert report.stop_reason == "idle_budget_exhausted"
    assert report.processed_work_units == 0
    assert report.remaining_nonterminal_tasks == 0
    assert report.trust_scores == ()


def test_run_continuously_rejects_unbounded_budget_overrides(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    try:
        controller.run_continuously(max_cycles=1001)
    except ValueError as error:
        assert "hard limits" in str(error)
    else:
        raise AssertionError("continuous loop must enforce a hard cycle budget")


def test_cycle_budget_report_does_not_hide_remaining_work(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller.register_handler("tests.continuous_pending", durable_handler, execution_class="read_only")
    first = controller.submit("tests.continuous_pending", {"id": "remaining-1"})
    second = controller.submit("tests.continuous_pending", {"id": "remaining-2"})
    report = controller.run_continuously(
        batch_size=1, max_cycles=1, max_idle_cycles=1, idle_sleep_seconds=0,
    )
    assert report.stop_reason == "cycle_budget_exhausted"
    assert report.processed_work_units == 1
    assert report.completed_tasks == 1
    assert report.failed_tasks == 0
    assert report.remaining_nonterminal_tasks == 1
    remaining = controller.task(second.id)
    assert remaining is not None and remaining.state == "pending"


def test_run_continuously_reports_failed_tasks_without_success_claim(tmp_path: Path):
    controller = ExecutionController(tmp_path)

    def fail_handler(payload):
        return {"success": False, "detail": "explicit handler failure"}

    controller.register_handler("tests.continuous_failure", fail_handler, execution_class="read_only")
    controller.submit("tests.continuous_failure", {"id": "failure"})
    report = controller.run_continuously(
        batch_size=1, max_cycles=2, max_idle_cycles=1, idle_sleep_seconds=0,
    )
    assert report.failed_tasks == 1
    assert report.completed_tasks == 0
    assert report.remaining_nonterminal_tasks == 0
    assert report.stop_reason == "idle_budget_exhausted"


def failing_durable_handler(payload):
    raise RuntimeError(f"controlled handler failure: {payload['id']}")


def test_run_continuously_reports_durable_handler_exceptions_and_keeps_draining(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller.register_handler(
        "tests.failing_durable_handler", failing_durable_handler, execution_class="read_only"
    )
    controller.register_handler("tests.durable_handler", durable_handler, execution_class="read_only")
    failed = controller.submit("tests.failing_durable_handler", {"id": "durable-failure"})
    completed = controller.submit("tests.durable_handler", {"id": "after-failure"})

    report = controller.run_continuously(
        batch_size=2, idle_sleep_seconds=0, max_cycles=3, max_idle_cycles=1,
    )

    assert report.stop_reason == "idle_budget_exhausted"
    assert report.processed_work_units == 2
    assert report.completed_tasks == 1
    assert report.failed_tasks == 1
    assert report.remaining_nonterminal_tasks == 0
    assert controller.task(failed.id).state == "failed"
    assert controller.task(completed.id).state == "completed"


def test_resume_pending_keeps_fail_fast_default_for_direct_callers(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller.register_handler(
        "tests.failing_durable_handler", failing_durable_handler, execution_class="read_only"
    )
    task = controller.submit("tests.failing_durable_handler", {"id": "direct-failure"})

    try:
        controller.resume_pending(limit=1)
    except RuntimeError as error:
        assert "controlled handler failure" in str(error)
    else:
        raise AssertionError("direct resume_pending must retain fail-fast behavior")
    assert controller.task(task.id).state == "failed"



def test_consequential_handler_uses_durable_receipt_without_reexecution(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace
    from portable.side_effect_ledger import DurableSideEffectLedger

    controller = ExecutionController(tmp_path)
    calls = []
    task = SimpleNamespace(
        id="consequential-task-1",
        handler="tests.deploy",
        payload={"target": "staging", "revision": "abc"},
    )
    monkeypatch.setattr(
        controller, "_invoke_with_lease_heartbeat",
        lambda _task, handler: handler(task.payload),
    )

    def provider(payload):
        calls.append(payload["revision"])
        return {"success": True, "detail": "deployed", "provider_operation_id": "op-1"}

    first = controller._invoke_consequential_with_ledger(task, provider)
    replay = controller._invoke_consequential_with_ledger(task, provider)

    assert first == replay
    assert calls == ["abc"]
    row = DurableSideEffectLedger(controller.db_path).get("execution-task:consequential-task-1")
    assert row["state"] == "completed"
    assert row["receipt"]["result"]["provider_operation_id"] == "op-1"


def test_consequential_receipt_write_failure_blocks_redispatch(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace
    from portable.side_effect_ledger import DurableSideEffectLedger

    controller = ExecutionController(tmp_path)
    calls = []
    task = SimpleNamespace(
        id="consequential-task-receipt-failure",
        handler="tests.deploy",
        payload={"target": "production", "revision": "def"},
    )
    monkeypatch.setattr(
        controller, "_invoke_with_lease_heartbeat",
        lambda _task, handler: handler(task.payload),
    )

    def provider(payload):
        calls.append(payload["revision"])
        return {"success": True, "detail": "accepted", "provider_operation_id": "op-2"}

    original_complete = DurableSideEffectLedger.complete

    def fail_once(self, key, receipt):
        if key == "execution-task:consequential-task-receipt-failure":
            raise OSError("simulated receipt persistence failure")
        return original_complete(self, key, receipt)

    monkeypatch.setattr(DurableSideEffectLedger, "complete", fail_once)
    import pytest
    with pytest.raises(OSError, match="receipt persistence failure"):
        controller._invoke_consequential_with_ledger(task, provider)

    monkeypatch.setattr(DurableSideEffectLedger, "complete", original_complete)
    with pytest.raises(RuntimeError, match="requires reconciliation"):
        controller._invoke_consequential_with_ledger(task, provider)

    assert calls == ["def"]
    row = DurableSideEffectLedger(controller.db_path).get("execution-task:consequential-task-receipt-failure")
    assert row["state"] == "reconcile_required"


def test_consequential_dispatch_key_replays_across_new_task_ids(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    controller = ExecutionController(tmp_path)
    calls = []

    def provider(payload):
        calls.append(payload["revision"])
        return {"success": True, "detail": "accepted", "provider_operation_id": "op-stable"}

    monkeypatch.setattr(
        controller, "_invoke_with_lease_heartbeat",
        lambda _task, handler: handler(_task.payload),
    )
    first_task = SimpleNamespace(
        id="task-before-restart",
        handler="tests.deploy",
        payload={
            "target": "staging",
            "revision": "abc123",
            "auren_dispatch": {"dispatch_key": "run-77:deploy:staging"},
        },
    )
    resumed_task = SimpleNamespace(
        id="task-after-restart",
        handler="tests.deploy",
        payload={
            "target": "staging",
            "revision": "abc123",
            "auren_dispatch": {"dispatch_key": "run-77:deploy:staging"},
        },
    )

    first = controller._invoke_consequential_with_ledger(first_task, provider)
    replay = controller._invoke_consequential_with_ledger(resumed_task, provider)

    assert first == replay
    assert calls == ["abc123"]
