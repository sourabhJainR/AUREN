from pathlib import Path


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
