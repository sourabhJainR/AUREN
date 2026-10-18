"""Policy-aware hybrid routing with bounded local inference fallback.

This module plans and executes only against explicitly configured endpoints. It
never sends private prompts to remote providers and never retries without a hard
attempt/time budget. Resource metrics are best-effort and labelled when absent.
"""
from __future__ import annotations
import json
import time
import urllib.error
import urllib.request
from .local_endpoint import open_loopback_request, validate_loopback_base_url
from dataclasses import dataclass
from typing import Any, Callable


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
        timeout = max(0.1, min(float(request.timeout_seconds), 120.0))
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

    def route(self, request: TaskRouteRequest) -> RouteResult:
        attempts_limit = max(1, min(int(request.max_attempts), 3))
        timeout = max(0.1, min(float(request.timeout_seconds), 120.0))
        bounded = TaskRouteRequest(request.task, request.capability, request.privacy,
                                   attempts_limit, timeout)
        started = time.monotonic()
        before = self.resource_reader()
        local = self.discovery.discover()
        candidates = [m["name"] for m in local.get("models", [])
                      if m.get("name") and self._capable(m["name"], bounded.capability)]
        private = bounded.privacy.casefold() not in {"public", "non_sensitive"}
        reason = None
        # Private/restricted data must remain local. No remote fallback is allowed.
        if candidates and self.local_executor:
            local_attempts = 0
            last_local_model = candidates[0]
            for model in candidates[:attempts_limit]:
                if time.monotonic() - started >= timeout:
                    reason = "overall timeout budget exhausted"
                    break
                try:
                    local_attempts += 1
                    last_local_model = model
                    remaining = max(0.1, timeout - (time.monotonic() - started))
                    attempt_request = TaskRouteRequest(bounded.task, bounded.capability, bounded.privacy,
                                                       bounded.max_attempts, remaining)
                    value = self.local_executor(attempt_request, model)
                    return RouteResult("local", model, local_attempts,
                        round((time.monotonic()-started)*1000, 2),
                        {"before": before, "after": self.resource_reader()},
                        reason, value)
                except Exception as exc:
                    reason = type(exc).__name__
            if private:
                return RouteResult("local", last_local_model, local_attempts,
                    round((time.monotonic()-started)*1000, 2),
                    {"before": before, "after": self.resource_reader()}, reason,
                    error="Local execution failed; privacy policy forbids remote fallback")
        elif private:
            reason = "no capable local model or local executor"
            return RouteResult("local", None, False, 0,
                round((time.monotonic()-started)*1000, 2),
                {"before": before, "after": self.resource_reader()}, reason,
                error="No capable local route; privacy policy forbids remote fallback")
        # Remote execution is opt-in and only for explicitly non-sensitive tasks.
        if self.remote_executor is None:
            return RouteResult("none", None, False, 0,
                round((time.monotonic()-started)*1000, 2),
                {"before": before, "after": self.resource_reader()}, reason,
                error="No eligible executor configured")
        remote_attempts = 0
        for attempt in range(attempts_limit):
            if time.monotonic() - started >= timeout:
                reason = "overall timeout budget exhausted"
                break
            try:
                remaining = max(0.1, timeout - (time.monotonic() - started))
                attempt_request = TaskRouteRequest(bounded.task, bounded.capability, bounded.privacy,
                                                   bounded.max_attempts, remaining)
                value = self.remote_executor(attempt_request)
                return RouteResult("remote", None, True, attempt+1,
                    round((time.monotonic()-started)*1000, 2),
                    {"before": before, "after": self.resource_reader()}, reason, value)
            except Exception as exc:
                reason = type(exc).__name__
        return RouteResult("remote", None, False, remote_attempts,
            round((time.monotonic()-started)*1000, 2),
            {"before": before, "after": self.resource_reader()}, reason,
            error="Eligible execution failed within bounded retry budget")

    def snapshot(self) -> dict[str, Any]:
        return {"local_discovery": self.discovery.discover(),
                "policy": {"private_remote_fallback": False,
                           "max_attempts": 3, "max_timeout_seconds": 120},
                "resource_usage": self.resource_reader()}

__all__ = ["HybridModelRouter", "RouteResult", "TaskRouteRequest"]
