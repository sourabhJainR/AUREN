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


def select_capabilities(route: dict[str, Any], *, extensions: dict[str, Any] | None = None) -> dict[str, Any]:
    """Select the smallest specialist set justified by route, risk and uncertainty.

    This is intentionally deterministic. External extensions are only exposed as hints when
    already available, explicitly trusted, and configured not to require per-task approval.
    Discovery alone never makes an extension eligible.
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
    extension_decisions: dict[str, dict[str, Any]] = {}
    for name, metadata in sorted((extensions or {}).items()):
        if not isinstance(metadata, dict):
            extension_decisions[str(name)] = {"allowed": False, "reasons": ["invalid_extension_metadata"]}
            continue
        decision = evaluate_invocation(metadata)
        extension_decisions[str(name)] = {
            "allowed": decision["allowed"],
            "reasons": decision["reasons"],
            "output_limit_chars": decision["output_limit_chars"],
        }
    return {
        "version": VERSION,
        "selected": selected,
        "max_parallel_read_only": max_parallel,
        "mutating_capabilities": [entry.name for entry in entries if not entry.read_only],
        "reports": {entry.name: entry.report_contract for entry in entries},
        "extension_hints": [name for name, decision in extension_decisions.items() if decision["allowed"]],
        "extension_decisions": extension_decisions,
        "strategy": "minimum-capability-set + deterministic-risk-escalation + fail-closed-extension-policy + explicit-report-contracts",
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
