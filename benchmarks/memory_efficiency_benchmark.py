#!/usr/bin/env python3
"""Paired mechanical benchmark for AUREN's lossless memory packing primitives.

This measures byte reduction and record coverage only. It does not measure
semantic recall, model task success, security effectiveness, or production
latency; those require a pinned task corpus and independent evaluation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import tracemalloc
from pathlib import Path

from portable.memory_efficiency import chunk_memory_context, compress_snapshot, decompress_snapshot

CRITICAL_CATEGORIES = {
    "acceptance", "constraint", "constraints", "guardrail", "guardrails",
    "security", "policy", "rollback", "failure", "negative_evidence",
    "requirement", "requirements", "invariant", "invariants",
}


def _load_corpus(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at line {line_number}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"record at line {line_number} must be an object")
            if not isinstance(record.get("id"), str) or not record["id"].strip():
                raise ValueError(f"record at line {line_number} needs a non-empty string id")
            if not isinstance(record.get("text"), str):
                raise ValueError(f"record at line {line_number} needs string text")
            records.append(record)
    ids = [record["id"] for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("corpus contains duplicate record IDs")
    return records


def run_benchmark(corpus_path: Path, *, chunk_chars: int) -> dict:
    records = _load_corpus(corpus_path)
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    corpus_digest = hashlib.sha256(canonical).hexdigest()

    tracemalloc.start()
    started = time.perf_counter()
    packed = compress_snapshot(records)
    compression_seconds = time.perf_counter() - started

    started = time.perf_counter()
    restored = decompress_snapshot(packed.payload)
    round_trip_seconds = time.perf_counter() - started
    round_trip_exact = restored == records

    started = time.perf_counter()
    chunks = chunk_memory_context(records, max_chars=chunk_chars)
    packing_seconds = time.perf_counter() - started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    expected_ids = {record["id"] for record in records}
    emitted_ids = {record_id for chunk in chunks for record_id in chunk.record_ids}
    missing_ids = sorted(expected_ids - emitted_ids)
    critical_ids = {
        record["id"] for record in records
        if str(record.get("category", "")).strip().lower() in CRITICAL_CATEGORIES
    }
    critical_missing = sorted(critical_ids - emitted_ids)
    max_observed_chunk_chars = max((len(chunk.text) for chunk in chunks), default=0)
    compressed_bytes = len(packed.payload)
    raw_bytes = len(canonical)
    return {
        "benchmark": "auren-lossless-memory-efficiency-v1",
        "interpretation": "mechanical corpus benchmark only; not a semantic-quality or production-performance claim",
        "corpus_path": str(corpus_path),
        "corpus_sha256": corpus_digest,
        "records": len(records),
        "critical_records": len(critical_ids),
        "raw_canonical_bytes": raw_bytes,
        "compressed_snapshot_bytes": compressed_bytes,
        "compressed_to_raw_ratio": round(compressed_bytes / raw_bytes, 6) if raw_bytes else 1.0,
        "byte_savings_percent": round((1 - compressed_bytes / raw_bytes) * 100, 3) if raw_bytes else 0.0,
        "compression_seconds": round(compression_seconds, 6),
        "round_trip_seconds": round(round_trip_seconds, 6),
        "context_packing_seconds": round(packing_seconds, 6),
        "peak_traced_bytes": peak_bytes,
        "chunks": len(chunks),
        "chunk_char_limit": chunk_chars,
        "max_observed_chunk_chars": max_observed_chunk_chars,
        "round_trip_exact": round_trip_exact,
        "record_coverage": round(len(expected_ids - set(missing_ids)) / len(expected_ids), 6) if expected_ids else 1.0,
        "critical_record_coverage": round(len(critical_ids - set(critical_missing)) / len(critical_ids), 6) if critical_ids else 1.0,
        "missing_record_ids": missing_ids,
        "missing_critical_record_ids": critical_missing,
        "promotion_eligible": bool(round_trip_exact and not missing_ids and not critical_missing and max_observed_chunk_chars <= chunk_chars),
        "promotion_note": "promotion still requires paired task-success, critical-fact recall, security, latency and uncertainty evaluation",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path, help="UTF-8 JSONL of memory records; keep sensitive corpora in approved local environments")
    parser.add_argument("--chunk-chars", type=int, default=4200)
    parser.add_argument("--output", type=Path, help="optional output JSON path; otherwise write to stdout")
    args = parser.parse_args(argv)
    try:
        if args.chunk_chars < 128:
            raise ValueError("--chunk-chars must be at least 128")
        result = run_benchmark(args.corpus, chunk_chars=args.chunk_chars)
    except (OSError, ValueError, TypeError) as exc:
        print(f"memory benchmark blocked: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if result["promotion_eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
