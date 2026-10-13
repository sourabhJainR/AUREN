"""Bounded read-only counterfactual prediction over AUREN's causal model."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from .causal_model import CausalModel


@dataclass(frozen=True)
class CounterfactualQuery:
    interventions: tuple[str, ...]
    targets: tuple[str, ...]
    max_hops: int = 8
    max_paths: int = 128

    def __post_init__(self) -> None:
        if not self.interventions or not self.targets:
            raise ValueError("interventions and targets are required")
        if any(not isinstance(value, str) or not value.strip() for value in (*self.interventions, *self.targets)):
            raise ValueError("interventions and targets must contain non-empty strings")
        if len(set(self.interventions)) != len(self.interventions) or len(set(self.targets)) != len(self.targets):
            raise ValueError("interventions and targets must be unique")
        if self.max_hops < 1 or self.max_paths < 1:
            raise ValueError("max_hops and max_paths must be positive")


@dataclass(frozen=True)
class CounterfactualResult:
    interventions: tuple[str, ...]
    targets: tuple[str, ...]
    paths: tuple[tuple[str, ...], ...]
    confidence: float
    alternate_predictions: Mapping[str, object] | None = None

    @property
    def reaches_target(self) -> bool:
        return bool(self.paths)


class CounterfactualEngine:
    """Answer bounded 'what if' questions without mutating live state."""

    def __init__(self, causal: CausalModel) -> None:
        if not isinstance(causal, CausalModel):
            raise TypeError("causal must be a CausalModel instance")
        self.causal = causal

    def evaluate(self, query: CounterfactualQuery) -> CounterfactualResult:
        for node_id in (*query.interventions, *query.targets):
            if self.causal.graph.get_node(node_id) is None:
                raise KeyError(f"unknown causal node: {node_id}")
        target_set = set(query.targets)
        found: list[tuple[tuple[str, ...], float]] = []
        for start in query.interventions:
            queue: list[tuple[str, tuple[str, ...], float]] = [(start, (start,), 1.0)]
            seen: set[tuple[str, int]] = set()
            while queue and len(found) < query.max_paths:
                node, path, confidence = queue.pop(0)
                state = (node, len(path) - 1)
                if state in seen:
                    continue
                seen.add(state)
                if node in target_set and len(path) > 1:
                    found.append((path, confidence))
                    continue
                if len(path) - 1 >= query.max_hops:
                    continue
                for edge in self.causal.effects_of(node, limit=self.causal.max_links):
                    if edge.target_id in path:
                        continue
                    queue.append((edge.target_id, path + (edge.target_id,), min(confidence, edge.confidence)))
        unique: dict[tuple[str, ...], float] = {}
        for path, confidence in found:
            unique[path] = max(unique.get(path, 0.0), confidence)
        ordered = sorted(unique.items(), key=lambda item: (len(item[0]), item[0]))[:query.max_paths]
        paths = tuple(path for path, _ in ordered)
        confidence = max((score for _, score in ordered), default=0.0)
        return CounterfactualResult(query.interventions, query.targets, paths, confidence)

    def simulate(self, query: CounterfactualQuery, *, initial_state: Mapping[str, object],
                 transition: Callable[[Mapping[str, object], str], Mapping[str, object]]) -> CounterfactualResult:
        """Run a caller-owned pure transition function over an alternate state."""
        if not callable(transition):
            raise TypeError("transition must be callable")
        result = self.evaluate(query)
        state = dict(initial_state)
        for intervention in query.interventions:
            state = dict(transition(dict(state), intervention))
        predictions = {target: state[target] for target in query.targets if target in state}
        return CounterfactualResult(result.interventions, result.targets, result.paths, result.confidence, predictions)


__all__ = ["CounterfactualEngine", "CounterfactualQuery", "CounterfactualResult"]
