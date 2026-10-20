"""Process-isolated execution for crew proposal handlers that may hang.

Unlike thread-based dispatch, this runner can terminate a non-cooperative worker.
Only trusted importable callables are accepted. Request/result data must be JSON
serializable; this is a liveness boundary, not a sandbox or authorization boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
import math
import multiprocessing
import threading
import time
from typing import Any, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class IsolatedCrewResult:
    crew_id: str
    status: str
    result: Any = None
    error: str | None = None
    elapsed_seconds: float = 0.0


def _resolve_handler(reference: str):
    if not isinstance(reference, str) or ":" not in reference:
        raise ValueError("handler_ref must use module:qualname syntax")
    module_name, qualname = reference.split(":", 1)
    if not module_name or not qualname or "<locals>" in qualname:
        raise ValueError("handler_ref must name an importable top-level callable")
    value: Any = importlib.import_module(module_name)
    for part in qualname.split("."):
        value = getattr(value, part)
    if not callable(value):
        raise TypeError("resolved handler_ref is not callable")
    return value


def _worker(handler_ref: str, request_json: str, send_conn, max_result_bytes: int) -> None:
    try:
        handler = _resolve_handler(handler_ref)
        request = json.loads(request_json)
        result = handler(request)
        encoded = json.dumps({"ok": True, "result": result}, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(encoded) > max_result_bytes:
            payload = {"ok": False, "error": "worker result exceeded output limit"}
        else:
            payload = {"ok": True, "result": result}
    except BaseException as exc:
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:2000]}
    try:
        send_conn.send_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    except (OSError, ValueError):
        pass
    finally:
        send_conn.close()


class ProcessIsolatedCrewDispatcher:
    """Run trusted importable crew handlers with hard deadlines and cancellation.

    The total deadline is shared by the entire batch. Each worker is terminated
    and then killed if it does not exit within the grace period. Results preserve
    request order. Timed-out/cancelled workers return explicit statuses and never
    fabricate a proposal.
    """

    MAX_TASKS = 8
    DEFAULT_OUTPUT_LIMIT_BYTES = 64 * 1024

    def __init__(self, handler_ref: str, *, output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES,
                 max_request_bytes: int = 256 * 1024,
                 terminate_grace_seconds: float = 0.2, poll_interval_seconds: float = 0.02):
        _resolve_handler(handler_ref)
        if type(output_limit_bytes) is not int or output_limit_bytes < 128:
            raise ValueError("output_limit_bytes must be an integer >= 128")
        if type(max_request_bytes) is not int or max_request_bytes < 128:
            raise ValueError("max_request_bytes must be an integer >= 128")
        for name, value in (("terminate_grace_seconds", terminate_grace_seconds), ("poll_interval_seconds", poll_interval_seconds)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        self.handler_ref = handler_ref
        self.output_limit_bytes = output_limit_bytes
        self.max_request_bytes = max_request_bytes
        self.terminate_grace_seconds = float(terminate_grace_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)

    def _stop(self, process) -> None:
        if process.is_alive():
            process.terminate()
            process.join(self.terminate_grace_seconds)
        if process.is_alive():
            process.kill()
            process.join(max(1.0, self.terminate_grace_seconds))

    def run(self, requests: Sequence[Mapping[str, Any]], *, timeout_seconds: float,
            cancellation_event: threading.Event | None = None) -> list[IsolatedCrewResult]:
        if isinstance(requests, (str, bytes)) or not isinstance(requests, Sequence):
            raise TypeError("requests must be a sequence of proposal request mappings")
        if len(requests) > self.MAX_TASKS:
            raise ValueError(f"crew batch exceeds the {self.MAX_TASKS} request limit")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        encoded_requests = []
        for index, request in enumerate(requests):
            if not isinstance(request, Mapping):
                raise TypeError(f"crew request at index {index} must be a mapping")
            if not isinstance(request.get("crew_id"), str) or not request["crew_id"].strip():
                raise ValueError(f"crew request at index {index} requires crew_id")
            if not isinstance(request.get("task"), str) or not request["task"].strip():
                raise ValueError(f"crew request at index {index} requires task")
            try:
                encoded = json.dumps(dict(request), sort_keys=True, separators=(",", ":"), allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"crew request at index {index} must be JSON serializable") from exc
            if len(encoded.encode("utf-8")) > self.max_request_bytes:
                raise ValueError(f"crew request at index {index} exceeds the request byte limit")
            encoded_requests.append(encoded)
        if not requests:
            return []
        context = multiprocessing.get_context("spawn")
        deadline = time.monotonic() + float(timeout_seconds)
        results: list[IsolatedCrewResult] = []
        for request, request_json in zip(requests, encoded_requests):
            crew_id = request["crew_id"]
            started = time.monotonic()
            if cancellation_event is not None and cancellation_event.is_set():
                results.append(IsolatedCrewResult(crew_id, "cancelled", error="cancellation requested"))
                results.extend(IsolatedCrewResult(item["crew_id"], "not_run", error="prior cancellation") for item in requests[len(results):])
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                results.append(IsolatedCrewResult(crew_id, "timed_out", error="total batch deadline exceeded"))
                results.extend(IsolatedCrewResult(item["crew_id"], "not_run", error="total batch deadline exceeded") for item in requests[len(results):])
                break
            receive_conn, send_conn = context.Pipe(duplex=False)
            process = context.Process(target=_worker, args=(self.handler_ref, request_json, send_conn, self.output_limit_bytes), daemon=True)
            try:
                process.start()
                send_conn.close()
                while process.is_alive() and not receive_conn.poll(0):
                    if cancellation_event is not None and cancellation_event.is_set():
                        self._stop(process)
                        results.append(IsolatedCrewResult(crew_id, "cancelled", error="cancellation requested", elapsed_seconds=time.monotonic()-started))
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._stop(process)
                        results.append(IsolatedCrewResult(crew_id, "timed_out", error="total batch deadline exceeded", elapsed_seconds=time.monotonic()-started))
                        break
                    time.sleep(min(self.poll_interval_seconds, remaining))
                else:
                    if not any(r.crew_id == crew_id for r in results[-1:]) or results[-1].status not in {"cancelled", "timed_out"}:
                        if receive_conn.poll(0.1):
                            try:
                                payload = json.loads(receive_conn.recv_bytes(self.output_limit_bytes + 4096).decode("utf-8"))
                                if payload.get("ok") is True:
                                    results.append(IsolatedCrewResult(crew_id, "completed", result=payload.get("result"), elapsed_seconds=time.monotonic()-started))
                                else:
                                    results.append(IsolatedCrewResult(crew_id, "failed", error=str(payload.get("error", "worker failed")), elapsed_seconds=time.monotonic()-started))
                            except (EOFError, OSError, ValueError, json.JSONDecodeError):
                                results.append(IsolatedCrewResult(crew_id, "failed", error="worker exited without a valid bounded result", elapsed_seconds=time.monotonic()-started))
                        else:
                            results.append(IsolatedCrewResult(crew_id, "failed", error=f"worker exited with code {process.exitcode}", elapsed_seconds=time.monotonic()-started))
            finally:
                if process.is_alive():
                    self._stop(process)
                receive_conn.close()
                try:
                    send_conn.close()
                except OSError:
                    pass
            if results[-1].status in {"timed_out", "cancelled"}:
                results.extend(IsolatedCrewResult(item["crew_id"], "not_run", error="prior worker did not complete") for item in requests[len(results):])
                break
        return results


__all__ = ["IsolatedCrewResult", "ProcessIsolatedCrewDispatcher"]
