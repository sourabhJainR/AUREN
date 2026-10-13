"""LLM-chat entry point for durable, fire-and-forget AdaptiveRuntime work.

The trigger persists an execution intent first, then optionally dispatches it
on a bounded background worker. Persistence and claim semantics remain owned
by TriggerRuntime, while execution remains owned by AdaptiveRuntime.
"""
from __future__ import annotations

from atexit import register
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Mapping
from weakref import WeakKeyDictionary

from .adaptive_runtime import AdaptiveRuntime
from .trigger_runtime import TriggerEvent, TriggerRuntime, TriggerStatus


_BACKGROUND_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="auren-adaptive-trigger")
register(_BACKGROUND_EXECUTOR.shutdown, wait=False, cancel_futures=False)

_PRIORITY = {"high": 0, "normal": 1, "low": 2}
_ADAPTER_CACHE: WeakKeyDictionary[AdaptiveRuntime, "AdaptiveTrigger"] = WeakKeyDictionary()
_ADAPTER_CACHE_LOCK = Lock()


@dataclass(frozen=True)
class AdaptiveTriggerRequest:
    task: str
    project_root: str
    context: Mapping[str, Any]
    priority: str = "normal"


@dataclass(frozen=True)
class TriggerReceipt:
    trigger_id: str
    status: str
    accepted_at: str


@dataclass(frozen=True)
class TriggerOutcome:
    trigger_id: str
    status: str
    result: Any = None


Runner = Callable[[AdaptiveTriggerRequest, str], Any]


