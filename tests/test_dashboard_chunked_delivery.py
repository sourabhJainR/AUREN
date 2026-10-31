from __future__ import annotations

import hashlib
import http.client
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

from portable import dashboard_server
from portable.agency_release_lifecycle import ArtifactStore
from portable.response_delivery_artifacts import ResponseArtifactRepository
from portable.progress_journal import ProgressJournal
from portable.hybrid_model_router import HybridModelRouter


def _start_server(monkeypatch, root: Path, result):
    if result is not None:
        monkeypatch.setattr(HybridModelRouter, "route", lambda _self, _request: result)
    runtime_root = root / ".auren"
    store = ArtifactStore(runtime_root / "releases")
    handler = type("ChunkDeliveryTestHandler", (dashboard_server._Handler,), {
        "artifact_store": store,
        "response_repository": ResponseArtifactRepository(store),
        "progress_journal": ProgressJournal(runtime_root / "progress.jsonl"),
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
        task_id = server.RequestHandlerClass._delivery_task_id(response_id)
        assert server.RequestHandlerClass.progress_journal.snapshot(task_id)["state"] == "running"
        # Duplicate acknowledgement is idempotent.
        status, _, duplicate = _request(server, "POST", manifest["ack"], first_ack)
        assert status == 200 and duplicate["acknowledged_index"] == 1
    finally:
        _stop_server(server, thread)

    # A fresh server process reconstructs artifact and cursor from the canonical
    # content-addressed release artifact store and ExecutionController journal path.
    server, thread = _start_server(monkeypatch, tmp_path, None)
    try:
        status, _, checkpoint = _request(server, "GET", manifest["status"])
        assert status == 200
        assert checkpoint["acknowledged_index"] == 1

        parts = [first_text]
        while True:
            status, _, chunk = _request(server, "GET", manifest["next"])
            assert status == 200
            if chunk.get("complete"):
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

        complete = {
            "response_sha256": response_id,
            "total_chunks": manifest["total_chunks"],
        }
        status, _, receipt = _request(server, "POST", manifest["complete"], complete)
        assert status == 200 and receipt["completed"] is True
        status, _, final = _request(server, "GET", manifest["status"])
        assert status == 200 and final["complete"] is True
        task_id = dashboard_server._Handler._delivery_task_id(response_id)
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
        task_id = server.RequestHandlerClass._delivery_task_id(manifest["response_id"])
        checkpoint = server.RequestHandlerClass.progress_journal.response_delivery_checkpoint(
            task_id, manifest["response_id"],
        )
        assert checkpoint["acknowledged_index"] == 0
    finally:
        _stop_server(server, thread)
