"""Approved, bounded specialist adapters and graph-owned handoff policy.

Adapters may call explicitly registered read-only tools. They never receive a
general-purpose tool handle, credentials, or authority to mutate/promote work.
The StateGraph node remains responsible for invoking adapters and accepting results.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class ApprovedTool:
    name: str
    handler: Callable[[Mapping[str, Any]], Any]
    description: str
    side_effecting: bool = False

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("approved tool name is required")
        if not callable(self.handler):
            raise TypeError("approved tool handler must be callable")
        if not self.description or not self.description.strip():
            raise ValueError("approved tool description is required")
        if type(self.side_effecting) is not bool:
            raise ValueError("side_effecting must be a boolean")


class ApprovedToolRegistry:
    """Immutable-by-interface allowlist; side-effecting tools are rejected."""

    def __init__(self, tools: Sequence[ApprovedTool] = ()) -> None:
        self._tools: dict[str, ApprovedTool] = {}
        for tool in tools:
            if not isinstance(tool, ApprovedTool):
                raise TypeError("registry entries must be ApprovedTool instances")
            if tool.side_effecting:
                raise ValueError(f"side-effecting tool is not eligible for proposal phase: {tool.name}")
            if tool.name in self._tools:
                raise ValueError(f"duplicate approved tool: {tool.name}")
            self._tools[tool.name] = tool

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def description(self, name: str) -> str:
        if name not in self._tools:
            raise KeyError(name)
        return self._tools[name].description

    def invoke(self, name: str, arguments: Mapping[str, Any]) -> Any:
        if name not in self._tools:
            raise PermissionError(f"tool is not approved for this adapter: {name}")
        if not isinstance(arguments, Mapping):
            raise ValueError("tool arguments must be a mapping")
        # Copy the top-level mapping so a handler cannot mutate caller-owned state.
        result = self._tools[name].handler(dict(arguments))
        # Only JSON-compatible outputs may cross the adapter boundary.
        try:
            json.dumps(result, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("approved tool result must be JSON-serializable") from exc
        return result


class ApprovedToolCrewAdapter:
    """Plug-compatible proposal agent with a two-phase, read-only tool boundary.

    planner(request) returns {"tool_calls": [{"name": str, "arguments": dict}],
    "proposal": <auren-crew-proposal-v1 mapping>}. Tools are invoked only from
    this adapter's allowlist. A finalizer, if supplied, receives only the minimal
    request, bounded tool results, and draft proposal. No raw tool objects escape.
    """

    def __init__(
        self,
        crew_id: str,
        planner: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        tools: ApprovedToolRegistry,
        *,
        allowed_tools: Sequence[str],
        finalizer: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        max_tool_calls: int = 4,
        implementation_version: str = "unversioned",
    ) -> None:
        if not isinstance(crew_id, str) or not crew_id.strip():
            raise ValueError("crew_id is required")
        if not callable(planner):
            raise TypeError("planner must be callable")
        if finalizer is not None and not callable(finalizer):
            raise TypeError("finalizer must be callable")
        if type(max_tool_calls) is not int or not 0 <= max_tool_calls <= 16:
            raise ValueError("max_tool_calls must be between 0 and 16")
        if not isinstance(implementation_version, str) or not implementation_version.strip():
            raise ValueError("implementation_version must be non-empty text")
        if len(set(allowed_tools)) != len(tuple(allowed_tools)):
            raise ValueError("allowed_tools must not contain duplicates")
        unknown = set(allowed_tools) - set(tools.names)
        if unknown:
            raise ValueError("adapter references unregistered tools: " + ", ".join(sorted(unknown)))
        self.crew_id = crew_id
        self._planner = planner
        self._tools = tools
        self._allowed_tools = frozenset(allowed_tools)
        self._finalizer = finalizer
        self._max_tool_calls = max_tool_calls
        self.implementation_version = implementation_version.strip()

    @property
    def configuration_digest(self) -> str:
        """Stable digest of tool scope and declared implementation version, not callable identity."""
        payload = {
            "schema_version": "auren-approved-tool-crew-adapter-v1",
            "crew_id": self.crew_id,
            "allowed_tools": [
                {"name": name, "description": self._tools.description(name)}
                for name in sorted(self._allowed_tools)
            ],
            "max_tool_calls": self._max_tool_calls,
            "has_finalizer": self._finalizer is not None,
            "implementation_version": self.implementation_version,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def propose(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(request, Mapping) or request.get("crew_id") != self.crew_id:
            raise ValueError("adapter request crew_id mismatch")
        # Pass only the documented proposal request fields, not arbitrary graph state.
        safe_request = {
            key: request[key]
            for key in (
                "crew_id", "specialty", "instructions", "task", "evidence",
                "required_artifacts", "acceptance_checks", "allowed_authorities",
                "graph_digest", "crew_plan_digest",
            )
            if key in request
        }
        draft = self._planner(safe_request)
        if not isinstance(draft, Mapping) or set(draft) != {"tool_calls", "proposal"}:
            raise ValueError("planner must return tool_calls and proposal")
        calls = draft["tool_calls"]
        if not isinstance(calls, list) or len(calls) > self._max_tool_calls:
            raise ValueError("planner tool-call list exceeds its bounded schema")
        results: list[dict[str, Any]] = []
        for call in calls:
            if not isinstance(call, Mapping) or set(call) != {"name", "arguments"}:
                raise ValueError("tool call must contain only name and arguments")
            name = call["name"]
            if not isinstance(name, str) or name not in self._allowed_tools:
                raise PermissionError(f"tool is not approved for crew {self.crew_id}: {name}")
            result = self._tools.invoke(name, call["arguments"])
            results.append({"name": name, "result": result})
        proposal = draft["proposal"]
        if not isinstance(proposal, Mapping):
            raise ValueError("draft proposal must be a mapping")
        if self._finalizer is not None:
            proposal = self._finalizer({
                "request": safe_request,
                "tool_results": results,
                "draft_proposal": dict(proposal),
            })
        if not isinstance(proposal, Mapping):
            raise ValueError("finalizer must return a structured proposal")
        # Match GraphAgentTeam's strict schema here, before results cross the adapter.
        required = {
            "schema_version", "recommendation", "artifacts", "evidence_refs",
            "uncertainty", "blockers", "acceptance_check_results",
        }
        if set(proposal) != required or proposal.get("schema_version") != "auren-crew-proposal-v1":
            raise ValueError("adapter proposal does not match auren-crew-proposal-v1")
        return dict(proposal)


# These core crews have explicit, graph-routed proposal contracts. Provider
# planners remain injected so credentials and model clients stay outside this
# module and only the approved read-only tool registry crosses the boundary.
SPECIALIST_CREW_IDS = (
    "backend_api",
    "database_schema",
    "dotnet",
    "end_user_validation",
    "frontend_ui",
    "product_pm",
    "qa_reviewer",
    "security_privacy",
)


def build_specialist_crew_adapters(
    *,
    planners: Mapping[str, Callable[[Mapping[str, Any]], Mapping[str, Any]]],
    tools: ApprovedToolRegistry,
    allowed_tools_by_crew: Mapping[str, Sequence[str]],
    finalizers: Mapping[str, Callable[[Mapping[str, Any]], Mapping[str, Any]]] | None = None,
    max_tool_calls: int = 4,
    implementation_versions: Mapping[str, str] | None = None,
) -> dict[str, ApprovedToolCrewAdapter]:
    """Build the eight core specialist adapters with explicit per-crew tool scopes.

    Missing or unknown core registrations are rejected instead of silently
    installing placeholders. The returned adapters still only propose; graph
    execution, acceptance, review, promotion, and rollback remain supervisor-owned.
    """
    if not isinstance(tools, ApprovedToolRegistry):
        raise TypeError("tools must be an ApprovedToolRegistry")
    if not isinstance(planners, Mapping):
        raise TypeError("planners must be a mapping of crew IDs to proposal planners")
    if not isinstance(allowed_tools_by_crew, Mapping):
        raise TypeError("allowed_tools_by_crew must be a mapping")
    finalizers = {} if finalizers is None else finalizers
    if not isinstance(finalizers, Mapping):
        raise TypeError("finalizers must be a mapping")
    implementation_versions = {} if implementation_versions is None else implementation_versions
    if not isinstance(implementation_versions, Mapping):
        raise TypeError("implementation_versions must be a mapping")
    expected = set(SPECIALIST_CREW_IDS)
    planner_ids = set(planners)
    tool_scope_ids = set(allowed_tools_by_crew)
    unknown = (planner_ids | tool_scope_ids | set(finalizers) | set(implementation_versions)) - expected
    if unknown:
        raise ValueError("unknown core specialist crew IDs: " + ", ".join(sorted(unknown)))
    missing_planners = expected - planner_ids
    missing_scopes = expected - tool_scope_ids
    if missing_planners:
        raise ValueError("missing specialist proposal planners: " + ", ".join(sorted(missing_planners)))
    if missing_scopes:
        raise ValueError("missing specialist tool scopes: " + ", ".join(sorted(missing_scopes)))
    return {
        crew_id: ApprovedToolCrewAdapter(
            crew_id,
            planners[crew_id],
            tools,
            allowed_tools=allowed_tools_by_crew[crew_id],
            finalizer=finalizers.get(crew_id),
            max_tool_calls=max_tool_calls,
            implementation_version=implementation_versions.get(crew_id, "unversioned"),
        )
        for crew_id in SPECIALIST_CREW_IDS
    }


@dataclass(frozen=True, slots=True)
class CrewHandoff:
    source_crew: str
    target_crew: str
    task: str
    source_plan_digest: str
    attempt: int = 0


class GraphHandoffController:
    """Create/claim handoffs for graph nodes; never invokes a crew itself."""

    def __init__(self, *, max_attempts: int = 3) -> None:
        if type(max_attempts) is not int or max_attempts < 1 or max_attempts > 10:
            raise ValueError("max_attempts must be between 1 and 10")
        self.max_attempts = max_attempts
        self._queue: list[CrewHandoff] = []
        self._completed: set[tuple[str, str, str]] = set()

    def enqueue(
        self,
        *,
        source_crew: str,
        target_crew: str,
        task: str,
        source_plan_digest: str,
        allowed_handoffs: Sequence[str],
        selected_crews: Sequence[str],
    ) -> CrewHandoff:
        if target_crew not in allowed_handoffs:
            raise PermissionError(f"handoff {source_crew}->{target_crew} is not declared by crew contract")
        if target_crew not in selected_crews:
            raise PermissionError(f"handoff target crew is not assigned to the graph plan: {target_crew}")
        if not task.strip() or not source_plan_digest.strip():
            raise ValueError("handoff task and source plan digest are required")
        handoff = CrewHandoff(source_crew, target_crew, task.strip(), source_plan_digest)
        self._queue.append(handoff)
        return handoff

    def claim_next(self, *, graph_node_active: bool) -> CrewHandoff | None:
        if not graph_node_active:
            raise RuntimeError("handoffs may only be claimed by an active graph node")
        if not self._queue:
            return None
        return self._queue.pop(0)

    def retry(self, handoff: CrewHandoff, *, retryable: bool) -> CrewHandoff | None:
        if not retryable or handoff.attempt + 1 >= self.max_attempts:
            return None
        retry = CrewHandoff(
            handoff.source_crew, handoff.target_crew, handoff.task,
            handoff.source_plan_digest, handoff.attempt + 1,
        )
        self._queue.append(retry)
        return retry

    def mark_completed(self, handoff: CrewHandoff, *, independent_review_passed: bool) -> None:
        if not independent_review_passed:
            raise PermissionError("handoff cannot complete before independent review passes")
        key = (handoff.source_crew, handoff.target_crew, hashlib.sha256(handoff.task.encode()).hexdigest())
        self._completed.add(key)

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe checkpoint for a supervisor-owned durable store."""
        return {
            "schema_version": "auren-crew-handoff-checkpoint-v1",
            "max_attempts": self.max_attempts,
            "queued": [
                {"source_crew": h.source_crew, "target_crew": h.target_crew,
                 "task": h.task, "source_plan_digest": h.source_plan_digest, "attempt": h.attempt}
                for h in self._queue
            ],
            "completed": [list(key) for key in sorted(self._completed)],
        }

    @classmethod
    def resume(cls, checkpoint: Mapping[str, Any]) -> "GraphHandoffController":
        if not isinstance(checkpoint, Mapping) or checkpoint.get("schema_version") != "auren-crew-handoff-checkpoint-v1":
            raise ValueError("unsupported handoff checkpoint")
        controller = cls(max_attempts=checkpoint.get("max_attempts", 3))
        queued = checkpoint.get("queued")
        completed = checkpoint.get("completed")
        if not isinstance(queued, list) or not isinstance(completed, list):
            raise ValueError("checkpoint queue and completed records must be lists")
        for raw in queued:
            if not isinstance(raw, Mapping):
                raise ValueError("invalid queued handoff")
            handoff = CrewHandoff(
                raw["source_crew"], raw["target_crew"], raw["task"],
                raw["source_plan_digest"], raw["attempt"],
            )
            if not 0 <= handoff.attempt < controller.max_attempts:
                raise ValueError("checkpoint handoff attempt is outside retry budget")
            controller._queue.append(handoff)
        for item in completed:
            if not isinstance(item, list) or len(item) != 3 or any(not isinstance(v, str) for v in item):
                raise ValueError("invalid completed handoff key")
            controller._completed.add(tuple(item))
        return controller


def rollback_readiness(
    *,
    consequential: bool,
    last_known_good_digest: str | None,
    rollback_procedure_ref: str | None,
    independent_review_passed: bool,
    required_evidence_refs: Sequence[str],
    verified_evidence_refs: Sequence[str],
) -> dict[str, Any]:
    """Fail-closed readiness predicate; it does not itself promote or roll back."""
    missing = sorted(set(required_evidence_refs) - set(verified_evidence_refs))
    reasons: list[str] = []
    if consequential and not last_known_good_digest:
        reasons.append("missing last-known-good digest")
    if consequential and not rollback_procedure_ref:
        reasons.append("missing rollback procedure reference")
    if consequential and not independent_review_passed:
        reasons.append("independent review has not passed")
    if missing:
        reasons.append("required evidence is unverified: " + ", ".join(missing))
    return {
        "ready": not reasons,
        "consequential": consequential,
        "last_known_good_digest": last_known_good_digest,
        "rollback_procedure_ref": rollback_procedure_ref,
        "missing_evidence_refs": missing,
        "blockers": reasons,
    }
