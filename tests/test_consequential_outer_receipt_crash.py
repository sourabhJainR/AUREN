"""Process-death regression for the provider/outer-receipt commit boundary.

The inner provider-operation receipt may be durable while the supervisor's
outer consequential receipt is not. Restart must reconcile the outer record
without dispatching the handler a second time.
"""
from __future__ import annotations

import multiprocessing
import os
import sqlite3
from pathlib import Path

from portable.execution_controller import ExecutionController
from portable.side_effect_ledger import DurableSideEffectLedger


def _crash_boundary_context_provider(_request):
    return {"trusted": True}


def _crash_boundary_ci_provider(_request):
    return {"status": "completed", "conclusion": "success"}


def _crash_boundary_handler(payload):
    marker = Path(payload["marker_path"])
    with marker.open("a", encoding="utf-8") as stream:
        stream.write("dispatched\\n")
    return {
        "success": True,
        "detail": "provider operation completed",
        "evidence": ["test://provider-operation/receipt-1"],
    }


def _register(controller: ExecutionController) -> None:
    controller.register_handler(
        "outer-receipt-crash-action",
        _crash_boundary_handler,
        promotion_gate_policy={"context_handler": "outer-receipt-crash-context"},
        ci_gate_policy={
            "expected_head_sha": "b" * 40,
            "required_checks": ["required-ci"],
            "evidence_handler": "outer-receipt-crash-ci",
        },
        execution_class="consequential",
    )
    controller.register_handler(
        "outer-receipt-crash-context",
        _crash_boundary_context_provider,
        execution_class="context_provider",
    )
    controller.register_handler(
        "outer-receipt-crash-ci",
        _crash_boundary_ci_provider,
        execution_class="context_provider",
    )
    # Keep this test focused on the durable cross-ledger boundary. Gate behavior
    # is covered by the controller's dedicated policy and exact-head suites.
    controller._preflight_gates = lambda _task: (True, "test gate passed", [])


def _die_after_inner_receipt_before_outer_receipt(root: str, task_id: str) -> None:
    controller = ExecutionController(root)
    _register(controller)
    original_complete = DurableSideEffectLedger.complete

    def crash_before_outer_complete(ledger, idempotency_key, receipt):
        if not idempotency_key.startswith("provider-operation:"):
            # The provider-operation receipt has already committed by this point.
            # Exit before the outer task-level receipt can be written.
            os._exit(91)
        return original_complete(ledger, idempotency_key, receipt)

    DurableSideEffectLedger.complete = crash_before_outer_complete
    controller.run_once(task_id)
    os._exit(92)


def test_restart_blocks_outer_receipt_gap_without_replaying_provider(tmp_path: Path):
    marker = tmp_path / "provider-dispatches.log"
    controller = ExecutionController(tmp_path)
    _register(controller)
    task = controller.submit(
        "outer-receipt-crash-action",
        {
            "marker_path": str(marker),
            "target": "staging",
            "revision": "abc123",
            "auren_idempotency": {"key": "outer-receipt-crash:staging:abc123"},
        },
        goal="prove provider receipt survives outer receipt crash",
        work_key="test:outer-provider-receipt-crash:staging:abc123",
    )

    process = multiprocessing.get_context("spawn").Process(
        target=_die_after_inner_receipt_before_outer_receipt,
        args=(str(tmp_path), task.id),
    )
    process.start()
    process.join(20)
    if process.is_alive():
        process.terminate()
        process.join(3)
        raise AssertionError("cross-ledger crash fixture exceeded its time budget")
    assert process.exitcode == 91
    assert marker.read_text(encoding="utf-8").splitlines() == ["dispatched"]

    outer_key = "consequential-idempotency:outer-receipt-crash:staging:abc123"
    provider_key = "provider-operation:" + outer_key
    ledger = DurableSideEffectLedger(controller.db_path)
    assert ledger.get(provider_key)["state"] == "completed"
    assert ledger.get(outer_key)["state"] == "executing"

    # A crashed process leaves a running task lease. Expire that lease explicitly
    # so the restarted controller can claim it and exercise the durable outer gate.
    with sqlite3.connect(controller.db_path) as db:
        db.execute(
            "UPDATE tasks SET lease_until=?, heartbeat_at=? WHERE id=?",
            ("2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00", task.id),
        )
        db.commit()

    restarted = ExecutionController(tmp_path)
    _register(restarted)
    assert restarted.run_once(task.id) is None
    assert restarted.task(task.id).state == "blocked_reconciliation"
    assert ledger.get(provider_key)["state"] == "completed"
    assert ledger.get(outer_key)["state"] == "reconcile_required"
    assert marker.read_text(encoding="utf-8").splitlines() == ["dispatched"]
    assert any(
        event.get("event") == "provider_operation_blocked"
        and event.get("task_id") == task.id
        for event in restarted.progress_journal._events()
    )
