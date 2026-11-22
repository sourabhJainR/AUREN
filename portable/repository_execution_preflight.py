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
import math
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
        if (
            isinstance(self.allowed_paths, (str, bytes))
            or not isinstance(self.allowed_paths, Sequence)
            or not self.allowed_paths
        ):
            raise ValueError("allowed_paths must be a non-empty sequence of path patterns")
        normalized_patterns = tuple(
            _safe_relative_path(pattern, "allowed path")
            for pattern in self.allowed_paths
        )
        object.__setattr__(self, "allowed_paths", normalized_patterns)
        for name in ("governance_repository", "evidence_repository"):
            value = getattr(self, name)
            if value and (
                not _SLUG.fullmatch(value)
                or any(part in {".", ".."} for part in value.split("/"))
            ):
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
        if not any(
            _path_pattern_matches(normalized, pattern)
            or (
                not any(marker in pattern for marker in "*?[")
                and normalized.startswith(pattern.rstrip("/") + "/")
            )
            for pattern in self.allowed_paths
        ):
            raise PermissionError(f"write path is outside the declared scope: {normalized}")
        return normalized


def _path_pattern_matches(path: str, pattern: str) -> bool:
    """Match glob patterns by path segment; '*' never crosses a directory."""
    path_parts = path.split("/")
    pattern_parts = pattern.split("/")
    positions = {0}
    for pattern_part in pattern_parts:
        if pattern_part == "**":
            expanded = set(positions)
            for index in range(len(path_parts)):
                if index in expanded:
                    expanded.add(index + 1)
            positions = expanded
        else:
            positions = {
                index + 1
                for index in positions
                if index < len(path_parts)
                and fnmatch.fnmatchcase(path_parts[index], pattern_part)
            }
        if not positions:
            return False
    return len(path_parts) in positions


def repository_slug_from_remote(remote_url: str) -> str | None:
    """Extract a GitHub owner/repo slug from HTTPS, SSH or SCP-style remotes."""
    value = _text(remote_url, "remote URL")
    match = re.fullmatch(
        r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)([^/\s]+/[^/\s]+?)(?:\.git)?/?",
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    slug = match.group(1)
    if not _SLUG.fullmatch(slug) or any(part in {"", ".", ".."} for part in slug.split("/")):
        return None
    return slug


def guard_governance_repository(
    remote_url: str,
    *,
    governance_repository: str,
    allow_governance_repository_changes: bool = False,
) -> str | None:
    """Reject accidental product writes to the orchestration repository."""
    if type(allow_governance_repository_changes) is not bool:
        raise ValueError("allow_governance_repository_changes must be boolean")
    if (
        not _SLUG.fullmatch(governance_repository)
        or any(part in {".", ".."} for part in governance_repository.split("/"))
    ):
        raise ValueError("governance_repository must be an owner/repo slug")
    observed = repository_slug_from_remote(remote_url)
    if (
        observed
        and observed.casefold() == governance_repository.casefold()
        and not allow_governance_repository_changes
    ):
        raise PermissionError(
            "workspace writes to the AUREN governance repository require explicit opt-in; "
            "select the product repository or set AUREN_ALLOW_GOVERNANCE_REPOSITORY_CHANGES=1"
        )
    return observed


class ToolContractError(ValueError):
    """Tool arguments do not satisfy the discovered input contract."""


_SCHEMA_ALLOWED_KEYWORDS = {
    "type", "properties", "required", "additionalProperties", "items", "enum",
    "minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems",
    "minProperties", "maxProperties", "description", "title", "default",
    "examples", "deprecated", "readOnly", "writeOnly",
}


