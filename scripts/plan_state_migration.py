"""CLI for read-only preflight of legacy AUREN state migration."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portable.state_migration_plan import plan_state_migration
from portable.state_paths import StatePathConflict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect a legacy .aer to canonical .auren migration without changing either root."
    )
    parser.add_argument("--canonical-root", required=True, type=Path, help="Target .auren state root")
    parser.add_argument("--legacy-root", required=True, type=Path, help="Existing legacy .aer state root")
    args = parser.parse_args(argv)
    try:
        plan = plan_state_migration(args.canonical_root, args.legacy_root)
    except (OSError, ValueError, StatePathConflict) as exc:
        print(json.dumps({"safe_to_apply": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(plan, sort_keys=True, indent=2))
    return 0 if plan["safe_to_apply"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
