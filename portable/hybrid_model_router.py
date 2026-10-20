"""Policy-aware hybrid routing with bounded local inference fallback.

This module plans and executes only against explicitly configured endpoints. It
never sends private prompts to remote providers and never retries without a hard
attempt/time budget. Resource metrics are best-effort and labelled when absent.
"""
from __future__ import annotations
import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from uuid import uuid4

from .runtime_recovery import ExecutionBudgetExceeded, RuntimeExecutionGuard
from .local_endpoint import open_loopback_request, validate_loopback_base_url


@dataclass(frozen=True)
class TaskRouteRequest:
    task: str
    capability: str
    privacy: str = "private"
    max_attempts: int = 2
    timeout_seconds: float = 30.0

@dataclass(frozen=True)
class RouteResult:
    provider: str
    model: str | None
    success: bool
    attempts: int
    latency_ms: float
    resource_usage: dict[str, Any]
    fallback_reason: str | None
    result: Any = None
    error: str | None = None

class _GuardPersistenceError(RuntimeError):
    """A shared-budget checkpoint could not be persisted; further dispatch is unsafe."""


class HybridModelRouter:
    """Route by capability/privacy; fallback is bounded and policy-preserving."""
    def __init__(self, *, discovery: LocalModelDiscovery | None = None,
                 remote_executor: Callable[[TaskRouteRequest], Any] | None = None,
                 local_executor: Callable[[TaskRouteRequest, str], Any] | None = None,
                 resource_reader: Callable[[], dict[str, Any]] | None = None):
        if discovery is None:
            from .muxin_session_adapter import LocalModelDiscovery
            discovery = LocalModelDiscovery()
        self.discovery = discovery
        self.remote_executor = remote_executor
        self.local_executor = local_executor or self._execute_local
        self.resource_reader = resource_reader or self._resources

    def _execute_local(self, request: TaskRouteRequest, model: str) -> Any:
        """Run a bounded Ollama-compatible generation request against loopback only."""
        base_url = validate_loopback_base_url(
            getattr(self.discovery, "base_url", "http://127.0.0.1:11434")
        )
        timeout = min(float(request.timeout_seconds), 120.0)
        if timeout <= 0:
            raise TimeoutError("local execution timeout budget exhausted")
        payload = json.dumps({"model": model, "prompt": request.task, "stream": False,
                              "options": {"num_predict": 1024}}).encode("utf-8")
        req = urllib.request.Request(base_url + "/api/generate", data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
        with open_loopback_request(req, timeout=timeout) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("local model response exceeds size limit")
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, dict) or not isinstance(decoded.get("response"), str):
            raise ValueError("local model returned an invalid response")
        return decoded["response"]

    @staticmethod
    def _resources():
        usage: dict[str, Any] = {"source": "process"}
        try:
            import resource
            usage["max_rss"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            usage["max_rss_unit"] = "platform-dependent"
        except (ImportError, OSError, ValueError):
            usage["max_rss"] = None
            usage["max_rss_unit"] = "unavailable"
        try:
            with open("/proc/meminfo", encoding="ascii") as stream:
                values = {}
                for line in stream:
                    if line.startswith(("MemAvailable:", "MemTotal:")):
                        key, raw = line.split(":", 1)
                        values[key] = int(raw.strip().split()[0]) * 1024
                usage["system_memory_bytes"] = values
        except (OSError, ValueError):
            usage["system_memory_bytes"] = None
        return usage

    @staticmethod
    def _capable(name: str, capability: str) -> bool:
        model = name.casefold()
        capability = capability.casefold()
        if capability in {"code", "coding", "repository", "software_engineering"}:
            return any(x in model for x in ("coder", "code", "deepseek-r1", "qwen2.5"))
        if capability in {"reasoning", "planning"}:
            return any(x in model for x in ("reason", "r1", "qwq", "thinking"))
        if capability in {"general", "chat", "summarization"}:
            return True
        return capability in model

    @classmethod
    def _ranked_candidates(cls, models: Any, capability: str) -> list[str]:
        """Prefer explicit provider capability metadata, then deterministic name heuristics."""
        if not isinstance(models, list):
            return []
        ranked: list[tuple[int, int, str]] = []
        wanted = capability.casefold().strip()
        for index, item in enumerate(models):
            if not isinstance(item, Mapping):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            declared = item.get("capabilities")
            if declared is not None:
                if not isinstance(declared, (list, tuple, set)) or not all(
                    isinstance(value, str) for value in declared
                ):
                    continue
                normalized = {value.casefold().strip() for value in declared}
                if wanted not in normalized and "*" not in normalized:
                    continue
                score = 100 if wanted in normalized else 50
            else:
                if not cls._capable(name, capability):
                    continue
                score = 10
            ranked.append((score, index, name.strip()))
        # Preserve the provider's configured priority within the same capability
        # tier; only explicit capability metadata outranks name heuristics.
        ranked.sort(key=lambda candidate: (-candidate[0], candidate[1]))
        return [name for _, _, name in ranked]

    def route(
        self,
        request: TaskRouteRequest,
        *,
        execution_guard: RuntimeExecutionGuard | None = None,
        execution_guard_persist: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> RouteResult:
        attempts_limit = max(1, min(int(request.max_attempts), 3))
        timeout = max(0.1, min(float(request.timeout_seconds), 120.0))
        bounded = TaskRouteRequest(request.task, request.capability, request.privacy,
                                   attempts_limit, timeout)
        started = time.monotonic()
        route_deadline = started + timeout
        guard = execution_guard or RuntimeExecutionGuard(
            max_steps=attempts_limit, max_attempts=attempts_limit, deadline_seconds=timeout
        )
        if execution_guard_persist is not None and not callable(execution_guard_persist):
            raise TypeError("execution_guard_persist must be callable")
        def persist_guard() -> None:
            if execution_guard_persist is None:
                return
            try:
                execution_guard_persist(guard.snapshot())
            except Exception as exc:
                raise _GuardPersistenceError("shared execution budget checkpoint failed; refusing further dispatch") from exc
        route_id = uuid4().hex
        before = self.resource_reader()
        try:
            guard.check()
            budget = guard.snapshot()
            if budget["attempts_used"] >= budget["max_attempts"]:
                raise ExecutionBudgetExceeded("task attempt budget exhausted before model discovery")
        except ExecutionBudgetExceeded as exc:
            return RouteResult("none", None, False, 0,
                round((time.monotonic()-started)*1000, 2),
                {"before": before, "after": self.resource_reader()}, str(exc),
                error="Execution budget is exhausted or cancelled before model discovery")
        try:
            local = self.discovery.discover()
            candidates = self._ranked_candidates(
                local.get("models") if isinstance(local, Mapping) else None,
                bounded.capability,
            )
            discovery_error = None
        except Exception as exc:
            local = {"available": False, "models": []}
            candidates = []
            discovery_error = type(exc).__name__
        private = bounded.privacy.casefold() not in {"public", "non_sensitive"}
        reason = None
        attempts_used = 0
        # Private/restricted data must remain local. No remote fallback is allowed.
        last_local_model = None
        if candidates and self.local_executor:
            for model in candidates[:attempts_limit]:
                try:
                    guard.check()
                    remaining = min(timeout - (time.monotonic() - started), route_deadline - time.monotonic())
                    shared_remaining = guard.snapshot().get("remaining_seconds")
                    if shared_remaining is not None:
                        remaining = min(remaining, float(shared_remaining))
                    if remaining <= 0:
                        raise ExecutionBudgetExceeded("overall timeout budget exhausted")
                    guard.charge_attempt(f"model-route:{route_id}:local:{attempts_used + 1}")
                    attempts_used += 1
                    persist_guard()
                    last_local_model = model
                    attempt_request = TaskRouteRequest(bounded.task, bounded.capability, bounded.privacy,
                                                       max(1, attempts_limit - attempts_used + 1), remaining)
                    value = self.local_executor(attempt_request, model)
                    if time.monotonic() >= route_deadline:
                        reason = "overall timeout budget exhausted"
                        break
                    guard.check()
                    return RouteResult("local", model, True, attempts_used,
                        round((time.monotonic()-started)*1000, 2),
                        {"before": before, "after": self.resource_reader()},
                        reason, value)
                except _GuardPersistenceError as exc:
                    return RouteResult("local", last_local_model, False, attempts_used,
                        round((time.monotonic()-started)*1000, 2),
                        {"before": before, "after": self.resource_reader()}, str(exc),
                        error="Shared execution budget checkpoint failed; further dispatch is blocked")
                except ExecutionBudgetExceeded as exc:
                    reason = str(exc)
                    break
                except Exception as exc:
                    reason = type(exc).__name__
            if private:
                return RouteResult("local", last_local_model, False, attempts_used,
                    round((time.monotonic()-started)*1000, 2),
                    {"before": before, "after": self.resource_reader()}, reason,
                    error="Local execution failed; privacy policy forbids remote fallback")
        elif private:
            reason = (f"local model discovery failed: {discovery_error}" if discovery_error
                      else "no capable local model or local executor")
            return RouteResult("local", None, False, 0,
                round((time.monotonic()-started)*1000, 2),
                {"before": before, "after": self.resource_reader()}, reason,
                error="No capable local route; privacy policy forbids remote fallback")
        # Remote execution is opt-in and only for explicitly non-sensitive tasks.
        if self.remote_executor is None:
            return RouteResult("none", None, False, attempts_used,
                round((time.monotonic()-started)*1000, 2),
                {"before": before, "after": self.resource_reader()}, reason,
                error="No eligible executor configured")
        remote_attempts = 0
        for _ in range(max(0, attempts_limit - attempts_used)):
            try:
                guard.check()
                remaining = min(timeout - (time.monotonic() - started), route_deadline - time.monotonic())
                shared_remaining = guard.snapshot().get("remaining_seconds")
                if shared_remaining is not None:
                    remaining = min(remaining, float(shared_remaining))
                if remaining <= 0:
                    raise ExecutionBudgetExceeded("overall timeout budget exhausted")
                guard.charge_attempt(f"model-route:{route_id}:remote:{remote_attempts + 1}")
                attempts_used += 1
                remote_attempts += 1
                persist_guard()
                attempt_request = TaskRouteRequest(bounded.task, bounded.capability, bounded.privacy,
                                                   max(1, attempts_limit - attempts_used + 1), remaining)
                value = self.remote_executor(attempt_request)
                if time.monotonic() >= route_deadline:
                    reason = "overall timeout budget exhausted"
                    break
                guard.check()
                return RouteResult("remote", None, True, attempts_used,
                    round((time.monotonic()-started)*1000, 2),
                    {"before": before, "after": self.resource_reader()}, reason, value)
            except _GuardPersistenceError as exc:
                return RouteResult(
                    "remote" if remote_attempts else ("local" if last_local_model else "none"),
                    None if remote_attempts else last_local_model,
                    False, attempts_used, round((time.monotonic()-started)*1000, 2),
                    {"before": before, "after": self.resource_reader()}, str(exc),
                    error="Shared execution budget checkpoint failed; further dispatch is blocked",
                )
            except ExecutionBudgetExceeded as exc:
                reason = str(exc)
                break
            except Exception as exc:
                reason = type(exc).__name__
        return RouteResult(
            "remote" if remote_attempts else ("local" if last_local_model else "none"),
            None if remote_attempts else last_local_model,
            False, attempts_used, round((time.monotonic()-started)*1000, 2),
            {"before": before, "after": self.resource_reader()}, reason,
            error=("Eligible execution failed within bounded retry budget" if remote_attempts
                   else "Attempt budget or deadline exhausted before remote execution"),
        )

    def route_with_context(
        self,
        request: TaskRouteRequest,
        context: Mapping[str, Any],
    ) -> RouteResult:
        """Route from a graph/specialist context using its canonical live guard.

        Graph nodes should pass their state mapping here rather than constructing
        a new router budget. A malformed injected capability fails closed before
        any model discovery or provider call.
        """
        if not isinstance(context, Mapping):
            raise TypeError("routing context must be a mapping")
        guard = context.get("_auren_runtime_execution_guard")
        if guard is not None and not isinstance(guard, RuntimeExecutionGuard):
            raise TypeError("routing context contains an invalid execution guard")
        guard_persist = context.get("auren_runtime_guard_persist")
        if guard_persist is not None and not callable(guard_persist):
            raise TypeError("routing context contains an invalid guard persistence callback")
        return self.route(
            request, execution_guard=guard,
            execution_guard_persist=guard_persist,
        )

    def snapshot(self) -> dict[str, Any]:
        return {"local_discovery": self.discovery.discover(),
                "policy": {"private_remote_fallback": False,
                           "max_attempts": 3, "max_timeout_seconds": 120},
                "resource_usage": self.resource_reader()}

__all__ = ["HybridModelRouter", "RouteResult", "TaskRouteRequest"]