def _json_values_equal(left: Any, right: Any) -> bool:
    """Compare JSON values without Python's bool/int equality overlap."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return (
            left.keys() == right.keys()
            and all(_json_values_equal(left[key], right[key]) for key in left)
        )
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return (
            len(left) == len(right)
            and all(_json_values_equal(a, b) for a, b in zip(left, right))
        )
    return type(left) is type(right) and left == right


def _validate_schema_value(schema: Mapping[str, Any], value: Any, path: str) -> None:
    """Validate the supported recursive JSON-Schema subset; reject unknown constraints."""
    if not isinstance(schema, Mapping):
        raise ToolContractError(f"invalid schema for {path}")
    if any(not isinstance(key, str) for key in schema):
        raise ToolContractError(f"schema keywords for {path} must be strings")
    unsupported = sorted(set(schema) - _SCHEMA_ALLOWED_KEYWORDS)
    if unsupported:
        raise ToolContractError(
            f"unsupported schema keywords for {path}: " + ", ".join(unsupported)
        )
    kind = schema.get("type")
    if not isinstance(kind, str) or kind not in {"string", "integer", "number", "boolean", "array", "object", "null"}:
        raise ToolContractError(f"unsupported schema type for {path}: {kind}")

    scoped_keywords = {
        "properties": "object", "required": "object", "additionalProperties": "object",
        "minProperties": "object", "maxProperties": "object",
        "items": "array", "minItems": "array", "maxItems": "array",
        "minLength": "string", "maxLength": "string",
        "minimum": "number", "maximum": "number",
    }
    misplaced = sorted(
        key for key, owner in scoped_keywords.items()
        if key in schema and kind not in ({owner, "integer"} if owner == "number" else {owner})
    )
    if misplaced:
        raise ToolContractError(
            f"schema constraints do not apply to {path} ({kind}): " + ", ".join(misplaced)
        )

    if "enum" in schema:
        choices = schema["enum"]
        if not isinstance(choices, (list, tuple)) or not choices:
            raise ToolContractError(f"enum for {path} must be a non-empty array")
        if not any(_json_values_equal(value, choice) for choice in choices):
            raise ToolContractError(f"{path} is not an allowed enum value")

    def integer_bound(key: str) -> int | None:
        if key not in schema:
            return None
        bound = schema[key]
        if type(bound) is not int or bound < 0:
            raise ToolContractError(f"{key} for {path} must be a non-negative integer")
        return bound

    if kind == "string":
        if not isinstance(value, str):
            raise ToolContractError(f"{path} must be string")
        minimum, maximum = integer_bound("minLength"), integer_bound("maxLength")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ToolContractError(f"invalid string bounds for {path}")
        if minimum is not None and len(value) < minimum:
            raise ToolContractError(f"{path} is shorter than minLength")
        if maximum is not None and len(value) > maximum:
            raise ToolContractError(f"{path} is longer than maxLength")
        return

    if kind == "integer":
        if type(value) is not int:
            raise ToolContractError(f"{path} must be integer")
    elif kind == "number":
        if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
            raise ToolContractError(f"{path} must be a finite number")
    elif kind == "boolean":
        if type(value) is not bool:
            raise ToolContractError(f"{path} must be boolean")
    elif kind == "null":
        if value is not None:
            raise ToolContractError(f"{path} must be null")
    elif kind == "array":
        if not isinstance(value, (list, tuple)):
            raise ToolContractError(f"{path} must be array")
        minimum, maximum = integer_bound("minItems"), integer_bound("maxItems")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ToolContractError(f"invalid array bounds for {path}")
        if minimum is not None and len(value) < minimum:
            raise ToolContractError(f"{path} has fewer than minItems")
        if maximum is not None and len(value) > maximum:
            raise ToolContractError(f"{path} has more than maxItems")
        if "items" in schema:
            item_schema = schema["items"]
            if not isinstance(item_schema, Mapping):
                raise ToolContractError(f"items schema for {path} must be an object")
            for index, item in enumerate(value):
                _validate_schema_value(item_schema, item, f"{path}[{index}]")
    elif kind == "object":
        if not isinstance(value, Mapping):
            raise ToolContractError(f"{path} must be object")
        if any(not isinstance(key, str) for key in value):
            raise ToolContractError(f"{path} object keys must be strings")
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if (
            not isinstance(properties, Mapping)
            or any(not isinstance(key, str) for key in properties)
            or any(not isinstance(child_schema, Mapping) for child_schema in properties.values())
        ):
            raise ToolContractError(f"properties schema for {path} is malformed")
        if not isinstance(required, list) or any(not isinstance(key, str) for key in required):
            raise ToolContractError(f"required schema for {path} is malformed")
        missing = sorted(set(required) - set(value))
        if missing:
            raise ToolContractError(
                f"{path} is missing required properties: " + ", ".join(missing)
            )
        minimum, maximum = integer_bound("minProperties"), integer_bound("maxProperties")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ToolContractError(f"invalid property bounds for {path}")
        if minimum is not None and len(value) < minimum:
            raise ToolContractError(f"{path} has fewer than minProperties")
        if maximum is not None and len(value) > maximum:
            raise ToolContractError(f"{path} has more than maxProperties")
        additional = schema.get("additionalProperties", True)
        if type(additional) is not bool and not isinstance(additional, Mapping):
            raise ToolContractError(f"additionalProperties schema for {path} is malformed")
        extra = sorted(set(value) - set(properties))
        if extra and additional is False:
            raise ToolContractError(
                f"{path} has unexpected properties: " + ", ".join(extra)
            )
        for key, item in value.items():
            child_schema = properties.get(key)
            if child_schema is None:
                if isinstance(additional, Mapping):
                    _validate_schema_value(additional, item, f"{path}.{key}")
                continue
            _validate_schema_value(child_schema, item, f"{path}.{key}")

    if kind in {"integer", "number"}:
        for key in ("minimum", "maximum"):
            if key in schema:
                bound = schema[key]
                if type(bound) not in (int, float) or (type(bound) is float and not math.isfinite(bound)):
                    raise ToolContractError(f"{key} for {path} must be a finite number")
        minimum, maximum = schema.get("minimum"), schema.get("maximum")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ToolContractError(f"invalid numeric bounds for {path}")
        if minimum is not None and value < minimum:
            raise ToolContractError(f"{path} is below minimum")
        if maximum is not None and value > maximum:
            raise ToolContractError(f"{path} is above maximum")


def validate_tool_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> None:
    """Validate supported nested JSON-Schema constraints before tool dispatch."""
    if not isinstance(schema, Mapping) or schema.get("type") != "object":
        raise ToolContractError("tool input schema must declare an object")
    if not isinstance(arguments, Mapping):
        raise ToolContractError("tool arguments must be an object")
    _validate_schema_value(schema, arguments, "arguments")


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
        if (
            not _SLUG.fullmatch(self.repository)
            or any(part in {".", ".."} for part in self.repository.split("/"))
            or not _SHA.fullmatch(self.head_sha)
        ):
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


def exact_head_ci_gate(
    expected_sha: str,
    checks: Sequence[Mapping[str, Any]],
    *,
    required_check_names: Sequence[str],
) -> dict[str, Any]:
    """Fail closed: stale, missing, skipped-required and pending checks never pass."""
    if not _SHA.fullmatch(expected_sha):
        raise ValueError("expected_sha must be a full lowercase commit SHA")
    if (
        isinstance(required_check_names, (str, bytes))
        or not isinstance(required_check_names, Sequence)
        or not required_check_names
    ):
        raise ValueError("required_check_names must provide the authoritative non-empty required-check set")
    if any(not isinstance(name, str) or not name.strip() for name in required_check_names):
        raise ValueError("required_check_names must contain non-empty names")
    expected_required = set(required_check_names)
    if len(expected_required) != len(required_check_names):
        raise ValueError("required_check_names must not contain duplicates")
    results = []
    seen_names: set[str] = set()
    for check in checks:
        name = _text(check.get("name"), "check name")
        if name in seen_names:
            raise ValueError(f"duplicate CI check name: {name}")
        seen_names.add(name)
        sha, conclusion = check.get("head_sha"), check.get("conclusion")
        required = check.get("required", True)
        if type(required) is not bool:
            raise ValueError("check required must be boolean")
        required = required or name in expected_required
        if sha != expected_sha:
            state = "stale"
        elif conclusion == "success":
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
    expected_required.update(item["name"] for item in results if item["required"])
    missing = sorted(expected_required - seen_names)
    results.extend(
        {"name": name, "state": "missing", "required": True, "head_sha": None}
        for name in missing
    )
    passed = bool(expected_required) and all(
        item["state"] == "passed" for item in results if item["required"]
    )
    return {"expected_sha": expected_sha, "passed": passed, "missing_required": missing, "checks": results}
