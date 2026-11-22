"""Immutable execution-plan admission and dispatch boundary for AUREN.

The plan is a caller-supplied proposal, not an authorization token. The controller
revalidates repository identity, branch, base SHA, capabilities and declared tool
contracts at dispatch. Filesystem adapters enforce its path scope while active.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterator, Mapping, Sequence

from .repository_execution_preflight import (
    CapabilityCheck, RepositoryTarget, ToolContractError, capability_blockers,
    validate_tool_arguments,
)

_ACTIVE_PLAN: ContextVar["CanonicalExecutionPlan | None"] = ContextVar(
    "auren_canonical_execution_plan", default=None
)
_SHA = re.compile(r"^[0-9a-f]{40}$")


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be a sequence of strings")
    result = tuple(value)
    if any(not isinstance(item, str) or not item.strip() for item in result):
        raise ValueError(f"{field} must contain non-empty strings")
    return result


@dataclass(frozen=True, slots=True)
class CanonicalExecutionPlan:
    """Immutable, digest-bound execution intent stored in the durable task."""

    target: RepositoryTarget
    requirements: tuple[str, ...]
    acceptance: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    capabilities: tuple[tuple[str, bool, str], ...]
    stopping_condition: str
    tool_contracts: tuple[tuple[str, str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.target, RepositoryTarget):
            raise TypeError("target must be a RepositoryTarget")
        for name in ("requirements", "acceptance", "required_capabilities"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not values:
                raise ValueError(f"{name} must be a non-empty immutable tuple")
            if any(not isinstance(item, str) or not item.strip() for item in values):
                raise ValueError(f"{name} must contain non-empty strings")
        _required_text(self.stopping_condition, "stopping_condition")
        if not isinstance(self.capabilities, tuple):
            raise ValueError("capabilities must be an immutable tuple")
        names: set[str] = set()
        for item in self.capabilities:
            if not isinstance(item, tuple) or len(item) != 3:
                raise ValueError("each capability must be (name, available, evidence)")
            name, available, evidence = item
            _required_text(name, "capability name")
            if name in names or type(available) is not bool:
                raise ValueError("capability names must be unique and availability must be boolean")
            _required_text(evidence, "capability evidence")
            names.add(name)
        if not isinstance(self.tool_contracts, tuple):
            raise ValueError("tool_contracts must be an immutable tuple")
        for contract in self.tool_contracts:
            if (
                not isinstance(contract, tuple) or len(contract) != 3
                or any(not isinstance(value, str) for value in contract)
            ):
                raise ValueError("tool contracts must contain only immutable strings")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CanonicalExecutionPlan":
        if not isinstance(value, Mapping):
            raise TypeError("execution_plan must be a mapping")
        if value.get("schema_version", "auren-canonical-execution-plan-v1") != "auren-canonical-execution-plan-v1":
            raise ValueError("unsupported canonical execution plan schema version")
        target_data = value.get("target")
        if not isinstance(target_data, Mapping):
            raise ValueError("execution plan target is required")
        target = RepositoryTarget(
            repository=target_data.get("repository"),
            branch=target_data.get("branch"),
            base_sha=target_data.get("base_sha"),
            allowed_paths=_string_tuple(target_data.get("allowed_paths", ()), "allowed_paths"),
            governance_repository=target_data.get("governance_repository", ""),
            evidence_repository=target_data.get("evidence_repository", ""),
            allow_governance_repository_changes=target_data.get("allow_governance_repository_changes", False),
        )
        capabilities = tuple(
            (item.get("name"), item.get("available"), item.get("evidence"))
            for item in value.get("capabilities", ())
            if isinstance(item, Mapping)
        )
        if len(capabilities) != len(value.get("capabilities", ())):
            raise ValueError("malformed capability evidence")
        contracts = tuple(
            (
                item.get("name"),
                json.dumps(item.get("schema"), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
                json.dumps(item.get("arguments"), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
            )
            for item in value.get("tool_contracts", ())
            if isinstance(item, Mapping)
        )
        if len(contracts) != len(value.get("tool_contracts", ())):
            raise ValueError("malformed tool contract")
        return cls(
            target=target,
            requirements=_string_tuple(value.get("requirements", ()), "requirements"),
            acceptance=_string_tuple(value.get("acceptance", ()), "acceptance"),
            required_capabilities=_string_tuple(value.get("required_capabilities", ()), "required_capabilities"),
            capabilities=capabilities,
            stopping_condition=value.get("stopping_condition", ""),
            tool_contracts=contracts,
        )

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": "auren-canonical-execution-plan-v1",
            "target": {
                "repository": self.target.repository,
                "branch": self.target.branch,
                "base_sha": self.target.base_sha,
                "allowed_paths": list(self.target.allowed_paths),
                "governance_repository": self.target.governance_repository,
                "evidence_repository": self.target.evidence_repository,
                "allow_governance_repository_changes": self.target.allow_governance_repository_changes,
            },
            "requirements": list(self.requirements),
            "acceptance": list(self.acceptance),
            "required_capabilities": list(self.required_capabilities),
            "capabilities": [
                {"name": name, "available": available, "evidence": evidence}
                for name, available, evidence in self.capabilities
            ],
            "stopping_condition": self.stopping_condition,
            "tool_contracts": [
                {"name": name, "schema": json.loads(schema_json), "arguments": json.loads(arguments_json)}
                for name, schema_json, arguments_json in self.tool_contracts
            ],
        }

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            self.payload(), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def validate_runtime(self, *, repository: str, branch: str, head_sha: str) -> None:
        if not isinstance(head_sha, str) or not _SHA.fullmatch(head_sha):
            raise PermissionError("runtime HEAD is unavailable or invalid")
        self.target.validate_observed_target(
            repository=repository, branch=branch, base_sha=head_sha,
        )
        blockers = capability_blockers(
            [
                CapabilityCheck(
                    name=name, available=available, evidence=evidence,
                    checked_at="plan-admission",
                )
                for name, available, evidence in self.capabilities
            ],
            self.required_capabilities,
        )
        if blockers:
            raise PermissionError("execution plan capability gate blocked: " + "; ".join(blockers))
        for name, schema_json, arguments_json in self.tool_contracts:
            _required_text(name, "tool name")
            try:
                schema = json.loads(schema_json)
                arguments = json.loads(arguments_json)
            except (TypeError, ValueError) as exc:
                raise ToolContractError(f"tool contract {name} is malformed") from exc
            if not isinstance(schema, Mapping) or not isinstance(arguments, Mapping):
                raise ToolContractError(f"tool contract {name} is malformed")
            validate_tool_arguments(schema, arguments)

    def authorize_path(self, relative_path: str) -> str:
        return self.target.authorize_path(relative_path)


def current_execution_plan() -> CanonicalExecutionPlan | None:
    return _ACTIVE_PLAN.get()


def enforce_execution_plan_write(relative_path: str) -> None:
    plan = _ACTIVE_PLAN.get()
    if plan is not None:
        plan.authorize_path(relative_path)


@contextmanager
def execution_plan_scope(plan: CanonicalExecutionPlan | None) -> Iterator[None]:
    token = _ACTIVE_PLAN.set(plan)
    try:
        yield
    finally:
        _ACTIVE_PLAN.reset(token)


__all__ = [
    "CanonicalExecutionPlan", "current_execution_plan",
    "enforce_execution_plan_write", "execution_plan_scope",
]
