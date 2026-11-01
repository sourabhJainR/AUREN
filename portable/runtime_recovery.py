"""Shared bounded-execution guard for resumable AUREN runtimes."""
from __future__ import annotations
import hashlib
import json
import math
import time
from threading import Event, Lock
from typing import Any, Mapping


def _stable_json_value(value: Any) -> Any:
    """Project runtime state to a deterministic, JSON-safe fingerprint.

    Runtime contexts may contain live guards, callbacks, adapters, or other
    capabilities. Fingerprints include their type marker, never object reprs,
    memory addresses, or executable objects.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else {"__type__": "non-finite-float"}
    if isinstance(value, Mapping):
        return {
            str(key): _stable_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) != "_auren_runtime_execution_guard"
        }
    if isinstance(value, (list, tuple)):
        return [_stable_json_value(item) for item in value]
    return {"__type__": type(value).__qualname__}


def _digest(value: object) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TypeError("guard values must be finite JSON-compatible data") from exc
    return hashlib.sha256(raw).hexdigest()


class ExecutionBudgetExceeded(RuntimeError):
    """The task exhausted its step, attempt, or time budget."""


class NoProgressDetected(RuntimeError):
    """The same node failed on the same state repeatedly."""


class RuntimeExecutionGuard:
    """One thread-safe budget for graph nodes, handoffs, and supervisor retries.

    Pass one instance through all task execution layers. Resume from snapshot()
    to preserve charges. Cancellation is cooperative; workers must poll the
    cancellation_event and external effects still need idempotency keys.
    """
    SCHEMA_VERSION = "auren-execution-budget-v1"

    def __init__(self, *, max_steps: int = 200, max_attempts: int = 12,
                 deadline_seconds: float | None = None, repeated_failure_limit: int = 2,
                 cancellation_event: Event | None = None,
                 max_delivery_bytes: int = 8_000_000) -> None:
        if type(max_steps) is not int or max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if deadline_seconds is not None and (
            isinstance(deadline_seconds, bool)
            or not isinstance(deadline_seconds, (int, float))
            or not math.isfinite(deadline_seconds)
            or deadline_seconds <= 0
        ):
            raise ValueError("deadline_seconds must be a finite positive number")
        if type(repeated_failure_limit) is not int or repeated_failure_limit < 1:
            raise ValueError("repeated_failure_limit must be a positive integer")
        if type(max_delivery_bytes) is not int or max_delivery_bytes < 0:
            raise ValueError("max_delivery_bytes must be a non-negative integer")
        self.max_steps, self.max_attempts = max_steps, max_attempts
        self.max_delivery_bytes = max_delivery_bytes
        self._delivery_charges: dict[str, int] = {}
        self.repeated_failure_limit = repeated_failure_limit
        self._deadline = time.monotonic() + deadline_seconds if deadline_seconds is not None else None
        self._steps = self._attempts = 0
        self._charged: set[str] = set()
        self._failures: dict[str, int] = {}
        self._cancel = cancellation_event or Event()
        self._reason: str | None = None
        self._lock = Lock()

    @property
    def cancellation_event(self) -> Event:
        return self._cancel

    @property
    def attempts_used(self) -> int:
        with self._lock:
            return self._attempts

    @property
    def cancellation_reason(self) -> str | None:
        with self._lock:
            return self._reason

    def cancel(self, reason: str = "cancelled") -> None:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("cancellation reason must be non-empty")
        with self._lock:
            if self._reason is None:
                self._reason = reason.strip()[:500]
            self._cancel.set()

    def tighten_deadline(self, deadline_seconds: float) -> float:
        """Shorten the current monotonic deadline without ever extending it."""
        if (
            isinstance(deadline_seconds, bool)
            or not isinstance(deadline_seconds, (int, float))
            or not math.isfinite(deadline_seconds)
            or deadline_seconds <= 0
        ):
            raise ValueError("deadline_seconds must be a finite positive number")
        candidate = time.monotonic() + float(deadline_seconds)
        with self._lock:
            if self._deadline is None or candidate < self._deadline:
                self._deadline = candidate
            return max(0.0, self._deadline - time.monotonic())

    def charge_step(self) -> int:
        with self._lock:
            self._check_locked()
            if self._steps >= self.max_steps:
                raise ExecutionBudgetExceeded(f"task step budget exhausted ({self.max_steps})")
            self._steps += 1
            return self._steps

    def charge_attempt(self, operation_key: str) -> bool:
        if not isinstance(operation_key, str) or not operation_key.strip():
            raise ValueError("operation_key is required")
        with self._lock:
            if operation_key in self._charged:
                return False
            self._check_locked()
            if self._attempts >= self.max_attempts:
                raise ExecutionBudgetExceeded(f"task attempt budget exhausted ({self.max_attempts})")
            self._charged.add(operation_key)
            self._attempts += 1
            return True

    @property
    def delivery_bytes_used(self) -> int:
        with self._lock:
            return sum(self._delivery_charges.values())

    def consume_delivery_bytes(self, amount: int, *, operation_key: str) -> bool:
        """Charge output bytes once against the shared task/crew budget.

        A repeated operation key is not charged twice and is not authorized to
        resend. This is deliberately fail-closed after an uncertain send.
        """
        if type(amount) is not int or amount < 0:
            raise ValueError("delivery byte charge must be a non-negative integer")
        if not isinstance(operation_key, str) or not operation_key.strip():
            raise ValueError("delivery operation key is required")
        with self._lock:
            self._check_locked()
            previous = self._delivery_charges.get(operation_key)
            if previous is not None:
                if previous != amount:
                    raise ValueError("delivery operation key was reused with a different byte charge")
                return False
            if sum(self._delivery_charges.values()) + amount > self.max_delivery_bytes:
                return False
            self._delivery_charges[operation_key] = amount
            return True

    def check(self) -> None:
        with self._lock:
            self._check_locked()

    def observe_failure(self, *, node: str, state: Mapping[str, Any], error: BaseException) -> int:
        if not isinstance(node, str) or not node.strip():
            raise ValueError("node is required")
        signature = node + ":" + _digest([_stable_json_value(state), type(error).__qualname__, str(error)[:1000]])
        with self._lock:
            count = self._failures.get(signature, 0) + 1
            self._failures[signature] = count
            if count >= self.repeated_failure_limit:
                self._reason = f"no progress: repeated failure at {node} ({count} times)"
                self._cancel.set()
                raise NoProgressDetected(self._reason)
            return count

    def observe_progress(self, *, node: str, state: Mapping[str, Any]) -> None:
        signature = _digest([node, _stable_json_value(state)])
        with self._lock:
            self._failures = {
                key: value for key, value in self._failures.items()
                if not key.startswith(node + ":")
            }
            self._failures[node + ":" + signature] = 0

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            remaining = max(0.0, self._deadline - time.monotonic()) if self._deadline is not None else None
            payload = {
                "schema_version": self.SCHEMA_VERSION,
                "max_steps": self.max_steps, "steps_used": self._steps,
                "max_attempts": self.max_attempts, "attempts_used": self._attempts,
                "max_delivery_bytes": self.max_delivery_bytes,
                "delivery_bytes_used": sum(self._delivery_charges.values()),
                "delivery_charges": dict(sorted(self._delivery_charges.items())),
                "remaining_seconds": remaining,
                "deadline_unix": time.time() + remaining if remaining is not None else None,
                "charged_operations": sorted(self._charged),
                "failure_counts": dict(sorted(self._failures.items())),
                "cancelled": self._cancel.is_set(), "cancellation_reason": self._reason,
            }
            payload["digest"] = _digest(payload)
            return payload

    @classmethod
    def resume(cls, snapshot: Mapping[str, Any], *, cancellation_event: Event | None = None) -> "RuntimeExecutionGuard":
        if not isinstance(snapshot, Mapping) or snapshot.get("schema_version") != cls.SCHEMA_VERSION:
            raise ValueError("unsupported execution budget checkpoint")
        supplied = snapshot.get("digest")
        payload = {key: value for key, value in snapshot.items() if key != "digest"}
        if not isinstance(supplied, str) or supplied != _digest(payload):
            raise ValueError("execution budget checkpoint digest mismatch")
        max_steps, steps = snapshot.get("max_steps"), snapshot.get("steps_used")
        max_attempts, attempts = snapshot.get("max_attempts"), snapshot.get("attempts_used")
        remaining = snapshot.get("remaining_seconds")
        deadline_unix = snapshot.get("deadline_unix")
        if type(max_steps) is not int or max_steps < 1 or type(steps) is not int or not 0 <= steps <= max_steps:
            raise ValueError("invalid step budget checkpoint")
        if type(max_attempts) is not int or max_attempts < 1 or type(attempts) is not int or not 0 <= attempts <= max_attempts:
            raise ValueError("invalid attempt budget checkpoint")
        if remaining is not None and (
            isinstance(remaining, bool)
            or not isinstance(remaining, (int, float))
            or not math.isfinite(remaining)
            or remaining < 0
        ):
            raise ValueError("invalid remaining deadline")
        if deadline_unix is not None and (
            isinstance(deadline_unix, bool)
            or not isinstance(deadline_unix, (int, float))
            or not math.isfinite(deadline_unix)
        ):
            raise ValueError("invalid absolute deadline")
        if remaining is not None and deadline_unix is None:
            raise ValueError("checkpoint lacks absolute deadline; explicit recovery is required")
        if remaining is None and deadline_unix is not None:
            raise ValueError("absolute deadline has no remaining-time field")
        if deadline_unix is not None:
            remaining = max(0.0, float(deadline_unix) - time.time())
        charged, failures = snapshot.get("charged_operations"), snapshot.get("failure_counts")
        if not isinstance(charged, list) or any(not isinstance(v, str) or not v for v in charged):
            raise ValueError("invalid charged operation ledger")
        if len(charged) != len(set(charged)) or len(charged) != attempts:
            raise ValueError("attempt count does not match charged operation ledger")
        if not isinstance(failures, Mapping) or any(not isinstance(k, str) or type(v) is not int or v < 0 for k, v in failures.items()):
            raise ValueError("invalid failure-cycle ledger")
        max_delivery_bytes = snapshot.get("max_delivery_bytes", 8_000_000)
        delivery_charges = snapshot.get("delivery_charges", {})
        delivery_used = snapshot.get("delivery_bytes_used", 0)
        if type(max_delivery_bytes) is not int or max_delivery_bytes < 0:
            raise ValueError("invalid delivery byte budget checkpoint")
        if not isinstance(delivery_charges, Mapping) or any(
            not isinstance(k, str) or not k.strip() or type(v) is not int or v < 0
            for k, v in delivery_charges.items()
        ):
            raise ValueError("invalid delivery byte charge ledger")
        if type(delivery_used) is not int or delivery_used != sum(delivery_charges.values()) or delivery_used > max_delivery_bytes:
            raise ValueError("delivery byte charge total does not match its ledger")
        cancelled, reason = snapshot.get("cancelled"), snapshot.get("cancellation_reason")
        if type(cancelled) is not bool or (reason is not None and not isinstance(reason, str)):
            raise ValueError("invalid cancellation checkpoint")
        guard = cls(max_steps=max_steps, max_attempts=max_attempts,
                    deadline_seconds=remaining if remaining is not None and remaining > 0 else None,
                    cancellation_event=cancellation_event, max_delivery_bytes=max_delivery_bytes)
        guard._steps, guard._attempts = steps, attempts
        guard._charged, guard._failures = set(charged), dict(failures)
        guard._delivery_charges = dict(delivery_charges)
        guard._reason = reason
        if cancelled or (remaining is not None and remaining <= 0):
            guard._reason = reason or "task deadline exhausted before resume"
            guard._cancel.set()
        return guard

    def _check_locked(self) -> None:
        if self._cancel.is_set():
            raise ExecutionBudgetExceeded(self._reason or "execution cancelled")
        if self._deadline is not None and time.monotonic() >= self._deadline:
            self._reason = "task deadline exceeded"
            self._cancel.set()
            raise ExecutionBudgetExceeded(self._reason)


__all__ = ["ExecutionBudgetExceeded", "NoProgressDetected", "RuntimeExecutionGuard"]
