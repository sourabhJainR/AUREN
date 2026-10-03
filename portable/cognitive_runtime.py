"""Composition facade for AUREN's cognitive primitives.

This facade centralizes the semantic subsystems around one project key while
keeping StateGraph/Orchestrator as the execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass

from .agi_evaluation import CapabilityEvaluator
from .causal_model import CausalModel
from .context_graph import ContextGraph
from .goal_manager import GoalManager
from .hypothesis_engine import HypothesisEngine
from .information_planner import InformationPlanner
from .persistent_memory import PersistentMemory
from .reasoning_ledger import ReasoningLedger
from .self_model import SelfModel
from .world_model import WorldModel


@dataclass(frozen=True)
class CognitiveRuntime:
    """Project-scoped access to AUREN cognitive state and evaluation."""

    project: str
    memory: PersistentMemory
    graph: ContextGraph
    world: WorldModel
    hypotheses: HypothesisEngine
    reasoning: ReasoningLedger
    causal: CausalModel
    goals: GoalManager
    information: InformationPlanner
    self_model: SelfModel
    evaluator: CapabilityEvaluator

    @classmethod
    def create(cls, memory: PersistentMemory, project: str) -> "CognitiveRuntime":
        graph = ContextGraph(memory, project)
        return cls(project, memory, graph, WorldModel(memory, project), HypothesisEngine(memory, project),
                   ReasoningLedger(memory, project), CausalModel(graph), GoalManager(memory, project),
                   InformationPlanner(), SelfModel(memory, project), CapabilityEvaluator())


__all__ = ["CognitiveRuntime"]
