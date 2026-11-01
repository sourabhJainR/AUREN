from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import sqlite3
import time
from types import SimpleNamespace

from portable import dashboard_server
from portable.agency_release_lifecycle import ArtifactStore
from portable.response_delivery_artifacts import ResponseArtifactRepository
from portable.progress_journal import ProgressJournal
from portable.execution_controller import ExecutionController
from portable.response_delivery_supervisor import ResponseDeliverySupervisor
from portable.hybrid_model_router import HybridModelRouter


def _start_server(monkeypatch, root: Path, result, *, delivery_deadline_seconds=600.0):
    if result is not None:
        monkeypatch.setattr(HybridModelRouter, "route", lambda _self, _request: result)
    runtime_root = root / ".auren"
    store = ArtifactStore(runtime_root / "releases")
    handler = type("ChunkDeliveryTestHandler", (dashboard_server._Handler,), {
        "artifact_store": store,
        "response_repository": ResponseArtifactRepository(store),
        "progress_journal": ProgressJournal(runtime_root / "progress.jsonl"),
        "delivery_deadline_seconds": delivery_deadline_seconds,
        "delivery_supervisor": ResponseDeliverySupervisor(
            ExecutionController(root), runtime_root / "progress.jsonl"
        ),
    })
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _request(server, method, path, payload=None, *, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request_headers = dict(headers or {})
    if body is not None:
        request_headers.setdefault("Content-Type", "application/json")
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    response_headers = dict(response.getheaders())
    connection.close()
    decoded = json.loads(raw.decode("utf-8")) if raw else None
    return status, response_headers, decoded


def _stop_server(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_chunked_route_supports_acknowledged_resume_after_server_restart(monkeypatch, tmp_path):
    answer = ("Research evidence with Unicode ₹ € 東京 and source provenance. " * 900)
    result = SimpleNamespace(
        provider="test", model="fixture", success=True, attempts=1, latency_ms=1.0,
        resource_usage={}, fallback_reason=None, result={"answer": answer}, error=None,
    )
    server, thread = _start_server(monkeypatch, tmp_path, result)
    try:
        status, headers, manifest = _request(
            server, "POST", "/api/route", {"task": "build multi-ticker evidence brief"},
            headers={"Accept": dashboard_server.CHUNKED_RESPONSE_ACCEPT},
        )
        assert status == 202
        assert headers["X-AUREN-Delivery-Mode"] == "chunked-pull-v1"
        assert headers["X-AUREN-Chunk-Ack"] == "receiver-confirmed"
        assert manifest["delivery_mode"] == "chunked-pull-v1"
        assert manifest["total_chunks"] > 1
        response_id = manifest["response_id"]

        status, _, first = _request(server, "GET", manifest["next"])
        assert status == 200
        assert first["chunk_index"] == 1
        assert first["utf8_bytes"] <= dashboard_server.MAX_DELIVERY_CHUNK_BYTES
        first_text = first["text"]

        # A read is only an offer. Without an explicit receiver ack, the next
        # read returns the same chunk and the durable cursor remains at zero.
        status, _, offered_again = _request(server, "GET", manifest["next"])
        assert status == 200
        assert offered_again["chunk_index"] == 1
        status, _, checkpoint = _request(server, "GET", manifest["status"])
        assert status == 200
        assert checkpoint["acknowledged_index"] == 0

        bad_ack = {
            "chunk_index": 1, "chunk_sha256": "0" * 64,
            "response_sha256": manifest["response_sha256"],
            "total_chunks": manifest["total_chunks"],
        }
        status, _, _ = _request(server, "POST", manifest["ack"], bad_ack)
        assert status == 409

        first_ack = {
            "chunk_index": first["chunk_index"], "chunk_sha256": first["chunk_sha256"],
            "response_sha256": manifest["response_sha256"],
            "total_chunks": manifest["total_chunks"],
        }
        status, _, ack_result = _request(server, "POST", manifest["ack"], first_ack)
        assert status == 200 and ack_result["acknowledged_index"] == 1
        task_id = server.RequestHandlerClass._delivery_task_id(response_id, manifest["delivery_id"])
        assert server.RequestHandlerClass.progress_journal.snapshot(task_id)["state"] == "running"
        # Duplicate acknowledgement is idempotent.
        status, _, duplicate = _request(server, "POST", manifest["ack"], first_ack)
        assert status == 200 and duplicate["acknowledged_index"] == 1
    finally:
        _stop_server(server, thread)

    # Simulate an abrupt host exit. The next host may reclaim only after the
    # durable worker lease and heartbeat are expired.
    task_id = server.RequestHandlerClass._delivery_task_id(response_id, manifest["delivery_id"])
    with sqlite3.connect(tmp_path / ".auren" / "execution.sqlite3") as db:
        db.execute(
            "UPDATE tasks SET lease_until=?,heartbeat_at=? WHERE id=?",
            ("2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00", task_id),
        )

    # A fresh host reconstructs the artifact, parent task, and acknowledged cursor.
    server, thread = _start_server(monkeypatch, tmp_path, None)
    try:
        status, _, checkpoint = _request(server, "GET", manifest["status"])
        assert status == 200
        assert checkpoint["acknowledged_index"] == 1

        parts = [first_text]
        while True:
            status, _, chunk = _request(server, "GET", manifest["next"])
            assert status == 200
            if chunk.get("all_chunks_acknowledged"):
                break
            assert chunk["chunk_index"] == len(parts) + 1
            parts.append(chunk["text"])
            ack = {
                "chunk_index": chunk["chunk_index"],
                "chunk_sha256": chunk["chunk_sha256"],
                "response_sha256": manifest["response_sha256"],
                "total_chunks": manifest["total_chunks"],
            }
            status, _, acknowledged = _request(server, "POST", manifest["ack"], ack)
            assert status == 200
            assert acknowledged["acknowledged_index"] == chunk["chunk_index"]

        assembled = "".join(parts)
        assert hashlib.sha256(assembled.encode("utf-8")).hexdigest() == response_id
        assert len(assembled.encode("utf-8")) == manifest["total_bytes"]
        parsed = json.loads(assembled)
        assert parsed["result"]["answer"] == answer

        status, _, pre_complete = _request(server, "GET", manifest["status"])
        assert status == 200
        assert pre_complete["all_chunks_acknowledged"] is True
        assert pre_complete["complete"] is False
        assert pre_complete["state"] == "running"

        complete = {
            "response_sha256": response_id,
            "total_chunks": manifest["total_chunks"],
        }
        status, _, receipt = _request(server, "POST", manifest["complete"], complete)
        assert status == 202 and receipt["accepted"] is True
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status, _, final = _request(server, "GET", manifest["status"])
            if status == 200 and final["complete"]:
                break
            time.sleep(0.02)
        assert status == 200 and final["complete"] is True
        assert server.RequestHandlerClass.delivery_supervisor.state(task_id) == "completed"
        assert server.RequestHandlerClass.progress_journal.snapshot(task_id)["state"] == "completed"
    finally:
        _stop_server(server, thread)


def test_chunked_ack_rejects_gaps_and_wrong_manifest(monkeypatch, tmp_path):
    result = SimpleNamespace(
        provider="test", model="fixture", success=True, attempts=1, latency_ms=1.0,
        resource_usage={}, fallback_reason=None, result={"answer": "x" * 25000}, error=None,
    )
    server, thread = _start_server(monkeypatch, tmp_path, result)
    try:
        status, _, manifest = _request(
            server, "POST", "/api/route", {"task": "test"},
            headers={"Accept": dashboard_server.CHUNKED_RESPONSE_ACCEPT},
        )
        assert status == 202
        gap = {
            "chunk_index": 2, "chunk_sha256": "0" * 64,
            "response_sha256": manifest["response_sha256"],
            "total_chunks": manifest["total_chunks"],
        }
        status, _, _ = _request(server, "POST", manifest["ack"], gap)
        assert status == 409
        status, _, checkpoint = _request(server, "GET", manifest["status"])
        assert status == 200 and checkpoint["acknowledged_index"] == 0
        wrong_manifest = dict(gap, chunk_index=1, total_chunks=manifest["total_chunks"] + 1)
        status, _, _ = _request(server, "POST", manifest["ack"], wrong_manifest)
        assert status == 409
    finally:
        _stop_server(server, thread)


def test_tampered_response_artifact_blocks_delivery_after_restart(monkeypatch, tmp_path):
    result = SimpleNamespace(
        provider="test", model="fixture", success=True, attempts=1, latency_ms=1.0,
        resource_usage={}, fallback_reason=None, result={"answer": "evidence-" * 5000}, error=None,
    )
    server, thread = _start_server(monkeypatch, tmp_path, result)
    try:
        status, _, manifest = _request(
            server, "POST", "/api/route", {"task": "tamper check"},
            headers={"Accept": dashboard_server.CHUNKED_RESPONSE_ACCEPT},
        )
        assert status == 202
        artifact_root = tmp_path / ".auren" / "releases" / "artifacts" / manifest["artifact"]["digest"]
        chunk_path = artifact_root / "chunks" / "00000001.utf8"
        chunk_path.write_text("tampered", encoding="utf-8")
    finally:
        _stop_server(server, thread)

    # A new process has no verified in-memory cache and must reject the
    # corrupted content-addressed artifact rather than send altered bytes.
    server, thread = _start_server(monkeypatch, tmp_path, None)
    try:
        status, _, payload = _request(server, "GET", manifest["next"])
        assert status == 503
        assert "automatic replay is blocked" in payload["error"]
        task_id = server.RequestHandlerClass._delivery_task_id(manifest["response_id"], manifest["delivery_id"])
        checkpoint = server.RequestHandlerClass.progress_journal.response_delivery_checkpoint(
            task_id, manifest["response_id"],
        )
        assert checkpoint["acknowledged_index"] == 0
    finally:
        _stop_server(server, thread)


def test_identical_response_bodies_use_independent_receiver_cursors(monkeypatch, tmp_path):
    result = SimpleNamespace(
        provider="test", model="fixture", success=True, attempts=1, latency_ms=1.0,
        resource_usage={}, fallback_reason=None, result={"answer": "same-evidence-" * 4000}, error=None,
    )
    server, thread = _start_server(monkeypatch, tmp_path, result)
    try:
        headers = {"Accept": dashboard_server.CHUNKED_RESPONSE_ACCEPT}
        status, _, first = _request(server, "POST", "/api/route", {"task": "receiver one"}, headers=headers)
        assert status == 202
        status, _, second = _request(server, "POST", "/api/route", {"task": "receiver two"}, headers=headers)
        assert status == 202
        assert first["response_id"] == second["response_id"]
        assert first["delivery_id"] != second["delivery_id"]

        status, _, chunk = _request(server, "GET", first["next"])
        assert status == 200 and chunk["chunk_index"] == 1
        ack = {
            "chunk_index": 1, "chunk_sha256": chunk["chunk_sha256"],
            "response_sha256": first["response_sha256"],
            "total_chunks": first["total_chunks"],
        }
        status, _, _ = _request(server, "POST", first["ack"], ack)
        assert status == 200

        status, _, first_status = _request(server, "GET", first["status"])
        assert status == 200 and first_status["acknowledged_index"] == 1
        status, _, second_status = _request(server, "GET", second["status"])
        assert status == 200 and second_status["acknowledged_index"] == 0
    finally:
        _stop_server(server, thread)



def test_blocked_delivery_cannot_accept_late_ack_or_complete(monkeypatch, tmp_path):
    result = SimpleNamespace(
        provider="test", model="fixture", success=True, attempts=1, latency_ms=1.0,
        resource_usage={}, fallback_reason=None, result={"answer": "blocked-delivery-" * 5000}, error=None,
    )
    server, thread = _start_server(monkeypatch, tmp_path, result)
    try:
        headers = {"Accept": dashboard_server.CHUNKED_RESPONSE_ACCEPT}
        status, _, manifest = _request(server, "POST", "/api/route", {"task": "blocked ack"}, headers=headers)
        assert status == 202
        task_id = server.RequestHandlerClass._delivery_task_id(manifest["response_id"], manifest["delivery_id"])
        journal = server.RequestHandlerClass.progress_journal
        status, _, offered = _request(server, "GET", manifest["next"])
        assert status == 200
        ack = {
            "chunk_index": offered["chunk_index"],
            "chunk_sha256": offered["chunk_sha256"],
            "response_sha256": manifest["response_sha256"],
            "total_chunks": manifest["total_chunks"],
        }
        journal.record(
            task_id, "delivery_reconciliation_required", state="blocked",
            stage="reconciliation", message="operator reconciliation required",
        )
        status, _, _ = _request(server, "POST", manifest["ack"], ack)
        assert status == 409
        status, _, blocked_offer = _request(server, "GET", manifest["next"])
        assert status == 409
        assert blocked_offer["state"] == "blocked"
        snapshot = journal.snapshot(task_id)
        assert snapshot["reported_state"] == "blocked"
        assert not any(
            row["task_id"] == task_id and row["event"] == "response_chunk_acknowledged"
            for row in journal._events()
        )

        status, _, complete_manifest = _request(
            server, "POST", "/api/route", {"task": "blocked completion"}, headers=headers,
        )
        assert status == 202
        complete_task_id = server.RequestHandlerClass._delivery_task_id(
            complete_manifest["response_id"], complete_manifest["delivery_id"],
        )
        while True:
            status, _, chunk = _request(server, "GET", complete_manifest["next"])
            assert status == 200
            if chunk.get("all_chunks_acknowledged"):
                break
            ack = {
                "chunk_index": chunk["chunk_index"],
                "chunk_sha256": chunk["chunk_sha256"],
                "response_sha256": complete_manifest["response_sha256"],
                "total_chunks": complete_manifest["total_chunks"],
            }
            status, _, _ = _request(server, "POST", complete_manifest["ack"], ack)
            assert status == 200

        journal.record(
            complete_task_id, "delivery_reconciliation_required", state="blocked",
            stage="reconciliation", message="operator reconciliation required",
        )
        complete = {
            "response_sha256": complete_manifest["response_sha256"],
            "total_chunks": complete_manifest["total_chunks"],
        }
        status, _, rejected = _request(server, "POST", complete_manifest["complete"], complete)
        assert status == 503
        assert "completion is not confirmed" in rejected["error"]
        final = journal.snapshot(complete_task_id)
        assert final["reported_state"] == "blocked"
        assert not any(
            row["task_id"] == complete_task_id and row["event"] == "response_delivery_completed"
            for row in journal._events()
        )
    finally:
        _stop_server(server, thread)


def test_stale_delivery_cannot_accept_ack_or_complete(monkeypatch, tmp_path):
    result = SimpleNamespace(
        provider="test", model="fixture", success=True, attempts=1, latency_ms=1.0,
        resource_usage={}, fallback_reason=None, result={"answer": "stale-delivery-" * 5000}, error=None,
    )
    server, thread = _start_server(monkeypatch, tmp_path, result)
    try:
        headers = {"Accept": dashboard_server.CHUNKED_RESPONSE_ACCEPT}
        status, _, manifest = _request(server, "POST", "/api/route", {"task": "stale ack"}, headers=headers)
        assert status == 202
        task_id = server.RequestHandlerClass._delivery_task_id(manifest["response_id"], manifest["delivery_id"])
        journal = server.RequestHandlerClass.progress_journal
        status, _, offered = _request(server, "GET", manifest["next"])
        assert status == 200
        ack = {
            "chunk_index": offered["chunk_index"],
            "chunk_sha256": offered["chunk_sha256"],
            "response_sha256": manifest["response_sha256"],
            "total_chunks": manifest["total_chunks"],
        }
        journal.record(
            task_id, "delivery_became_stale", state="running", stage="heartbeat",
            message="stale delivery fixture",
            timestamp="2026-10-30T00:00:00+00:00",
        )
        status, _, rejected = _request(server, "POST", manifest["ack"], ack)
        assert status == 503
        assert "completion is not confirmed" in rejected["error"] or "delivery state unavailable" in rejected["error"]
        snapshot = journal.snapshot(task_id)
        assert snapshot["state"] == "stale"
        assert snapshot["reported_state"] == "running"
        assert not any(
            row["task_id"] == task_id and row["event"] == "response_chunk_acknowledged"
            for row in journal._events()
        )

        status, _, complete_manifest = _request(
            server, "POST", "/api/route", {"task": "stale completion"}, headers=headers,
        )
        assert status == 202
        complete_task_id = server.RequestHandlerClass._delivery_task_id(
            complete_manifest["response_id"], complete_manifest["delivery_id"],
        )
        while True:
            status, _, chunk = _request(server, "GET", complete_manifest["next"])
            assert status == 200
            if chunk.get("all_chunks_acknowledged"):
                break
            ack = {
                "chunk_index": chunk["chunk_index"],
                "chunk_sha256": chunk["chunk_sha256"],
                "response_sha256": complete_manifest["response_sha256"],
                "total_chunks": complete_manifest["total_chunks"],
            }
            status, _, _ = _request(server, "POST", complete_manifest["ack"], ack)
            assert status == 200

        journal.record(
            complete_task_id, "delivery_became_stale", state="running", stage="heartbeat",
            message="stale delivery fixture",
            timestamp="2026-10-30T00:00:00+00:00",
        )
        complete = {
            "response_sha256": complete_manifest["response_sha256"],
            "total_chunks": complete_manifest["total_chunks"],
        }
        status, _, rejected = _request(server, "POST", complete_manifest["complete"], complete)
        assert status == 503
        final = journal.snapshot(complete_task_id)
        assert final["state"] == "stale"
        assert final["reported_state"] == "running"
        assert not any(
            row["task_id"] == complete_task_id and row["event"] == "response_delivery_completed"
            for row in journal._events()
        )
    finally:
        _stop_server(server, thread)

def test_concurrent_duplicate_acknowledgements_are_idempotent(monkeypatch, tmp_path):
    result = SimpleNamespace(
        provider="test", model="fixture", success=True, attempts=1, latency_ms=1.0,
        resource_usage={}, fallback_reason=None, result={"answer": "parallel-ack-" * 3000}, error=None,
    )
    server, thread = _start_server(monkeypatch, tmp_path, result)
    try:
        status, _, manifest = _request(
            server, "POST", "/api/route", {"task": "parallel duplicate ack"},
            headers={"Accept": dashboard_server.CHUNKED_RESPONSE_ACCEPT},
        )
        assert status == 202
        status, _, chunk = _request(server, "GET", manifest["next"])
        assert status == 200
        ack = {
            "chunk_index": 1, "chunk_sha256": chunk["chunk_sha256"],
            "response_sha256": manifest["response_sha256"],
            "total_chunks": manifest["total_chunks"],
        }
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(
                lambda _index: _request(server, "POST", manifest["ack"], ack),
                range(8),
            ))
        assert all(status == 200 for status, _headers, _body in results)
        task_id = server.RequestHandlerClass._delivery_task_id(
            manifest["response_id"], manifest["delivery_id"],
        )
        checkpoint = server.RequestHandlerClass.progress_journal.response_delivery_checkpoint(
            task_id, manifest["response_id"],
        )
        assert checkpoint["acknowledged_index"] == 1
        rows = [
            row for row in server.RequestHandlerClass.progress_journal._events()
            if row["task_id"] == task_id and row["event"] == "response_chunk_acknowledged"
        ]
        assert len(rows) == 1
    finally:
        _stop_server(server, thread)


def test_host_boundary_cancellation_keeps_parent_nonterminal_until_supervisor_confirms(monkeypatch, tmp_path):
    result = SimpleNamespace(
        provider="test", model="fixture", success=True, attempts=1, latency_ms=1.0,
        resource_usage={}, fallback_reason=None, result={"answer": "cancel me " * 3000}, error=None,
    )
    server, thread = _start_server(monkeypatch, tmp_path, result)
    try:
        status, _, manifest = _request(
            server, "POST", "/api/route", {"task": "cancellation fixture"},
            headers={"Accept": dashboard_server.CHUNKED_RESPONSE_ACCEPT},
        )
        assert status == 202
        task_id = server.RequestHandlerClass._delivery_task_id(
            manifest["response_id"], manifest["delivery_id"]
        )
        status, _, receipt = _request(server, "POST", manifest["cancel"], {
            "response_sha256": manifest["response_sha256"],
            "total_chunks": manifest["total_chunks"],
        })
        assert status == 202 and receipt["cancellation_requested"] is True
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = server.RequestHandlerClass.delivery_supervisor.state(task_id)
            if state == "cancelled":
                break
            time.sleep(0.02)
        assert state == "cancelled"
        status, _, _ = _request(server, "GET", manifest["next"])
        assert status == 409
    finally:
        _stop_server(server, thread)


def test_host_boundary_deadline_expires_parent_without_receiver_ack(monkeypatch, tmp_path):
    result = SimpleNamespace(
        provider="test", model="fixture", success=True, attempts=1, latency_ms=1.0,
        resource_usage={}, fallback_reason=None, result={"answer": "deadline fixture " * 3000}, error=None,
    )
    server, thread = _start_server(
        monkeypatch, tmp_path, result, delivery_deadline_seconds=0.25
    )
    try:
        status, _, manifest = _request(
            server, "POST", "/api/route", {"task": "deadline fixture"},
            headers={"Accept": dashboard_server.CHUNKED_RESPONSE_ACCEPT},
        )
        assert status == 202
        task_id = server.RequestHandlerClass._delivery_task_id(
            manifest["response_id"], manifest["delivery_id"]
        )
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = server.RequestHandlerClass.delivery_supervisor.state(task_id)
            if state == "failed":
                break
            time.sleep(0.02)
        assert state == "failed"
        status, _, _ = _request(server, "GET", manifest["next"])
        assert status == 409
    finally:
        _stop_server(server, thread)


def test_session_delivery_metrics_report_durable_acknowledgements(monkeypatch, tmp_path):
    result = SimpleNamespace(
        provider="test", model="fixture", success=True, attempts=1, latency_ms=1.0,
        resource_usage={}, fallback_reason=None, result={"answer": "metrics-" * 4000}, error=None,
    )
    server, thread = _start_server(monkeypatch, tmp_path, result)
    try:
        status, _, manifest = _request(
            server, "POST", "/api/route", {"task": "delivery metrics"},
            headers={"Accept": dashboard_server.CHUNKED_RESPONSE_ACCEPT},
        )
        assert status == 202
        status, _, chunk = _request(server, "GET", manifest["next"])
        assert status == 200
        ack = {
            "chunk_index": chunk["chunk_index"], "chunk_sha256": chunk["chunk_sha256"],
            "response_sha256": manifest["response_sha256"],
            "total_chunks": manifest["total_chunks"],
        }
        status, _, _ = _request(server, "POST", manifest["ack"], ack)
        assert status == 200
        metrics = server.RequestHandlerClass.delivery_supervisor.metrics_snapshot()
        assert metrics["acknowledged_chunks"] == 1
        assert metrics["running_tasks"] >= 1
        assert metrics["capacity"]["worker_limit"] == 8
        assert metrics["integrity"]["unknown_outcomes_block_replay"] is True

        # Session Control Center APIs expose the same delivery counters, not a
        # separately reconstructed UI-only approximation.
        status, _, sessions = _request(server, "GET", "/api/sessions?limit=10")
        assert status == 200
        assert sessions["delivery_metrics"]["acknowledged_chunks"] == 1
        status, _, center = _request(server, "GET", "/api/control-center")
        assert status == 200
        assert center["delivery_metrics"]["acknowledged_chunks"] == 1
    finally:
        _stop_server(server, thread)



def test_http_delivery_charges_one_shared_crew_budget_across_deliveries_and_restart(monkeypatch, tmp_path):
    from portable.runtime_recovery import RuntimeExecutionGuard

    answer = "crew-budget-output-" * 5000
    result = SimpleNamespace(
        provider="test", model="fixture", success=True, attempts=1, latency_ms=1.0,
        resource_usage={}, fallback_reason=None, result={"answer": answer}, error=None,
    )
    server, thread = _start_server(monkeypatch, tmp_path, result)
    guard = RuntimeExecutionGuard(max_delivery_bytes=dashboard_server.MAX_DELIVERY_CHUNK_BYTES)
    server.RequestHandlerClass.delivery_execution_guard = guard
    server.RequestHandlerClass.delivery_budget_id = "crew-budget-regression"
    try:
        headers = {"Accept": dashboard_server.CHUNKED_RESPONSE_ACCEPT}
        status, _, first = _request(server, "POST", "/api/route", {"task": "crew budget one"}, headers=headers)
        assert status == 202
        status, _, chunk = _request(server, "GET", first["next"])
        assert status == 200
        assert chunk["utf8_bytes"] <= dashboard_server.MAX_DELIVERY_CHUNK_BYTES
        assert guard.delivery_bytes_used == chunk["utf8_bytes"]

        # Re-reading an unacknowledged chunk is idempotent: it does not consume
        # budget twice and the same bytes remain available for receiver retry.
        status, _, repeated = _request(server, "GET", first["next"])
        assert status == 200
        assert repeated["chunk_sha256"] == chunk["chunk_sha256"]
        assert guard.delivery_bytes_used == chunk["utf8_bytes"]

        status, _, second = _request(server, "POST", "/api/route", {"task": "crew budget two"}, headers=headers)
        assert status == 202
        status, _, blocked = _request(server, "GET", second["next"])
        assert status == 429
        assert "shared crew delivery byte budget exhausted" in blocked["error"]
        assert server.RequestHandlerClass.delivery_supervisor.delivery_budget_snapshot(
            server.RequestHandlerClass._delivery_task_id(second["response_id"], second["delivery_id"])
        )["delivery_bytes_used"] == chunk["utf8_bytes"]
    finally:
        _stop_server(server, thread)

    # A new host restores the same crew budget ledger. A repeated offer remains
    # idempotent; a different delivery cannot spend the exhausted budget again.
    server, thread = _start_server(monkeypatch, tmp_path, None)
    server.RequestHandlerClass.delivery_budget_id = "crew-budget-regression"
    try:
        status, _, repeated = _request(server, "GET", first["next"])
        assert status == 200
        assert repeated["chunk_sha256"] == chunk["chunk_sha256"]
        status, _, blocked = _request(server, "GET", second["next"])
        assert status == 429
        assert "shared crew delivery byte budget exhausted" in blocked["error"]
    finally:
        _stop_server(server, thread)
