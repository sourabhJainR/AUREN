#!/usr/bin/env python3
"""Reject stale AER product identity outside explicit compatibility boundaries.

Legacy AER environment names and .aer state roots remain supported only where
migration code/tests document that compatibility. They must not become active
product names, entry points, package names, or current state keys.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_ALLOWED = {
    "docs/LEGACY_NAME_COMPATIBILITY.md",
    "docs/LEGACY_LINEAGE_CONFORMANCE.md",
    "portable/identity_compat.py",
    "scripts/conformance_oracles.py",
    "scripts/validate_auren_identity.py",
    "tests/test_contract_alignment.py",
    "tests/test_state_paths.py",
    "tests/test_identity_compat.py",
    "tests/test_legacy_name_compatibility.py",
}
LEGACY_ALLOWED_LINE_PATTERNS = {
    "portable/orchestration.py": (
        re.compile(r'\bstate\.get\("auren_adaptive_policy", state\.get\("aer_adaptive_policy"\)\)'),
    ),
    # The active graph-team runtime may read these two old checkpoint keys as
    # compatibility fallbacks, but no other stale identity is allowed there.
    ".ai-harness/runtime/graph_agent_team.py": (
        re.compile(r'state\.get\("auren_execution_strategy",\s*state\.get\("aer_execution_strategy",\{\}\)\)'),
        re.compile(r'state\.get\("auren_execution_mode",\s*state\.get\("aer_execution_mode",\{\}\)\)'),
    ),
}
PATTERNS = (
    re.compile(r"\bAER\b"),
    re.compile(r"\baer_cli\.py\b"),
    re.compile(r"\baer-portable\b"),
    re.compile(r"\baer_[a-z][A-Za-z0-9_]*\b"),
)
OLD_FILENAME = re.compile(r"(^|/)aer[_-][^/]+$", re.IGNORECASE)


def main() -> int:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout.split(b"\0")
    findings: list[str] = []
    for raw_path in tracked:
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", errors="strict")
        if OLD_FILENAME.search(path):
            findings.append(f"{path}: stale AER filename")
        if path in LEGACY_ALLOWED:
            continue
        full_path = ROOT / path
        try:
            content = full_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_no, line in enumerate(content.splitlines(), 1):
            if any(pattern.search(line) for pattern in LEGACY_ALLOWED_LINE_PATTERNS.get(path, ())):
                continue
            for pattern in PATTERNS:
                if pattern.search(line):
                    findings.append(f"{path}:{line_no}: stale AER identity: {line.strip()[:180]}")
                    break
    if findings:
        print("AUREN identity validation failed:")
        print("\n".join(findings))
        return 1
    print("AUREN identity validation passed; legacy compatibility is confined to the allowlist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
