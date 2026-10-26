"""Canonical fail-closed policy for optional extension execution boundaries."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any


class ExtensionInvocationDenied(PermissionError):
    """An optional extension failed a policy check before its code was invoked."""


def evaluate_invocation(
    metadata: Mapping[str, Any] | None,
    *,
    trust_granted: bool = False,
    approval_granted: bool = False,
    requested_permissions: Sequence[str] = (),
    artifact_fresh: bool = False,
    requested_output_limit_chars: int | None = None,
    provider_id: str | None = None,
    require_provider_scope: bool = False,
) -> dict[str, Any]:
    """Return a deterministic decision. Missing or malformed policy always denies."""
    reasons: list[str] = []
    if not isinstance(metadata, Mapping):
        metadata = {}
        reasons.append("extension_policy_missing")
    if metadata.get("available") is not True:
        reasons.append("extension_unavailable")
    if metadata.get("manifest_valid") is not True:
        reasons.append("extension_manifest_invalid")
    if metadata.get("trust_class") != "trusted" or trust_granted is not True:
        reasons.append("explicit_trust_grant_required")
    requires_approval = metadata.get("requires_approval")
    if requires_approval is not False and approval_granted is not True:
        reasons.append("task_scoped_approval_required")
    side_effects = metadata.get("side_effects", ())
    if side_effects and approval_granted is not True:
        reasons.append("side_effect_approval_required")
    if metadata.get("privacy_class") == "external" and approval_granted is not True:
        reasons.append("external_data_approval_required")
    declared = metadata.get("permissions")
    if (
        not isinstance(declared, (list, tuple, set, frozenset))
        or any(not isinstance(item, str) or not item.strip() for item in declared)
    ):
        reasons.append("invalid_permission_manifest")
        declared = ()
    requested_values = requested_permissions
    if (
        not isinstance(requested_values, (list, tuple, set, frozenset))
        or any(not isinstance(item, str) or not item.strip() for item in requested_values)
    ):
        reasons.append("invalid_requested_permissions")
        requested_values = ()
    if not set(requested_values).issubset(set(declared)):
        reasons.append("permission_scope_exceeded")
    if artifact_fresh is not True or metadata.get("artifact_fresh") is not True:
        reasons.append("freshness_not_verified")
    if require_provider_scope:
        allowed_providers = metadata.get("provider_ids")
        if (
            not isinstance(provider_id, str) or not provider_id.strip()
            or not isinstance(allowed_providers, (list, tuple, set, frozenset))
            or any(not isinstance(item, str) or not item.strip() for item in allowed_providers)
            or provider_id not in allowed_providers
        ):
            reasons.append("provider_scope_not_authorized")
    elif provider_id is not None:
        allowed_providers = metadata.get("provider_ids")
        if (
            not isinstance(provider_id, str) or not provider_id.strip()
            or not isinstance(allowed_providers, (list, tuple, set, frozenset))
            or provider_id not in allowed_providers
        ):
            reasons.append("provider_scope_not_authorized")
    maximum = metadata.get("output_limit_chars")
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
    return {"allowed": not reasons, "reasons": sorted(set(reasons)), "output_limit_chars": effective_limit}


def invoke_extension(
    metadata: Mapping[str, Any] | None,
    invoke: Callable[[], Any],
    *,
    trust_granted: bool = False,
    approval_granted: bool = False,
    requested_permissions: Sequence[str] = (),
    artifact_fresh: bool = False,
    requested_output_limit_chars: int | None = None,
    provider_id: str | None = None,
    require_provider_scope: bool = False,
) -> str:
    """Authorize before dispatch and bound output; never synthesize trust or approval."""
    if not callable(invoke):
        raise TypeError("invoke must be callable")
    decision = evaluate_invocation(
        metadata, trust_granted=trust_granted, approval_granted=approval_granted,
        requested_permissions=requested_permissions, artifact_fresh=artifact_fresh,
        requested_output_limit_chars=requested_output_limit_chars,
        provider_id=provider_id, require_provider_scope=require_provider_scope,
    )
    if not decision["allowed"]:
        raise ExtensionInvocationDenied(";".join(decision["reasons"]))
    rendered = invoke()
    if not isinstance(rendered, str):
        rendered = str(rendered)
    limit = decision["output_limit_chars"]
    if len(rendered) <= limit:
        return rendered
    suffix = "\n[output truncated by AUREN policy]"
    if limit <= len(suffix):
        return rendered[:limit]
    return rendered[:limit - len(suffix)] + suffix
