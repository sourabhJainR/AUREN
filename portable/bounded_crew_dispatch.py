"""Bounded, deterministic fan-out for independent AUREN crew proposals.

The dispatcher does not create durable work or grant execution authority. Callers
remain responsible for checkpointing and validating each returned proposal.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Mapping, Sequence


class BoundedCrewDispatcher:
    """Run a small batch concurrently while preserving input-order results."""

    MAX_TASKS = 8
    MAX_WORKERS = 4

    def __init__(self, dispatch: Callable[[Mapping[str, Any]], Any]) -> None:
        if not callable(dispatch):
            raise TypeError("dispatch must be callable")
        self._dispatch = dispatch

    def run(
        self,
        requests: Sequence[Mapping[str, Any]],
        *,
        max_workers: int = 2,
    ) -> list[Any]:
        if isinstance(requests, (str, bytes)) or not isinstance(requests, Sequence):
            raise TypeError("requests must be a sequence of proposal request mappings")
        if not requests:
            return []
        if len(requests) > self.MAX_TASKS:
            raise ValueError(f"crew batch exceeds the {self.MAX_TASKS} request limit")
        if type(max_workers) is not int or not 1 <= max_workers <= self.MAX_WORKERS:
            raise ValueError(f"max_workers must be an integer between 1 and {self.MAX_WORKERS}")
        for index, request in enumerate(requests):
            if not isinstance(request, Mapping):
                raise TypeError(f"crew request at index {index} must be a mapping")
            if not isinstance(request.get("crew_id"), str) or not request["crew_id"].strip():
                raise ValueError(f"crew request at index {index} requires crew_id")
            if not isinstance(request.get("task"), str) or not request["task"].strip():
                raise ValueError(f"crew request at index {index} requires task")
            evidence = request.get("evidence")
            if evidence is not None and not isinstance(evidence, Mapping):
                raise ValueError(f"crew request at index {index} evidence must be a mapping")

        results: list[Any] = [None] * len(requests)
        with ThreadPoolExecutor(max_workers=min(max_workers, len(requests)),
                                thread_name_prefix="auren-crew") as pool:
            futures = {
                pool.submit(self._dispatch, request): index
                for index, request in enumerate(requests)
            }
            try:
                for future in as_completed(futures):
                    results[futures[future]] = future.result()
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
        return results


__all__ = ["BoundedCrewDispatcher"]
