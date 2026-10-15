#!/usr/bin/env python3
"""Reject stale AER product identity outside explicit compatibility boundaries.

Legacy AER environment names and .aer state roots remain supported only where
migration code/tests document that compatibility. They must not become active
product names, entry points, package names, or current state keys.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import tokenize
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
    "tests/test_identity_audit_patterns.py",
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
    "tests/test_state_migration_plan.py",
}
LEGACY_ALLOWED_RATIONALE = {
    "docs/LEGACY_NAME_COMPATIBILITY.md": "Normative migration boundary: documents exactly which historical names remain accepted and where they may be used.",
    "docs/LEGACY_LINEAGE_CONFORMANCE.md": "Historical conformance fixtures intentionally retain old lineage tokens to verify migration behavior.",
    "portable/identity_compat.py": "The sole bounded compatibility adapter must name and translate legacy environment/state identifiers.",
    "scripts/conformance_oracles.py": "Conformance oracle fixtures contain historical identifiers as test inputs, not active product names.",
    "scripts/validate_auren_identity.py": "The audit must contain the patterns and allowlist entries it validates.",
    "tests/test_contract_alignment.py": "Adversarial contract tests include legacy strings to prove stale-name detection and rejection.",
    "tests/test_state_paths.py": "Migration tests construct old state roots and assert safe, non-destructive compatibility behavior.",
    "tests/test_identity_compat.py": "Compatibility tests exercise old environment variables, persisted keys, conflicts, and canonicalization.",
    "tests/test_legacy_name_compatibility.py": "Dedicated regression fixtures verify legacy-name compatibility remains bounded.",
    "tests/test_identity_audit_patterns.py": "Adversarial mixed-case fixtures prove identifier casing cannot bypass the audit.",
    "tests/test_context_broker.py": "Legacy telemetry names appear only in compatibility regression fixtures.",
    "tests/test_extension_registry.py": "Legacy extension aliases are retained only to test registry compatibility and rejection behavior.",
    "tests/test_user_experience.py": "Legacy user-facing names are fixture inputs for compatibility regression coverage.",
    "tests/test_agent_capabilities.py": "Legacy capability identifiers are exercised as historical inputs, not current registration names.",
    "tests/test_provider_conformance.py": "Provider conformance tests retain historical environment/result tokens as fixtures.",
    "tests/test_engineering_evolution.py": "Evolution tests include old naming forms to ensure migration and rollback remain deterministic.",
    "tests/test_adaptive_runtime_recovery_integration.py": "Recovery fixtures exercise loading and resuming historical persisted state.",
    "tests/portable/test_adaptive_learning.py": "Learning compatibility fixtures preserve historical state labels for regression coverage.",
    "tests/test_graph_agent_evidence_decision.py": "Evidence-decision tests use legacy identifiers to prove fail-closed migration behavior.",
    "tests/test_state_migration.py": "Dedicated migration tests intentionally include legacy state files and keys.",
    "tests/test_state_migration_plan.py": "Read-only migration preflight fixtures use the legacy .aer root to verify that planned copies and conflicts are reported without mutation."
}
LEGACY_ALLOWED_LINE_RATIONALE = {
    "portable/engineering_dashboard.py": "Only the documented legacy state-root marker is retained for dashboard compatibility; new state writes use the canonical root.",
    ".ai-harness/runtime/graph_agent_team.py": "Only exact historical checkpoint-key fixtures are allowed; all runtime reads must pass through the canonical compatibility adapter.",
    "tests/test_graph_agent_team.py": "Exact legacy checkpoint fixtures prove canonicalization removes old keys before persisted state is accepted.",
    ".ai-harness/runtime/sandbox.py": "AER_SANDBOX aliases are accepted only as explicit environment-variable compatibility inputs.",
    ".ai-harness/safe_provider.py": "Historical local-provider fallback and sandbox environment aliases remain compatibility inputs, not canonical settings.",
    ".ai-harness/runtime/lsp_server.py": "The old state-root spelling is retained only for locating an existing legacy workspace.",
    ".ai-harness/CORE_RUNTIME_SERVICES.md": "This documentation names legacy environment aliases to explain their retirement boundary.",
    ".ai-harness/BEHAVIORAL_CONFORMANCE.md": "This conformance document records historical telemetry naming for compatibility testing.",
    "scripts/provider_conformance.py": "Legacy conformance output names are protocol fixtures required to verify older providers.",
    "scripts/behavioral_conformance_ground_truth.py": "Historical conformance labels are ground-truth fixture values, not active runtime configuration.",
    "scripts/behavioral_conformance.py": "Historical conformance labels are emitted only for backward-compatible test fixtures.",
    "scripts/benchmark_oracles.py": "The old benchmark API key name is an adversarial fixture for secret/config compatibility tests.",
    "README.md": "The legacy state-root spelling appears only in migration guidance for existing installations.",
    "docs/USAGE_AND_PLATFORM_INTEGRATION.md": "The legacy state-root spelling appears only in upgrade instructions for existing installations.",
    "docs/REPOSITORY_CONSISTENCY_AND_ENHANCEMENT_PLAN.md": "The old wildcard/name is discussed as a migration audit target, not a supported current product identifier."
}

LEGACY_ALLOWED_LINE_PATTERNS = {
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
    # Configuration and internal identifiers must be detected regardless of
    # casing; case variants are not a way to bypass the compatibility registry.
    re.compile(r"\bAER_[A-Z][A-Z0-9_]*\b", re.IGNORECASE),
    re.compile(r"(?i)\.aer(?:/|\\|\b)"),
    re.compile(r"\baer_cli\.py\b", re.IGNORECASE),
    re.compile(r"\baer-portable\b", re.IGNORECASE),
    re.compile(r"\baer_[a-z][A-Za-z0-9_]*\b", re.IGNORECASE),
)
OLD_FILENAME = re.compile(r"(^|/)aer[_-][^/]+$", re.IGNORECASE)
# Match the legacy token as a complete identifier segment, not words such as Arena.
LEGACY_SYMBOL_TOKEN = re.compile(r"(?i)(?:^|_)aer(?:_|$)")
LEGACY_PATH_TOKEN = re.compile(r"(?i)(?:^|/)\.?aer(?:/|$)|(?:^|/)aer[_-][^/]+$")


def find_legacy_python_symbols(content: str) -> list[tuple[int, str]]:
    """Return legacy-branded Python NAME tokens, excluding strings and comments."""
    found: list[tuple[int, str]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(content).readline)
        for token in tokens:
            if token.type == tokenize.NAME and LEGACY_SYMBOL_TOKEN.search(token.string):
                found.append((token.start[0], token.string))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # The compiler/test suite reports syntax failures separately. Do not let
        # malformed source suppress the existing line-based identity audit.
        pass
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit active AUREN code and inventory approved legacy compatibility.")
    parser.add_argument(
        "--report-json",
        type=Path,
        help="Write a complete match inventory to a JSON file outside the repository (for operator review).",
    )
    args = parser.parse_args(argv)
    report_path: Path | None = None
    if args.report_json is not None:
        report_path = args.report_json.expanduser().resolve()
        if report_path == ROOT.resolve() or ROOT.resolve() in report_path.parents:
            print("AUREN identity report must be written outside the repository to avoid becoming a tracked legacy-name exception.")
            return 2

    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout.split(b"\0")
    findings: list[str] = []
    unsafe_allowlist = []
    for allowed_path in sorted(LEGACY_ALLOWED):
        if not _is_approved_allowlist_path(allowed_path):
            finding = f"{allowed_path}: unsafe broad legacy allowlist entry"
            findings.append(finding)
            unsafe_allowlist.append(finding)

    for allowed_path in sorted(set(LEGACY_ALLOWED) ^ set(LEGACY_ALLOWED_RATIONALE)):
        findings.append(f"{allowed_path}: legacy file allowlist rationale is missing or stale")
    for allowed_path in sorted(set(LEGACY_ALLOWED_LINE_PATTERNS) ^ set(LEGACY_ALLOWED_LINE_RATIONALE)):
        findings.append(f"{allowed_path}: legacy line allowlist rationale is missing or stale")
    for allowed_path, reason in LEGACY_ALLOWED_RATIONALE.items():
        if not isinstance(reason, str) or not reason.strip():
            findings.append(f"{allowed_path}: legacy file allowlist rationale must be non-empty")
    for allowed_path, reason in LEGACY_ALLOWED_LINE_RATIONALE.items():
        if not isinstance(reason, str) or not reason.strip():
            findings.append(f"{allowed_path}: legacy line allowlist rationale must be non-empty")

    inventory: list[dict[str, object]] = []
    for raw_path in tracked:
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", errors="strict")
        if OLD_FILENAME.search(path) or LEGACY_PATH_TOKEN.search(path):
            findings.append(f"{path}: stale AER file or folder name")
        full_path = ROOT / path
        try:
            content = full_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if path.endswith(".py") and not _is_approved_allowlist_path(path):
            for line_no, symbol in find_legacy_python_symbols(content):
                findings.append(f"{path}:{line_no}: stale AER Python identifier: {symbol}")
        line_patterns = LEGACY_ALLOWED_LINE_PATTERNS.get(path, ())
        file_allowed = path in LEGACY_ALLOWED
        for line_no, line in enumerate(content.splitlines(), 1):
            matched = [pattern.pattern for pattern in PATTERNS if pattern.search(line)]
            if not matched:
                continue
            line_allowed = any(pattern.search(line) for pattern in line_patterns)
            if line_allowed:
                disposition = "line_allowlisted"
            elif file_allowed:
                disposition = "file_allowlisted"
            else:
                disposition = "unapproved"
            inventory.append({
                "path": path,
                "line": line_no,
                "matched_patterns": matched,
                "disposition": disposition,
                "line_excerpt": line.strip()[:180],
            })
            if disposition == "unapproved":
                findings.append(f"{path}:{line_no}: stale AER identity: {line.strip()[:180]}")

    if report_path is not None:
        allowlisted = [item for item in inventory if item["disposition"] != "unapproved"]
        unapproved = [item for item in inventory if item["disposition"] == "unapproved"]
        report = {
            "schema_version": "auren-identity-audit-report-v1",
            "repository": str(ROOT.resolve()),
            "tracked_file_count": len([item for item in tracked if item]),
            "legacy_match_count": len(inventory),
            "allowlisted_match_count": len(allowlisted),
            "unapproved_match_count": len(unapproved),
            "audit_passed": not findings,
            "allowlist_entries": sorted(LEGACY_ALLOWED),
            "allowlist_rationale": LEGACY_ALLOWED_RATIONALE,
            "line_allowlist_rationale": LEGACY_ALLOWED_LINE_RATIONALE,
            "unsafe_allowlist_entries": unsafe_allowlist,
            "match_inventory": inventory,
            "findings": findings,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if findings:
        print("AUREN identity validation failed:")
        print("\n".join(findings))
        if report_path is not None:
            print(f"Full match inventory written to {report_path}")
        return 1
    if report_path is not None:
        print(
            f"AUREN identity validation passed; inventoried {len(inventory)} legacy matches "
            f"({sum(1 for item in inventory if item['disposition'] != 'unapproved')} allowlisted). "
            f"Report: {report_path}"
        )
    else:
        print("AUREN identity validation passed; legacy compatibility is confined to the allowlist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
