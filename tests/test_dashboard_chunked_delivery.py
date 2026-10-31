from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
import time
from types import SimpleNamespace

from portable import dashboard_server
from portable.agency_release_lifecycle import ArtifactStore
from portable.response_delivery_artifacts import ResponseArtifactRepository
from portable.progress_journal import ProgressJournal
from portable.hybrid_model_router import HybridModelRouter
from portable.execution_controller import ExecutionController
from portable.dashboard_delivery_task import HANDLER_NAME, dashboard_response_delivery_handler


def _start_server(monkeypatch, root: Path, result):
    if result is not None:
        monkeypatch.setattr(HybridModelRouter, "route", lambda _self, _request, **_kwargs: result)
    runtime_root = root / ".auren"
    store = ArtifactStore(runtime_root / "releases")
    controller = ExecutionController(root)
    controller.register_handler(
        HANDLER_NAME, dashboard_response_delivery_handler, execution_class="read_only",
    )
    handler = type("ChunkDeliveryTestHandler", (dashboard_server._Handler,), {
        "artifact_store": store,
        "response_repository": ResponseArtifactRepository(store),
        "progress_journal": controller.progress_journal,
        "execution_controller": controller,
        "project_root": root,
        "delivery_workers": {},
        "delivery_workers_lock": Lock(),
        "delivery_task_ids": set(),
    })
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _request(server, method, path, payload=None, *, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    if method == "POST" and path == "/api/route" and payload is not None:
        payload = dict(payload)
        payload.setdefault("delivery_timeout_seconds", 10.0)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request_headers = dict(headers or {})
    if method == "POST" and path == "/api/route":
        request_headers.setdefault("Idempotency-Key", hashlib.sha256(body or b"").hexdigest()[:32])
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


def _stop_server(server, thread, *, cancel=True):
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    handler = server.RequestHandlerClass
    controller = getattr(handler, "execution_controller", None)
    if cancel and controller is not None:
        journal = handler.progress_journal
        for task_id in list(getattr(handler, "delivery_task_ids", set())):
            task = controller.task(task_id)
            snapshot = journal.snapshot(task_id)
            if task is not None and task.state in {"pending", "running"} and snapshot.get("state") in {"queued", "running"}:
                controller.request_cancellation(task_id, reason="dashboard test cleanup")
        for worker in list(getattr(handler, "delivery_workers", {}).values()):
            worker.join(timeout=2)


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
        parent = server.RequestHandlerClass.execution_controller.task(task_id)
        assert parent is not None and parent.state == "running"
        assert parent.work_unit_id
        # Duplicate acknowledgement is idempotent.
        status, _, duplicate = _request(server, "POST", manifest["ack"], first_ack)
        assert status == 200 and duplicate["acknowledged_index"] == 1
    finally:
        _stop_server(server, thread, cancel=False)

    # A fresh HTTP server/controller instance reads the same durable task, artifact,
    # and acknowledgement cursor. The old in-process worker retains its lease until
    # the receiver confirms the final digest.
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
        assert status in {200, 202} and receipt["receiver_confirmed"] is True
        deadline = time.monotonic() + 3
        final = None
        while time.monotonic() < deadline:
            status, _, final = _request(server, "GET", manifest["status"])
            if status == 200 and final.get("complete") is True:
                break
            time.sleep(0.02)
        assert status == 200 and final["complete"] is True
        task_id = dashboard_server._Handler._delivery_task_id(response_id, manifest["delivery_id"])
        assert server.RequestHandlerClass.progress_journal.snapshot(task_id)["state"] == "completed"
        parent = server.RequestHandlerClass.execution_controller.task(task_id)
        assert parent is not None and parent.state == "completed"
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
        _stop_server(server, thread, cancel=False)

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
        task_id = dashboard_server._Handler._delivery_task_id(manifest["response_id"], manifest["delivery_id"])
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
        complete_task_id = dashboard_server._Handler._delivery_task_id(
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
        task_id = dashboard_server._Handler._delivery_task_id(manifest["response_id"], manifest["delivery_id"])
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
        complete_task_id = dashboard_server._Handler._delivery_task_id(
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



def test_chunked_delivery_uses_one_supervisor_task_for_repeated_idempotency_key(monkeypatch, tmp_path):
    calls = []
    result = SimpleNamespace(
        provider="test", model="fixture", success=True, attempts=1, latency_ms=1.0,
        resource_usage={}, fallback_reason=None, result={"answer": "idempotent-" * 2000}, error=None,
    )
    server, thread = _start_server(monkeypatch, tmp_path, None)
    monkeypatch.setattr(
        HybridModelRouter, "route",
        lambda _self, request, **_kwargs: (calls.append(request) or result),
    )
    try:
        headers = {
            "Accept": dashboard_server.CHUNKED_RESPONSE_ACCEPT,
            "Idempotency-Key": "same-logical-request-01",
        }
        payload = {"task": "same durable request"}
        status, _, first = _request(server, "POST", "/api/route", payload, headers=headers)
        assert status == 202
        status, _, second = _request(server, "POST", "/api/route", payload, headers=headers)
        assert status == 202
        assert first["delivery_id"] == second["delivery_id"]
        assert len(calls) == 1
        task = server.RequestHandlerClass.execution_controller.task(first["delivery_id"])
        assert task is not None and task.work_unit_id
    finally:
        _stop_server(server, thread)


def test_idempotency_key_cannot_be_reused_for_a_different_request(monkeypatch, tmp_path):
    result = SimpleNamespace(
        provider="test", model="fixture", success=True, attempts=1, latency_ms=1.0,
        resource_usage={}, fallback_reason=None, result={"answer": "one"}, error=None,
    )
    server, thread = _start_server(monkeypatch, tmp_path, result)
    try:
        headers = {
            "Accept": dashboard_server.CHUNKED_RESPONSE_ACCEPT,
            "Idempotency-Key": "one-key-one-intent",
        }
        status, _, first = _request(server, "POST", "/api/route", {"task": "first intent"}, headers=headers)
        assert status == 202
        status, _, rejected = _request(server, "POST", "/api/route", {"task": "different intent"}, headers=headers)
        assert status == 409
        assert "already bound" in rejected["error"]
        assert first["delivery_id"]
    finally:
        _stop_server(server, thread)


def test_delivery_parent_cancellation_prevents_late_receiver_completion(monkeypatch, tmp_path):
    result = SimpleNamespace(
        provider="test", model="fixture", success=True, attempts=1, latency_ms=1.0,
        resource_usage={}, fallback_reason=None, result={"answer": "cancel-me-" * 2000}, error=None,
    )
    server, thread = _start_server(monkeypatch, tmp_path, result)
    try:
        status, _, manifest = _request(
            server, "POST", "/api/route", {"task": "cancel parent"},
            headers={"Accept": dashboard_server.CHUNKED_RESPONSE_ACCEPT},
        )
        assert status == 202 and manifest["ready"] is True
        task_id = manifest["delivery_id"]
        controller = server.RequestHandlerClass.execution_controller
        assert controller.request_cancellation(task_id, reason="cancel response delivery")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            task = controller.task(task_id)
            if task is not None and task.state == "cancelled":
                break
            time.sleep(0.02)
        task = controller.task(task_id)
        assert task is not None and task.state == "cancelled"
        complete = {
            "response_sha256": manifest["response_sha256"],
            "total_chunks": manifest["total_chunks"],
        }
        status, _, rejected = _request(server, "POST", manifest["complete"], complete)
        assert status in {409, 503}
        assert rejected and not rejected.get("completed")
    finally:
        _stop_server(server, thread)


def test_delivery_deadline_expires_parent_without_claiming_completion(monkeypatch, tmp_path):
    result = SimpleNamespace(
        provider="test", model="fixture", success=True, attempts=1, latency_ms=1.0,
        resource_usage={}, fallback_reason=None, result={"answer": "deadline-" * 2000}, error=None,
    )
    server, thread = _start_server(monkeypatch, tmp_path, result)
    try:
        status, _, manifest = _request(
            server, "POST", "/api/route",
            {"task": "deadline parent", "delivery_timeout_seconds": 1.0},
            headers={"Accept": dashboard_server.CHUNKED_RESPONSE_ACCEPT},
        )
        assert status == 202 and manifest["ready"] is True
        task_id = manifest["delivery_id"]
        controller = server.RequestHandlerClass.execution_controller
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            task = controller.task(task_id)
            if task is not None and task.state == "failed":
                break
            time.sleep(0.02)
        task = controller.task(task_id)
        assert task is not None and task.state == "failed"
        status, _, snapshot = _request(server, "GET", manifest["status"])
        assert status == 200 and snapshot["complete"] is False
        assert snapshot["task_state"] == "failed"
    finally:
        _stop_server(server, thread)


def test_restart_after_receiver_confirmation_resumes_parent_without_provider_replay(monkeypatch, tmp_path):
    import json
    import sqlite3
    from datetime import datetime, timezone, timedelta

    root = tmp_path
    controller = ExecutionController(root)
    controller.register_handler(
        HANDLER_NAME, dashboard_response_delivery_handler, execution_class="read_only",
    )
    task = controller.submit(
        HANDLER_NAME,
        {
            "project_root": str(root), "task": "resume staged response",
            "capability": "general", "privacy": "private",
            "max_attempts": 1, "timeout_seconds": 1.0,
        },
        work_key="test-dashboard-resume-after-confirmation",
        deadline_seconds=60,
    )
    claimed = controller._claim(task.id)
    assert claimed is not None
    repository = ResponseArtifactRepository(ArtifactStore(root / ".auren" / "releases"))
    body = json.dumps(
        {"provider": "test", "model": "fixture", "success": True, "attempts": 1,
         "latency_ms": 1.0, "resource_usage": {}, "fallback_reason": None,
         "result": {"answer": "persisted"}, "error": None},
        separators=(",", ":"), ensure_ascii=False,
    )
    stored = repository.stage(body)
    journal = controller.progress_journal
    journal.record_response_artifact(
        task.id, response_id=stored.manifest.response_id,
        artifact_sha256=stored.artifact.digest,
    )
    for chunk in stored.chunks:
        journal.record_response_chunk_ack(
            task.id, response_id=stored.manifest.response_id,
            acknowledged_index=chunk.index, total_chunks=stored.manifest.total_chunks,
            response_sha256=stored.manifest.sha256,
        )
    journal.record_response_delivery_confirmation(
        task.id, response_id=stored.manifest.response_id,
        total_chunks=stored.manifest.total_chunks,
        response_sha256=stored.manifest.sha256,
    )
    with sqlite3.connect(controller.db_path) as db:
        expired = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        db.execute("UPDATE tasks SET lease_until=? WHERE id=?", (expired, task.id))
        db.commit()

    recovered = ExecutionController(root)
    recovered.register_handler(
        HANDLER_NAME, dashboard_response_delivery_handler, execution_class="read_only",
    )
    def must_not_route(_self, _request, **_kwargs):
        raise AssertionError("recovery must reuse the staged artifact and never replay provider dispatch")
    monkeypatch.setattr(HybridModelRouter, "route", must_not_route)
    recovered.run_once(task.id)
    current = recovered.task(task.id)
    assert current is not None and current.state == "completed"
    snapshot = recovered.progress_journal.snapshot(task.id)
    assert snapshot["reported_state"] == "completed"
    assert recovered.progress_journal.response_delivery_confirmation(
        task.id, stored.manifest.response_id, stored.manifest.total_chunks,
    )


def test_uncertain_generation_marker_blocks_provider_replay_after_restart(monkeypatch, tmp_path):
    import json
    import sqlite3
    from datetime import datetime, timezone, timedelta
    import pytest

    root = tmp_path
    controller = ExecutionController(root)
    controller.register_handler(
        HANDLER_NAME, dashboard_response_delivery_handler, execution_class="read_only",
    )
    payload = {
        "project_root": str(root), "task": "uncertain generation",
        "capability": "general", "privacy": "private",
        "max_attempts": 1, "timeout_seconds": 1.0,
    }
    task = controller.submit(
        HANDLER_NAME, payload, work_key="test-dashboard-uncertain-generation",
        deadline_seconds=60,
    )
    claimed = controller._claim(task.id)
    assert claimed is not None
    request_data = {
        "task": payload["task"], "capability": payload["capability"],
        "privacy": payload["privacy"], "max_attempts": payload["max_attempts"],
        "timeout_seconds": payload["timeout_seconds"],
    }
    request_digest = hashlib.sha256(json.dumps(
        request_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")).hexdigest()
    controller.progress_journal.begin_response_generation(task.id, request_digest)
    with sqlite3.connect(controller.db_path) as db:
        expired = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        db.execute("UPDATE tasks SET lease_until=? WHERE id=?", (expired, task.id))
        db.commit()

    recovered = ExecutionController(root)
    recovered.register_handler(
        HANDLER_NAME, dashboard_response_delivery_handler, execution_class="read_only",
    )
    def must_not_route(_self, _request, **_kwargs):
        raise AssertionError("unknown provider outcome must never be replayed automatically")
    monkeypatch.setattr(HybridModelRouter, "route", must_not_route)
    with pytest.raises(RuntimeError, match="outcome is unknown"):
        recovered.run_once(task.id)
    current = recovered.task(task.id)
    assert current is not None and current.state == "failed"
    assert recovered.progress_journal.snapshot(task.id)["reported_state"] == "failed"
