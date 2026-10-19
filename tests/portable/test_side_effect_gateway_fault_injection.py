from pathlib import Path

import pytest

from portable.action_governance import ActionGovernanceGateway, ActionPolicy, ActionRequest
from portable.side_effect_ledger import DurableSideEffectLedger


def request():
    return ActionRequest(
        run_id="r1", action_id="a1", actor="supervisor",
        capability="release.publish", action="publish", target="staging",
        risk="external_effect", idempotency_key="r1:task1:publish", task_id="task1",
    )


def gateway(ledger):
    policy = ActionPolicy(
        allowed_capabilities=frozenset({"release.publish"}),
        require_durable_ledger_for_external_effects=True,
    )
    return ActionGovernanceGateway(policy, side_effect_ledger=ledger)


class FailBeginOnce:
    def __init__(self, ledger):
        self.ledger = ledger
        self.failed = False

    def begin(self, key, payload):
        if not self.failed:
            self.failed = True
            raise OSError("injected begin failure")
        return self.ledger.begin(key, payload)

    def complete(self, key, receipt):
        return self.ledger.complete(key, receipt)

    def require_reconciliation(self, key, reason):
        return self.ledger.require_reconciliation(key, reason)


class FailComplete:
    def __init__(self, ledger):
        self.ledger = ledger

    def begin(self, key, payload):
        return self.ledger.begin(key, payload)

    def complete(self, key, receipt):
        raise OSError("injected receipt failure")

    def require_reconciliation(self, key, reason):
        return self.ledger.require_reconciliation(key, reason)


class CommitThenRaise:
    def __init__(self, ledger):
        self.ledger = ledger

    def begin(self, key, payload):
        return self.ledger.begin(key, payload)

    def complete(self, key, receipt):
        self.ledger.complete(key, receipt)
        raise OSError("injected lost acknowledgement")

    def require_reconciliation(self, key, reason):
        return self.ledger.require_reconciliation(key, reason)


class FailReconciliation:
    def __init__(self, ledger):
        self.ledger = ledger

    def begin(self, key, payload):
        return self.ledger.begin(key, payload)

    def complete(self, key, receipt):
        return self.ledger.complete(key, receipt)

    def require_reconciliation(self, key, reason):
        raise OSError("injected reconciliation failure")


def test_begin_failure_does_not_call_handler_and_retry_is_safe(tmp_path: Path):
    ledger = DurableSideEffectLedger(tmp_path / "effects.sqlite3")
    gate = gateway(FailBeginOnce(ledger))
    calls = []

    denied = gate.execute(request(), lambda: calls.append("first"))
    assert not denied.executed
    assert denied.verdict.reason == "durable_idempotency_rejected"
    assert calls == []

    accepted = gate.execute(request(), lambda: calls.append("second") or {"id": "op-1"})
    assert accepted.executed
    assert calls == ["second"]
    assert ledger.get("r1:task1:publish")["state"] == "completed"


def test_receipt_failure_leaves_operation_blocked_after_restart(tmp_path: Path):
    ledger = DurableSideEffectLedger(tmp_path / "effects.sqlite3")
    with pytest.raises(OSError, match="receipt failure"):
        gateway(FailComplete(ledger)).execute(request(), lambda: {"id": "op-1"})

    assert ledger.get("r1:task1:publish")["state"] == "executing"
    calls = []
    blocked = gateway(ledger).execute(request(), lambda: calls.append("duplicate"))
    assert not blocked.executed
    assert blocked.verdict.reason == "side_effect_reconciliation_required"
    assert calls == []
    assert ledger.get("r1:task1:publish")["state"] == "reconcile_required"


def test_lost_commit_ack_replays_receipt_without_second_handler_call(tmp_path: Path):
    ledger = DurableSideEffectLedger(tmp_path / "effects.sqlite3")
    calls = []
    with pytest.raises(OSError, match="lost acknowledgement"):
        gateway(CommitThenRaise(ledger)).execute(
            request(), lambda: calls.append("first") or {"id": "op-1"}
        )

    assert ledger.get("r1:task1:publish")["state"] == "completed"
    replay = gateway(ledger).execute(request(), lambda: calls.append("duplicate"))
    assert replay.replayed
    assert not replay.executed
    assert replay.result_digest
    assert calls == ["first"]


def test_reconciliation_write_failure_still_blocks_restart_replay(tmp_path: Path):
    ledger = DurableSideEffectLedger(tmp_path / "effects.sqlite3")

    def uncertain():
        raise TimeoutError("outcome unknown")

    with pytest.raises(TimeoutError, match="outcome unknown"):
        gateway(FailReconciliation(ledger)).execute(request(), uncertain)

    assert ledger.get("r1:task1:publish")["state"] == "executing"
    calls = []
    blocked = gateway(ledger).execute(request(), lambda: calls.append("duplicate"))
    assert not blocked.executed
    assert blocked.verdict.reason == "side_effect_reconciliation_required"
    assert calls == []
    assert ledger.get("r1:task1:publish")["state"] == "reconcile_required"
