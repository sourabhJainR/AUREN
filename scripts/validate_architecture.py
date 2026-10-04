#!/usr/bin/env python3
"""Validate AUREN architecture contracts without third-party dependencies."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "architecture" / "architecture.yaml"
COMPATIBILITY = ROOT / "architecture" / "compatibility.yaml"
EXECUTION_SCHEMA = ROOT / "state" / "execution-envelope.schema.json"
REQUIRED_PRINCIPLES = {"one_owner_per_concern", "portable_is_dependency_free", "learning_cannot_authorize", "release_requires_verification", "stale_evidence_requires_refresh", "adapters_and_views_are_not_authority", "execution_state_is_canonical_and_serializable"}
ALLOWED_STATUSES = {"canonical", "adapter", "strategy", "view", "deprecated", "historical"}
FORBIDDEN_PORTABLE_IMPORT_PREFIXES = ("ai_harness", "skills", ".agents", ".claude", "architecture")


def load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise AssertionError(f"{label} missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AssertionError(f"{label} must be JSON-compatible: {exc}") from exc
    if not isinstance(value, dict):
        raise AssertionError(f"{label} root must be an object")
    return value


def _require(mapping: dict[str, Any], key: str, label: str) -> Any:
    if key not in mapping:
        raise AssertionError(f"{label} is missing required key: {key}")
    return mapping[key]


def validate_execution_schema() -> None:
    schema = load_json(EXECUTION_SCHEMA, "execution envelope schema")
    if schema.get("$id", "").endswith("/execution-envelope/v1") is False:
        raise AssertionError("execution envelope schema id is not versioned")
    if schema.get("properties", {}).get("schema_version", {}).get("const") != "1.0":
        raise AssertionError("execution envelope schema version drifted")
    required = set(schema.get("required", []))
    if not {"schema_version", "intent", "repository", "plan", "evidence"}.issubset(required):
        raise AssertionError("execution envelope schema lost canonical identity fields")


def validate_compatibility_registry() -> None:
    registry = load_json(COMPATIBILITY, "compatibility registry")
    rows = registry.get("surfaces")
    if not isinstance(rows, list) or not rows:
        raise AssertionError("compatibility registry must contain surfaces")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise AssertionError("compatibility registry entries must be objects")
        for key in ("name", "status", "canonical_owner", "replacement", "removal_condition"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise AssertionError(f"compatibility entry missing {key}: {row}")
        if row["status"] not in ALLOWED_STATUSES:
            raise AssertionError(f"unsupported compatibility status: {row['status']}")
        if row["name"] in seen:
            raise AssertionError(f"duplicate compatibility surface: {row['name']}")
        seen.add(row["name"])
        if row["status"] not in {"canonical", "historical"} and row["replacement"] == row["name"]:
            raise AssertionError(f"compatibility surface replaces itself: {row['name']}")


def validate_contract(contract: dict[str, Any]) -> None:
    if contract.get("contract_version") != 1:
        raise AssertionError("unsupported architecture contract version")
    architecture = _require(contract, "architecture", "architecture")
    if architecture.get("short_name") != "AUREN" or architecture.get("target_line") != "AUREN":
        raise AssertionError("architecture identity or target line drifted")
    principles = set(_require(contract, "principles", "architecture contract"))
    missing = REQUIRED_PRINCIPLES - principles
    if missing:
        raise AssertionError(f"missing architecture principles: {sorted(missing)}")
    domains = _require(contract, "domains", "architecture contract")
    owners: set[str] = set()
    for name, domain in domains.items():
        if not isinstance(domain, dict):
            raise AssertionError(f"domain {name} must be an object")
        owner = _require(domain, "canonical_owner", f"domain {name}")
        if not isinstance(owner, str) or not owner.strip() or owner in owners:
            raise AssertionError(f"invalid or duplicate canonical owner: {owner}")
        owners.add(owner)
        if owner in domain.get("compatibility_surfaces", []):
            raise AssertionError(f"domain {name} lists its owner as a compatibility surface")
        if not isinstance(domain.get("rule"), str) or not domain["rule"].strip():
            raise AssertionError(f"domain {name} must declare an ownership rule")
    lifecycle = _require(contract, "lifecycle", "architecture contract")
    expected = ["intent", "contract", "repo_facts", "decisions", "evidence", "plan", "execute", "verify", "review", "regression", "release", "outcome", "learn"]
    if lifecycle != expected:
        raise AssertionError("lifecycle ordering does not match the engineering spine")
    dependencies = _require(contract, "dependency_direction", "architecture contract")
    for key in ("portable", "skills", "harness", "adapters", "views"):
        if not isinstance(dependencies.get(key), list):
            raise AssertionError(f"dependency_direction.{key} must be an array")
    if any(item in dependencies["portable"] for item in ("skills", "harness", "views", "adapters")):
        raise AssertionError("portable may not depend on host or view surfaces")
    policy = _require(contract, "compatibility_policy", "architecture contract")
    if set(_require(policy, "statuses", "compatibility_policy")) != ALLOWED_STATUSES:
        raise AssertionError("compatibility status vocabulary drifted")
    if set(_require(policy, "required_metadata", "compatibility_policy")) != {"status", "canonical_owner", "replacement", "removal_condition"}:
        raise AssertionError("compatibility metadata contract drifted")
    enforcement = _require(contract, "enforcement", "architecture contract")
    for key, relative in {"validator":"scripts/validate_architecture.py", "tests":"tests/test_architecture_contract.py", "ci":".github/workflows/architecture-integrity.yml"}.items():
        if enforcement.get(key) != relative:
            raise AssertionError(f"enforcement.{key} must point to {relative}")


def _import_root(node: ast.AST) -> str | None:
    if isinstance(node, ast.Import):
        return node.names[0].name
    if isinstance(node, ast.ImportFrom):
        return node.module or ""
    return None


def validate_portable_import_direction() -> None:
    portable = ROOT / "portable"
    for path in sorted(portable.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            value = _import_root(node)
            if value and value.lstrip(".").startswith(FORBIDDEN_PORTABLE_IMPORT_PREFIXES):
                raise AssertionError(f"portable dependency direction violation in {path}: import {value!r}")


def validate() -> None:
    contract = load_json(CONTRACT, "architecture contract")
    validate_contract(contract)
    validate_compatibility_registry()
    validate_execution_schema()
    validate_portable_import_direction()


if __name__ == "__main__":
    validate()
    print(f"Architecture contracts valid: {CONTRACT.relative_to(ROOT)}")
