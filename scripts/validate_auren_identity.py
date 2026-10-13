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
    # These suites intentionally exercise documented legacy inputs or adversarial fixtures.
    "tests/test_context_broker.py",
    "tests/test_extension_registry.py",
    "tests/test_user_experience.py",
    "tests/test_agent_capabilities.py",
    "tests/test_provider_conformance.py",
    "tests/test_engineering_evolution.py",
    "tests/test_adaptive_runtime_recovery_integration.py",
    "tests/portable/test_adaptive_learning.py",
    "tests/test_graph_agent_evidence_decision.py",
    "tests/test_state_migration.py",
}
LEGACY_ALLOWED_LINE_PATTERNS = {
    "portable/orchestration.py": (
        re.compile(r'\bstate\.get\("auren_adaptive_policy", state\.get\("aer_adaptive_policy"\)\)'),
    ),
    "portable/engineering_dashboard.py": (
        re.compile(r'".aer"'),
    ),
    # The active graph-team runtime may read these two old checkpoint keys as
    # compatibility fallbacks, but no other stale identity is allowed there.
    ".ai-harness/runtime/graph_agent_team.py": (
        re.compile(r'state\.get\("auren_execution_strategy",\s*state\.get\("aer_execution_strategy",\{\}\)\)'),
        re.compile(r'state\.get\("auren_execution_mode",\s*state\.get\("aer_execution_mode",\{\}\)\)'),
    ),
    # These exact strings are compatibility fixtures proving that legacy
    # checkpoint keys are migrated and removed; do not allow other old names.
    "tests/test_graph_agent_team.py": (
        re.compile(r'"aer_execution_strategy":\s*\{"name":\s*"default"\}'),
        re.compile(r'"aer_execution_mode":\s*\{"name":\s*"balanced"\}'),
        re.compile(r'self\.assertNotIn\("aer_execution_(?:strategy|mode)",\s*saved\.state\)'),
    ),
    # Canonical markers are emitted alongside these legacy protocol aliases
    # only while downstream consumers still require them.
    ".ai-harness/runtime/sandbox.py": (
        re.compile(r'env\["AER_SANDBOX(?:_NETWORK)?"]'),
    ),
    ".ai-harness/safe_provider.py": (
        re.compile(r'AER_LOCAL_LLM_FALLBACK'),
        re.compile(r'"AER_(?:SANDBOX|COMPACTION_DIGEST)"'),
    ),
    ".ai-harness/runtime/lsp_server.py": (
        re.compile(r'".aer"'),
    ),
    ".ai-harness/CORE_RUNTIME_SERVICES.md": (
        re.compile(r'AER_SANDBOX_NETWORK|AER_COMPACTION_DIGEST'),
    ),
    ".ai-harness/BEHAVIORAL_CONFORMANCE.md": (
        re.compile(r'AER_CONTEXT_BROKER_TELEMETRY'),
    ),
    "scripts/provider_conformance.py": (
        re.compile(r'AER_CONFORMANCE_(?:OK|PROBE)'),
    ),
    "scripts/behavioral_conformance_ground_truth.py": (
        re.compile(r'AER_(?:CONFORMANCE_BEHAVIORAL|CONFORMANCE_TASK|CONFORMANCE_TRACE|CONTEXT_BROKER_TELEMETRY)'),
    ),
    "scripts/behavioral_conformance.py": (
        re.compile(r'AER_(?:CONFORMANCE_BEHAVIORAL|CONFORMANCE_TASK|CONFORMANCE_TRACE)'),
    ),
    "scripts/benchmark_oracles.py": (
        re.compile(r'AER_BC07_API_KEY'),
    ),
    "README.md": (
        re.compile(r'(?i)(?:legacy.*\.aer|~/.aer)'),
    ),
    "docs/USAGE_AND_PLATFORM_INTEGRATION.md": (
        re.compile(r'(?i)(?:legacy.*\.aer|~/.aer)'),
    ),
    "docs/REPOSITORY_CONSISTENCY_AND_ENHANCEMENT_PLAN.md": (
        re.compile(r'AER_\*|\.aer'),
    ),
}
LEGACY_ALLOWED_CODE_FILES = {
    "portable/identity_compat.py",
    "scripts/conformance_oracles.py",
    "scripts/validate_auren_identity.py",
}


def _is_approved_allowlist_path(path: str) -> bool:
    """Prevent the legacy allowlist from expanding into active runtime code."""
    return (
        path.startswith("tests/")
        or path.startswith("docs/LEGACY_")
        or path in LEGACY_ALLOWED_CODE_FILES
    )


PATTERNS = (
    re.compile(r"\bAER\b"),
    re.compile(r"\bAER_[A-Z][A-Z0-9_]*\b"),
    re.compile(r"(?i)\.aer(?:/|\\|\b)"),
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
    for allowed_path in sorted(LEGACY_ALLOWED):
        if not _is_approved_allowlist_path(allowed_path):
            findings.append(f"{allowed_path}: unsafe broad legacy allowlist entry")
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
