#!/usr/bin/env python3
"""Operator-facing, non-destructive migration for legacy AUREN state roots.

Dry-run is the default. A write operation requires the explicit --apply flag.
The legacy source is retained as rollback material; destinations are never
overwritten unless the existing content already matches the source manifest.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from portable.state_migration import migrate_state_root


def _payload(result) -> dict[str, object]:
    return {
        "schema_version": "auren-state-migration-cli-v1",
        "source": str(result.source),
        "destination": str(result.destination),
        "status": result.status,
        "manifest_sha256": result.manifest_sha256,
        "file_count": result.file_count,
        "byte_count": result.byte_count,
        "source_retained_for_rollback": True,
        "destination_overwrite_allowed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect or safely copy a legacy AUREN state root. Dry-run is the default."
    )
    parser.add_argument("--source", required=True, help="Existing legacy state directory")
    parser.add_argument("--destination", required=True, help="New canonical state directory")
    parser.add_argument(
        "--apply", action="store_true",
        help="Perform the verified copy (without deleting or modifying the source)",
    )
    args = parser.parse_args(argv)
    try:
        result = migrate_state_root(args.source, args.destination, dry_run=not args.apply)
        payload = _payload(result)
        if not args.apply and result.status == "dry_run":
            payload["next_action"] = "rerun with --apply only after reviewing the source, destination, and manifest"
        print(json.dumps(payload, sort_keys=True, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({
            "schema_version": "auren-state-migration-cli-v1",
            "status": "blocked",
            "error_type": type(exc).__name__,
            "reason": str(exc),
            "source_retained_for_rollback": True,
            "destination_overwrite_allowed": False,
        }, sort_keys=True, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
