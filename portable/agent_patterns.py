"""Deterministic agent-pattern primitives composed with AUREN's canonical runtime.

These primitives adapt useful multi-agent patterns without creating a second
capability, memory, graph, or execution owner. Model/tool execution stays in
the host; AUREN supplies policy, verification, state, and promotion controls.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_RISK_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _tokens(value: str) -> set[str]:
    return {part.lower() for part in _TOKEN_RE.findall(value) if len(part) > 1}


@dataclass(frozen=True)
class Specialist:
    """A task specialist with explicit capabilities and a bounded tool set."""
    name: str
    capabilities: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    risk: str = "medium"
    priority: int = 0

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("specialist name is required")
        if self.risk not in _RISK_RANK:
            raise ValueError(f"invalid specialist risk: {self.risk}")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("specialist capabilities must be unique")
        if len(set(self.tools)) != len(self.tools):
            raise ValueError("specialist tools must be unique")

    def covers(self, required: Iterable[str]) -> int:
        return len(set(required).intersection(self.capabilities))


@dataclass(frozen=True)
class RouteRequest:
    task: str
    required_capabilities: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    max_risk: str = "high"
    require_full_coverage: bool = True

    def __post_init__(self) -> None:
        if not self.task.strip():
            raise ValueError("task is required")
        if self.max_risk not in _RISK_RANK:
            raise ValueError(f"invalid max risk: {self.max_risk}")


@dataclass(frozen=True)
class RouteDecision:
    specialist: str
    tools: tuple[str, ...]
    coverage: float
    reason: str


class SpecialistRouter:
    """Select the least-privileged suitable specialist or fail closed."""
    def __init__(self, specialists: Sequence[Specialist]) -> None:
        if not specialists:
            raise ValueError("at least one specialist is required")
        names = [item.name for item in specialists]
        if len(set(names)) != len(names):
            raise ValueError("specialist names must be unique")
        self._specialists = tuple(specialists)

    def _candidates(self, request: RouteRequest) -> list[tuple[Specialist, tuple[str, ...], int]]:
        wanted = set(request.required_capabilities)
        allowed = set(request.allowed_tools)
        candidates = []
        for specialist in self._specialists:
            if _RISK_RANK[specialist.risk] > _RISK_RANK[request.max_risk]:
                continue
            covered = specialist.covers(wanted)
            if wanted and request.require_full_coverage and covered != len(wanted):
                continue
            if wanted and covered == 0:
                continue
            tools = tuple(sorted(set(specialist.tools) & allowed)) if allowed else tuple(sorted(specialist.tools))
            candidates.append((specialist, tools, covered))
        return candidates

    def route(self, request: RouteRequest) -> RouteDecision:
        candidates = self._candidates(request)
        if not candidates:
            raise RuntimeError("no specialist satisfies the capability and risk constraints")
        wanted = set(request.required_capabilities)
        selected, tools, covered = max(
            candidates,
            key=lambda item: (item[2], item[0].priority, -len(item[1]), -_RISK_RANK[item[0].risk], item[0].name),
        )
        coverage = 1.0 if not wanted else covered / len(wanted)
        return RouteDecision(selected.name, tools, coverage, "selected by full capability coverage, priority, least-privilege tools and risk")

    def route_many(self, request: RouteRequest, *, max_specialists: int = 4) -> tuple[RouteDecision, ...]:
        """Cover a multi-capability task with the smallest bounded specialist set."""
        if max_specialists < 1:
            raise ValueError("max_specialists must be positive")
        wanted = set(request.required_capabilities)
        if not wanted:
            return (self.route(request),)
        allowed = set(request.allowed_tools)
        remaining = set(wanted)
        chosen: list[RouteDecision] = []
        available = list(self._candidates(RouteRequest(request.task, tuple(wanted), request.allowed_tools, request.max_risk, False)))
        while remaining and len(chosen) < max_specialists:
            viable = [item for item in available if item[2] > 0]
            if not viable:
                break
            selected, tools, _ = max(
                viable,
                key=lambda item: (len(set(item[0].capabilities) & remaining), item[0].priority,
                                  -len(set(item[1]) if allowed else set(item[0].tools)),
                                  -_RISK_RANK[item[0].risk], item[0].name),
            )
            newly = set(selected.capabilities) & remaining
            if not newly:
                break
            chosen.append(RouteDecision(selected.name, tools, len(newly) / len(wanted), "bounded specialist set-cover routing"))
            remaining -= newly
            available = [item for item in available if item[0].name != selected.name]
        if remaining:
            raise RuntimeError(f"unable to cover required capabilities: {sorted(remaining)}")
        return tuple(chosen)


@dataclass(frozen=True)
class ResearchTask:
    id: str
    question: str
    capabilities: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.question.strip():
            raise ValueError("research task id and question are required")


@dataclass(frozen=True)
class ResearchPlan:
    tasks: tuple[ResearchTask, ...]
    waves: tuple[tuple[str, ...], ...]
    digest: str


class ResearchPlanner:
    """Build bounded dependency-aware parallel research waves."""
    def build(self, questions: Sequence[str], capabilities: Mapping[str, tuple[str, ...]] | None = None,
              dependencies: Mapping[str, Sequence[str]] | None = None, *, max_tasks: int = 16,
              max_waves: int = 8) -> ResearchPlan:
        if not questions:
            raise ValueError("at least one research question is required")
        if max_tasks < 1 or max_waves < 1:
            raise ValueError("max_tasks and max_waves must be positive")
        capabilities = capabilities or {}
        dependencies = dependencies or {}
        tasks = tuple(ResearchTask(f"research-{index + 1}", question.strip(), capabilities.get(question, ()), tuple(dependencies.get(question, ())))
                      for index, question in enumerate(questions) if question.strip())
        if not tasks:
            raise ValueError("research questions cannot be empty")
        if len(tasks) > max_tasks:
            raise ValueError("research task budget exceeded")
        ids = {task.id for task in tasks}
        normalized = []
        for task in tasks:
            missing = set(task.depends_on) - ids
            if missing:
                raise ValueError(f"unknown research dependencies: {sorted(missing)}")
            if task.id in task.depends_on:
                raise ValueError(f"research task depends on itself: {task.id}")
            normalized.append(task)
        remaining = {task.id: set(task.depends_on) for task in normalized}
        waves: list[tuple[str, ...]] = []
        while remaining:
            ready = tuple(sorted(task_id for task_id, deps in remaining.items() if not deps))
            if not ready:
                raise ValueError("research dependency graph contains a cycle")
            waves.append(ready)
            if len(waves) > max_waves:
                raise ValueError("research wave budget exceeded")
            for task_id in ready:
                remaining.pop(task_id)
            for deps in remaining.values():
                deps.difference_update(ready)
        digest = hashlib.sha256("|".join(f"{task.id}:{task.question}:{','.join(task.capabilities)}:{','.join(task.depends_on)}" for task in normalized).encode()).hexdigest()
        return ResearchPlan(tuple(normalized), tuple(waves), digest)


@dataclass(frozen=True)
class EvidenceItem:
    source: str
    claim: str
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if not self.source.strip() or not self.claim.strip():
            raise ValueError("evidence source and claim are required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("evidence confidence must be between 0 and 1")


@dataclass(frozen=True)
class ConsensusResult:
    answer: str
    selected: tuple[int, ...]
    agreement: float
    evidence: tuple[EvidenceItem, ...] = ()


class MixtureOfAgents:
    """Require explicit judging before independent outputs become a result."""
    def run(self, answers: Sequence[str], judge: Callable[[Sequence[str]], tuple[str, Sequence[int], float]], evidence: Sequence[EvidenceItem] = ()) -> ConsensusResult:
        if not answers or any(not answer.strip() for answer in answers):
            raise ValueError("answers must be non-empty")
        answer, selected, agreement = judge(tuple(answers))
        selected_tuple = tuple(sorted({index for index in selected if 0 <= index < len(answers)}))
        if not answer.strip():
            raise ValueError("judge returned an empty answer")
        if not 0.0 <= agreement <= 1.0:
            raise ValueError("agreement must be between 0 and 1")
        return ConsensusResult(answer.strip(), selected_tuple, float(agreement), tuple(evidence))


@dataclass(frozen=True)
class OptimizationRound:
    round_number: int
    score: float
    kept: bool
    diagnosis: str
    change: str


@dataclass(frozen=True)
class OptimizationResult:
    baseline_score: float
    final_score: float
    rounds: tuple[OptimizationRound, ...]
    artifact: str


class OneChangeOptimizer:
    """Apply one bounded change at a time and keep it only when it improves."""
    def optimize(self, artifact: str, *, evaluate: Callable[[str], float], diagnose: Callable[[str, float], str], mutate: Callable[[str, str], str], max_rounds: int = 5) -> OptimizationResult:
        if max_rounds < 1:
            raise ValueError("max_rounds must be positive")
        current = artifact
        baseline = self._score(evaluate(current))
        score = baseline
        rounds: list[OptimizationRound] = []
        for number in range(1, max_rounds + 1):
            diagnosis = diagnose(current, score).strip()
            if not diagnosis:
                break
            candidate = mutate(current, diagnosis)
            if not isinstance(candidate, str) or not candidate.strip():
                raise ValueError("mutate must return a non-empty artifact")
            if candidate == current:
                rounds.append(OptimizationRound(number, score, False, diagnosis, self._digest_change(current, candidate)))
                break
            candidate_score = self._score(evaluate(candidate))
            kept = candidate_score > score
            before = current
            if kept:
                current, score = candidate, candidate_score
            rounds.append(OptimizationRound(number, candidate_score, kept, diagnosis, self._digest_change(before, candidate)))
        return OptimizationResult(baseline, score, tuple(rounds), current)

    @staticmethod
    def _score(value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("evaluation score must be between 0 and 1")
        return float(value)

    @staticmethod
    def _digest_change(before: str, after: str) -> str:
        return hashlib.sha256(f"{before}\n---\n{after}".encode()).hexdigest()[:16]


@dataclass(frozen=True)
class ScopeFinding:
    path: str
    relatedness: float
    classification: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScopeReport:
    findings: tuple[ScopeFinding, ...]
    likely_creep: int
    digest: str


class ScopeChecker:
    """Flag likely scope drift; policy remains the authoritative decision-maker."""
    def check(self, intent: str, changed_paths: Sequence[str]) -> ScopeReport:
        if not intent.strip():
            raise ValueError("intent is required")
        intent_tokens = _tokens(intent)
        findings: list[ScopeFinding] = []
        for path in changed_paths:
            path_tokens = _tokens(path)
            shared = len(intent_tokens.intersection(path_tokens))
            relatedness = shared / max(1, len(intent_tokens))
            reasons: list[str] = []
            lower = path.lower()
            if lower.endswith((".lock", "requirements.txt", "pyproject.toml", "package.json", "package-lock.json")):
                reasons.append("dependency manifest changed")
            if lower.startswith(".github/") or "/.github/" in lower or "/ci/" in lower:
                reasons.append("CI/configuration surface changed")
            if not path_tokens.intersection(intent_tokens):
                reasons.append("no shared intent/path tokens")
            classification = "likely_creep" if reasons and "no shared intent/path tokens" in reasons else "in_scope"
            findings.append(ScopeFinding(path, round(relatedness, 4), classification, tuple(reasons)))
        findings.sort(key=lambda item: (item.classification != "likely_creep", item.path))
        digest = hashlib.sha256("|".join(f"{x.path}:{x.classification}" for x in findings).encode()).hexdigest()
        return ScopeReport(tuple(findings), sum(x.classification == "likely_creep" for x in findings), digest)


__all__ = [
    "ConsensusResult", "EvidenceItem", "MixtureOfAgents", "OneChangeOptimizer", "OptimizationResult", "OptimizationRound",
    "ResearchPlan", "ResearchPlanner", "ResearchTask", "RouteDecision", "RouteRequest", "ScopeChecker", "ScopeFinding", "ScopeReport",
    "Specialist", "SpecialistRouter",
]
