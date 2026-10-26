"""Fail-closed invocation boundary for optional AUREN extensions.

Discovery metadata is descriptive only. A caller must supply an explicit trust grant
and task-scoped approval where required before this module dispatches an extension.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any


class ExtensionInvocationDenied(PermissionError):
    """An optional extension failed a policy check before its code was invoked."""


def evaluate_invocation(
    metadata: Mapping[str, Any],
    *,
    trust_granted: bool = False,
    approval_granted: bool = False,
    requested_permissions: Sequence[str] = (),
    artifact_fresh: bool = False,
    requested_output_limit_chars: int | None = None,
) -> dict[str, Any]:
    """Return a deterministic allow/deny decision; never infer trust from discovery."""
    reasons: list[str] = []
    if metadata.get("available") is not True:
        reasons.append("extension_unavailable")
    if metadata.get("trust_class") != "trusted" or trust_granted is not True:
        reasons.append("explicit_trust_grant_required")
    if metadata.get("requires_approval", True) is not False and approval_granted is not True:
        reasons.append("task_scoped_approval_required")
    declared = metadata.get("permissions", ())
    if not isinstance(declared, (list, tuple, set, frozenset)):
        declared = ()
    requested = set(requested_permissions)
    if not requested.issubset(set(declared)):
        reasons.append("permission_scope_exceeded")
    if artifact_fresh is not True:
        reasons.append("freshness_not_verified")
    maximum = metadata.get("output_limit_chars", 0)
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
        reasons.append("invalid_output_limit")
        maximum = 0
    if requested_output_limit_chars is not None and (
        not isinstance(requested_output_limit_chars, int)
        or isinstance(requested_output_limit_chars, bool)
        or requested_output_limit_chars < 1
    ):
        reasons.append("invalid_requested_output_limit")
    effective_limit = min(maximum, requested_output_limit_chars) if requested_output_limit_chars else maximum
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "output_limit_chars": effective_limit,
    }


def invoke_extension(
    metadata: Mapping[str, Any],
    invoke: Callable[[], Any],
    *,
    trust_granted: bool = False,
    approval_granted: bool = False,
    requested_permissions: Sequence[str] = (),
    artifact_fresh: bool = False,
    requested_output_limit_chars: int | None = None,
) -> str:
    """Authorize before dispatch and return a bounded textual result.

    This wrapper is intended for extension/tool adapters. It does not create a trust
    grant, approval, sandbox, or provider-health claim.
    """
    if not callable(invoke):
        raise TypeError("invoke must be callable")
    decision = evaluate_invocation(
        metadata,
        trust_granted=trust_granted,
        approval_granted=approval_granted,
        requested_permissions=requested_permissions,
        artifact_fresh=artifact_fresh,
        requested_output_limit_chars=requested_output_limit_chars,
    )
    if not decision["allowed"]:
        raise ExtensionInvocationDenied(";".join(decision["reasons"]))
    result = invoke()
    rendered = result if isinstance(result, str) else str(result)
    limit = decision["output_limit_chars"]
    if len(rendered) <= limit:
        return rendered
    suffix = "\n[output truncated by AUREN policy]"
    if limit <= len(suffix):
        return rendered[:limit]
    return rendered[: limit - len(suffix)] + suffix
