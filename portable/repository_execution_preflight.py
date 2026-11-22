"""Repository, tool, capability and acceptance preflight contracts.

These are deterministic preconditions for the existing AUREN execution path.
They do not replace host authorization, branch protection, sandboxing or CI.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import fnmatch
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

_SHA = re.compile(r"^[0-9a-f]{40}$")
_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _safe_relative_path(value: str, field: str) -> str:
    value = _text(value, field).replace("\\", "/")
    parts = value.split("/")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value) or any(p in {"", ".", ".."} for p in parts):
        raise ValueError(f"{field} must be a normalized repository-relative path")
    return value


@dataclass(frozen=True, slots=True)
class RepositoryTarget:
    """Explicit separation of implementation, governance and evidence repositories."""
    repository: str
    branch: str
    base_sha: str
    allowed_paths: tuple[str, ...]
    governance_repository: str = ""
    evidence_repository: str = ""
    allow_governance_repository_changes: bool = False

    def __post_init__(self) -> None:
        repo = _text(self.repository, "repository")
        if not _SLUG.fullmatch(repo) or any(part in {".", ".."} for part in repo.split("/")):
            raise ValueError("repository must be a canonical owner/repo slug")
        _text(self.branch, "branch")
        if not isinstance(self.base_sha, str) or not _SHA.fullmatch(self.base_sha):
            raise ValueError("base_sha must be a full lowercase 40-character commit SHA")
        if not self.allowed_paths:
            raise ValueError("at least one allowed path scope is required")
        for pattern in self.allowed_paths:
            _safe_relative_path(pattern, "allowed path")
        for name in ("governance_repository", "evidence_repository"):
            value = getattr(self, name)
            if value and not _SLUG.fullmatch(value):
                raise ValueError(f"{name} must be an owner/repo slug")
        if self.governance_repository and self.repository.casefold() == self.governance_repository.casefold() and not self.allow_governance_repository_changes:
            raise PermissionError("implementation target is the governance repository; explicit opt-in is required")
        if self.evidence_repository and self.evidence_repository.casefold() == self.repository.casefold():
            raise ValueError("evidence repository must be distinct from the implementation repository")
        if type(self.allow_governance_repository_changes) is not bool:
            raise ValueError("allow_governance_repository_changes must be boolean")

    def validate_observed_target(self, *, repository: str, branch: str, base_sha: str) -> None:
        observed = (_text(repository, "observed repository"), _text(branch, "observed branch"), base_sha)
        expected = (self.repository, self.branch, self.base_sha)
        if observed != expected:
            raise PermissionError("observed repository, branch or base SHA does not match the approved target")

    def authorize_path(self, path: str) -> str:
        normalized = _safe_relative_path(path, "write path")
        if not any(fnmatch.fnmatchcase(normalized, pattern) or normalized.startswith(pattern.rstrip("/") + "/") for pattern in self.allowed_paths):
            raise PermissionError(f"write path is outside the declared scope: {normalized}")
        return normalized


class ToolContractError(ValueError):
    """Tool arguments do not satisfy the discovered input contract."""


def validate_tool_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> None:
    """Validate the intentionally supported JSON-Schema subset before dispatch."""
    if not isinstance(schema, Mapping) or schema.get("type") != "object":
        raise ToolContractError("tool input schema must declare an object")
    if not isinstance(arguments, Mapping):
        raise ToolContractError("tool arguments must be an object")
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(properties, Mapping) or not isinstance(required, list) or any(not isinstance(x, str) for x in required):
        raise ToolContractError("tool schema properties/required are malformed")
    missing = [key for key in required if key not in arguments]
    if missing:
        raise ToolContractError("missing required tool arguments: " + ", ".join(sorted(missing)))
    if schema.get("additionalProperties", True) is False:
        extra = sorted(set(arguments) - set(properties))
        if extra:
            raise ToolContractError("unexpected tool arguments: " + ", ".join(extra))
    for key, value in arguments.items():
        rule = properties.get(key)
        if rule is None:
            continue
        if not isinstance(rule, Mapping):
            raise ToolContractError(f"invalid schema for argument: {key}")
        kind = rule.get("type")
        valid = {
            "string": lambda v: isinstance(v, str),
            "integer": lambda v: type(v) is int,
            "number": lambda v: type(v) in (int, float) and not isinstance(v, bool),
            "boolean": lambda v: type(v) is bool,
            "array": lambda v: isinstance(v, (list, tuple)),
            "object": lambda v: isinstance(v, Mapping),
            "null": lambda v: v is None,
        }.get(kind)
        if valid is None:
            raise ToolContractError(f"unsupported schema type for {key}: {kind}")
        if not valid(value):
            raise ToolContractError(f"argument {key} must be {kind}")
        if "enum" in rule and value not in rule["enum"]:
            raise ToolContractError(f"argument {key} is not an allowed enum value")
        if isinstance(value, str) and (len(value) < rule.get("minLength", 0) or len(value) > rule.get("maxLength", float("inf"))):
            raise ToolContractError(f"argument {key} violates string length constraints")
        if type(value) in (int, float) and (value < rule.get("minimum", float("-inf")) or value > rule.get("maximum", float("inf"))):
            raise ToolContractError(f"argument {key} violates numeric bounds")
        if isinstance(value, (list, tuple)) and (len(value) < rule.get("minItems", 0) or len(value) > rule.get("maxItems", float("inf"))):
            raise ToolContractError(f"argument {key} violates array length constraints")


@dataclass(frozen=True, slots=True)
class CapabilityCheck:
    name: str
    available: bool
    evidence: str
    checked_at: str
    version: str = ""

    def __post_init__(self) -> None:
        _text(self.name, "capability name")
        _text(self.evidence, "capability evidence")
        _text(self.checked_at, "capability checked_at")
        if type(self.available) is not bool:
            raise ValueError("capability available must be boolean")


def capability_blockers(checks: Sequence[CapabilityCheck], required: Sequence[str]) -> tuple[str, ...]:
    by_name = {item.name: item for item in checks}
    if len(by_name) != len(checks):
        raise ValueError("capability names must be unique")
    return tuple(f"{name}: unavailable or not evidenced" for name in sorted(set(required))
                 if name not in by_name or by_name[name].available is not True)


class AcceptanceStatus(str, Enum):
    NOT_STARTED = "not_started"
    WORKING = "working"
    BLOCKED = "blocked"
    FAILED = "failed"
    PASSED = "passed"


@dataclass(frozen=True, slots=True)
class AcceptanceRow:
    requirement_id: str
    description: str
    implementation: str
    positive_test: str
    negative_test: str
    integration_test: str
    status: AcceptanceStatus = AcceptanceStatus.NOT_STARTED
    evidence_uri: str = ""
    evidence_sha: str = ""
    residual_risk: str = ""

    def validate_for_head(self, expected_sha: str) -> None:
        _text(self.requirement_id, "requirement_id")
        _text(self.description, "description")
        if self.status == AcceptanceStatus.PASSED:
            for field in ("implementation", "positive_test", "negative_test", "integration_test", "evidence_uri"):
                _text(getattr(self, field), field)
            if not _SHA.fullmatch(self.evidence_sha) or self.evidence_sha != expected_sha:
                raise ValueError(f"acceptance row {self.requirement_id} has missing or stale exact-head evidence")


@dataclass(frozen=True, slots=True)
class AcceptanceMatrix:
    repository: str
    head_sha: str
    rows: tuple[AcceptanceRow, ...]

    def __post_init__(self) -> None:
        if not _SLUG.fullmatch(self.repository) or not _SHA.fullmatch(self.head_sha):
            raise ValueError("acceptance matrix requires canonical repository and full head SHA")
        ids = [row.requirement_id for row in self.rows]
        if not self.rows or len(ids) != len(set(ids)):
            raise ValueError("acceptance matrix must contain unique requirement rows")
        for row in self.rows:
            row.validate_for_head(self.head_sha)

    @property
    def status(self) -> str:
        states = {row.status for row in self.rows}
        if states == {AcceptanceStatus.PASSED}:
            return "passed"
        if AcceptanceStatus.FAILED in states:
            return "failed"
        if AcceptanceStatus.BLOCKED in states:
            return "blocked"
        return "incomplete"

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": "auren-acceptance-matrix-v1",
            "repository": self.repository, "head_sha": self.head_sha, "status": self.status,
            "rows": [{key: (getattr(row, key).value if isinstance(getattr(row, key), Enum) else getattr(row, key))
                      for key in row.__dataclass_fields__} for row in self.rows],
        }

    @property
    def digest(self) -> str:
        raw = json.dumps(self.payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        return hashlib.sha256(raw).hexdigest()

    def persist(self, ledger: Any, *, signer_id: str) -> dict[str, str]:
        """Store the matrix reference in the existing canonical evidence ledger."""
        if self.status != "passed":
            raise ValueError("only a fully passed acceptance matrix can be recorded as passed evidence")
        if not callable(getattr(ledger, "record", None)):
            raise TypeError("ledger must implement canonical record()")
        return ledger.record(
            evidence_id=f"acceptance-matrix:{self.repository}:{self.head_sha}",
            repository=self.repository, head_sha=self.head_sha, evidence_type="acceptance_matrix",
            status="passed", source_uri=f"urn:auren:acceptance-matrix:{self.digest}",
            payload_digest=self.digest, signer_id=_text(signer_id, "signer_id"),
        )


def exact_head_ci_gate(expected_sha: str, checks: Sequence[Mapping[str, Any]], *,
                       required_check_names: Sequence[str] = ()) -> dict[str, Any]:
    """Fail closed: stale, missing, skipped-required and pending checks never pass."""
    if not _SHA.fullmatch(expected_sha):
        raise ValueError("expected_sha must be a full lowercase commit SHA")
    results = []
    seen_names: set[str] = set()
    for check in checks:
        name = _text(check.get("name"), "check name")
        if name in seen_names:
            raise ValueError(f"duplicate CI check name: {name}")
        seen_names.add(name)
        sha, conclusion, required = check.get("head_sha"), check.get("conclusion"), check.get("required", True)
        if type(required) is not bool:
            raise ValueError("check required must be boolean")
        if sha != expected_sha:
            state = "stale"
        elif conclusion in {"success", "neutral"}:
            state = "passed"
        elif conclusion == "skipped" and not required:
            state = "skipped_not_required"
        elif conclusion in {"failure", "cancelled", "timed_out", "action_required"}:
            state = "failed"
        elif conclusion == "skipped":
            state = "skipped_required"
        else:
            state = "pending_or_unknown"
        results.append({"name": name, "state": state, "required": required, "head_sha": sha})
    expected_required = set(required_check_names)
    if any(not isinstance(name, str) or not name.strip() for name in expected_required):
        raise ValueError("required_check_names must contain non-empty names")
    expected_required.update(item["name"] for item in results if item["required"])
    missing = sorted(expected_required - seen_names)
    results.extend({"name": name, "state": "missing", "required": True, "head_sha": None} for name in missing)
    passed = bool(expected_required) and all(item["state"] == "passed" for item in results if item["required"])
    return {"expected_sha": expected_sha, "passed": passed, "missing_required": missing, "checks": results}
