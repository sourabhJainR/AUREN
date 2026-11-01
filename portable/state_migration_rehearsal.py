"""Operator evidence capture for non-destructive state migration rehearsals.

This helper proves file-copy facts only. It deliberately cannot attest runtime
resume, production cutover, rollback execution, or post-cutover write reconciliation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from .state_migration import inspect_state_migration, migrate_state_root

_REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _sqlite_integrity(root: Path) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    candidates = sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
    )
    for path in candidates:
        try:
            uri = path.resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=2) as db:
                rows = [str(row[0]) for row in db.execute("PRAGMA integrity_check").fetchall()]
            reports.append({
                "path": path.relative_to(root).as_posix(),
                "status": "passed" if rows == ["ok"] else "failed",
                "integrity_check": rows,
            })
        except Exception as exc:
            reports.append({
                "path": path.relative_to(root).as_posix(),
                "status": "failed",
                "error_type": type(exc).__name__,
            })
    return reports


def capture_migration_rehearsal(
    *,
    source: str | Path,
    destination: str | Path,
    operator_id: str,
    candidate_revision: str,
    last_known_good_revision: str,
    mode: str = "dry-run",
) -> dict[str, Any]:
    """Inspect or copy a staging state root and emit bounded file-level evidence."""
    if not isinstance(operator_id, str) or not operator_id.strip() or len(operator_id) > 200:
        raise ValueError("operator_id must be a non-empty bounded identifier")
    for name, value in (
        ("candidate_revision", candidate_revision),
        ("last_known_good_revision", last_known_good_revision),
    ):
        if not isinstance(value, str) or not _REVISION.fullmatch(value):
            raise ValueError(f"{name} must be a full lowercase Git revision")
    if mode not in {"dry-run", "apply"}:
        raise ValueError("mode must be dry-run or apply")

    src, dst = Path(source).expanduser().resolve(), Path(destination).expanduser().resolve()
    before = inspect_state_migration(src, dst)
    result = migrate_state_root(src, dst, dry_run=(mode == "dry-run"))
    after_source = inspect_state_migration(src, dst)
    source_unchanged = (
        before.manifest_sha256 == after_source.manifest_sha256
        and before.file_count == after_source.file_count
        and before.byte_count == after_source.byte_count
    )
    destination_verified = False
    sqlite_reports: list[dict[str, Any]] = []
    if mode == "apply":
        # A dry-run against an existing destination only succeeds when its
        # manifest exactly matches the retained source manifest.
        verified = migrate_state_root(src, dst, dry_run=True)
        destination_verified = (
            verified.status == "already_migrated_verified"
            and verified.manifest_sha256 == before.manifest_sha256
            and verified.file_count == before.file_count
            and verified.byte_count == before.byte_count
        )
        sqlite_reports = _sqlite_integrity(dst)
    # A missing database is not proof of integrity for state roots expected to carry durable sessions/evidence.
    sqlite_ok = bool(sqlite_reports) and all(item["status"] == "passed" for item in sqlite_reports)
    file_copy_verified = (
        mode == "apply" and result.status in {"copied_verified_source_retained", "already_migrated_verified"}
        and source_unchanged and destination_verified and sqlite_ok
    )
    return {
        "schema_version": "auren-state-migration-rehearsal-v1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "operator_id": operator_id.strip(),
        "candidate_revision": candidate_revision,
        "last_known_good_revision": last_known_good_revision,
        "mode": mode,
        "source_root": str(src),
        "destination_root": str(dst),
        "source_manifest_sha256": before.manifest_sha256,
        "source_entry_count": before.file_count,
        "source_byte_count": before.byte_count,
        "destination_manifest_sha256": result.manifest_sha256 if destination_verified else None,
        "source_unchanged": source_unchanged,
        "destination_manifest_verified": destination_verified,
        "sqlite_integrity": sqlite_reports,
        "sqlite_integrity_passed": sqlite_ok,
        "file_copy_verified": file_copy_verified,
        "runtime_resume_verified": False,
        "rollback_rehearsal_verified": False,
        "post_cutover_write_reconciliation_verified": False,
        "external_evidence_required": [
            "fresh-process runtime resume and evidence-ledger readability",
            "operator-executed rollback to last-known-good state root",
            "post-cutover write reconciliation and no-loss confirmation",
        ],
        "status": (
            "file_copy_verified_operator_runtime_checks_required"
            if file_copy_verified
            else "dry_run_only" if mode == "dry-run" and source_unchanged
            else "blocked_or_incomplete"
        ),
        "completion_claim_permitted": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--operator-id", required=True)
    parser.add_argument("--candidate-revision", required=True)
    parser.add_argument("--last-known-good-revision", required=True)
    parser.add_argument("--mode", choices=("dry-run", "apply"), default="dry-run")
    parser.add_argument("--report", required=True, help="Path for JSON evidence report")
    args = parser.parse_args(argv)
    report = capture_migration_rehearsal(
        source=args.source, destination=args.destination, operator_id=args.operator_id,
        candidate_revision=args.candidate_revision,
        last_known_good_revision=args.last_known_good_revision, mode=args.mode,
    )
    output = Path(args.report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "file_copy_verified": report["file_copy_verified"],
        "completion_claim_permitted": report["completion_claim_permitted"],
        "report": str(output),
    }, sort_keys=True))
    return 0 if report["status"] in {"dry_run_only", "file_copy_verified_operator_runtime_checks_required"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
