"""Bounded work strategies for long-running AUREN tasks.

These helpers make resource degradation explicit. They do not authorize actions,
skip mandatory checks, or turn partial evidence into a complete result.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable, Iterator, TypeVar

T = TypeVar("T")


class WorkBudgetExceeded(RuntimeError):
    """Raised when no safe bounded strategy can preserve the required coverage."""


@dataclass(frozen=True)
class WorkPlan:
    strategy: str
    estimated_items: int
    estimated_chars: int
    reason: str
    complete_coverage: bool


def plan_bounded_work(
    *,
    item_count: int,
    payload_chars: int,
    max_items_per_call: int,
    max_chars_per_call: int,
    chunkable: bool = False,
    streamable: bool = False,
    independent_batches: bool = False,
    allow_sampling: bool = False,
    complete_coverage_required: bool = True,
) -> WorkPlan:
    """Choose a bounded strategy without silently dropping required work.

    Sampling is available only when explicitly allowed and full coverage is not
    required. Security, policy, acceptance, and holdout checks should require
    complete coverage and therefore cannot degrade to sampling.
    """
    values = (item_count, payload_chars, max_items_per_call, max_chars_per_call)
    if any(type(value) is not int for value in values):
        raise TypeError("work estimates and limits must be integers")
    if item_count < 0 or payload_chars < 0:
        raise ValueError("work estimates cannot be negative")
    if max_items_per_call < 1 or max_chars_per_call < 1:
        raise ValueError("work limits must be positive")

    too_many_items = item_count > max_items_per_call
    too_many_chars = payload_chars > max_chars_per_call
    if not too_many_items and not too_many_chars:
        return WorkPlan("direct", item_count, payload_chars, "within configured limits", True)
    if too_many_items and too_many_chars and independent_batches and chunkable:
        return WorkPlan(
            "chunked_batches", item_count, payload_chars,
            "split independent items into batches and bound each item's payload", True,
        )
    if too_many_items and independent_batches:
        return WorkPlan("batch", item_count, payload_chars, "process independent items in bounded batches", True)
    if too_many_chars and chunkable:
        return WorkPlan("chunk", item_count, payload_chars, "split payload at safe boundaries; preserve all chunks", True)
    if streamable:
        return WorkPlan("stream", item_count, payload_chars, "consume incrementally with a bounded working set", True)
    if allow_sampling and not complete_coverage_required:
        return WorkPlan(
            "sample", item_count, payload_chars,
            "exploratory sample explicitly permitted; conclusions must be labelled sampled", False,
        )
    raise WorkBudgetExceeded(
        "limits would be exceeded and no coverage-preserving strategy is available; "
        "decompose the task, enable a safe streaming/batching interface, or request an explicit scope change"
    )


def iter_text_chunks(text: str, max_chars: int, *, overlap: int = 0) -> Iterator[str]:
    """Yield bounded text chunks, preferring newline/space boundaries without data loss.

    Chunks are complete source slices. A nonzero overlap deliberately repeats context
    between adjacent chunks; callers must deduplicate findings by source offsets or IDs.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if type(max_chars) is not int or max_chars < 1:
        raise ValueError("max_chars must be a positive integer")
    if type(overlap) is not int or overlap < 0 or overlap >= max_chars:
        raise ValueError("overlap must be an integer from zero to max_chars - 1")
    start = 0
    length = len(text)
    while start < length:
        end = min(start + max_chars, length)
        if end < length:
            floor = start + max(1, max_chars // 2)
            boundary = max(text.rfind("\n", floor, end), text.rfind(" ", floor, end))
            if boundary >= floor:
                end = boundary + 1
        yield text[start:end]
        if end >= length:
            break
        next_start = end - overlap
        if next_start <= start:
            raise RuntimeError("chunker failed to make forward progress")
        start = next_start


def iter_batches(items: Iterable[T], batch_size: int, *, max_batches: int | None = None) -> Iterator[list[T]]:
    """Batch an iterable lazily; if a configured batch cap is exceeded, fail, never truncate."""
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    if max_batches is not None and (type(max_batches) is not int or max_batches < 1):
        raise ValueError("max_batches must be a positive integer when supplied")
    iterator = iter(items)
    batches = 0
    while True:
        batch: list[T] = []
        for _ in range(batch_size):
            try:
                batch.append(next(iterator))
            except StopIteration:
                break
        if not batch:
            return
        if max_batches is not None and batches >= max_batches:
            raise WorkBudgetExceeded("batch budget exceeded; remaining items were not silently discarded")
        batches += 1
        yield batch
        if len(batch) < batch_size:
            return


@dataclass(frozen=True)
class SampleResult:
    items: tuple[T, ...]
    population_count: int
    sample_size: int
    seed: int


def reservoir_sample(
    items: Iterable[T],
    sample_size: int,
    *,
    seed: int,
    allow_sampling: bool = False,
    complete_coverage_required: bool = True,
) -> SampleResult[T]:
    """Return a reproducible O(sample_size)-memory sample from a stream.

    Sampling must be explicitly enabled and is forbidden for tasks requiring complete
    coverage. The population is still consumed in full so the sample count is accurate.
    """
    if type(sample_size) is not int or sample_size < 1:
        raise ValueError("sample_size must be a positive integer")
    if type(seed) is not int:
        raise TypeError("seed must be an integer")
    if not allow_sampling or complete_coverage_required:
        raise WorkBudgetExceeded("sampling is disabled unless explicitly allowed for non-critical exploratory work")
    rng = random.Random(seed)
    reservoir: list[T] = []
    population = 0
    for item in items:
        population += 1
        if len(reservoir) < sample_size:
            reservoir.append(item)
        else:
            index = rng.randrange(population)
            if index < sample_size:
                reservoir[index] = item
    return SampleResult(tuple(reservoir), population, len(reservoir), seed)