class AdaptiveTrigger:
    """Durable LLM-chat trigger with bounded fire-and-forget dispatch."""

    def __init__(
        self,
        trigger_runtime: TriggerRuntime,
        runner: Runner,
    ) -> None:
        if not isinstance(trigger_runtime, TriggerRuntime):
            raise TypeError("trigger_runtime must be a TriggerRuntime")
        if not callable(runner):
            raise TypeError("runner must be callable")
        self.trigger_runtime = trigger_runtime
        self.runner = runner
        self._lock = Lock()
        self._closed = False
        self._futures: set[Future[Any]] = set()

    @classmethod
    def for_runtime(cls, runtime: AdaptiveRuntime) -> "AdaptiveTrigger":
        """Bind and reuse one trigger adapter for an AdaptiveRuntime instance."""
        if not isinstance(runtime, AdaptiveRuntime):
            raise TypeError("runtime must be an AdaptiveRuntime")
        with _ADAPTER_CACHE_LOCK:
            cached = _ADAPTER_CACHE.get(runtime)
            if cached is not None and not cached._closed:
                return cached

            def runner(request: AdaptiveTriggerRequest, trigger_id: str) -> Any:
                provider = request.context.get("provider")
                return runtime.run(
                    session_id=f"llm-chat:{trigger_id}",
                    task_id=f"llm-chat:{trigger_id}",
                    project_root=request.project_root,
                    intent=request.task,
                    provider=str(provider) if provider else None,
                    context=request.context,
                )

            trigger = cls(runtime.trigger_runtime, runner)
            _ADAPTER_CACHE[runtime] = trigger
            return trigger

    def trigger_adaptive_runtime(
        self,
        task: str,
        project_root: Path | str,
        context: Mapping[str, Any] | None = None,
        *,
        priority: str = "normal",
        event_id: str | None = None,
        max_attempts: int = 3,
        fire_and_forget: bool = True,
    ) -> TriggerReceipt:
        """Persist a chat request and optionally dispatch it without blocking."""
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task is required")
        if priority not in _PRIORITY:
            raise ValueError("priority must be one of: low, normal, high")
        root = str(Path(project_root).expanduser().resolve())
        request = AdaptiveTriggerRequest(task.strip(), root, dict(context or {}), priority)
        with self._lock:
            if self._closed:
                raise RuntimeError("adaptive trigger is closed")
            event = self.trigger_runtime.emit(
                "adaptive_runtime",
                {
                    "task": request.task,
                    "project_root": request.project_root,
                    "context": dict(request.context),
                    "priority": request.priority,
                },
                event_id=event_id,
                max_attempts=max_attempts,
                priority=_PRIORITY[request.priority],
            )
            if fire_and_forget:
                future = _BACKGROUND_EXECUTOR.submit(self._dispatch_event, event.event_id)
                self._futures.add(future)
                future.add_done_callback(self._forget_future)
        return TriggerReceipt(event.event_id, event.status, event.created_at)

    def get_status(self, trigger_id: str) -> TriggerStatus | None:
        """Read the durable lifecycle state for a trigger."""
        return self.trigger_runtime.get(trigger_id)

    @staticmethod
    def _result_metadata(result: Any) -> dict[str, Any]:
        status = getattr(result, "status", None)
        if hasattr(status, "value"):
            return {"result_status": str(status.value)}
        if status is not None:
            return {"result_status": str(status)}
        return {"result_type": type(result).__name__}

    def _dispatch_event(self, event_id: str) -> TriggerOutcome | None:
        claim = self.trigger_runtime.claim(event_id)
        if claim is None:
            return None
        now = datetime.now(timezone.utc)
        try:
            outcome = self._handle_event(claim.event)
            self.trigger_runtime.complete(
                event_id,
                claim.claim_id,
                "success",
                now=now,
                outcome=self._result_metadata(outcome.result),
            )
            return outcome
        except Exception as exc:
            self.trigger_runtime.complete(
                event_id,
                claim.claim_id,
                "retryable",
                str(exc),
                now=now,
                outcome={"error_type": type(exc).__name__},
            )
            return None

    def _handle_event(self, event: TriggerEvent) -> TriggerOutcome:
        if event.kind != "adaptive_runtime":
            raise ValueError(f"unsupported adaptive trigger kind: {event.kind}")
        payload = event.payload
        request = AdaptiveTriggerRequest(
            task=str(payload["task"]),
            project_root=str(payload["project_root"]),
            context=dict(payload.get("context", {})),
            priority=str(payload.get("priority", "normal")),
        )
        if request.priority not in _PRIORITY:
            raise ValueError("persisted trigger has invalid priority")
        result = self.runner(request, event.event_id)
        return TriggerOutcome(event.event_id, "accepted", result)

    def dispatch_once(self, *, limit: int = 20) -> list[TriggerOutcome]:
        """Synchronously drain due adaptive chat triggers only."""
        if limit < 1:
            return []
        results: list[TriggerOutcome] = []
        for event in self.trigger_runtime.due(limit=limit, kind="adaptive_runtime"):
            outcome = self._dispatch_event(event.event_id)
            if outcome is not None:
                results.append(outcome)
        return results

    def _forget_future(self, future: Future[Any]) -> None:
        with self._lock:
            self._futures.discard(future)

    def close(self, *, wait_for_background: bool = True) -> None:
        with self._lock:
            self._closed = True
            futures = tuple(self._futures)
        if wait_for_background and futures:
            wait(futures)


def trigger_adaptive_runtime(
    runtime: AdaptiveRuntime,
    task: str,
    project_root: Path | str,
    context: Mapping[str, Any] | None = None,
    *,
    priority: str = "normal",
    event_id: str | None = None,
    max_attempts: int = 3,
) -> TriggerReceipt:
    """Convenience function backed by the runtime-keyed trigger adapter."""
    return AdaptiveTrigger.for_runtime(runtime).trigger_adaptive_runtime(
        task,
        project_root,
        context,
        priority=priority,
        event_id=event_id,
        max_attempts=max_attempts,
        fire_and_forget=True,
    )


__all__ = ["AdaptiveTrigger", "AdaptiveTriggerRequest", "TriggerOutcome", "TriggerReceipt", "trigger_adaptive_runtime"]
