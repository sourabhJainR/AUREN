"""Zero-dependency local AUREN engineering dashboard server."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
from threading import Event, Lock, Thread
import time
from .agency_release_lifecycle import ArtifactStore
from .engineering_dashboard import EngineeringDashboard
from .muxin_session_adapter import LocalModelDiscovery, MuxinSessionAdapter
from .hybrid_model_router import HybridModelRouter, TaskRouteRequest
from .agency_observability_ops import observability_snapshot
from .identity_compat import canonical_runtime_home
from .progress_journal import ProgressJournal
from .response_delivery import assemble_response
from .response_delivery_artifacts import ResponseArtifactRepository
from .execution_controller import ExecutionController, WorkKeyConflict
from .dashboard_delivery_task import HANDLER_NAME as DASHBOARD_DELIVERY_HANDLER
from .dashboard_delivery_task import dashboard_response_delivery_handler

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
    execution_controller = None
    project_root = None
    delivery_workers = {}
    delivery_workers_lock = Lock()
    delivery_task_ids = set()

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
    def _delivery_task_id(response_id, delivery_id=None):
        """Resolve the delivery identity to the controller-generated task ID."""
        task_id = delivery_id if delivery_id is not None else response_id
        if not isinstance(task_id, str) or not re.fullmatch(r"[0-9a-f]{32}", task_id):
            raise ValueError("invalid supervisor delivery task identity")
        return task_id

    def _start_delivery_worker(self, task_id):
        controller = self.execution_controller
        if controller is None:
            raise RuntimeError("supervisor execution controller is unavailable")
        workers = type(self).delivery_workers
        with type(self).delivery_workers_lock:
            current = workers.get(task_id)
            if current is not None and current.is_alive():
                return
            worker = Thread(
                target=_run_delivery_task, args=(controller, task_id),
                name="auren-dashboard-delivery-" + task_id[:8], daemon=True,
            )
            workers[task_id] = worker
            type(self).delivery_task_ids.add(task_id)
            worker.start()

    def _load_delivery(self, delivery_id, response_id=None):
        task_id = self._delivery_task_id(delivery_id)
        if self.execution_controller is None or self.response_repository is None or self.progress_journal is None:
            raise RuntimeError("supervisor-owned response delivery is not configured")
        if self.execution_controller.task(task_id) is None:
            raise FileNotFoundError("supervisor delivery task is unavailable")
        artifact = self.progress_journal.response_artifact_manifest(task_id)
        if artifact is None:
            raise FileNotFoundError("response artifact is not ready")
        if response_id is not None and artifact["response_id"] != response_id:
            raise ValueError("response identity does not match the supervisor task")
        return self.response_repository.load(artifact["response_id"], artifact["artifact_sha256"])

    def _delivery_checkpoint(self, delivery_id, response_id):
        return self.progress_journal.response_delivery_checkpoint(
            self._delivery_task_id(delivery_id), response_id,
        )

    def _handle_delivery_get(self, path):
        match = re.fullmatch(r"/api/delivery/([0-9a-f]{32})/(next|status)", path)
        if not match:
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        delivery_id, action = match.groups()
        task_id = self._delivery_task_id(delivery_id)
        try:
            controller = self.execution_controller
            task = controller.task(task_id) if controller is not None else None
            if task is None:
                raise FileNotFoundError("supervisor delivery task is unavailable")
            snapshot = self.progress_journal.snapshot(task_id)
            artifact = self.progress_journal.response_artifact_manifest(task_id)
            if action == "status":
                if artifact is None:
                    state = "queued" if task.state == "pending" else snapshot.get("state", task.state)
                    self._send_delivery_json(200, {
                        "delivery_id": delivery_id, "ready": False, "complete": False,
                        "delivery_complete": False, "state": state, "task_state": task.state,
                    })
                    return
                stored = self._load_delivery(delivery_id)
                manifest = stored.manifest
                checkpoint = self._delivery_checkpoint(delivery_id, manifest.response_id)
                acknowledged = checkpoint["acknowledged_index"]
                all_acknowledged = acknowledged == manifest.total_chunks
                receiver_confirmed = self.progress_journal.response_delivery_confirmation(
                    task_id, manifest.response_id, manifest.total_chunks,
                )
                journal_state = snapshot.get("reported_state", snapshot.get("state"))
                if task.state == "completed":
                    if journal_state != "completed" or not receiver_confirmed or not all_acknowledged:
                        raise RuntimeError("supervisor task and receiver receipt are inconsistent")
                    state, complete = "completed", True
                elif task.state == "blocked_reconciliation":
                    state, complete = "blocked", False
                elif task.state in {"failed", "cancelled"}:
                    if journal_state != task.state:
                        raise RuntimeError("supervisor terminal state is not reconciled to the progress journal")
                    state, complete = task.state, False
                elif task.state == "running":
                    state, complete = snapshot.get("state", "unknown"), False
                else:
                    state, complete = task.state, False
                self._send_delivery_json(200, {
                    "delivery_id": delivery_id, "ready": True,
                    "response_id": manifest.response_id,
                    "response_sha256": manifest.sha256,
                    "total_chunks": manifest.total_chunks,
                    "total_bytes": manifest.total_bytes,
                    "acknowledged_index": acknowledged,
                    "all_chunks_acknowledged": all_acknowledged,
                    "receiver_confirmed": receiver_confirmed,
                    "delivery_complete": receiver_confirmed and all_acknowledged,
                    "complete": complete, "state": state, "task_state": task.state,
                    "next": "/api/delivery/" + delivery_id + "/next",
                    "status": "/api/delivery/" + delivery_id + "/status",
                    "ack": "/api/delivery/" + delivery_id + "/ack",
                    "complete_url": "/api/delivery/" + delivery_id + "/complete",
                })
                return

            stored = self._load_delivery(delivery_id)
            manifest, chunks = stored.manifest, stored.chunks
            checkpoint = self._delivery_checkpoint(delivery_id, manifest.response_id)
            acknowledged = checkpoint["acknowledged_index"]
            all_acknowledged = acknowledged == manifest.total_chunks
            receiver_confirmed = self.progress_journal.response_delivery_confirmation(
                task_id, manifest.response_id, manifest.total_chunks,
            )
            if task.state == "completed":
                if snapshot.get("reported_state") != "completed" or not receiver_confirmed or not all_acknowledged:
                    raise RuntimeError("completed supervisor task has no matching receiver receipt")
                self._send_delivery_json(200, {
                    "response_id": manifest.response_id, "delivery_id": delivery_id,
                    "all_chunks_acknowledged": True, "delivery_complete": True, "complete": True,
                    "acknowledged_index": acknowledged, "total_chunks": manifest.total_chunks,
                })
                return
            if task.state != "running" or snapshot.get("state") != "running":
                self._send_delivery_json(409, {
                    "error": "delivery parent is not running; reconcile its durable supervisor state",
                    "state": snapshot.get("state", task.state), "task_state": task.state,
                })
                return
            if all_acknowledged:
                self._send_delivery_json(200, {
                    "response_id": manifest.response_id, "delivery_id": delivery_id,
                    "all_chunks_acknowledged": True, "delivery_complete": receiver_confirmed,
                    "complete": False, "acknowledged_index": acknowledged,
                    "total_chunks": manifest.total_chunks,
                })
                return
            chunk = chunks[acknowledged]
            self._send_delivery_json(200, {
                "response_id": manifest.response_id, "delivery_id": delivery_id,
                "response_sha256": manifest.sha256,
                "chunk_index": chunk.index, "total_chunks": manifest.total_chunks,
                "chunk_sha256": chunk.sha256, "text": chunk.text,
                "utf8_bytes": chunk.utf8_bytes,
            })
        except FileNotFoundError:
            self._send_delivery_json(404, {"error": "response artifact not found"})
        except (ValueError, TypeError, json.JSONDecodeError):
            self._send_delivery_json(400, {"error": "invalid delivery request"})
        except Exception:
            self._send_delivery_json(503, {"error": "response delivery state unavailable; automatic replay is blocked"})

    def _handle_delivery_post(self, path):
        match = re.fullmatch(r"/api/delivery/([0-9a-f]{32})/(ack|complete)", path)
        if not match:
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        delivery_id, action = match.groups()
        task_id = self._delivery_task_id(delivery_id)
        try:
            payload = self._read_json_request(max_bytes=16_384)
            controller = self.execution_controller
            task = controller.task(task_id) if controller is not None else None
            if task is None:
                raise FileNotFoundError("supervisor delivery task is unavailable")
            stored = self._load_delivery(delivery_id)
            manifest, chunks = stored.manifest, stored.chunks
            checkpoint = self._delivery_checkpoint(delivery_id, manifest.response_id)
            acknowledged = checkpoint["acknowledged_index"]
            if payload.get("response_sha256") != manifest.sha256:
                raise ValueError("response digest mismatch")
            supplied_total = payload.get("total_chunks")
            if isinstance(supplied_total, bool) or not isinstance(supplied_total, int) or supplied_total != manifest.total_chunks:
                raise ValueError("response chunk count mismatch")
            snapshot = self.progress_journal.snapshot(task_id)
            receiver_confirmed = self.progress_journal.response_delivery_confirmation(
                task_id, manifest.response_id, manifest.total_chunks,
            )
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
                if task.state == "completed":
                    if not (
                        snapshot.get("reported_state") == "completed"
                        and index == acknowledged == manifest.total_chunks
                        and receiver_confirmed
                    ):
                        raise ValueError("completed delivery only accepts an exact duplicate final acknowledgement")
                elif task.state != "running" or snapshot.get("state") != "running":
                    raise RuntimeError("delivery parent is not running; reconcile before acknowledging")
                self.progress_journal.record_response_chunk_ack(
                    task_id, response_id=manifest.response_id,
                    acknowledged_index=index, total_chunks=manifest.total_chunks,
                    response_sha256=manifest.sha256,
                )
                self._send_delivery_json(200, {
                    "acknowledged": True, "response_id": manifest.response_id,
                    "delivery_id": delivery_id, "acknowledged_index": index,
                    "chunk_sha256": chunk.sha256,
                })
                return

            if task.state == "completed" and snapshot.get("reported_state") == "completed":
                if not receiver_confirmed or acknowledged != manifest.total_chunks:
                    raise ValueError("completed supervisor task has no matching receiver confirmation")
                self._send_delivery_json(200, {
                    "completed": True, "receiver_confirmed": True,
                    "response_id": manifest.response_id, "delivery_id": delivery_id,
                    "response_sha256": manifest.sha256,
                    "acknowledged_index": acknowledged, "total_chunks": manifest.total_chunks,
                })
                return
            if task.state != "running" or snapshot.get("state") != "running":
                raise RuntimeError("delivery parent is not running; reconcile before completion")
            if acknowledged != manifest.total_chunks:
                raise ValueError("receiver has not acknowledged every chunk")
            verified = assemble_response(manifest, chunks)
            if hashlib.sha256(verified.encode("utf-8")).hexdigest() != payload.get("response_sha256"):
                raise ValueError("receiver completion digest mismatch")
            self.progress_journal.record_response_delivery_confirmation(
                task_id, response_id=manifest.response_id,
                total_chunks=manifest.total_chunks, response_sha256=manifest.sha256,
            )
            wait_until = time.monotonic() + 2.0
            current = task
            while time.monotonic() < wait_until:
                current = controller.task(task_id)
                latest = self.progress_journal.snapshot(task_id)
                if current is None or current.state in {"completed", "failed", "cancelled", "blocked_reconciliation"}:
                    break
                time.sleep(0.02)
            latest = self.progress_journal.snapshot(task_id)
            completed = (
                current is not None and current.state == "completed"
                and latest.get("reported_state") == "completed"
                and self.progress_journal.response_delivery_confirmation(
                    task_id, manifest.response_id, manifest.total_chunks,
                )
            )
            self._send_delivery_json(200 if completed else 202, {
                "completed": completed, "receiver_confirmed": True,
                "delivery_complete": True, "response_id": manifest.response_id,
                "delivery_id": delivery_id, "response_sha256": manifest.sha256,
                "acknowledged_index": acknowledged, "total_chunks": manifest.total_chunks,
                "task_state": current.state if current is not None else "unknown",
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
            accepts_chunked = CHUNKED_RESPONSE_ACCEPT in self.headers.get("Accept", "")
            if accepts_chunked:
                controller = self.execution_controller
                if controller is None or self.response_repository is None or self.progress_journal is None:
                    self._send_delivery_json(503, {"error": "supervisor-owned chunked delivery is not configured"})
                    return
                idempotency_key = self.headers.get("Idempotency-Key") or uuid4().hex
                if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", idempotency_key):
                    self._send_delivery_json(400, {"error": "invalid idempotency key"})
                    return
                delivery_timeout = payload.get("delivery_timeout_seconds", 300.0)
                if (
                    isinstance(delivery_timeout, bool)
                    or not isinstance(delivery_timeout, (int, float))
                    or not 1.0 <= float(delivery_timeout) <= 600.0
                ):
                    self._send_delivery_json(400, {"error": "delivery timeout must be between 1 and 600 seconds"})
                    return
                task_payload = {
                    "project_root": str(Path(self.project_root or ".").expanduser().resolve()),
                    "task": request.task, "capability": request.capability,
                    "privacy": request.privacy, "max_attempts": request.max_attempts,
                    "timeout_seconds": request.timeout_seconds,
                }
                work_key = "dashboard-response:" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
                try:
                    durable_task = controller.submit(
                        DASHBOARD_DELIVERY_HANDLER, task_payload,
                        goal="route and deliver a bounded dashboard response",
                        work_key=work_key, deadline_seconds=float(delivery_timeout),
                    )
                except WorkKeyConflict:
                    self._send_delivery_json(409, {
                        "error": "idempotency key is already bound to a different response request",
                    })
                    return
                delivery_id = durable_task.id
                self._start_delivery_worker(delivery_id)
                ready_by = time.monotonic() + 5.0
                artifact = self.progress_journal.response_artifact_manifest(delivery_id)
                while artifact is None and time.monotonic() < ready_by:
                    current = controller.task(delivery_id)
                    if current is None or current.state in {"failed", "cancelled", "blocked_reconciliation"}:
                        break
                    time.sleep(0.02)
                    artifact = self.progress_journal.response_artifact_manifest(delivery_id)
                status_url = "/api/delivery/" + delivery_id + "/status"
                if artifact is None:
                    current = controller.task(delivery_id)
                    if current is not None and current.state in {"failed", "cancelled", "blocked_reconciliation"}:
                        self._send_delivery_json(503, {
                            "error": "supervisor response task did not produce a verified artifact",
                            "delivery_id": delivery_id, "task_state": current.state,
                            "status": status_url,
                        })
                        return
                    self._send_delivery_json(202, {
                        "delivery_mode": "chunked-pull-v1", "ready": False,
                        "delivery_id": delivery_id, "idempotency_key": idempotency_key,
                        "status": status_url,
                    })
                    return
                stored = self._load_delivery(delivery_id)
                manifest, ref = stored.manifest, stored.artifact
                self._send_delivery_json(202, {
                    "delivery_mode": "chunked-pull-v1", "ready": True,
                    "response_id": manifest.response_id, "delivery_id": delivery_id,
                    "idempotency_key": idempotency_key,
                    "response_sha256": manifest.sha256,
                    "total_chunks": manifest.total_chunks,
                    "total_chars": manifest.total_chars,
                    "total_bytes": manifest.total_bytes,
                    "chunk_bytes": MAX_DELIVERY_CHUNK_BYTES,
                    "artifact": {
                        "artifact_id": ref.artifact_id, "digest": ref.digest,
                        "size": ref.size, "layout": ref.layout,
                    },
                    "next": "/api/delivery/" + delivery_id + "/next",
                    "status": status_url,
                    "ack": "/api/delivery/" + delivery_id + "/ack",
                    "complete": "/api/delivery/" + delivery_id + "/complete",
                })
                return
            result = HybridModelRouter().route(request)
            response_payload = {
                "provider": result.provider, "model": result.model, "success": result.success,
                "attempts": result.attempts, "latency_ms": result.latency_ms,
                "resource_usage": result.resource_usage, "fallback_reason": result.fallback_reason,
                "result": result.result, "error": result.error,
            }
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
        if path == "/api/sessions":
            try:
                query = parse_qs(urlparse(self.path).query, keep_blank_values=False)
                offset = int(query.get("offset", ["0"])[0])
                limit = int(query.get("limit", ["50"])[0])
                search = query.get("q", [""])[0]
                status = query.get("status", ["all"])[0]
                payload = self.control_center.session_snapshot(
                    offset=offset, limit=limit, query=search, status=status,
                )
                self._send_api_json(200, payload)
            except (TypeError, ValueError):
                self._send(400, b'{"error":"invalid session pagination bounds"}', "application/json; charset=utf-8")
            except Exception:
                self._send(503, b'{"error":"durable session snapshot unavailable"}', "application/json; charset=utf-8")
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


def _run_delivery_task(controller, task_id):
    """Run one bounded durable task; its terminal state remains in controller storage."""
    try:
        controller.run_once(task_id)
    except Exception:
        # run_once records durable failure or leaves an uncertain lease for recovery.
        # The HTTP status endpoint reads that canonical state; no success is inferred here.
        return


def _run_recovery_slice(controller, stop_event):
    """Resume eligible dashboard tasks in one bounded supervisor-owned cycle."""
    try:
        controller.run_continuously(
            stop_event=stop_event, batch_size=10, idle_sleep_seconds=4.0,
            max_cycles=1000, max_idle_cycles=100,
        )
    except Exception:
        return


def serve(project_root=".", host="127.0.0.1", port=8765):
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("dashboard server only binds to loopback; remote access requires a separately secured deployment")
    project_path = Path(project_root).expanduser().resolve()
    dashboard = EngineeringDashboard(project_path)
    control_center = MuxinSessionAdapter(project_path, dashboard=dashboard)
    runtime_root = project_path / ".auren"
    controller = ExecutionController(project_path)
    controller.register_handler(
        DASHBOARD_DELIVERY_HANDLER, dashboard_response_delivery_handler,
        execution_class="read_only",
    )
    artifact_store = ArtifactStore(runtime_root / "releases")
    response_repository = ResponseArtifactRepository(artifact_store)
    progress_journal = controller.progress_journal
    handler = type("AURENDashboardHandler", (_Handler,), {
        "dashboard": dashboard, "control_center": control_center,
        "ui_root": UI_ROOT, "control_center_path": CONTROL_CENTER,
        "artifact_store": artifact_store, "response_repository": response_repository,
        "progress_journal": progress_journal, "execution_controller": controller,
        "project_root": project_path, "delivery_workers": {},
        "delivery_workers_lock": Lock(), "delivery_task_ids": set(),
    })
    server = ThreadingHTTPServer((host, port), handler)
    recovery_stop = Event()
    recovery_thread = Thread(
        target=_run_recovery_slice, args=(controller, recovery_stop),
        name="auren-dashboard-recovery", daemon=True,
    )
    recovery_thread.start()
    print(f"AUREN Engineering Console: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        recovery_stop.set()
        recovery_thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description="Run the local AUREN engineering dashboard.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(args.project_root, args.host, args.port)


if __name__ == "__main__":
    main()
