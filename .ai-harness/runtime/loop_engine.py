#!/usr/bin/env python3
"""Planning helpers for AUREN loop execution.

Bounded execution is owned by ``portable.feedback_loop.BoundedLoop``. This module
keeps the older planning/scoring helpers for compatibility, but does not define
a second loop executor. Runtime callers should use the canonical portable loop
contract so loop receipts and provenance lineage stay consistent with release
lifecycle evidence.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from portable.feedback_loop import (
    BoundedLoop,
    LoopAction,
    LoopDefinition,
    LoopPass,
    LoopRunReceipt,
    VerificationResult,
)

VERSION = "1.1"
LAYERS = ("generation", "evaluation", "memory", "scheduling", "optimization", "recursion")
DEFAULT_AGENTS = {
    "planner": {"role": "strategy", "mutates": False},
    "builder": {"role": "execution", "mutates": True},
    "evaluator": {"role": "verification", "mutates": False},
    "reviewer": {"role": "quality", "mutates": False},
    "optimizer": {"role": "improvement", "mutates": False},
}


def _digest(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def task_profile(task: str, route: dict[str, Any], risk: str = "normal") -> dict[str, Any]:
    text = str(task).lower()
    signals = ("legacy", "migration", "architecture", "security", "regression", "rca", "distributed")
    complexity = min(10, 1 + sum(signal in text for signal in signals) + len(route.get("capabilities", [])))
    return {"risk": risk, "complexity": complexity, "mode": route.get("mode", "adaptive"),
            "intent_digest": _digest({"task": task, "route": route})}


def _matching_extensions(agent: str, extensions: dict[str, Any]) -> list[str]:
    wanted = {
        "planner": {"planning", "task-decomposition"}, "builder": {"implementation", "tdd"},
        "evaluator": {"testing", "verification"}, "reviewer": {"review", "regression", "architecture", "security"},
        "optimizer": {"context-compression", "retrieval", "impact-analysis"},
    }.get(agent, set())
    return sorted(name for name, spec in extensions.items()
                  if isinstance(spec, dict) and spec.get("available")
                  and ({str(x).lower() for x in spec.get("capabilities", [])} & wanted))


def select_subagents(profile: dict[str, Any], *, extensions: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    selected = ["planner", "builder", "evaluator"]
    if str(profile.get("risk")) in {"high", "critical"} or int(profile.get("complexity", 1)) >= 6:
        selected.append("reviewer")
    if int(profile.get("complexity", 1)) >= 7 or str(profile.get("mode")) in {"rca", "research"}:
        selected.append("optimizer")
    return [{"name": name, **DEFAULT_AGENTS[name], "extensions": _matching_extensions(name, extensions or {})} for name in selected]


def iteration_budget(profile: dict[str, Any], *, explicit_loop: bool = False, configured_max: int = 3) -> dict[str, Any]:
    if not explicit_loop:
        return {"max_iterations": 1, "reason": "single-adaptive-run"}
    ceiling = min(max(1, int(configured_max)), 6)
    target = 2 + (int(profile.get("complexity", 1)) >= 6) + (str(profile.get("risk")) in {"high", "critical"})
    return {"max_iterations": min(ceiling, int(target)), "reason": "explicit-bounded-loop"}


def score_iteration(result: dict[str, Any], *, token_cost: int = 0, latency_ms: int = 0) -> dict[str, Any]:
    evidence = min(1.0, float(result.get("evidence_score", 0.0)))
    verification = min(1.0, float(result.get("verification_score", 0.0)))
    quality = min(1.0, float(result.get("quality_score", 0.0)))
    regressions = max(0, int(result.get("regressions", 0)))
    uncertainty = min(1.0, max(0.0, float(result.get("uncertainty", 1.0))))
    penalty = min(0.2, token_cost / 200000 + latency_ms / 600000)
    utility = round(0.30*evidence + 0.35*verification + 0.25*quality - 0.15*uncertainty - 0.10*min(1, regressions) - penalty, 4)
    return {"utility": utility, "efficiency_penalty": round(penalty, 4)}


def next_action(history: list[dict[str, Any]], budget: dict[str, Any], *, minimum_gain: float = 0.03) -> dict[str, Any]:
    if not history: return {"action": "execute", "reason": "first-iteration"}
    if len(history) >= int(budget["max_iterations"]): return {"action": "stop", "reason": "iteration-budget"}
    current = float(history[-1].get("utility", 0.0))
    if current >= 0.92 and int(history[-1].get("regressions", 0)) == 0: return {"action": "stop", "reason": "quality-sufficient"}
    if len(history) >= 2:
        gain = current - float(history[-2].get("utility", 0.0))
        if gain < minimum_gain: return {"action": "stop", "reason": "diminishing-returns", "gain": round(gain, 4)}
    if int(history[-1].get("regressions", 0)) > 0: return {"action": "repair", "reason": "regression-detected"}
    if float(history[-1].get("uncertainty", 1.0)) > 0.35: return {"action": "research", "reason": "uncertainty"}
    return {"action": "refine", "reason": "measurable-improvement-available"}


def loop_plan(task: str, route: dict[str, Any], *, risk: str = "normal", explicit_loop: bool = False,
              configured_max: int = 3, extensions: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = task_profile(task, route, risk)
    return {"version": VERSION, "layers": list(LAYERS), "profile": profile,
            "budget": iteration_budget(profile, explicit_loop=explicit_loop, configured_max=configured_max),
            "agents": select_subagents(profile, extensions=extensions),
            "stop_rules": ["iteration-budget", "quality-sufficient", "diminishing-returns", "no-new-evidence"],
            "collaboration": "intent-bound handoffs + provenance graph + bounded memory",
            "token_policy": "parallelize independent read-only work; summarize before handoff; never replay full transcripts"}


def iteration_record(iteration: int, result: dict[str, Any], *, token_cost: int = 0, latency_ms: int = 0) -> dict[str, Any]:
    return {"iteration": iteration, **score_iteration(result, token_cost=token_cost, latency_ms=latency_ms),
            "regressions": int(result.get("regressions", 0)), "uncertainty": float(result.get("uncertainty", 1.0)),
            "evidence_ids": sorted(set(str(x) for x in result.get("evidence_ids", []) if str(x))),
            "token_cost": token_cost, "latency_ms": latency_ms}


__all__ = [
    "BoundedLoop", "LoopAction", "LoopDefinition", "LoopPass", "LoopRunReceipt",
    "VerificationResult", "task_profile", "select_subagents", "iteration_budget",
    "score_iteration", "next_action", "loop_plan", "iteration_record",
]
