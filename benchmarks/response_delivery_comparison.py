#!/usr/bin/env python3
"""Paired deterministic baseline/candidate response-delivery measurements.

Provider spend is intentionally reported as null: this benchmark uses local
receivers and has no provider billing meter. Transported UTF-8 bytes are the
cost proxy. Results are descriptive, not a promotion gate by themselves.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path

from portable.progress_journal import ProgressJournal
from portable.response_delivery import chunk_response, deliver_response as deliver_baseline
from portable.response_delivery_transport import deliver_response as deliver_candidate
from portable.runtime_recovery import RuntimeExecutionGuard


def _payload() -> str:
    block = "AUREN delivery benchmark 東京🙂 | evidence-linked engineering result\n"
    return block * 4096


def _measure_baseline(payload: str) -> dict:
    with tempfile.TemporaryDirectory() as temp:
        journal = ProgressJournal(Path(temp) / "progress.jsonl")
        journal.record("baseline", "delivery_started", state="running", stage="response-delivery")
        chunks = []
        manifest, expected = chunk_response(payload, max_bytes=4096)

        def receiver(chunk, _key):
            chunks.append(chunk)
            return {"response_id": chunk.response_id, "index": chunk.index, "sha256": chunk.sha256}

        tracemalloc.start()
        started = time.perf_counter()
        receipt = deliver_baseline(payload, task_id="baseline", journal=journal,
                                   send_chunk=receiver, max_bytes=4096)
        elapsed = (time.perf_counter() - started) * 1000
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        delivered = "".join(chunk.text for chunk in chunks)
        return {
            "latency_ms": round(elapsed, 3),
            "peak_memory_bytes": peak,
            "transported_bytes": sum(chunk.utf8_bytes for chunk in chunks),
            "chunks": len(chunks),
            "integrity_ok": hashlib.sha256(delivered.encode()).hexdigest() == manifest.sha256,
            "complete": receipt.complete,
        }


def _measure_candidate(payload: str) -> dict:
    with tempfile.TemporaryDirectory() as temp:
        journal = ProgressJournal(Path(temp) / "progress.jsonl")
        journal.record("candidate", "delivery_started", state="running", stage="response-delivery")
        chunks = []
        guard = RuntimeExecutionGuard(max_delivery_bytes=len(payload.encode("utf-8")))

        def receiver(chunk):
            chunks.append(chunk)
            return {
                "response_id": chunk.response_id,
                "index": chunk.index,
                "chunk_sha256": chunk.sha256,
            }

        tracemalloc.start()
        started = time.perf_counter()
        receipt = deliver_candidate(
            "candidate", payload, receiver=receiver, journal=journal,
            max_chunk_bytes=4096, execution_guard=guard,
        )
        elapsed = (time.perf_counter() - started) * 1000
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        delivered = "".join(chunk.text for chunk in chunks)
        return {
            "latency_ms": round(elapsed, 3),
            "peak_memory_bytes": peak,
            "transported_bytes": sum(chunk.utf8_bytes for chunk in chunks),
            "chunks": len(chunks),
            "integrity_ok": hashlib.sha256(delivered.encode()).hexdigest() == receipt.response_id,
            "complete": receipt.acknowledged_chunks == receipt.total_chunks,
            "shared_budget_bytes_used": guard.delivery_bytes_used,
        }


def _measure_recovery(payload: str) -> dict:
    with tempfile.TemporaryDirectory() as temp:
        journal = ProgressJournal(Path(temp) / "progress.jsonl")
        journal.record("recovery", "delivery_started", state="running", stage="response-delivery")
        first_pass = []
        manifest, _chunks = chunk_response(payload, max_bytes=4096)

        def receiver(chunk):
            first_pass.append(chunk.index)
            return {"response_id": chunk.response_id, "index": chunk.index,
                    "chunk_sha256": chunk.sha256}

        interrupted = False
        try:
            deliver_candidate(
                "recovery", payload, receiver=receiver, journal=journal,
                max_chunk_bytes=4096,
                consume_shared_budget=lambda _size: len(first_pass) < 2,
            )
        except Exception as exc:
            from portable.response_delivery_transport import DeliveryBlocked
            if not isinstance(exc, DeliveryBlocked):
                raise
            interrupted = True

        resumed_indices = []
        resumed = deliver_candidate(
            "recovery", payload,
            receiver=lambda chunk: (
                resumed_indices.append(chunk.index),
                {"response_id": chunk.response_id, "index": chunk.index,
                 "chunk_sha256": chunk.sha256},
            )[1],
            journal=journal, max_chunk_bytes=4096,
        )
        return {
            "interrupted_after_acknowledged_chunks": len(first_pass),
            "resumed_from_chunk": resumed.resumed_from_chunk,
            "resumed_chunks": len(resumed_indices),
            "recovery_complete": resumed.acknowledged_chunks == resumed.total_chunks,
            "integrity_ok": resumed.response_id == manifest.sha256,
            "automatic_replay_of_acknowledged_chunks": bool(set(first_pass) & set(resumed_indices)),
            "interruption_observed": interrupted,
        }


def run_comparison(iterations: int = 3) -> dict:
    if type(iterations) is not int or not 1 <= iterations <= 20:
        raise ValueError("iterations must be between 1 and 20")
    payload = _payload()
    baseline = [_measure_baseline(payload) for _ in range(iterations)]
    candidate = [_measure_candidate(payload) for _ in range(iterations)]
    recovery = _measure_recovery(payload)

    def summarize(rows):
        return {
            key: round(statistics.median(row[key] for row in rows), 3)
            for key in ("latency_ms", "peak_memory_bytes", "transported_bytes", "chunks")
        } | {
            "integrity_ok_all": all(row["integrity_ok"] for row in rows),
            "complete_all": all(row["complete"] for row in rows),
        }

    base, cand = summarize(baseline), summarize(candidate)
    return {
        "schema_version": "auren-response-delivery-benchmark-v1",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "iterations": iterations,
            "baseline_revision": os.environ.get("AUREN_BENCHMARK_BASELINE_SHA"),
            "candidate_revision": os.environ.get("GITHUB_SHA"),
            "payload_bytes": len(payload.encode("utf-8")),
        },
        "baseline": base,
        "candidate": cand,
        "delta": {
            "latency_ms": round(cand["latency_ms"] - base["latency_ms"], 3),
            "peak_memory_bytes": cand["peak_memory_bytes"] - base["peak_memory_bytes"],
            "transported_bytes": cand["transported_bytes"] - base["transported_bytes"],
        },
        "recovery": recovery,
        "cost": {
            "provider_cost_usd": None,
            "provider_cost_status": "not_measured_no_metered_provider_in_local_benchmark",
            "transported_bytes_proxy": cand["transported_bytes"],
        },
        "interpretation": "Measurements are environment-specific. Do not promote on this report alone; require correctness, independent review, and deployment evidence.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_comparison(args.iterations)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if (
        result["baseline"]["integrity_ok_all"]
        and result["candidate"]["integrity_ok_all"]
        and result["candidate"]["complete_all"]
        and result["recovery"]["recovery_complete"]
        and not result["recovery"]["automatic_replay_of_acknowledged_chunks"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
