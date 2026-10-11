"""Explicit StateGraph execution and specialist-crew routing contracts for AUREN."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Callable, Mapping

from .orchestration import Graph, Node, NodeKind, NodeStatus, OrchestrationRun, Orchestrator

_FORBIDDEN_CREW_AUTHORITIES = frozenset({
    "merge", "promote", "self_approve", "approve_own_work", "bypass_review",
    "disable_guard", "disable_rollback", "rewrite_evidence", "forge_attestation",
})


@dataclass(frozen=True)
class StateTransition:
    source: str
    target: str
    condition: Callable[[Mapping[str, Any]], bool] | None = None


@dataclass(frozen=True, slots=True)
class CrewDefinition:
    """Role contract for a specialist crew; it never grants supervisor authority."""

    crew_id: str
    specialty: str
    triggers: tuple[str, ...]
    instructions: str
    required_artifacts: tuple[str, ...]
    allowed_authorities: tuple[str, ...]
    acceptance_checks: tuple[str, ...]
    handoffs_to: tuple[str, ...] = ()
    always_on: bool = False
    independent_review_required: bool = True

    def __post_init__(self) -> None:
        for name, value in (("crew_id", self.crew_id), ("specialty", self.specialty), ("instructions", self.instructions)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty text")
        for name, values in (
            ("triggers", self.triggers), ("required_artifacts", self.required_artifacts),
            ("allowed_authorities", self.allowed_authorities), ("acceptance_checks", self.acceptance_checks),
            ("handoffs_to", self.handoffs_to),
        ):
            if not isinstance(values, tuple) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError(f"{name} must be a tuple of non-empty strings")
        forbidden = _FORBIDDEN_CREW_AUTHORITIES.intersection(x.strip().lower() for x in self.allowed_authorities)
        if forbidden:
            raise ValueError("crew cannot be granted supervisor-only authority: " + ", ".join(sorted(forbidden)))
        if type(self.always_on) is not bool or type(self.independent_review_required) is not bool:
            raise ValueError("crew policy flags must be booleans")


@dataclass(frozen=True, slots=True)
class CrewAssignment:
    crew_id: str
    matched_triggers: tuple[str, ...]
    agent_registered: bool


@dataclass(frozen=True, slots=True)
class CrewPlan:
    graph_digest: str
    intent_digest: str
    assignments: tuple[CrewAssignment, ...]

    @property
    def digest(self) -> str:
        payload = {
            "schema_version": "auren-crew-plan-v1",
            "graph_digest": self.graph_digest,
            "intent_digest": self.intent_digest,
            "assignments": [asdict(item) for item in self.assignments],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "auren-crew-plan-v1",
            "graph_digest": self.graph_digest,
            "intent_digest": self.intent_digest,
            "assignments": [asdict(item) for item in self.assignments],
            "digest": self.digest,
        }


def _default_crews() -> tuple[CrewDefinition, ...]:
    return (
        CrewDefinition(
            "product_pm", "Product management", ("product", "requirements", "scope", "acceptance", "roadmap"),
            "Clarify outcome, scope, user impact, dependencies, and acceptance criteria before implementation.",
            ("requirements", "dependency map", "acceptance criteria"),
            ("clarify_scope", "prioritize", "propose_plan"),
            ("scope is explicit", "dependencies are represented in the graph", "acceptance criteria are testable"),
            ("backend_api", "end_user_validation"), True,
        ),
        CrewDefinition(
            "backend_api", "Backend and API engineering", ("backend", "api", "service", "endpoint", "server", "integration"),
            "Design and implement bounded service/API changes; preserve compatibility, observability, and failure semantics.",
            ("implementation proposal", "API/schema contract", "tests", "evidence"),
            ("inspect_code", "propose_changes", "implement_changes", "run_tests"),
            ("contracts and errors are explicit", "tests cover success and failure", "no secret or authority bypass"),
            ("qa_reviewer", "security_privacy"), True,
        ),
        CrewDefinition(
            "frontend_ui", "Frontend and UI engineering", ("frontend", "front-end", "ui", "ux", "web", "screen", "component", "accessibility"),
            "Implement accessible UI behavior with clear loading, empty, success, and error states.",
            ("UI change", "accessibility checks", "interaction tests"),
            ("inspect_ui", "propose_changes", "implement_changes", "run_ui_tests"),
            ("keyboard and accessibility behavior is checked", "error states are represented", "UI tests cover critical flows"),
            ("backend_api", "qa_reviewer"),
        ),
        CrewDefinition(
            "database_schema", "Database and data engineering", ("database", "dba", "schema", "sql", "migration", "query", "index", "persistence"),
            "Review data integrity, query plans, migration safety, rollback, and backward compatibility.",
            ("schema/query proposal", "migration", "rollback plan", "data-integrity tests"),
            ("inspect_schema", "propose_migration", "run_query_tests"),
            ("migration is reversible or recovery is documented", "integrity constraints are tested", "query behavior is bounded"),
            ("backend_api", "qa_reviewer"),
        ),
        CrewDefinition(
            "dotnet", "C# and .NET engineering", ("c#", "csharp", ".net", "dotnet", "asp.net", "nuget", "entity framework"),
            "Apply idiomatic C#/.NET patterns with explicit cancellation, async behavior, dependency injection, and testable contracts.",
            ("C#/.NET implementation", "build/test evidence", "compatibility notes"),
            ("inspect_dotnet", "propose_changes", "implement_changes", "run_dotnet_tests"),
            ("build succeeds", "async/cancellation paths are safe", "API compatibility is checked"),
            ("backend_api", "qa_reviewer"),
        ),
        CrewDefinition(
            "security_privacy", "Security and privacy", ("security", "privacy", "auth", "authorization", "permission", "secret", "token", "threat", "tenant", "injection"),
            "Threat-model consequential changes; minimize privilege and data exposure; verify fail-closed and rollback behavior.",
            ("threat model", "security tests", "data-flow/authority review", "residual risks"),
            ("inspect_security", "propose_mitigations", "run_security_tests", "block_unsafe_change"),
            ("trust boundaries are explicit", "negative tests cover bypass attempts", "secrets and tenant data are isolated"),
            ("qa_reviewer",), False,
        ),
        CrewDefinition(
            "qa_reviewer", "Independent QA and code review", ("test", "review", "regression", "release", "merge", "quality"),
            "Independently challenge implementation claims, verify tests and evidence, and request changes when criteria are unmet.",
            ("review findings", "regression results", "accept/reject recommendation"),
            ("inspect_diff", "run_tests", "request_changes", "recommend_acceptance"),
            ("tests are tied to requirements", "failure paths and regressions are covered", "review is independent of author"),
            ("product_pm", "end_user_validation"), True,
        ),
        CrewDefinition(
            "end_user_validation", "End-user validation", ("user", "customer", "workflow", "usability", "journey", "acceptance"),
            "Validate the end-to-end workflow against real user goals, including confusion, recovery, and accessibility.",
            ("user journey", "acceptance observations", "usability gaps"),
            ("review_workflow", "exercise_acceptance_cases", "report_blockers"),
            ("critical journey is executable", "recovery path is understandable", "acceptance gaps are recorded"),
            ("product_pm",), True,
        ),
    )


class StateGraph:
    """Persistent-supervisor-friendly state machine over the existing Graph primitive."""

    def __init__(self, graph: Graph, transitions: tuple[StateTransition, ...] = ()) -> None:
        self.graph = graph
        self.transitions = transitions
        self.states = tuple(node.name for node in graph.order())
        self._validate_transitions()

    @classmethod
    def from_nodes(cls, nodes: list[Node], transitions: tuple[StateTransition, ...] = ()) -> "StateGraph":
        return cls(Graph(nodes), transitions)

    def _validate_transitions(self) -> None:
        names = set(self.states)
        for transition in self.transitions:
            if transition.source not in names or transition.target not in names:
                raise ValueError(f"invalid StateGraph transition: {transition.source}->{transition.target}")

    def digest(self) -> str:
        payload = {"graph": self.graph.digest(), "transitions": [(t.source, t.target) for t in self.transitions]}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def run(self, task_id: str, intent: str, context: Mapping[str, Any] | None = None, *,
            max_total_attempts: int = 32) -> OrchestrationRun:
        return Orchestrator(self.graph, max_total_attempts=max_total_attempts).run(task_id, intent, context)


class GraphAgentTeam:
    """Crew-routing facade under StateGraph; the graph remains the execution authority."""

    def __init__(
        self,
        graph: StateGraph,
        agents: Mapping[str, Any] | None = None,
        crews: Mapping[str, CrewDefinition] | None = None,
    ) -> None:
        self.graph = graph
        self.agents = dict(agents or {})
        self.crews = {crew.crew_id: crew for crew in _default_crews()}
        for crew_id, crew in (crews or {}).items():
            if not isinstance(crew, CrewDefinition) or crew_id != crew.crew_id:
                raise ValueError("custom crews must be CrewDefinition values keyed by their crew_id")
            self.crews[crew_id] = crew

    def add_agent(self, name: str, agent: Any) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("agent name is required")
        self.agents[name] = agent

    def add_crew(self, crew: CrewDefinition) -> None:
        if not isinstance(crew, CrewDefinition):
            raise TypeError("crew must be a CrewDefinition")
        self.crews[crew.crew_id] = crew

    def plan_crews(self, goal: str) -> CrewPlan:
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must be non-empty text")
        text = goal.lower()
        selected: list[CrewAssignment] = []
        for crew in self.crews.values():
            matched = tuple(trigger for trigger in crew.triggers if trigger in text)
            if crew.always_on or matched:
                selected.append(CrewAssignment(crew.crew_id, matched, crew.crew_id in self.agents))
        # Keep routing deterministic even when callers register custom crews in a different order.
        selected.sort(key=lambda item: (0 if self.crews[item.crew_id].always_on else 1, item.crew_id))
        return CrewPlan(
            graph_digest=self.graph.digest(),
            intent_digest=hashlib.sha256(goal.strip().encode("utf-8")).hexdigest(),
            assignments=tuple(selected),
        )

    def required_personas(self, goal: str) -> tuple[str, ...]:
        aliases = {
            "product_pm": "pm", "backend_api": "backend", "frontend_ui": "frontend",
            "database_schema": "dba", "dotnet": "dotnet", "security_privacy": "security_privacy",
            "qa_reviewer": "reviewer", "end_user_validation": "enduser",
        }
        return tuple(aliases[item.crew_id] for item in self.plan_crews(goal).assignments)

    def run(self, task_id: str, intent: str, context: Mapping[str, Any] | None = None) -> OrchestrationRun:
        plan = self.plan_crews(intent)
        state = dict(context or {})
        # Reserved fields are overwritten, so caller payloads cannot self-authorize
        # a different crew plan or broaden the declared crew authority.
        state["auren_crew_plan"] = plan.to_payload()
        selected_ids = {assignment.crew_id for assignment in plan.assignments}
        state["auren_crew_agents"] = {key: value for key, value in self.agents.items() if key in selected_ids}
        state["auren_crew_guardrails"] = {
            "execution_authority": "StateGraph",
            "crews_may_self_approve": False,
            "merge_or_promotion_authority": False,
            "required_independent_review": True,
            "required_rollback_for_consequential_changes": True,
            "forbidden_authorities": sorted(_FORBIDDEN_CREW_AUTHORITIES),
        }
        return self.graph.run(task_id, intent, state)


__all__ = [
    "StateGraph", "StateTransition", "CrewDefinition", "CrewAssignment", "CrewPlan", "GraphAgentTeam",
]
