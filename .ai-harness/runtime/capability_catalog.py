#!/usr/bin/env python3
"""Small, deterministic catalog for selecting the minimum useful agent capabilities.

The catalog borrows the useful architectural idea of a typed agent catalog: specialists have
explicit responsibilities, mutability and report contracts. It intentionally does not depend on
an agent framework. The provider remains responsible for execution; this module only decides what
kind of work is justified and makes that decision inspectable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from extension_policy import evaluate_invocation

VERSION = "1.1"


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    purpose: str
    read_only: bool
    parallel_safe: bool
    minimum_risk: str
    report_contract: str


CATALOG: tuple[Capability, ...] = (
    Capability("planner", "turn the task contract into independently verifiable work", True, True, "low", "plan + assumptions + risks"),
    Capability("explorer", "trace repository structure, callers, dependencies and data flow", True, True, "low", "finding + evidence + trace"),
    Capability("researcher", "answer questions requiring external or repository research", True, True, "low", "claims + sources + uncertainty"),
    Capability("builder", "implement the requested change within the approved scope", False, False, "low", "changes + rationale + verification"),
    Capability("verifier", "run and interpret repository-native verification", True, True, "low", "checks + results + gaps"),
    Capability("reviewer", "independently challenge correctness, regression and architecture", True, True, "medium", "severity + evidence + disposition"),
    Capability("security_reviewer", "challenge trust boundaries, secrets, authorization and unsafe behavior", True, True, "high", "finding + evidence + severity"),
    Capability("rca_investigator", "produce evidence-backed root-cause findings without patching", True, True, "medium", "timeline + flow + hypotheses + proof status"),
)

_BY_NAME = {item.name: item for item in CATALOG}


def select_capabilities(
    route: dict[str, Any], *, extensions: dict[str, Any] | None = None,
    extension_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select the smallest specialist set justified by route, risk and uncertainty.

    This is intentionally deterministic. External extensions are only exposed as hints when a
    trusted policy supplies the required trust, approval, permission, and freshness grants.
    Task-generated routes cannot grant trust or authorize external data use. Discovery alone
    never makes an extension eligible.
    """
    mode = str(route.get("mode", "implement"))
    risk = str(route.get("risk", "low"))
    uncertainty = str(route.get("uncertainty", "known"))
    capabilities: list[str] = ["planner"]

    if mode in {"research", "poc"}:
        capabilities.append("researcher")
    if mode in {"debug", "review", "grill", "rca"} or uncertainty == "unknown":
        capabilities.append("explorer")
    if mode == "rca":
        capabilities.append("rca_investigator")
    elif mode not in {"research", "grill", "review", "rca"}:
        capabilities.append("builder")
    if mode not in {"research", "grill", "rca"}:
        capabilities.append("verifier")
    if mode in {"review", "debug", "grill"} or risk in {"medium", "high", "critical"}:
        capabilities.append("reviewer")
    if risk in {"high", "critical"} or "security" in str(route.get("reason", "")).lower():
        capabilities.append("security_reviewer")

    selected = list(dict.fromkeys(capabilities))
    entries = [_BY_NAME[name] for name in selected]
    max_parallel = sum(1 for entry in entries if entry.read_only and entry.parallel_safe)
    extension_catalog = extensions if isinstance(extensions, dict) else {}
    trusted_policy = extension_policy if isinstance(extension_policy, dict) else {}
    try:
        extension_routing = rank_extension_candidates(
            {
                "required_capabilities": route.get("required_extension_capabilities", []),
            },
            extension_catalog,
            grants=trusted_policy.get("grants", {}),
            privacy_requirement=trusted_policy.get("privacy_requirement", "local-only"),
            max_cost_class=trusted_policy.get("max_cost_class", "medium"),
            allow_external=trusted_policy.get("allow_external") is True,
        )
    except ValueError as exc:
        extension_routing = {
            "selected": None,
            "eligible": [],
            "excluded": {str(name): ["invalid_extension_routing_policy"] for name in extension_catalog},
            "fallback": "repository_native",
            "policy_error": str(exc),
        }
    extension_decisions: dict[str, dict[str, Any]] = {}
    eligible_names = set(extension_routing.get("eligible", []))
    excluded = extension_routing.get("excluded", {})
    for name, metadata in sorted(extension_catalog.items()):
        if not isinstance(metadata, dict):
            extension_decisions[str(name)] = {"allowed": False, "reasons": ["invalid_extension_metadata"]}
            continue
        allowed = name in eligible_names
        extension_decisions[str(name)] = {
            "allowed": allowed,
            "reasons": [] if allowed else excluded.get(name, ["not_selected_or_ineligible"]),
            "output_limit_chars": metadata.get("output_limit_chars", 0),
        }
    return {
        "version": VERSION,
        "selected": selected,
        "max_parallel_read_only": max_parallel,
        "mutating_capabilities": [entry.name for entry in entries if not entry.read_only],
        "reports": {entry.name: entry.report_contract for entry in entries},
        "extension_hints": extension_routing.get(
            "selected_extensions",
            [extension_routing["selected"]] if extension_routing.get("selected") else [],
        ),
        "extension_decisions": extension_decisions,
        "extension_routing": extension_routing,
        "strategy": "minimum-capability-set + deterministic-risk-escalation + cost-capability-privacy-ranked-extensions + fail-closed-fallback + explicit-report-contracts",
    }



