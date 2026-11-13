from __future__ import annotations

from types import SimpleNamespace

import pytest

from portable.local_composition import _resolve_local_vibe_approval


class _Operator:
    principal_digest = "a" * 64

    def __init__(self, valid=True):
        self.valid = valid

    def verify_digest(self, digest):
        return self.valid and digest == self.principal_digest


class _ApprovalStore:
    def __init__(self):
        self.calls = []

    def decide(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return SimpleNamespace(status="approved", approval_id=args[0])


def _composition(valid=True):
    value = SimpleNamespace(operator=_Operator(valid), approval_store=_ApprovalStore())
    return value


def test_single_user_owner_auto_records_a_durable_approval_without_waking_worker():
    composition = _composition()
    pending = SimpleNamespace(status="pending", approval_id="approval-1")
    config = SimpleNamespace(approval_mode="owner_auto")

    result = _resolve_local_vibe_approval(composition, pending, config)

    assert result.status == "approved"
    assert len(composition.approval_store.calls) == 1
    args, kwargs = composition.approval_store.calls[0]
    assert args == ("approval-1", "approve")
    assert kwargs["approver_id"] == composition.operator.principal_digest
    assert kwargs["roles"] == ("engineering_manager",)
    assert kwargs["scope"] == "repo:write"
    assert kwargs["wake_worker"] is False


def test_manual_mode_keeps_approval_pending():
    composition = _composition()
    pending = SimpleNamespace(status="pending", approval_id="approval-2")
    config = SimpleNamespace(approval_mode="manual")

    assert _resolve_local_vibe_approval(composition, pending, config) is pending
    assert composition.approval_store.calls == []


def test_remote_request_does_not_inherit_local_owner_auto_approval():
    composition = _composition()
    pending = SimpleNamespace(status="pending", approval_id="approval-remote")
    config = SimpleNamespace(approval_mode="owner_auto")

    assert _resolve_local_vibe_approval(
        composition, pending, config, remote_request=True,
    ) is pending
    assert composition.approval_store.calls == []


def test_owner_auto_fails_closed_when_host_operator_identity_is_invalid():
    composition = _composition(valid=False)
    pending = SimpleNamespace(status="pending", approval_id="approval-3")
    config = SimpleNamespace(approval_mode="owner_auto")

    with pytest.raises(PermissionError, match="host-owned local operator identity"):
        _resolve_local_vibe_approval(composition, pending, config)
    assert composition.approval_store.calls == []
