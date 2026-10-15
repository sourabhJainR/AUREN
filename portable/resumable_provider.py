"""Checkpointable provider failover for interrupted graph work.

The graph/supervisor owns this value object and supplies persistence/evidence
callbacks. Provider clients and credentials remain host-injected. This module
never sleeps: rate-limited work is checkpointed with a not-before time so a
scheduler can resume it after reset, including after a process restart.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Callable, Mapping, Sequence

from .crew_adapters import TaskRetryBudget


SCHEMA_VERSION = "auren-provider-resume-v1"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("provider reset timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("provider reset timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


class ProviderCallError(RuntimeError):
    """Typed provider failure used by hosts that can classify HTTP/network errors."""

    def __init__(self, message: str, *, retryable: bool = True, status_code: int | None = None,
                 retry_after_seconds: int | float | None = None, reset_at: str | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.reset_at = reset_at


class ResumableProviderExecutor:
    """Failover executor whose progress can be restored from a graph checkpoint.

    Provider callables receive (request, idempotency_key). They should perform
    proposal/read-only work; irreversible side effects require a separate durable
    outbox/inbox and downstream idempotency enforcement.
    """

    def __init__(
        self, *, task_id: str, stage_id: str, providers: Mapping[str, Callable[[Mapping[str, Any], str], Any]],
        provider_versions: Mapping[str, str], preferred_provider: str, retry_budget: TaskRetryBudget,
        persist: Callable[[Mapping[str, Any]], None], record_evidence: Callable[[Mapping[str, Any]], None] | None = None,
        checkpoint: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(task_id, str) or not task_id.strip() or not isinstance(stage_id, str) or not stage_id.strip():
            raise ValueError("task_id and stage_id are required")
        if not isinstance(providers, Mapping) or not providers or set(providers) != set(provider_versions):
            raise ValueError("provider callables and pinned versions must have matching non-empty keys")
        if preferred_provider not in providers:
            raise ValueError("preferred provider must be registered")
        if any(not callable(fn) for fn in providers.values()):
            raise TypeError("all providers must be callable")
        if any(not isinstance(v, str) or not v.strip() or v.strip().lower() in {"latest", "unknown", "unversioned"}
               for v in provider_versions.values()):
            raise ValueError("provider versions must be pinned")
        if not isinstance(retry_budget, TaskRetryBudget):
            raise TypeError("retry_budget must be the canonical task retry budget")
        if not callable(persist) or (record_evidence is not None and not callable(record_evidence)):
            raise TypeError("persistence and evidence callbacks must be callable")
        self.task_id, self.stage_id = task_id, stage_id
        self.providers, self.provider_versions = dict(providers), dict(provider_versions)
        self.preferred_provider = preferred_provider
        self.retry_budget, self.persist, self.record_evidence = retry_budget, persist, record_evidence
        self._state = {
            "schema_version": SCHEMA_VERSION, "task_id": task_id, "stage_id": stage_id,
            "preferred_provider": preferred_provider, "attempted_providers": [],
            "deferred_until": {}, "status": "ready", "result_digest": None,
        }
        if checkpoint is not None:
            self._restore(checkpoint)

    def _restore(self, checkpoint: Mapping[str, Any]) -> None:
        if not isinstance(checkpoint, Mapping) or checkpoint.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported provider resume checkpoint")
        supplied = checkpoint.get("digest")
        payload = {k: v for k, v in checkpoint.items() if k != "digest"}
        if not isinstance(supplied, str) or supplied != _digest(payload):
            raise ValueError("provider resume checkpoint digest mismatch")
        if checkpoint.get("task_id") != self.task_id or checkpoint.get("stage_id") != self.stage_id:
            raise ValueError("provider resume checkpoint belongs to a different task or stage")
        preferred = checkpoint.get("preferred_provider")
        attempted = checkpoint.get("attempted_providers")
        deferred = checkpoint.get("deferred_until")
        if not isinstance(preferred, str) or not preferred.strip() or not isinstance(attempted, list):
            raise ValueError("provider resume checkpoint contains invalid provider identity")
        if any(not isinstance(x, str) or not x.strip() for x in attempted):
            raise ValueError("provider resume checkpoint contains invalid attempted provider")
        if not isinstance(deferred, Mapping) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in deferred.items()):
            raise ValueError("provider resume checkpoint has invalid rate-limit state")
        for value in deferred.values():
            _parse_time(value)
        if checkpoint.get("status") not in {"ready", "deferred", "completed", "failed"}:
            raise ValueError("provider resume checkpoint has invalid status")
        # Provider sets can change across restart. Keep only cooldowns/attempt
        # metadata for currently registered providers and use the new operator
        # preference when the prior provider was removed.
        self._state = dict(payload)
        self._state["preferred_provider"] = preferred if preferred in self.providers else self.preferred_provider
        self._state["attempted_providers"] = [x for x in attempted if x in self.providers]
        self._state["deferred_until"] = {k: v for k, v in deferred.items() if k in self.providers}
        saved_budget = checkpoint.get("retry_budget")
        if not isinstance(saved_budget, Mapping):
            raise ValueError("provider checkpoint requires the shared task retry budget")
        self.retry_budget.merge_snapshot(saved_budget)

    def snapshot(self) -> dict[str, Any]:
        payload = dict(self._state)
        payload["retry_budget"] = self.retry_budget.snapshot()
        payload["provider_versions"] = dict(sorted(self.provider_versions.items()))
        payload["provider_configuration_digest"] = _digest({
            "versions": payload["provider_versions"], "preferred": self.preferred_provider,
        })
        payload["digest"] = _digest(payload)
        return payload

    def _save(self, *, status: str | None = None) -> None:
        if status is not None:
            self._state["status"] = status
        snapshot = self.snapshot()
        self.persist(snapshot)
        if self.record_evidence is not None:
            self.record_evidence({
                "event": "provider_resume_checkpoint",
                "task_id": self.task_id,
                "stage_id": self.stage_id,
                "checkpoint_digest": snapshot["digest"],
                "provider_configuration_digest": snapshot["provider_configuration_digest"],
                "status": snapshot["status"],
            })

    def run(self, request: Mapping[str, Any], *, now: datetime | None = None) -> Any:
        if not isinstance(request, Mapping):
            raise ValueError("provider request must be a mapping")
        if self._state["status"] == "completed":
            raise RuntimeError("completed provider stage must be resumed from its saved result, not re-executed")
        current = (now or _utcnow()).astimezone(timezone.utc)
        ordered = [self.preferred_provider] + [p for p in sorted(self.providers) if p != self.preferred_provider]
        available = []
        next_reset: datetime | None = None
        for provider in ordered:
            reset = _parse_time(self._state["deferred_until"].get(provider))
            if reset is None or reset <= current:
                available.append(provider)
            elif next_reset is None or reset < next_reset:
                next_reset = reset
        if not available:
            self._save(status="deferred")
            return {"status": "deferred", "resume_at": _iso(next_reset), "checkpoint": self.snapshot()["digest"]}

        idempotency_key = hashlib.sha256(f"{self.task_id}:{self.stage_id}".encode()).hexdigest()
        errors = []
        for index, provider in enumerate(available):
            # Initial provider call is free; switching/retrying consumes the one
            # task-scoped budget shared with graph nodes and crew handoffs.
            if index > 0 and not self.retry_budget.consume(
                component="provider_failover",
                operation_key=f"provider:{self.task_id}:{self.stage_id}:{provider}",
            ):
                self._save(status="failed")
                return {"status": "blocked", "reason": "shared task retry budget exhausted", "checkpoint": self.snapshot()["digest"]}
            if provider not in self._state["attempted_providers"]:
                self._state["attempted_providers"].append(provider)
            self._save(status="ready")
            try:
                result = self.providers[provider](dict(request), idempotency_key)
                encoded = _canonical(result)
                self._state["result_digest"] = hashlib.sha256(encoded).hexdigest()
                self._state["selected_provider"] = provider
                self._state["selected_provider_version"] = self.provider_versions[provider]
                self._state["deferred_until"].pop(provider, None)
                self._save(status="completed")
                return result
            except Exception as exc:
                retryable = getattr(exc, "retryable", None)
                status_code = getattr(exc, "status_code", None)
                if retryable is False:
                    self._save(status="failed")
                    raise
                if status_code == 429 or isinstance(exc, ProviderCallError) and status_code == 429:
                    reset_at = _parse_time(getattr(exc, "reset_at", None))
                    delay = getattr(exc, "retry_after_seconds", None)
                    if reset_at is None and isinstance(delay, (int, float)) and delay >= 0:
                        reset_at = current + timedelta(seconds=delay)
                    if reset_at is None:
                        # Unknown reset: do not busy-loop or discard work. Caller
                        # can schedule a conservative retry and inject fresh state.
                        reset_at = current + timedelta(minutes=1)
                    self._state["deferred_until"][provider] = _iso(reset_at)
                errors.append({"provider": provider, "error_type": type(exc).__name__})
                # Connection resets, timeouts, and transient server errors try the
                # next pinned provider. No exception message/prompt is persisted.
                continue
        future_resets = [_parse_time(v) for v in self._state["deferred_until"].values()]
        future_resets = [x for x in future_resets if x is not None and x > current]
        if future_resets:
            self._save(status="deferred")
            return {"status": "deferred", "resume_at": _iso(min(future_resets)), "errors": errors,
                    "checkpoint": self.snapshot()["digest"]}
        self._save(status="failed")
        return {"status": "failed", "errors": errors, "checkpoint": self.snapshot()["digest"]}


__all__ = ["ProviderCallError", "ResumableProviderExecutor", "SCHEMA_VERSION"]
