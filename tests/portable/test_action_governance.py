from portable.action_governance import (
    ActionGovernanceGateway,
    ActionPolicy,
    ActionRequest,
    ApprovalReceipt,
)


def make_request(**overrides):
    values = dict(
        run_id="run-1",
        action_id="action-1",
        actor="builder",
        capability="repository.write",
        action="edit",
        target="src/module.py",
        risk="write",
    )
    values.update(overrides)
    return ActionRequest(**values)


def make_gateway(**kwargs):
    return ActionGovernanceGateway(
        ActionPolicy(
            allowed_capabilities=frozenset({"repository.write", "release.publish"}),
            protected_targets=("main", ".github/workflows"),
        ),
        **kwargs,
    )


def test_unallowlisted_capability_fails_closed_without_running_handler():
    gateway = make_gateway()
    called = []
    result = gateway.execute(make_request(capability="shell.root"), lambda: called.append(True))
    assert not result.executed
    assert result.verdict.decision == "deny"
    assert result.verdict.reason == "capability_not_allowlisted"
    assert called == []


def test_protected_target_requires_verified_request_bound_approval():
    gateway = make_gateway(approval_verifier=lambda receipt, request: receipt.approver == "maintainer")
    request = make_request(action="edit", target=".github/workflows/build.yml")
    first = gateway.evaluate(request)
    assert first.decision == "approval_required"

    receipt = ApprovalReceipt("approval-1", "maintainer", request.fingerprint())
    assert gateway.evaluate(request, approval=receipt).allowed
    wrong_request = make_request(action="edit", target="src/other.py")
    assert not gateway.evaluate(wrong_request, approval=receipt).allowed


def test_target_normalization_cannot_bypass_protected_path():
    gateway = make_gateway()
    request = make_request(target="src/../.github/workflows/build.yml")
    assert gateway.evaluate(request).decision == "approval_required"


def test_approval_fails_closed_when_no_verifier_is_configured():
    gateway = make_gateway()
    request = make_request(action="merge", target="main", evidence_refs=("ci:sha:abc",))
    receipt = ApprovalReceipt("approval-1", "maintainer", request.fingerprint())
    verdict = gateway.evaluate(request, approval=receipt)
    assert verdict.decision == "approval_required"


def test_release_requires_evidence_and_approval():
    gateway = make_gateway(approval_verifier=lambda receipt, request: True)
    request = make_request(action="release", target="release/v1", capability="release.publish", risk="release")
    assert gateway.evaluate(request).reason == "required_evidence_missing"

    evidenced = make_request(
        action="release", target="release/v1", capability="release.publish",
        risk="release", evidence_refs=("ci:sha:abc",),
    )
    assert gateway.evaluate(evidenced).decision == "approval_required"
    receipt = ApprovalReceipt("approval-2", "maintainer", evidenced.fingerprint())
    assert gateway.evaluate(evidenced, approval=receipt).allowed


def test_external_effect_requires_idempotency_key():
    gateway = make_gateway()
    missing = make_request(action="publish", risk="external_effect")
    assert gateway.evaluate(missing).reason == "idempotency_key_required"
    keyed = make_request(action="publish", risk="external_effect", idempotency_key="publish-run-1")
    assert gateway.evaluate(keyed).allowed


def test_handler_runs_only_after_allow_and_trace_verifies():
    gateway = make_gateway()
    called = []
    result = gateway.execute(make_request(), lambda: called.append("ran") or {"ok": True})
    assert result.executed
    assert called == ["ran"]
    assert gateway.verify_trace()["passed"]
    assert [event["event"] for event in gateway.events] == [
        "policy_verdict", "action_started", "action_completed"
    ]


def test_duplicate_action_identity_is_not_executed_twice():
    gateway = make_gateway()
    called = []
    first = gateway.execute(make_request(), lambda: called.append("ran") or "ok")
    second = gateway.execute(make_request(), lambda: called.append("ran-again") or "ok")
    assert first.executed
    assert not second.executed
    assert second.verdict.reason == "duplicate_action_identity"
    assert called == ["ran"]
    assert gateway.verify_trace()["passed"]


def test_arbitrary_handler_result_does_not_break_receipt_creation():
    class Result:
        pass

    gateway = make_gateway()
    result = gateway.execute(make_request(), Result)
    assert result.executed
    assert len(result.result_digest) == 64


def test_handler_failure_is_traced_without_storing_exception_message():
    gateway = make_gateway()

    def fail():
        raise RuntimeError("secret-bearing provider response")

    try:
        gateway.execute(make_request(), fail)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected handler failure")

    assert gateway.verify_trace()["passed"]
    assert gateway.events[-1]["event"] == "action_failed"
    assert gateway.events[-1]["error_type"] == "RuntimeError"
    assert "secret-bearing" not in repr(gateway.events)


def test_invalid_policy_and_request_are_rejected():
    try:
        ActionPolicy(allowed_capabilities=frozenset()).validate()
    except ValueError:
        pass
    else:
        raise AssertionError("empty allowlist must be rejected")

    try:
        make_gateway().evaluate(make_request(target=" "))
    except ValueError:
        pass
    else:
        raise AssertionError("blank target must be rejected")


def test_trace_tampering_is_detected():
    gateway = make_gateway()
    gateway.evaluate(make_request())
    gateway._events[0]["reason"] = "forged"
    assert gateway.verify_trace()["passed"] is False
