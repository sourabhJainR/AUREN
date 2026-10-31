"""Supervisor-owned parent task for dashboard response delivery.

The controller task remains running until the receiver confirms every chunk and
the complete response digest. A restart resumes from the artifact/ack journal;
an uncertain provider dispatch is never replayed automatically.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from typing import Any, Mapping

from .agency_release_lifecycle import ArtifactStore
from .hybrid_model_router import HybridModelRouter, TaskRouteRequest
from .progress_journal import ProgressJournal
from .provider_operation_lifecycle import ProviderOutcomeBlocked
from .response_delivery import assemble_response
from .response_delivery_artifacts import ResponseArtifactRepository

HANDLER_NAME = "dashboard_response_delivery_v1"
MAX_CHUNKED_RESPONSE_BYTES = 16_000_000
MAX_DELIVERY_CHUNK_BYTES = 12_000


def _encode_response(payload: Mapping[str, Any], *, max_bytes: int) -> bytes:
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    parts: list[bytes] = []
    total = 0
    for part in encoder.iterencode(payload):
        encoded = part.encode("utf-8")
        total += len(encoded)
        if total > max_bytes:
            raise ValueError("chunked response exceeds the configured total byte budget")
        parts.append(encoded)
    return b"".join(parts)


def dashboard_response_delivery_handler(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Generate one response and hold the supervisor task until receiver completion."""
    task_id = payload.get("auren_task_id")
    project_root = payload.get("project_root")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("trusted supervisor task identity is missing")
    if not isinstance(project_root, str) or not project_root.strip():
        raise ValueError("trusted project root is missing")
    root = Path(project_root).expanduser().resolve()
    journal = ProgressJournal(root / ".auren" / "progress.jsonl")
    repository = ResponseArtifactRepository(ArtifactStore(root / ".auren" / "releases"))
    cancel_event = payload.get("auren_cancel_event")
    deadline_epoch = payload.get("auren_deadline_epoch")
    if cancel_event is not None and not hasattr(cancel_event, "is_set"):
        raise ValueError("supervisor cancellation signal is invalid")
    if deadline_epoch is not None and (
        isinstance(deadline_epoch, bool) or not isinstance(deadline_epoch, (int, float))
    ):
        raise ValueError("supervisor deadline is invalid")

    artifact = journal.response_artifact_manifest(task_id)
    if artifact is None:
        request_data = {
            "task": payload.get("task"),
            "capability": payload.get("capability"),
            "privacy": payload.get("privacy"),
            "max_attempts": payload.get("max_attempts"),
            "timeout_seconds": payload.get("timeout_seconds"),
        }
        request_sha256 = hashlib.sha256(json.dumps(
            request_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")).hexdigest()
        if not journal.begin_response_generation(task_id, request_sha256):
            raise ProviderOutcomeBlocked(
                "provider dispatch was previously started without a durable response artifact; "
                "outcome is unknown and automatic replay is blocked"
            )
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("response generation cancelled before provider dispatch")
        remaining = (
            float(deadline_epoch) - time.time()
            if deadline_epoch is not None else float(payload.get("timeout_seconds", 30.0))
        )
        if remaining <= 0:
            raise TimeoutError("supervisor deadline expired before provider dispatch")
        timeout_seconds = min(float(payload.get("timeout_seconds", 30.0)), remaining)
        request = TaskRouteRequest(
            task=str(payload.get("task", "")),
            capability=str(payload.get("capability", "general")),
            privacy=str(payload.get("privacy", "private")),
            max_attempts=int(payload.get("max_attempts", 2)),
            timeout_seconds=timeout_seconds,
        )
        result = HybridModelRouter().route_supervised(request, execution_payload=payload)
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("response generation returned after supervisor cancellation")
        if type(result.success) is not bool:
            raise ValueError("model route returned a non-boolean success state")
        response_payload = {
            "provider": result.provider, "model": result.model, "success": result.success,
            "attempts": result.attempts, "latency_ms": result.latency_ms,
            "resource_usage": result.resource_usage, "fallback_reason": result.fallback_reason,
            "result": result.result, "error": result.error,
        }
        body = _encode_response(response_payload, max_bytes=MAX_CHUNKED_RESPONSE_BYTES)
        stored = repository.stage(body.decode("utf-8"), max_chunk_bytes=MAX_DELIVERY_CHUNK_BYTES)
        journal.record_response_artifact(
            task_id, response_id=stored.manifest.response_id,
            artifact_sha256=stored.artifact.digest,
        )
        artifact = journal.response_artifact_manifest(task_id)
        if artifact is None:
            raise RuntimeError("response artifact receipt could not be verified after staging")

    stored = repository.load(artifact["response_id"], artifact["artifact_sha256"])
    response_text = assemble_response(stored.manifest, stored.chunks)
    response_payload = json.loads(response_text)
    if type(response_payload.get("success")) is not bool:
        raise ValueError("stored response artifact has no boolean route outcome")

    while True:
        if journal.response_delivery_confirmation(
            task_id, stored.manifest.response_id, stored.manifest.total_chunks,
        ):
            snapshot = journal.snapshot(task_id)
            if snapshot.get("state") != "running" or snapshot.get("reported_state") != "running":
                raise RuntimeError(
                    "receiver receipt exists but the supervisor journal is not running; reconcile before completion"
                )
            return {
                "success": response_payload["success"],
                "detail": (
                    "receiver confirmed the complete response digest"
                    if response_payload["success"] else
                    "receiver confirmed delivery of an unsuccessful model-route result"
                ),
                "evidence": [
                    "response-sha256:" + stored.manifest.sha256,
                    "artifact-sha256:" + stored.artifact.digest,
                ],
            }
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("response delivery cancelled; parent task remains nonterminal until supervisor finalization")
        if deadline_epoch is not None and time.time() >= float(deadline_epoch):
            raise TimeoutError("response delivery deadline expired before receiver confirmation")
        time.sleep(0.05)


__all__ = ["HANDLER_NAME", "dashboard_response_delivery_handler"]
