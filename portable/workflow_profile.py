"""Validated, versioned workflow preferences for AUREN.

A profile describes user workflow preferences. It is not an authorization grant:
repository policy, approvals, and the execution controller remain authoritative.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Mapping

PROFILE_VERSION = "auren-workflow-v1"
DELIVERY_MODES = frozenset({"commit_each_change", "stage_only", "working_tree", "pull_request"})
EXECUTION_MODES = frozenset({"checkpointed", "bounded_batches", "autonomous"})
APPROVAL_MODES = frozenset({"before_edits", "before_commit", "before_push_or_pr", "scoped_autonomy"})
REVIEW_MODES = frozenset({"targeted", "broad_regression", "security_focused", "all_applicable"})
COMMUNICATION_MODES = frozenset({"concise", "detailed", "final_only"})
RECOVERY_MODES = frozenset({"preserve_work", "checkpoint", "revert_task_changes"})
_FIELDS = frozenset({
    "profile_version", "delivery_mode", "execution_mode", "approval_mode",
    "review_mode", "communication_mode", "recovery_mode",
})


class WorkflowProfileError(ValueError):
    """Raised when a workflow profile is invalid or incompatible."""


@dataclass(frozen=True, slots=True)
class WorkflowProfile:
    delivery_mode: str = "pull_request"
    execution_mode: str = "checkpointed"
    approval_mode: str = "before_push_or_pr"
    review_mode: str = "all_applicable"
    communication_mode: str = "concise"
    recovery_mode: str = "preserve_work"
    profile_version: str = PROFILE_VERSION

    def __post_init__(self) -> None:
        if self.profile_version != PROFILE_VERSION:
            raise WorkflowProfileError("unsupported workflow profile version")
        for field, allowed in (
            ("delivery_mode", DELIVERY_MODES),
            ("execution_mode", EXECUTION_MODES),
            ("approval_mode", APPROVAL_MODES),
            ("review_mode", REVIEW_MODES),
            ("communication_mode", COMMUNICATION_MODES),
            ("recovery_mode", RECOVERY_MODES),
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or value not in allowed:
                raise WorkflowProfileError(f"invalid {field}: {value!r}")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "WorkflowProfile":
        if not isinstance(value, Mapping):
            raise WorkflowProfileError("workflow profile must be a mapping")
        unknown = set(value) - _FIELDS
        if unknown:
            raise WorkflowProfileError("unknown workflow profile fields: " + ", ".join(sorted(unknown)))
        return cls(**dict(value))

    def as_dict(self) -> dict[str, str]:
        return asdict(self)

    @property
    def digest(self) -> str:
        canonical = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve_profile(
    *,
    task: Mapping[str, Any] | None = None,
    project: Mapping[str, Any] | None = None,
    saved: Mapping[str, Any] | None = None,
) -> WorkflowProfile:
    """Resolve preferences by task > project > saved > safe defaults.

    Inputs are complete profiles, not partial patches. Invalid higher-priority
    profiles fail closed rather than silently falling back to a lower-priority one.
    The caller must still enforce project/platform policy before taking actions.
    """
    for candidate in (task, project, saved):
        if candidate is not None:
            return WorkflowProfile.from_mapping(candidate)
    return WorkflowProfile()


__all__ = ["PROFILE_VERSION", "WorkflowProfile", "WorkflowProfileError", "resolve_profile"]
