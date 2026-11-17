"""Build a report from externally collected paired benchmark receipts."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from benchmarks.paired_engineering_benchmark import (
    BenchmarkManifest, BenchmarkRun, compare_pair, summarize_pairs,
)

MAX_INPUT_BYTES = 16 * 1024 * 1024


def _load(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("input must be a regular non-symlink JSON file under 16 MiB")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("benchmark input must be a JSON object")
    return value


def _run(raw: dict) -> BenchmarkRun:
    value = dict(raw)
    value["evidence_refs"] = tuple(value.get("evidence_refs", ()))
    return BenchmarkRun(**value)


def build_report(source: dict) -> dict:
    if set(source) != {"manifest", "pairs"} or not isinstance(source["pairs"], list):
        raise ValueError("input must contain exactly manifest and pairs")
    if not isinstance(source["manifest"], dict):
        raise ValueError("manifest must be an object")
    try:
        manifest = BenchmarkManifest(**source["manifest"])
    except (TypeError, ValueError) as exc:
        raise ValueError("manifest does not satisfy the benchmark contract") from exc
    results = []
    for item in source["pairs"]:
        if not isinstance(item, dict) or set(item) != {"baseline", "candidate"}:
            raise ValueError("each pair must contain baseline and candidate run receipts")
        if not isinstance(item["baseline"], dict) or not isinstance(item["candidate"], dict):
            raise ValueError("baseline and candidate receipts must be objects")
        try:
            results.append(compare_pair(
                manifest,
                baseline=_run(item["baseline"]),
                candidate=_run(item["candidate"]),
            ))
        except (TypeError, ValueError) as exc:
            raise ValueError("pair contains an invalid run receipt") from exc
    return summarize_pairs(manifest, results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and summarize matched AUREN/direct benchmark receipts.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = build_report(_load(args.input))
        output = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps({
            "status": "reported", "output": str(args.output.resolve()),
            "sample_size": report["sample_size"],
            "promotion_eligible_pairs": report["promotion_eligible_pairs"],
            "manifest_digest": report["manifest_digest"],
        }, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(f"benchmark report blocked: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
