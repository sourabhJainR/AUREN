"""Zero-dependency local AUREN engineering dashboard server."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4
from .agency_release_lifecycle import ArtifactStore
from .engineering_dashboard import EngineeringDashboard
from .muxin_session_adapter import LocalModelDiscovery, MuxinSessionAdapter
from .hybrid_model_router import HybridModelRouter, TaskRouteRequest
from .agency_observability_ops import observability_snapshot
from .identity_compat import canonical_runtime_home
from .progress_journal import ProgressJournal
from .response_delivery import assemble_response
from .response_delivery_artifacts import ResponseArtifactRepository

MAX_API_RESPONSE_BYTES = 4_000_000
MAX_CHUNKED_RESPONSE_BYTES = 16_000_000
MAX_DELIVERY_CHUNK_BYTES = 12_000
CHUNKED_RESPONSE_ACCEPT = "application/vnd.auren.chunked+json"


class ApiResponseTooLarge(ValueError):
    """The atomic local HTTP response cannot be delivered within its byte cap."""


def _encode_bounded_json(payload, *, max_bytes: int = MAX_API_RESPONSE_BYTES) -> bytes:
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    encoder = json.JSONEncoder(
        ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    )
    chunks = []
    total = 0
    for part in encoder.iterencode(payload):
        encoded = part.encode("utf-8")
        total += len(encoded)
        if total > max_bytes:
            raise ApiResponseTooLarge("serialized API response exceeds the atomic delivery byte limit")
        chunks.append(encoded)
    return b"".join(chunks)


UI_ROOT = Path(__file__).resolve().parent.parent / "dashboard"
CONTROL_CENTER = Path(__file__).resolve().parent.parent / "site" / "control-center.html"


class _Handler(BaseHTTPRequestHandler):
    dashboard = None
    control_center = None
    ui_root = UI_ROOT
    control_center_path = CONTROL_CENTER
    artifact_store = None
    response_repository = None
    progress_journal = None

    def _send(self, status, body, content_type, *, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_api_json(self, status, payload):
        headers = {
            "X-AUREN-Delivery-Mode": "atomic-json",
            "X-AUREN-Chunk-Ack": "unsupported",
        }
        try:
            body = _encode_bounded_json(payload)
        except ApiResponseTooLarge:
            status = 502
            body = (
                b'{"error":"API response exceeds the configured atomic delivery limit",'
                b'"delivery":{"mode":"atomic-json","chunk_ack_supported":false,'
                b'"action":"split the task or configure an explicitly acknowledgement-capable receiver"}}'
            )
        except (TypeError, ValueError):
            status = 503
            body = b'{"error":"API response could not be serialized safely"}'
        self._send(status, body, "application/json; charset=utf-8", extra_headers=headers)

    def _origin_is_local(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True  # CLI and same-origin legacy clients may omit Origin.
        try:
            parsed = urlparse(origin)
            port = parsed.port
        except ValueError:
            return False
        return (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost"}
            and port == self.server.server_port
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )

    def _guard_api_origin(self, path):
        if path.startswith("/api/") and not self._origin_is_local():
            self._send(403, b'{"error":"untrusted origin"}', "application/json; charset=utf-8")
            return False
        return True


    def _send_delivery_json(self, status, payload):
        body = _encode_bounded_json(payload, max_bytes=MAX_API_RESPONSE_BYTES)
        self._send(status, body, "application/json; charset=utf-8", extra_headers={
            "X-AUREN-Delivery-Mode": "chunked-pull-v1",
            "X-AUREN-Chunk-Ack": "receiver-confirmed",
        })

    def _read_json_request(self, *, max_bytes):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > max_bytes:
            raise ValueError("request body size is outside the allowed limit")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("request body ended before Content-Length")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    @staticmethod
    def _delivery_task_id(response_id, delivery_id):
        return "dashboard-response:" + response_id + ":" + delivery_id

    def _persist_response_artifact(self, response_id, delivery_id, body):
        if self.response_repository is None or self.progress_journal is None:
            raise RuntimeError("canonical response artifact repository is unavailable")
        stored = self.response_repository.stage(
            body.decode("utf-8"), max_chunk_bytes=MAX_DELIVERY_CHUNK_BYTES,
        )
        task_id = self._delivery_task_id(response_id, delivery_id)
        self.progress_journal.record_response_artifact(
            task_id, response_id=response_id, artifact_sha256=stored.artifact.digest,
        )
        return stored

    def _load_delivery(self, response_id, delivery_id):
        if not re.fullmatch(r"[0-9a-f]{64}", response_id):
            raise ValueError("invalid response identity")
        if self.response_repository is None or self.progress_journal is None:
            raise RuntimeError("durable response delivery is not configured")
        task_id = self._delivery_task_id(response_id, delivery_id)
        artifact_digest = self.progress_journal.response_artifact_reference(task_id, response_id)
        if artifact_digest is None:
            raise FileNotFoundError("response artifact is unavailable")
        return self.response_repository.load(response_id, artifact_digest)

    def _delivery_checkpoint(self, response_id, delivery_id):
        return self.progress_journal.response_delivery_checkpoint(
            self._delivery_task_id(response_id, delivery_id), response_id,
        )

    def _handle_delivery_get(self, path):
        match = re.fullmatch(r"/api/delivery/([0-9a-f]{64})/([0-9a-f]{32})/(next|status)", path)
        if not match:
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        response_id, delivery_id, action = match.groups()
        try:
            stored = self._load_delivery(response_id, delivery_id)
            manifest, chunks = stored.manifest, stored.chunks
            checkpoint = self._delivery_checkpoint(response_id, delivery_id)
            acknowledged = checkpoint["acknowledged_index"]
            if checkpoint.get("total_chunks") not in (None, manifest.total_chunks):
                raise RuntimeError("delivery acknowledgement manifest mismatch")
            if action == "status":
                self._send_delivery_json(200, {
                    "response_id": response_id,
                    "delivery_id": delivery_id,
                    "response_sha256": manifest.sha256,
                    "total_chunks": manifest.total_chunks,
                    "total_bytes": manifest.total_bytes,
                    "acknowledged_index": acknowledged,
                    "complete": acknowledged == manifest.total_chunks,
                })
                return
            if acknowledged >= manifest.total_chunks:
                self._send_delivery_json(200, {
                    "response_id": response_id, "complete": True,
                    "acknowledged_index": acknowledged,
                    "total_chunks": manifest.total_chunks,
                })
                return
            chunk = chunks[acknowledged]
            self._send_delivery_json(200, {
                "response_id": response_id,
                "response_sha256": manifest.sha256,
                "chunk_index": chunk.index,
                "total_chunks": manifest.total_chunks,
                "chunk_sha256": chunk.sha256,
                "text": chunk.text,
                "utf8_bytes": chunk.utf8_bytes,
            })
        except FileNotFoundError:
            self._send_delivery_json(404, {"error": "response artifact not found"})
        except (ValueError, TypeError, json.JSONDecodeError):
            self._send_delivery_json(400, {"error": "invalid delivery request"})
        except Exception:
            self._send_delivery_json(503, {"error": "response delivery state unavailable; automatic replay is blocked"})

    def _handle_delivery_post(self, path):
        match = re.fullmatch(r"/api/delivery/([0-9a-f]{64})/([0-9a-f]{32})/(ack|complete)", path)
        if not match:
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        response_id, delivery_id, action = match.groups()
        try:
            payload = self._read_json_request(max_bytes=16_384)
            stored = self._load_delivery(response_id, delivery_id)
            manifest, chunks = stored.manifest, stored.chunks
            checkpoint = self._delivery_checkpoint(response_id, delivery_id)
            acknowledged = checkpoint["acknowledged_index"]
            if payload.get("response_sha256") != manifest.sha256:
                raise ValueError("response digest mismatch")
            supplied_total = payload.get("total_chunks")
            if isinstance(supplied_total, bool) or not isinstance(supplied_total, int) or supplied_total != manifest.total_chunks:
                raise ValueError("response chunk count mismatch")
            task_id = self._delivery_task_id(response_id, delivery_id)
            if action == "ack":
                index = payload.get("chunk_index")
                chunk_digest = payload.get("chunk_sha256")
                if isinstance(index, bool) or not isinstance(index, int) or not 1 <= index <= manifest.total_chunks:
                    raise ValueError("invalid chunk index")
                chunk = chunks[index - 1]
                if chunk_digest != chunk.sha256:
                    raise ValueError("chunk digest mismatch")
                if index not in {acknowledged, acknowledged + 1}:
                    raise ValueError("acknowledgements must be contiguous")
                self.progress_journal.record_response_chunk_ack(
                    task_id, response_id=response_id,
                    acknowledged_index=index, total_chunks=manifest.total_chunks,
                    response_sha256=manifest.sha256,
                )
                self._send_delivery_json(200, {
                    "acknowledged": True, "response_id": response_id, "delivery_id": delivery_id,
                    "acknowledged_index": index, "chunk_sha256": chunk.sha256,
                })
                return
            if acknowledged != manifest.total_chunks:
                raise ValueError("receiver has not acknowledged every chunk")
            # The receiver sends this only after reassembling and hashing its
            # persisted chunks. The server independently verifies its immutable
            # source artifact and the digest supplied by the receiver.
            verified = assemble_response(manifest, chunks)
            if hashlib.sha256(verified.encode("utf-8")).hexdigest() != payload.get("response_sha256"):
                raise ValueError("receiver completion digest mismatch")
            snapshot = self.progress_journal.snapshot(task_id)
            if snapshot.get("state") != "completed":
                self.progress_journal.record(
                    task_id, "response_delivery_completed", state="completed",
                    stage="response-delivery", message="receiver confirmed complete response",
                    evidence_ref="sha256:" + manifest.sha256,
                )
            self._send_delivery_json(200, {
                "completed": True, "response_id": response_id, "delivery_id": delivery_id,
                "response_sha256": manifest.sha256,
                "acknowledged_index": acknowledged,
                "total_chunks": manifest.total_chunks,
            })
        except (ValueError, TypeError, json.JSONDecodeError):
            self._send_delivery_json(409, {"error": "delivery acknowledgement rejected"})
        except FileNotFoundError:
            self._send_delivery_json(404, {"error": "response artifact not found"})
        except Exception:
            self._send_delivery_json(503, {"error": "delivery state unavailable; completion is not confirmed"})


    def do_POST(self):
        path = urlparse(self.path).path
        if not self._guard_api_origin(path):
            return
        if path.startswith("/api/delivery/"):
            self._handle_delivery_post(path)
            return
        if path != "/api/route":
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        try:
            payload = self._read_json_request(max_bytes=65536)
            if not isinstance(payload.get("task"), str) or not payload["task"].strip():
                raise ValueError("task must be a non-empty string")
            request = TaskRouteRequest(
                task=payload["task"][:60000],
                capability=str(payload.get("capability", "general"))[:80],
                privacy=str(payload.get("privacy", "private"))[:40],
                max_attempts=min(3, max(1, int(payload.get("max_attempts", 2)))),
                timeout_seconds=min(120.0, max(0.1, float(payload.get("timeout_seconds", 30.0)))),
            )
            result = HybridModelRouter().route(request)
            response_payload = {
                "provider": result.provider, "model": result.model, "success": result.success,
                "attempts": result.attempts, "latency_ms": result.latency_ms,
                "resource_usage": result.resource_usage, "fallback_reason": result.fallback_reason,
                "result": result.result, "error": result.error,
            }
            accepts_chunked = CHUNKED_RESPONSE_ACCEPT in self.headers.get("Accept", "")
            if accepts_chunked:
                if self.response_repository is None or self.progress_journal is None:
                    self._send_delivery_json(503, {"error": "durable chunked delivery is not configured"})
                    return
                try:
                    body = _encode_bounded_json(
                        response_payload, max_bytes=MAX_CHUNKED_RESPONSE_BYTES,
                    )
                except ApiResponseTooLarge:
                    raise
                except (TypeError, ValueError):
                    self._send_delivery_json(503, {"error": "routing response could not be serialized safely"})
                    return
                response_id = hashlib.sha256(body).hexdigest()
                delivery_id = uuid4().hex
                stored = self._persist_response_artifact(response_id, delivery_id, body)
                manifest = stored.manifest
                ref = stored.artifact
                self._send_delivery_json(202, {
                    "delivery_mode": "chunked-pull-v1",
                    "response_id": manifest.response_id,
                    "delivery_id": delivery_id,
                    "response_sha256": manifest.sha256,
                    "total_chunks": manifest.total_chunks,
                    "total_chars": manifest.total_chars,
                    "total_bytes": manifest.total_bytes,
                    "chunk_bytes": MAX_DELIVERY_CHUNK_BYTES,
                    "artifact": {
                        "artifact_id": ref.artifact_id,
                        "digest": ref.digest,
                        "size": ref.size,
                        "layout": ref.layout,
                    },
                    "next": "/api/delivery/" + manifest.response_id + "/" + delivery_id + "/next",
                    "status": "/api/delivery/" + manifest.response_id + "/" + delivery_id + "/status",
                    "ack": "/api/delivery/" + manifest.response_id + "/" + delivery_id + "/ack",
                    "complete": "/api/delivery/" + manifest.response_id + "/" + delivery_id + "/complete",
                })
                return
            self._send_api_json(200 if result.success else 503, response_payload)
        except ApiResponseTooLarge:
            self._send_api_json(502, {
                "error": "chunked API response exceeds the configured total byte budget",
                "delivery": {"mode": "bounded", "chunk_ack_supported": False},
            })
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._send(400, json.dumps({"error": str(exc)}).encode(), "application/json; charset=utf-8")
        except Exception:
            self._send(503, b'{"error":"routing request failed"}', "application/json; charset=utf-8")

    def do_GET(self):
        path = urlparse(self.path).path
        if not self._guard_api_origin(path):
            return
        if path.startswith("/api/delivery/"):
            self._handle_delivery_get(path)
            return
        if path == "/api/dashboard":
            self._send_api_json(200, self.dashboard.snapshot().as_dict())
            return
        if path == "/api/observability":
            try:
                trace_path = canonical_runtime_home() / "observability" / "traces.jsonl"
                self._send_api_json(200, observability_snapshot(trace_path))
            except Exception:
                self._send(503, b'{"error":"observability snapshot unavailable"}', "application/json; charset=utf-8")
            return
        if path == "/api/control-center":
            try:
                snapshot = self.control_center.snapshot()
                snapshot["models"] = LocalModelDiscovery().discover()
                self._send_api_json(200, snapshot)
            except Exception:
                # Keep the local console responsive; report a bounded error without internals.
                self._send(503, b'{"error":"control center snapshot unavailable"}', "application/json; charset=utf-8")
            return
        if path == "/api/health":
            self._send_api_json(200, {"status": "ok"})
            return
        if path == "/control-center":
            self._serve(self.control_center_path, "text/html; charset=utf-8")
            return
        if path in {"/", "/index.html"}:
            self._serve(self.ui_root / "index.html", "text/html; charset=utf-8")
            return
        if path.startswith("/assets/"):
            target = self.ui_root / path.removeprefix("/assets/")
            if target.is_file() and target.resolve().is_relative_to(self.ui_root.resolve()):
                self._serve(target, self._content_type(target))
                return
        self._send(404, b'{"error":"not found"}', "application/json")

    def _serve(self, path, content_type):
        try:
            body = path.read_bytes()
        except OSError:
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        self._send(200, body, content_type)

    @staticmethod
    def _content_type(path):
        return {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".svg": "image/svg+xml", ".json": "application/json"}.get(path.suffix.lower(), "application/octet-stream")

    def log_message(self, format, *args):
        return


def serve(project_root=".", host="127.0.0.1", port=8765):
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("dashboard server only binds to loopback; remote access requires a separately secured deployment")
    dashboard = EngineeringDashboard(project_root)
    control_center = MuxinSessionAdapter(project_root, dashboard=dashboard)
    runtime_root = Path(project_root).expanduser().resolve() / ".auren"
    artifact_store = ArtifactStore(runtime_root / "releases")
    response_repository = ResponseArtifactRepository(artifact_store)
    progress_journal = ProgressJournal(runtime_root / "progress.jsonl")
    handler = type("AERDashboardHandler", (_Handler,), {
        "dashboard": dashboard, "control_center": control_center,
        "ui_root": UI_ROOT, "control_center_path": CONTROL_CENTER,
        "artifact_store": artifact_store, "response_repository": response_repository,
        "progress_journal": progress_journal,
    })
    server = ThreadingHTTPServer((host, port), handler)
    print(f"AUREN Engineering Console: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="Run the local AUREN engineering dashboard.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(args.project_root, args.host, args.port)


if __name__ == "__main__":
    main()
