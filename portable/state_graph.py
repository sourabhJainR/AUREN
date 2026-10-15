"""Explicit StateGraph execution and specialist-crew routing contracts for AUREN."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any, Callable, Mapping

from .orchestration import Graph, Node, NodeKind, NodeStatus, OrchestrationRun, Orchestrator
from .crew_adapters import CrewHandoff, GraphHandoffController, TaskRetryBudget, rollback_readiness
from .runtime_recovery import RuntimeExecutionGuard

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
        normalized_authorities = {x.strip().lower().replace("-", "_").replace(" ", "_") for x in self.allowed_authorities}
        forbidden = {
            authority for authority in normalized_authorities
            if authority in _FORBIDDEN_CREW_AUTHORITIES
            or authority.startswith(("merge_", "promote_", "approve_", "disable_guard", "disable_rollback", "forge_attestation", "rewrite_evidence"))
            or "bypass_review" in authority
        }
        if forbidden:
            raise ValueError("crew cannot be granted supervisor-only authority: " + ", ".join(sorted(forbidden)))
        if type(self.always_on) is not bool or type(self.independent_review_required) is not bool:
            raise ValueError("crew policy flags must be booleans")


@dataclass(frozen=True, slots=True)
class CrewAssignment:
    crew_id: str
    matched_triggers: tuple[str, ...]
    agent_registered: bool
    contract_digest: str
    agent_config_digest: str = ""


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
            "python", "Python engineering", ("python", "pytest", "pyproject", "pip", "pydantic", "django", "fastapi"),
            "Build maintainable Python modules and services; use typing, clear dependency boundaries, robust error handling, and focused tests.",
            ("Python implementation proposal", "type/lint/test evidence", "dependency and compatibility notes"),
            ("inspect_python", "propose_changes", "implement_changes", "run_python_tests"),
            ("supported Python versions are explicit", "types and failure paths are covered", "tests and dependency checks pass"),
            ("backend_api", "qa_reviewer"),
        ),
        CrewDefinition(
            "rust", "Rust engineering", ("rust", "rustc", "cargo", "crate", "borrow checker", "rustfmt", "clippy"),
            "Use idiomatic Rust with explicit ownership, borrowing, lifetimes, error types, safe concurrency, and reproducible Cargo validation.",
            ("Rust implementation proposal", "cargo test/check evidence", "safety and compatibility notes"),
            ("inspect_rust", "propose_changes", "implement_changes", "run_cargo_tests"),
            ("ownership and lifetime behavior is sound", "unsafe blocks are justified and bounded", "cargo checks and tests pass"),
            ("backend_api", "qa_reviewer", "security_privacy"),
        ),
        CrewDefinition(
            "java", "Java and JVM engineering", ("java", "jvm", "spring boot", "maven", "gradle", "junit", "javac"),
            "Implement idiomatic Java/JVM changes with explicit API contracts, resource lifecycle, concurrency behavior, and build-tool-aligned tests.",
            ("Java implementation proposal", "build/test evidence", "API and runtime compatibility notes"),
            ("inspect_java", "propose_changes", "implement_changes", "run_java_tests"),
            ("supported JDK and build versions are explicit", "exceptions and resource handling are safe", "unit/integration tests pass"),
            ("backend_api", "qa_reviewer"),
        ),
        CrewDefinition(
            "react", "React frontend engineering", ("react", "jsx", "tsx", "react hooks", "react component", "next.js"),
            "Build composable React interfaces with correct hooks, state boundaries, accessible interactions, and component-level tests.",
            ("React component proposal", "interaction/accessibility tests", "state and rendering notes"),
            ("inspect_react", "propose_changes", "implement_changes", "run_react_tests"),
            ("hooks rules are respected", "loading/error/empty states are handled", "critical interactions and accessibility are tested"),
            ("frontend_ui", "qa_reviewer", "end_user_validation"),
        ),
        CrewDefinition(
            "angular", "Angular frontend engineering", ("angular", "ng cli", "rxjs", "angular component", "angular service", "angular signals"),
            "Implement Angular features using typed components, dependency injection, RxJS or signals as appropriate, accessible templates, and testable services.",
            ("Angular implementation proposal", "component/service tests", "template and dependency notes"),
            ("inspect_angular", "propose_changes", "implement_changes", "run_angular_tests"),
            ("subscriptions and lifecycle are safe", "templates are typed and accessible", "unit/build tests pass"),
            ("frontend_ui", "qa_reviewer", "end_user_validation"),
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



def default_crew_definitions() -> tuple[CrewDefinition, ...]:
    """Return the canonical immutable specialist contract catalogue."""
    return _default_crews()


def build_crew_plan(
    goal: str,
    *,
    graph_digest: str,
    intent_digest: str,
    agents: Mapping[str, Any],
    crews: Mapping[str, CrewDefinition] | None = None,
) -> CrewPlan:
    """Build the same deterministic crew plan for either supported graph adapter."""
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("goal must be non-empty text")
    if not isinstance(graph_digest, str) or not graph_digest.strip():
        raise ValueError("graph_digest is required")
    if not isinstance(intent_digest, str) or not intent_digest.strip():
        raise ValueError("intent_digest is required")
    definitions = dict(crews or {crew.crew_id: crew for crew in _default_crews()})
    text = goal.lower()
    selected: list[CrewAssignment] = []
    for crew in definitions.values():
        matched = tuple(
            trigger for trigger in crew.triggers
            if re.search(r"(?<![a-z0-9])" + re.escape(trigger.lower()) + r"(?![a-z0-9])", text)
        )
        if crew.always_on or matched:
            contract = {
                "crew_id": crew.crew_id, "specialty": crew.specialty,
                "triggers": crew.triggers, "instructions": crew.instructions,
                "required_artifacts": crew.required_artifacts,
                "allowed_authorities": crew.allowed_authorities,
                "acceptance_checks": crew.acceptance_checks,
                "handoffs_to": crew.handoffs_to,
                "always_on": crew.always_on,
                "independent_review_required": crew.independent_review_required,
            }
            contract_digest = hashlib.sha256(
                json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            agent = agents.get(crew.crew_id)
            agent_config_digest = getattr(agent, "configuration_digest", "") if agent is not None else ""
            if not isinstance(agent_config_digest, str):
                agent_config_digest = ""
            selected.append(CrewAssignment(
                crew_id=crew.crew_id,
                matched_triggers=matched,
                agent_registered=agent is not None,
                contract_digest=contract_digest,
                agent_config_digest=agent_config_digest,
            ))
    selected.sort(key=lambda item: (0 if definitions[item.crew_id].always_on else 1, item.crew_id))
    return CrewPlan(graph_digest=graph_digest, intent_digest=intent_digest, assignments=tuple(selected))


def _dispatch_crew_proposal(
    crew_id: str,
    task: str,
    evidence: Mapping[str, Any] | None,
    *,
    active: bool,
    plan: CrewPlan,
    graph_digest: str,
    agents: Mapping[str, Any],
    crews: Mapping[str, CrewDefinition],
) -> dict[str, Any]:
    """Validate and invoke one selected proposal adapter without granting execution authority."""
    if type(active) is not bool:
        raise ValueError("active must be a boolean")
    if not isinstance(plan, CrewPlan):
        raise TypeError("plan must be a CrewPlan")
    if not active:
        raise RuntimeError("crew dispatch is only available during graph execution")
    selected_ids = {assignment.crew_id for assignment in plan.assignments}
    if crew_id not in selected_ids:
        raise ValueError(f"crew {crew_id} is not assigned to this graph task")
    agent = agents.get(crew_id)
    if agent is None:
        raise ValueError(f"no registered proposal agent for crew {crew_id}")
    proposer = getattr(agent, "propose", None)
    if not callable(proposer):
        raise TypeError("crew agents must expose a side-effect-free propose(request) interface")
    if not isinstance(task, str) or not task.strip():
        raise ValueError("crew task must be non-empty text")
    if evidence is not None and not isinstance(evidence, Mapping):
        raise ValueError("crew evidence must be a mapping")
    definition = crews[crew_id]
    request = {
        "crew_id": crew_id,
        "specialty": definition.specialty,
        "instructions": definition.instructions,
        "task": task,
        "evidence": dict(evidence or {}),
        "required_artifacts": list(definition.required_artifacts),
        "acceptance_checks": list(definition.acceptance_checks),
        "allowed_authorities": list(definition.allowed_authorities),
        "graph_digest": graph_digest,
        "crew_plan_digest": plan.digest,
    }
    try:
        json.dumps(request, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("crew proposal request must be JSON-serializable") from exc
    proposal = proposer(request)
    if not isinstance(proposal, Mapping):
        raise ValueError("crew proposal must be a structured mapping")
    required_fields = {
        "schema_version", "recommendation", "artifacts", "evidence_refs",
        "uncertainty", "blockers", "acceptance_check_results",
    }
    proposal_fields = set(proposal)
    if proposal_fields not in (required_fields, required_fields | {"handoff_requests"}):
        raise ValueError("crew proposal fields do not match the required structured schema")
    handoff_requests = proposal.get("handoff_requests", [])
    if not isinstance(handoff_requests, list) or len(handoff_requests) > 8:
        raise ValueError("crew proposal handoff_requests must be a list with at most 8 items")
    for request in handoff_requests:
        if not isinstance(request, Mapping) or set(request) != {"target_crew", "task"}:
            raise ValueError("each handoff request must contain only target_crew and task")
        if any(not isinstance(request.get(key), str) or not request[key].strip() for key in ("target_crew", "task")):
            raise ValueError("handoff request target_crew and task must be non-empty text")
        if len(request["task"]) > 2000:
            raise ValueError("handoff request task exceeds the 2000 character limit")
    if proposal.get("schema_version") != "auren-crew-proposal-v1":
        raise ValueError("unsupported crew proposal schema version")
    if not isinstance(proposal.get("recommendation"), str) or not proposal["recommendation"].strip():
        raise ValueError("crew proposal recommendation must be non-empty text")
    for field in ("artifacts", "evidence_refs", "uncertainty", "blockers"):
        values = proposal.get(field)
        if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError(f"crew proposal {field} must be a list of non-empty strings")
    checks = proposal.get("acceptance_check_results")
    if not isinstance(checks, Mapping) or set(checks) != set(definition.acceptance_checks):
        raise ValueError("crew proposal must report every declared acceptance check")
    for check_name, result in checks.items():
        if not isinstance(result, Mapping) or set(result) != {"status", "evidence_refs"}:
            raise ValueError(f"acceptance result for {check_name} must include status and evidence_refs")
        if result.get("status") not in {"pass", "fail", "blocked", "not_run"}:
            raise ValueError(f"acceptance result for {check_name} has an invalid status")
        refs = result.get("evidence_refs")
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
            raise ValueError(f"acceptance evidence refs for {check_name} must be a list of non-empty strings")
        if result["status"] == "pass" and not refs:
            raise ValueError(f"acceptance check {check_name} cannot pass without evidence references")
    # Bound proposal payloads before they enter checkpointed graph state.
    payload = dict(proposal)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if len(encoded) > 32_000:
        raise ValueError("crew proposal exceeds the 32 KB structured proposal limit")
    adapter_digest = str(getattr(agent, "configuration_digest", ""))
    implementation_version = str(getattr(agent, "implementation_version", "unversioned"))
    digest_payload = {
        "crew_id": crew_id,
        "proposal": payload,
        "adapter_configuration_digest": adapter_digest,
        "implementation_version": implementation_version,
        "graph_digest": graph_digest,
        "intent_digest": plan.intent_digest,
        "crew_plan_digest": plan.digest,
    }
    proposal_digest = hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "crew_id": crew_id,
        "proposal": payload,
        "proposal_digest": proposal_digest,
        "adapter_configuration_digest": adapter_digest,
        "implementation_version": implementation_version,
        "graph_digest": graph_digest,
        "intent_digest": plan.intent_digest,
        "crew_plan_digest": plan.digest,
        "review_required": True,
        "review_status": "pending",
        "acceptance_checks": list(definition.acceptance_checks),
        "self_attested_acceptance_is_not_authoritative": True,
    }


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
        *,
        handoff_checkpoint_writer: Callable[[str, Mapping[str, Any]], None] | None = None,
        require_durable_handoffs: bool = False,
        independent_handoff_reviewer: Callable[[CrewHandoff], Mapping[str, Any]] | None = None,
        independent_handoff_reviewer_id: str | None = None,
        independent_handoff_reviewer_version: str | None = None,
    ) -> None:
        if handoff_checkpoint_writer is not None and not callable(handoff_checkpoint_writer):
            raise TypeError("handoff_checkpoint_writer must be callable")
        if type(require_durable_handoffs) is not bool:
            raise ValueError("require_durable_handoffs must be a boolean")
        if independent_handoff_reviewer is not None and not callable(independent_handoff_reviewer):
            raise TypeError("independent_handoff_reviewer must be callable")
        if independent_handoff_reviewer is not None and (
            not isinstance(independent_handoff_reviewer_id, str) or not independent_handoff_reviewer_id.strip()
            or not isinstance(independent_handoff_reviewer_version, str) or not independent_handoff_reviewer_version.strip()
        ):
            raise ValueError("independent reviewer id and version are required when a reviewer is configured")
        if independent_handoff_reviewer is None and (
            independent_handoff_reviewer_id not in (None, "") or independent_handoff_reviewer_version not in (None, "")
        ):
            raise ValueError("reviewer identity cannot be set without an independent handoff reviewer")
        self.graph = graph
        self.handoff_checkpoint_writer = handoff_checkpoint_writer
        self.require_durable_handoffs = require_durable_handoffs
        self.independent_handoff_reviewer = independent_handoff_reviewer
        self.independent_handoff_reviewer_id = independent_handoff_reviewer_id.strip() if independent_handoff_reviewer_id else "unconfigured"
        self.independent_handoff_reviewer_version = independent_handoff_reviewer_version.strip() if independent_handoff_reviewer_version else "unconfigured"
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
        graph_contract_digest = hashlib.sha256(json.dumps({
            "graph_digest": self.graph.digest(),
            "independent_handoff_reviewer_id": self.independent_handoff_reviewer_id,
            "independent_handoff_reviewer_version": self.independent_handoff_reviewer_version,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return build_crew_plan(
            goal,
            graph_digest=graph_contract_digest,
            intent_digest=hashlib.sha256(goal.strip().encode("utf-8")).hexdigest() if isinstance(goal, str) else "",
            agents=self.agents,
            crews=self.crews,
        )

    def required_personas(self, goal: str) -> tuple[str, ...]:
        selected = {item.crew_id for item in self.plan_crews(goal).assignments}
        personas = ["pm", "backend", "reviewer", "enduser"]
        if "frontend_ui" in selected:
            personas.insert(1, "frontend")
        if "database_schema" in selected:
            personas.insert(1, "dba")
        if "security_privacy" in selected:
            personas.insert(1, "security_privacy")
        if "dotnet" in selected:
            personas.insert(personas.index("backend") + 1, "dotnet")
        for crew_id, persona in (
            ("python", "python"), ("rust", "rust"), ("java", "java"),
            ("react", "react"), ("angular", "angular"),
        ):
            if crew_id in selected:
                personas.insert(personas.index("backend") + 1, persona)
        return tuple(dict.fromkeys(personas))

    def run(
        self,
        task_id: str,
        intent: str,
        context: Mapping[str, Any] | None = None,
        *,
        handoff_checkpoint_writer: Callable[[str, Mapping[str, Any]], None] | None = None,
        require_durable_handoffs: bool | None = None,
    ) -> OrchestrationRun:
        plan = self.plan_crews(intent)
        checkpoint_writer = self.handoff_checkpoint_writer if handoff_checkpoint_writer is None else handoff_checkpoint_writer
        require_persistence = self.require_durable_handoffs if require_durable_handoffs is None else require_durable_handoffs
        if type(require_persistence) is not bool:
            raise ValueError("require_durable_handoffs must be a boolean")
        if checkpoint_writer is not None and not callable(checkpoint_writer):
            raise TypeError("handoff_checkpoint_writer must be callable")
        if require_persistence and checkpoint_writer is None:
            raise ValueError("durable handoff checkpoint writer is required by task policy")
        state = dict(context or {})
        # Provider checkpoint callbacks/state are supervisor-owned; never accept
        # caller-supplied resume data as authoritative.
        state.pop("auren_provider_resume_state", None)
        state.pop("auren_provider_resume_persist", None)
        supervisor_envelope = state.pop("auren_supervisor_checkpoint_envelope", None)
        if supervisor_envelope is not None:
            if not isinstance(supervisor_envelope, Mapping):
                raise ValueError("supervisor handoff checkpoint envelope must be a mapping")
            if supervisor_envelope.get("schema_version") != "auren-task-handoff-envelope-v1":
                raise ValueError("unsupported supervisor handoff checkpoint envelope")
            if supervisor_envelope.get("task_id") != task_id:
                raise ValueError("supervisor checkpoint task_id does not match active task")
            if supervisor_envelope.get("intent_digest") != plan.intent_digest:
                raise ValueError("supervisor checkpoint intent digest does not match active task")
            if supervisor_envelope.get("crew_plan_digest") != plan.digest:
                raise ValueError("supervisor checkpoint crew-plan digest does not match active plan")
            saved_handoffs = supervisor_envelope.get("handoff_checkpoint")
            saved_budget = supervisor_envelope.get("retry_budget")
            saved_provider_resume = supervisor_envelope.get("provider_resume_state")
            saved_runtime_guard = supervisor_envelope.get("runtime_guard")
            if not isinstance(saved_handoffs, Mapping) or not isinstance(saved_budget, Mapping):
                raise ValueError("supervisor checkpoint requires handoff and retry-budget snapshots")
            if saved_provider_resume is not None and not isinstance(saved_provider_resume, Mapping):
                raise ValueError("supervisor provider resume state must be a mapping")
            if saved_runtime_guard is not None and not isinstance(saved_runtime_guard, Mapping):
                raise ValueError("supervisor runtime guard checkpoint must be a mapping")
            # The supervisor-provided checkpoint is authoritative over caller state.
            state["auren_crew_handoff_checkpoint_data"] = dict(saved_handoffs)
            state["auren_retry_budget"] = dict(saved_budget)
            if isinstance(saved_runtime_guard, Mapping):
                state["auren_runtime_guard"] = dict(saved_runtime_guard)
            elif require_persistence:
                raise ValueError("durable resume checkpoint lacks runtime recovery budget; explicit recovery is required")
            if isinstance(saved_provider_resume, Mapping):
                state["auren_provider_resume_state"] = dict(saved_provider_resume)
        # Reserved fields are overwritten, so caller payloads cannot self-authorize
        # a different crew plan or broaden the declared crew authority.
        state["auren_crew_plan"] = plan.to_payload()
        selected_ids = {assignment.crew_id for assignment in plan.assignments}
        state["auren_crew_guardrails"] = {
            "execution_authority": "StateGraph",
            "crews_may_self_approve": False,
            "merge_or_promotion_authority": False,
            "required_independent_review": True,
            "required_rollback_for_consequential_changes": True,
            "forbidden_authorities": sorted(_FORBIDDEN_CREW_AUTHORITIES),
        }
        dispatch_active = {"value": True}

        def dispatch_crew(crew_id: str, task: str, evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
            return _dispatch_crew_proposal(
                crew_id, task, evidence,
                active=dispatch_active["value"],
                plan=plan,
                graph_digest=self.graph.digest(),
                agents=self.agents,
                crews=self.crews,
            )

        # Handoffs and retry charges live in the supervisor-owned checkpoint,
        # not in a separate queue database.
        checkpoint = state.get("auren_crew_handoff_checkpoint_data", state.get("auren_crew_handoff_checkpoint"))
        if checkpoint is not None and not isinstance(checkpoint, Mapping):
            raise ValueError("crew handoff checkpoint must be a serialized mapping")
        saved_budget = state.get("auren_retry_budget")
        if isinstance(saved_budget, Mapping):
            retry_budget = TaskRetryBudget.resume(saved_budget)
        elif isinstance(checkpoint, Mapping) and isinstance(checkpoint.get("retry_budget"), Mapping):
            retry_budget = TaskRetryBudget.resume(checkpoint["retry_budget"])
        else:
            retry_budget = TaskRetryBudget(max_retries=state.get("auren_retry_budget_limit", 3))

        runtime_snapshot = state.get("auren_runtime_guard")
        runtime_guard = (
            RuntimeExecutionGuard.resume(runtime_snapshot)
            if isinstance(runtime_snapshot, Mapping)
            else RuntimeExecutionGuard(max_steps=max(1, len(self.graph.states)), max_attempts=32)
        )
        state["_auren_runtime_execution_guard"] = runtime_guard

        def persist_handoff_checkpoint(snapshot: Mapping[str, Any]) -> None:
            # Reconcile all retry charges from the durable task ledger into the
            # same runtime budget used by graph nodes. This is monotonic across resume.
            for charge in retry_budget.snapshot()["charges"]:
                runtime_guard.charge_attempt("shared-retry:" + charge["operation_key"])
            state["auren_runtime_guard"] = runtime_guard.snapshot()
            state["auren_crew_handoff_checkpoint_data"] = dict(snapshot)
            state["auren_retry_budget"] = retry_budget.snapshot()
            if checkpoint_writer is not None:
                checkpoint_writer(task_id, {
                    "schema_version": "auren-task-handoff-envelope-v1",
                    "handoff_checkpoint": dict(snapshot),
                    "retry_budget": retry_budget.snapshot(),
                    **({"runtime_guard": dict(state["auren_runtime_guard"])}
                       if isinstance(state.get("auren_runtime_guard"), Mapping) else {}),
                    "task_id": task_id,
                    "intent_digest": plan.intent_digest,
                    "crew_plan_digest": plan.digest,
                    **({"provider_resume_state": dict(state["auren_provider_resume_state"])}
                       if isinstance(state.get("auren_provider_resume_state"), Mapping) else {}),
                })

        persist_callback = persist_handoff_checkpoint if checkpoint_writer is not None else None
        handoffs = (
            GraphHandoffController.resume(
                checkpoint, retry_budget=retry_budget, persist=persist_callback,
                require_persistence=require_persistence,
            )
            if checkpoint is not None
            else GraphHandoffController(
                max_attempts=3, retry_budget=retry_budget, persist=persist_callback,
                require_persistence=require_persistence,
            )
        )

        def refresh_checkpoint_state() -> None:
            state["auren_crew_handoff_checkpoint_data"] = handoffs.snapshot()
            state["auren_retry_budget"] = retry_budget.snapshot()

        def persist_provider_resume(snapshot: Mapping[str, Any]) -> None:
            if not dispatch_active["value"]:
                raise RuntimeError("provider resume checkpoints may only be persisted during graph execution")
            if not isinstance(snapshot, Mapping):
                raise ValueError("provider resume checkpoint must be a mapping")
            provider_budget = snapshot.get("retry_budget")
            if not isinstance(provider_budget, Mapping):
                raise ValueError("provider resume checkpoint requires the shared retry budget")
            # Reconcile charges monotonically into the graph-owned budget before
            # persisting the same snapshot that handoffs and review receipts use.
            retry_budget.merge_snapshot(provider_budget)
            state["auren_retry_budget"] = retry_budget.snapshot()
            supplied = snapshot.get("digest")
            payload = {key: value for key, value in snapshot.items() if key != "digest"}
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
            expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if not isinstance(supplied, str) or supplied != expected:
                raise ValueError("provider resume checkpoint digest mismatch")
            if snapshot.get("task_id") != task_id:
                raise ValueError("provider resume checkpoint task_id does not match active graph task")
            state["auren_provider_resume_state"] = dict(snapshot)
            # Persist through the same atomic supervisor envelope as handoffs,
            # review receipts, and the task-scoped retry budget.
            persist_handoff_checkpoint(handoffs.snapshot())

        def enqueue_handoff(
            source_crew: str, target_crew: str, task: str, source_proposal_digest: str = ""
        ) -> dict[str, Any]:
            if not dispatch_active["value"]:
                raise RuntimeError("handoffs may only be enqueued during graph execution")
            if require_persistence and not source_proposal_digest:
                raise ValueError("durable handoffs require the source proposal digest")
            if source_crew not in selected_ids:
                raise ValueError(f"handoff source crew {source_crew} is not assigned to this graph task")
            source = self.crews[source_crew]
            item = handoffs.enqueue(
                source_crew=source_crew,
                target_crew=target_crew,
                task=task,
                source_plan_digest=plan.digest,
                allowed_handoffs=source.handoffs_to,
                selected_crews=tuple(selected_ids),
                source_proposal_digest=source_proposal_digest,
            )
            refresh_checkpoint_state()
            if item is None:
                return {
                    "source_crew": source_crew,
                    "target_crew": target_crew,
                    "task": task.strip(),
                    "source_plan_digest": plan.digest,
                    "attempt": 0,
                    "handoff_id": handoffs.handoff_id_for(source_crew, target_crew, task, plan.digest, source_proposal_digest),
                    "source_proposal_digest": source_proposal_digest,
                    "status": "already_completed",
                }
            return {
                "source_crew": item.source_crew,
                "target_crew": item.target_crew,
                "task": item.task,
                "source_plan_digest": item.source_plan_digest,
                "attempt": item.attempt,
                "handoff_id": item.handoff_id,
                "source_proposal_digest": item.source_proposal_digest,
                "status": "queued",
            }

        def claim_handoff() -> dict[str, Any] | None:
            item = handoffs.claim_next(graph_node_active=dispatch_active["value"])
            if item is None:
                return None
            refresh_checkpoint_state()
            return {
                "source_crew": item.source_crew,
                "target_crew": item.target_crew,
                "task": item.task,
                "source_plan_digest": item.source_plan_digest,
                "attempt": item.attempt,
                "handoff_id": item.handoff_id,
                "source_proposal_digest": item.source_proposal_digest,
            }

        def retry_handoff(record: Mapping[str, Any], retryable: bool) -> dict[str, Any] | None:
            if not dispatch_active["value"]:
                raise RuntimeError("handoff retry is only available during graph execution")
            item = handoffs.retry(
                CrewHandoff(
                    record["source_crew"], record["target_crew"], record["task"],
                    record["source_plan_digest"], record.get("attempt", 0), record.get("handoff_id", ""),
                    record.get("source_proposal_digest", ""), "",
                ),
                retryable=retryable,
            )
            refresh_checkpoint_state()
            if item is None:
                return None
            return {
                "source_crew": item.source_crew,
                "target_crew": item.target_crew,
                "task": item.task,
                "source_plan_digest": item.source_plan_digest,
                "attempt": item.attempt,
                "handoff_id": item.handoff_id,
                "source_proposal_digest": item.source_proposal_digest,
            }

        def complete_handoff(record: Mapping[str, Any]) -> None:
            if not dispatch_active["value"]:
                raise RuntimeError("handoff completion is only available during graph execution")
            handoff = CrewHandoff(
                record["source_crew"], record["target_crew"], record["task"],
                record["source_plan_digest"], record.get("attempt", 0), record.get("handoff_id", ""),
                record.get("source_proposal_digest", ""), record.get("target_proposal_digest", ""),
            )
            if require_persistence and (
                not handoff.source_proposal_digest or not handoff.target_proposal_digest
            ):
                raise ValueError("durable handoff completion requires source and target proposal digests")
            handoffs.validate_claimed(handoff)
            reviewer = self.independent_handoff_reviewer
            if reviewer is None:
                raise RuntimeError("independent handoff reviewer is not configured")
            raw_review = reviewer(handoff)
            required_review_fields = {
                "schema_version", "reviewer_id", "reviewer_version",
                "status", "evidence_refs", "reason",
            }
            if not isinstance(raw_review, Mapping) or set(raw_review) != required_review_fields:
                raise TypeError("independent handoff reviewer must return a structured evidence receipt")
            refs = raw_review.get("evidence_refs")
            if (
                raw_review.get("schema_version") != "auren-handoff-review-receipt-v1"
                or raw_review.get("reviewer_id") != self.independent_handoff_reviewer_id
                or raw_review.get("reviewer_version") != self.independent_handoff_reviewer_version
                or raw_review.get("status") not in {"pass", "reject"}
                or not isinstance(refs, list) or not refs
                or any(not isinstance(ref, str) or not ref.strip() for ref in refs)
                or not isinstance(raw_review.get("reason"), str) or not raw_review["reason"].strip()
            ):
                raise ValueError("independent handoff review receipt failed identity or evidence validation")
            if require_persistence and not {
                handoff.source_proposal_digest, handoff.target_proposal_digest
            }.issubset(set(refs)):
                raise ValueError("independent handoff review must reference both proposal digests")
            if raw_review["status"] != "pass":
                raise PermissionError("independent handoff review did not pass")
            review_payload = dict(raw_review)
            review_receipt = {
                **review_payload,
                "digest": hashlib.sha256(
                    json.dumps(review_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
            }
            handoffs.mark_completed(
                handoff, independent_review_passed=True, review_receipt=review_receipt
            )
            refresh_checkpoint_state()

        # Only graph nodes receive scoped functions. Adapter/tool objects are never
        # placed into graph state; the graph node remains the sole execution authority.
        state["auren_crew_dispatch"] = dispatch_crew
        state["auren_crew_handoff_enqueue"] = enqueue_handoff
        state["auren_crew_handoff_claim"] = claim_handoff
        state["auren_crew_handoff_retry"] = retry_handoff
        state["auren_crew_handoff_complete"] = complete_handoff
        state["auren_crew_handoff_checkpoint_data"] = handoffs.snapshot()
        state["auren_retry_budget"] = retry_budget.snapshot()
        state["auren_provider_resume_persist"] = persist_provider_resume
        state["auren_runtime_guard_persist"] = lambda _snapshot=None: persist_handoff_checkpoint(handoffs.snapshot())
        state["auren_crew_handoff_checkpoint"] = handoffs.snapshot
        state["auren_crew_rollback_readiness"] = rollback_readiness
        try:
            return self.graph.run(task_id, intent, state)
        finally:
            dispatch_active["value"] = False


__all__ = [
    "StateGraph", "StateTransition", "CrewDefinition", "CrewAssignment", "CrewPlan", "GraphAgentTeam",
    "default_crew_definitions", "build_crew_plan",
]
