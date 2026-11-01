"""Machine-verifiable evidence helper for the operator-run #534 rehearsal.

This tool never changes the legacy source. It does not start/stop AUREN or switch
runtime configuration; those actions remain explicit operator steps.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from portable.state_migration import inspect_state_migration, migrate_state_root


def _inspect(root: Path) -> dict[str, Any]:
    probe = root.parent / f".{root.name}.manifest-probe"
    result = inspect_state_migration(root, probe)
    return {
        "path": str(result.source),
        "manifest_sha256": result.manifest_sha256,
        "entry_count": result.file_count,
        "byte_count": result.byte_count,
    }


def _sqlite_integrity(root: Path) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
            continue
        uri = path.resolve().as_uri() + "?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True, timeout=5) as db:
                rows = [str(row[0]) for row in db.execute("PRAGMA integrity_check")]
            outcome = "ok" if rows == ["ok"] else "; ".join(rows)
        except sqlite3.Error as exc:
            outcome = f"ERROR: {type(exc).__name__}: {exc}"
        results.append({"path": str(path), "integrity": outcome})
    return results


def run(args: argparse.Namespace) -> dict[str, Any]:
    source = args.source.expanduser().resolve()
    destination = args.destination.expanduser().resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("source and destination must be distinct, non-nested roots")

    record: dict[str, Any] = {
        "schema_version": "auren.state-migration-rehearsal.v1",
        "phase": args.phase,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "operator": args.operator,
        "candidate_revision": args.candidate_revision,
        "last_known_good_revision": args.last_known_good_revision,
        "source": str(source),
        "destination": str(destination),
    }
    baseline = _inspect(source)
    record["source_baseline"] = baseline

    if args.phase == "inspect":
        if destination.exists():
            raise FileExistsError(f"destination already exists: {destination}")
        plan = migrate_state_root(source, destination, dry_run=True)
        record["status"] = plan.status
        record["destination_absent"] = not destination.exists()
        record["ready"] = plan.status == "dry_run" and not destination.exists()
    elif args.phase == "apply":
        if destination.exists():
            raise FileExistsError(f"apply requires a new destination: {destination}")
        result = migrate_state_root(source, destination, dry_run=False)
        destination_manifest = _inspect(destination)
        source_after = _inspect(source)
        record.update({
            "status": result.status,
            "source_after": source_after,
            "destination_after": destination_manifest,
            "source_retained": source.is_dir(),
            "manifests_match": source_after["manifest_sha256"] == destination_manifest["manifest_sha256"],
            "counts_match": (
                source_after["entry_count"] == destination_manifest["entry_count"]
                and source_after["byte_count"] == destination_manifest["byte_count"]
            ),
        })
        record["ready"] = (
            result.status in {"copied_verified_source_retained", "already_migrated_verified"}
            and record["source_retained"] and record["manifests_match"] and record["counts_match"]
        )
    elif args.phase == "verify":
        if not destination.is_dir():
            raise FileNotFoundError(f"migrated destination does not exist: {destination}")
        destination_manifest = _inspect(destination)
        integrity = _sqlite_integrity(destination)
        record.update({
            "source_after": _inspect(source),
            "destination_after": destination_manifest,
            "sqlite_integrity": integrity,
            "manifests_match": baseline["manifest_sha256"] == destination_manifest["manifest_sha256"],
            "counts_match": (
                baseline["entry_count"] == destination_manifest["entry_count"]
                and baseline["byte_count"] == destination_manifest["byte_count"]
            ),
            "sqlite_integrity_passed": all(item["integrity"] == "ok" for item in integrity),
        })
        record["ready"] = (
            record["manifests_match"] and record["counts_match"]
            and record["sqlite_integrity_passed"]
        )
        record["operator_runtime_restart_verified"] = False
        record["note"] = "Record fresh-process session, graph-checkpoint and evidence-ledger resume separately."
    elif args.phase == "rollback-check":
        if not destination.is_dir():
            raise FileNotFoundError(f"migrated destination does not exist: {destination}")
        source_after = _inspect(source)
        record.update({
            "source_after": source_after,
            "destination_retained": destination.is_dir(),
            "source_matches_baseline": source_after["manifest_sha256"] == args.expected_manifest,
            "destination_after": _inspect(destination),
            "runtime_configuration_rollback_verified": False,
            "note": "Operator must restore last-known-good configuration, start a fresh process, and reconcile post-cutover writes.",
        })
        record["ready"] = record["source_matches_baseline"] and record["destination_retained"]
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("inspect", "apply", "verify", "rollback-check"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--candidate-revision", required=True)
    parser.add_argument("--last-known-good-revision", required=True)
    parser.add_argument("--expected-manifest", default="")
    args = parser.parse_args(argv)
    if args.phase == "rollback-check" and not args.expected_manifest:
        parser.error("rollback-check requires --expected-manifest from the initial inspect record")
    try:
        record = run(args)
    except Exception as exc:
        print(json.dumps({
            "schema_version": "auren.state-migration-rehearsal.v1",
            "phase": args.phase,
            "ready": False,
            "error": f"{type(exc).__name__}: {exc}",
        }, indent=2, sort_keys=True))
        return 2
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0 if record.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
