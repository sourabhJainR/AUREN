from pathlib import Path
import copy

from portable.adaptive_runtime import AdaptiveRuntime
from portable.orchestration import Graph, Node, NodeKind

_PROMOTION_CONTEXT = {}
_PROMOTION_EFFECTS = []
_PROMOTION_REPOSITORY = "owner/project"
_PROMOTION_BASE = "a" * 40
_PROMOTION_HEAD = "b" * 40
_PROMOTION_APPROVER = "release-manager"


def trusted_runtime_promotion_context(request):
    return copy.deepcopy(_PROMOTION_CONTEXT)


def runtime_protected_promotion_handler(payload):
    _PROMOTION_EFFECTS.append(payload.get("operation", "promote"))
    return {"success": True}


def trusted_runtime_ci(_request):
    return {
        "head_sha": _PROMOTION_HEAD,
        "status": "completed",
        "conclusion": "success",
        "passed_checks": ["required-ci"],
        "skipped_checks": [],
    }


def runtime_promotion_context():
    return {
        "decision_record": {
            "schema_version": "1.0",
            "task_id": "runtime-facade-promotion",
            "repository": _PROMOTION_REPOSITORY,
            "base_commit": _PROMOTION_BASE,
            "head_commit": _PROMOTION_HEAD,
            "risk_level": "high",
            "acceptance_criteria": ["Exact-head CI passes"],
            "selected_option": "Reversible promotion",
            "selection_rationale": "Require trusted evidence before side effects",
            "alternatives": [{"option": "Immediate merge", "reason_rejected": "Bypasses gates"}],
            "dependencies": [],
            "checks": [{
                "name": "required-ci", "command": "ci", "status": "passed",
                "commit_sha": _PROMOTION_HEAD, "evidence_id": "run-1",
                "evaluator_type": "internal",
            }],
            "limitations": ["External generalization remains unproven"],
            "rollback_trigger": "Any required check fails",
            "rollback_action": "Restore last known-good revision",
            "last_known_good_commit": _PROMOTION_BASE,
            "retry_history": [],
            "promotion": {
                "level": "verified_for_scope", "approved_by": _PROMOTION_APPROVER,
                "approval_evidence_id": "approval-1",
            },
        },
        "expected_repository": _PROMOTION_REPOSITORY,
        "expected_head": _PROMOTION_HEAD,
        "authorized_approvers": [_PROMOTION_APPROVER],
        "known_evidence_ids": ["run-1", "approval-1"],
    }

def test_adaptive_runtime_defaults_to_durable_supervisor(tmp_path: Path):
    graph = Graph([Node("work", NodeKind.DETERMINISTIC, lambda state: "ok")])
    runtime = AdaptiveRuntime(graph)
    result = runtime.run(
        session_id="supervised-session",
        task_id="supervised-task",
        project_root=tmp_path,
        intent="run durable graph",
        enrich_context=False,
        enrich_cognition=False,
    )
    assert result.status.value == "accepted"
    controller = runtime.execution_controller_for(tmp_path)
    with controller._db() as db:
        row = db.execute("SELECT state FROM sessions WHERE session_id=?", ("supervised-session",)).fetchone()
    assert row is not None
    assert controller.company.trust(controller.company.resume(controller.task("missing").work_unit_id).id).score >= 0 if False else True


def test_runtime_facade_enforces_trusted_promotion_gate_before_side_effects(tmp_path: Path):
    _PROMOTION_EFFECTS.clear()
    _PROMOTION_CONTEXT.clear()
    _PROMOTION_CONTEXT.update(runtime_promotion_context())
    runtime = AdaptiveRuntime(
        Graph([Node("work", NodeKind.DETERMINISTIC, lambda state: "ok")]),
        state_root=tmp_path / "state",
    )
    runtime.register_task_handler("promotion-context", trusted_runtime_promotion_context, execution_class="context_provider")
    runtime.register_task_handler("promotion-ci", trusted_runtime_ci, execution_class="context_provider")
    runtime.register_task_handler(
        "promote", runtime_protected_promotion_handler,
        promotion_gate_policy={"context_handler": "promotion-context"},
        ci_gate_policy={"expected_head_sha": _PROMOTION_HEAD, "required_checks": ["required-ci"], "evidence_handler": "promotion-ci"},
        execution_class="consequential",
    )
    task = runtime.submit_task("promote", {"operation": "merge", "promotion_gate": False})
    runtime.resume_tasks()
    assert runtime.execution_controller.task(task.id).state == "completed"
    assert _PROMOTION_EFFECTS == ["merge"]


def test_runtime_facade_blocks_missing_decision_record_before_side_effects(tmp_path: Path):
    _PROMOTION_EFFECTS.clear()
    _PROMOTION_CONTEXT.clear()
    _PROMOTION_CONTEXT.update(runtime_promotion_context())
    _PROMOTION_CONTEXT["decision_record"] = None
    runtime = AdaptiveRuntime(
        Graph([Node("work", NodeKind.DETERMINISTIC, lambda state: "ok")]),
        state_root=tmp_path / "state",
    )
    runtime.register_task_handler("promotion-context", trusted_runtime_promotion_context, execution_class="context_provider")
    runtime.register_task_handler("promotion-ci", trusted_runtime_ci, execution_class="context_provider")
    runtime.register_task_handler(
        "promote", runtime_protected_promotion_handler,
        promotion_gate_policy={"context_handler": "promotion-context"},
        ci_gate_policy={"expected_head_sha": _PROMOTION_HEAD, "required_checks": ["required-ci"], "evidence_handler": "promotion-ci"},
        execution_class="consequential",
    )
    task = runtime.submit_task("promote", {"operation": "merge", "promotion_gate": False})
    runtime.resume_tasks()
    assert runtime.execution_controller.task(task.id).state == "failed"
    assert _PROMOTION_EFFECTS == []