def rank_extension_candidates(
    route: dict[str, Any],
    extensions: dict[str, Any],
    *,
    grants: dict[str, Any] | None = None,
    privacy_requirement: str = "standard",
    max_cost_class: str = "medium",
    allow_external: bool = False,
) -> dict[str, Any]:
    """Choose the cheapest eligible extension; otherwise fall back to repository-native work."""
    cost_order = {"low": 0, "medium": 1, "high": 2}
    if not isinstance(route, dict) or not isinstance(extensions, dict):
        raise ValueError("route and extensions must be mappings")
    if any(not isinstance(name, str) or not name for name in extensions):
        raise ValueError("extension names must be non-empty strings")
    if grants is not None and not isinstance(grants, dict):
        raise ValueError("grants must be a mapping")
    if type(allow_external) is not bool:
        raise ValueError("allow_external must be a boolean")
    if not isinstance(privacy_requirement, str) or privacy_requirement not in {"standard", "local-only"}:
        raise ValueError("privacy_requirement must be standard or local-only")
    if not isinstance(max_cost_class, str) or max_cost_class not in cost_order:
        raise ValueError("max_cost_class must be low, medium, or high")
    required = route.get("required_capabilities", [])
    if not isinstance(required, list) or any(not isinstance(item, str) or not item for item in required):
        raise ValueError("required_capabilities must be a list of non-empty strings")
    required_set = set(required)
    grants = grants or {}
    if not required_set:
        return {
            "selected": None,
            "eligible": [],
            "excluded": {name: ["no_extension_capability_demand"] for name in extensions},
            "fallback": "repository_native",
            "policy": {
                "privacy_requirement": privacy_requirement,
                "max_cost_class": max_cost_class,
                "allow_external": allow_external,
                "required_capabilities": [],
            },
        }
    candidates: list[tuple[int, int, str, set[str]]] = []
    excluded: dict[str, list[str]] = {}
    for name, metadata in sorted(extensions.items()):
        if not isinstance(metadata, dict):
            excluded[name] = ["invalid_extension_metadata"]
            continue
        reasons: list[str] = []
        grant = grants.get(name, {})
        if not isinstance(grant, dict):
            grant = {}
            reasons.append("invalid_grant_record")
        privacy = metadata.get("privacy_class", "unknown")
        if not isinstance(privacy, str) or privacy not in {"local-only", "external", "unknown"}:
            reasons.append("privacy_class_invalid")
        else:
            if privacy == "unknown":
                reasons.append("privacy_class_unverified")
            if privacy_requirement == "local-only" and privacy != "local-only":
                reasons.append("local_only_privacy_required")
            if privacy == "external" and not allow_external:
                reasons.append("external_data_use_not_authorized")
        cost = metadata.get("cost_class", "unknown")
        if not isinstance(cost, str) or cost not in cost_order:
            reasons.append("cost_class_unknown")
        elif cost_order[cost] > cost_order[max_cost_class]:
            reasons.append("cost_limit_exceeded")
        capabilities = metadata.get("capabilities", [])
        capability_set: set[str] = set()
        if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
            reasons.append("capability_manifest_invalid")
        else:
            capability_set = set(capabilities)
            if not (required_set & capability_set):
                reasons.append("no_required_capability_overlap")
        requested_permissions = grant.get("requested_permissions", [])
        if (
            not isinstance(requested_permissions, (list, tuple, set, frozenset))
            or any(not isinstance(item, str) or not item.strip() for item in requested_permissions)
        ):
            reasons.append("invalid_requested_permissions")
            requested_permissions = []
        decision = evaluate_invocation(
            metadata,
            trust_granted=grant.get("trust_granted") is True,
            approval_granted=grant.get("approval_granted") is True,
            requested_permissions=requested_permissions,
            artifact_fresh=metadata.get("artifact_fresh") is True and grant.get("artifact_fresh") is True,
            requested_output_limit_chars=grant.get("output_limit_chars"),
        )
        if not decision["allowed"]:
            reasons.extend(decision["reasons"])
        if reasons:
            excluded[name] = sorted(set(reasons))
            continue
        side_effects = metadata.get("side_effects", [])
        side_effect_count = len(side_effects) if isinstance(side_effects, list) else 0
        candidates.append((cost_order[cost], side_effect_count, name, capability_set))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    eligible = [name for _, _, name, _ in candidates]
    selected_names: list[str] = []
    uncovered = set(required_set)
    remaining = list(candidates)
    while uncovered:
        options = []
        for cost_rank, side_effect_count, name, capability_set in remaining:
            newly_covered = uncovered & capability_set
            if newly_covered:
                options.append((-len(newly_covered), cost_rank, side_effect_count, name, capability_set))
        if not options:
            break
        _, cost_rank, side_effect_count, name, capability_set = sorted(options)[0]
        selected_names.append(name)
        uncovered -= capability_set
        remaining = [item for item in remaining if item[2] != name]
    complete = not uncovered
    if not complete:
        selected_names = []
    selected = selected_names[0] if selected_names else None
    return {
        "selected": selected,
        "selected_extensions": selected_names,
        "eligible": eligible,
        "excluded": excluded,
        "uncovered_capabilities": sorted(uncovered),
        "fallback": None if complete and selected_names else "repository_native",
        "policy": {
            "privacy_requirement": privacy_requirement,
            "max_cost_class": max_cost_class,
            "allow_external": allow_external,
            "required_capabilities": sorted(required_set),
        },
    }


def validate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate a capability plan before it is consumed by another component."""
    selected = plan.get("selected", [])
    reasons: list[str] = []
    if not isinstance(selected, list) or not selected:
        reasons.append("empty_selection")
    elif any(name not in _BY_NAME for name in selected):
        reasons.append("unknown_capability")
    if len(selected) != len(set(selected)):
        reasons.append("duplicate_capability")
    if plan.get("max_parallel_read_only", 0) < 0:
        reasons.append("invalid_parallel_limit")
    hints = plan.get("extension_hints", [])
    decisions = plan.get("extension_decisions", {})
    if not isinstance(hints, list) or not isinstance(decisions, dict):
        reasons.append("invalid_extension_policy_projection")
    elif any(name not in decisions or decisions[name].get("allowed") is not True for name in hints):
        reasons.append("unapproved_extension_hint")
    return {"passed": not reasons, "reasons": reasons}
