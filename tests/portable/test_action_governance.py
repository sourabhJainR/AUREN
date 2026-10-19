import tempfile
from pathlib import Path

from portable.side_effect_ledger import DurableSideEffectLedger
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



def test_action_verbs_are_allowlisted():
    gateway = make_gateway()
    verdict = gateway.evaluate(make_request(action="shell.exec"))
    assert verdict.decision == "deny"
    assert verdict.reason == "action_not_allowlisted"



def test_absolute_workspace_target_cannot_bypass_protected_path():
    gateway = make_gateway()
    request = make_request(target="/workspace/repository/.github/workflows/build.yml")
    assert gateway.evaluate(request).decision == "approval_required"


def test_durable_side_effect_receipt_replays_after_gateway_restart():
    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = Path(tmp) / "effects.sqlite3"
        ledger = DurableSideEffectLedger(ledger_path)
        policy = ActionPolicy(
            allowed_capabilities=frozenset({"repository.write", "release.publish"}),
            require_durable_ledger_for_external_effects=True,
        )
        request = make_request(
            action="publish", risk="external_effect", idempotency_key="publish:run-1"
        )
        called = []
        first = ActionGovernanceGateway(policy, side_effect_ledger=ledger).execute(
            request, lambda: called.append("publish") or {"provider_id": "op-1"}
        )
        restarted = ActionGovernanceGateway(
            policy, side_effect_ledger=DurableSideEffectLedger(ledger_path)
        )
        replay = restarted.execute(request, lambda: called.append("duplicate") or {"provider_id": "op-2"})
        assert first.executed
        assert replay.replayed and not replay.executed
        assert replay.result_digest == first.result_digest
        assert called == ["publish"]


def test_durable_external_effect_fails_closed_without_ledger_when_required():
    policy = ActionPolicy(
        allowed_capabilities=frozenset({"repository.write", "release.publish"}),
        require_durable_ledger_for_external_effects=True,
    )
    gateway = ActionGovernanceGateway(policy)
    called = []
    request = make_request(action="publish", risk="external_effect", idempotency_key="publish:run-1")
    result = gateway.execute(request, lambda: called.append(True))
    assert not result.executed
    assert result.verdict.reason == "durable_idempotency_ledger_required"
    assert called == []


def test_ambiguous_external_handler_failure_requires_reconciliation():
    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = Path(tmp) / "effects.sqlite3"
        policy = ActionPolicy(
            allowed_capabilities=frozenset({"repository.write", "release.publish"}),
            require_durable_ledger_for_external_effects=True,
        )
        request = make_request(
            action="publish", risk="external_effect", idempotency_key="publish:run-1"
        )
        gateway = ActionGovernanceGateway(policy, side_effect_ledger=DurableSideEffectLedger(ledger_path))
        try:
            gateway.execute(request, lambda: (_ for _ in ()).throw(TimeoutError("provider outcome unknown")))
        except TimeoutError:
            pass
        else:
            raise AssertionError("expected provider timeout")
        called = []
        restarted = ActionGovernanceGateway(
            policy, side_effect_ledger=DurableSideEffectLedger(ledger_path)
        )
        result = restarted.execute(request, lambda: called.append(True))
        assert not result.executed
        assert result.verdict.reason == "side_effect_reconciliation_required"
        assert called == []


def test_duplicate_action_identity_does_not_create_stranded_ledger_intent():
    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = Path(tmp) / "effects.sqlite3"
        ledger = DurableSideEffectLedger(ledger_path)
        policy = ActionPolicy(
            allowed_capabilities=frozenset({"repository.write", "release.publish"}),
            require_durable_ledger_for_external_effects=True,
        )
        gateway = ActionGovernanceGateway(policy, side_effect_ledger=ledger)
        first_request = make_request(
            action="publish", risk="external_effect", idempotency_key="publish:key-1"
        )
        first = gateway.execute(first_request, lambda: {"provider_id": "op-1"})
        second_request = make_request(
            action="publish", risk="external_effect", idempotency_key="publish:key-2"
        )
        second = gateway.execute(second_request, lambda: {"provider_id": "must-not-run"})
        assert first.executed
        assert not second.executed
        assert second.verdict.reason == "duplicate_action_identity"
        assert ledger.get("publish:key-2") is None

def test_external_effect_is_not_called_when_intent_persistence_fails():
    policy = ActionPolicy(
        allowed_capabilities=frozenset({"repository.write", "release.publish"}),
        require_durable_ledger_for_external_effects=True,
    )
    request = make_request(
        action="publish", risk="external_effect", idempotency_key="publish:intent-fault"
    )

    class FailingBeginLedger:
        def begin(self, *_args, **_kwargs):
            raise OSError("simulated intent persistence failure")

    called = []
    gateway = ActionGovernanceGateway(policy, side_effect_ledger=FailingBeginLedger())
    result = gateway.execute(request, lambda: called.append("provider"))

    assert not result.executed
    assert result.verdict.reason == "durable_idempotency_rejected"
    assert called == []


def test_receipt_persistence_failure_requires_reconciliation_without_replay():
    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = Path(tmp) / "effects.sqlite3"
        durable_ledger = DurableSideEffectLedger(ledger_path)
        policy = ActionPolicy(
            allowed_capabilities=frozenset({"repository.write", "release.publish"}),
            require_durable_ledger_for_external_effects=True,
        )
        request = make_request(
            action="publish", risk="external_effect", idempotency_key="publish:receipt-fault"
        )

        class FailingCompleteLedger:
            def __init__(self, delegate):
                self.delegate = delegate

            def begin(self, *args, **kwargs):
                return self.delegate.begin(*args, **kwargs)

            def complete(self, *_args, **_kwargs):
                raise OSError("simulated receipt persistence failure")

            def require_reconciliation(self, *args, **kwargs):
                return self.delegate.require_reconciliation(*args, **kwargs)

        called = []
        faulted_gateway = ActionGovernanceGateway(
            policy, side_effect_ledger=FailingCompleteLedger(durable_ledger)
        )
        try:
            faulted_gateway.execute(
                request, lambda: called.append("provider") or {"provider_id": "op-1"}
            )
        except OSError as error:
            assert "receipt persistence failure" in str(error)
        else:
            raise AssertionError("receipt persistence fault must be visible to the caller")

        assert called == ["provider"]
        assert durable_ledger.get("publish:receipt-fault")["state"] == "executing"

        restarted_gateway = ActionGovernanceGateway(
            policy, side_effect_ledger=DurableSideEffectLedger(ledger_path)
        )
        retry = restarted_gateway.execute(
            request, lambda: called.append("duplicate-provider") or {"provider_id": "op-2"}
        )

        assert not retry.executed
        assert retry.verdict.reason == "side_effect_reconciliation_required"
        assert called == ["provider"]
        assert durable_ledger.get("publish:receipt-fault")["state"] == "reconcile_required"

