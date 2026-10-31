from __future__ import annotations

import http.client
import json
from http.server import ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest

from portable import dashboard_server


def _run_server(monkeypatch, result):
    monkeypatch.setattr(dashboard_server.HybridModelRouter, "route", lambda _self, _request: result)
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server._Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_dashboard_route_declares_atomic_delivery_and_returns_bounded_json(monkeypatch):
    result = SimpleNamespace(
        provider="test", model="fixture", success=True, attempts=1, latency_ms=1.0,
        resource_usage={}, fallback_reason=None, result={"answer": "ready"}, error=None,
    )
    server, thread = _run_server(monkeypatch, result)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        payload = json.dumps({"task": "hello"}).encode("utf-8")
        connection.request("POST", "/api/route", body=payload, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        body = response.read()
        assert response.status == 200
        assert response.getheader("X-AUREN-Delivery-Mode") == "atomic-json"
        assert response.getheader("X-AUREN-Chunk-Ack") == "unsupported"
        assert json.loads(body)["result"] == {"answer": "ready"}
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_dashboard_route_rejects_oversized_result_without_truncation(monkeypatch):
    secret_marker = "must-not-be-returned"
    result = SimpleNamespace(
        provider="test", model="fixture", success=True, attempts=1, latency_ms=1.0,
        resource_usage={}, fallback_reason=None,
        result={"answer": "x" * dashboard_server.MAX_API_RESPONSE_BYTES, "marker": secret_marker},
        error=None,
    )
    server, thread = _run_server(monkeypatch, result)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        payload = json.dumps({"task": "hello"}).encode("utf-8")
        connection.request("POST", "/api/route", body=payload, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        body = response.read()
        assert response.status == 502
        assert response.getheader("X-AUREN-Delivery-Mode") == "atomic-json"
        assert response.getheader("X-AUREN-Chunk-Ack") == "unsupported"
        decoded = json.loads(body)
        assert decoded["delivery"]["chunk_ack_supported"] is False
        assert secret_marker.encode() not in body
        assert len(body) < 512
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_json_response_limit_counts_utf8_bytes_not_characters():
    with pytest.raises(dashboard_server.ApiResponseTooLarge):
        dashboard_server._encode_bounded_json({"value": "界" * 20}, max_bytes=40)
