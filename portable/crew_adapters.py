"""Approved, bounded specialist adapters and graph-owned handoff policy.

Adapters may call explicitly registered read-only tools. They never receive a
general-purpose tool handle, credentials, or authority to mutate/promote work.
The StateGraph node remains responsible for invoking adapters and accepting results.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import time
from queue import Empty, Queue
from threading import BoundedSemaphore, RLock, Thread
from typing import Any, Callable, Mapping, Sequence


# Shared cap across the resumable executor and host-injected provider adapters.
# Python cannot kill a running thread, so timed-out workers remain daemonized but
# cannot grow without bound if a host ignores its native timeout.
_MAX_PROVIDER_CALL_WORKERS = 8
_PROVIDER_CALL_SLOTS = BoundedSemaphore(_MAX_PROVIDER_CALL_WORKERS)


def invoke_provider_with_bounded_wait(
    invoke: Callable[..., Any], args: Sequence[Any], *, timeout_seconds: float,
    provider_id: str, on_event: Callable[..., None] | None = None,
) -> tuple[str, Any]:
    """Bound the caller wait for one provider invocation.

    Returns (result, value), (error, exception), (timeout, None), or
    (capacity, None). A timed-out call may finish later; callers must not retry
    it automatically until the attempt has been reconciled.
    """
    if not callable(invoke):
        raise TypeError("provider invoker must be callable")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("provider timeout must be finite and positive")
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise ValueError("provider_id is required")
    if on_event is not None and not callable(on_event):
        raise TypeError("provider event callback must be callable")

    def emit(event: str, **fields: Any) -> None:
        if on_event is not None:
            on_event(event, provider=provider_id, **fields)

    if not _PROVIDER_CALL_SLOTS.acquire(blocking=False):
        emit("provider_call_blocked", reason="bounded provider worker capacity exhausted",
             max_workers=_MAX_PROVIDER_CALL_WORKERS)
        return "capacity", None
    outcome: Queue[tuple[str, Any]] = Queue(maxsize=1)

    def worker_call() -> None:
        try:
            outcome.put(("result", invoke(*args)))
        except Exception as exc:
            outcome.put(("error", exc))
        finally:
            _PROVIDER_CALL_SLOTS.release()

    try:
        emit("provider_call_started", timeout_seconds=float(timeout_seconds))
    except Exception:
        _PROVIDER_CALL_SLOTS.release()
        raise
    worker = Thread(target=worker_call, name=f"auren-provider-{provider_id}", daemon=True)
    started = time.monotonic()
    try:
        worker.start()
    except Exception:
        _PROVIDER_CALL_SLOTS.release()
        raise
    while True:
        remaining = float(timeout_seconds) - (time.monotonic() - started)
        if remaining <= 0:
            try:
                emit("provider_call_timed_out", timeout_seconds=float(timeout_seconds),
                     late_result_possible=True, retry_blocked=True)
            except Exception:
                # The timeout return is still the caller-visible liveness signal.
                pass
            return "timeout", None
        try:
            kind, value = outcome.get(timeout=min(remaining, 0.05))
        except Empty:
            continue
        return ("error", value) if kind == "error" else ("result", value)


class TaskRetryBudget:
    """Thread-safe task-scoped retry budget with checkpointable idempotent charges.

    This is a value object owned by the task graph/supervisor, not a second
    persistence layer. Its snapshot belongs in the graph's canonical checkpoint.
    """

    SCHEMA_VERSION = "auren-task-retry-budget-v1"

    def __init__(self, max_retries: int = 3, *, charges: Mapping[str, str] | None = None) -> None:
        if type(max_retries) is not int or not 0 <= max_retries <= 100:
            raise ValueError("max_retries must be an integer between 0 and 100")
        self.max_retries = max_retries
        self._charges = dict(charges or {})
        if any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
               for k, v in self._charges.items()):
            raise ValueError("retry budget charges must map non-empty operation keys to components")
        if len(self._charges) > self.max_retries:
            raise ValueError("checkpoint charges exceed retry budget")
        self._lock = RLock()

    @property
    def used(self) -> int:
        with self._lock:
            return len(self._charges)

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_retries - len(self._charges))

    def consume(self, *, component: str, operation_key: str) -> bool:
        """Charge a retry once; duplicate charge keys do not consume budget twice."""
        if not isinstance(component, str) or not component.strip():
            raise ValueError("retry component is required")
        if not isinstance(operation_key, str) or not operation_key.strip():
            raise ValueError("retry operation_key is required")
        with self._lock:
            existing = self._charges.get(operation_key)
            if existing is not None:
                if existing != component:
                    raise ValueError("retry operation key was reused by a different component")
                # A previously charged attempt must not be executed again after
                # resume; it is already spent, even if its completion is unknown.
                return False
            if len(self._charges) >= self.max_retries:
                return False
            self._charges[operation_key] = component
            return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            charges = [
                {"operation_key": key, "component": value}
                for key, value in sorted(self._charges.items())
            ]
            payload = {
                "schema_version": self.SCHEMA_VERSION,
                "max_retries": self.max_retries,
                "used_retries": len(charges),
                "charges": charges,
            }
            payload["digest"] = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            return payload

    def merge_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        """Monotonically reconcile a persisted budget into this task-owned ledger."""
        incoming = self.resume(snapshot)
        with self._lock:
            if incoming.max_retries != self.max_retries:
                raise ValueError("cannot merge retry budgets with different limits")
            merged = dict(self._charges)
            for key, component in incoming._charges.items():
                previous = merged.get(key)
                if previous is not None and previous != component:
                    raise ValueError("conflicting retry budget charge across task checkpoints")
                merged[key] = component
            if len(merged) > self.max_retries:
                raise ValueError("merged retry budget exceeds task limit")
            self._charges = merged
            return self.snapshot()

    @classmethod
    def resume(cls, snapshot: Mapping[str, Any]) -> "TaskRetryBudget":
        if not isinstance(snapshot, Mapping) or snapshot.get("schema_version") != cls.SCHEMA_VERSION:
            raise ValueError("unsupported task retry budget checkpoint")
        raw_charges = snapshot.get("charges")
        if not isinstance(raw_charges, list):
            raise ValueError("retry budget charges must be a list")
        charges: dict[str, str] = {}
        for item in raw_charges:
            if not isinstance(item, Mapping):
                raise ValueError("invalid retry budget charge")
            key, component = item.get("operation_key"), item.get("component")
            if not isinstance(key, str) or not key.strip() or not isinstance(component, str) or not component.strip():
                raise ValueError("invalid retry budget charge fields")
            if key in charges and charges[key] != component:
                raise ValueError("conflicting retry budget charge")
            charges[key] = component
        budget = cls(snapshot.get("max_retries", 3), charges=charges)
        if snapshot.get("used_retries") != budget.used:
            raise ValueError("retry budget used count does not match charge ledger")
        supplied_digest = snapshot.get("digest")
        if not isinstance(supplied_digest, str) or not supplied_digest:
            raise ValueError("retry budget checkpoint digest is required")
        expected = budget.snapshot()["digest"]
        if supplied_digest != expected:
            raise ValueError("retry budget checkpoint digest mismatch")
        return budget


def merge_retry_budget_snapshots(current: Any, incoming: Any) -> dict[str, Any]:
    """Monotonic reducer for concurrent graph-node budget snapshots."""
    if current is None:
        return TaskRetryBudget.resume(incoming).snapshot()
    if incoming is None:
        return TaskRetryBudget.resume(current).snapshot()
    left = TaskRetryBudget.resume(current)
    right = TaskRetryBudget.resume(incoming)
    if left.max_retries != right.max_retries:
        raise ValueError("cannot merge retry budgets with different limits")
    merged = {item["operation_key"]: item["component"] for item in left.snapshot()["charges"]}
    for item in right.snapshot()["charges"]:
        previous = merged.get(item["operation_key"])
        if previous is not None and previous != item["component"]:
            raise ValueError("conflicting retry budget charge across graph branches")
        merged[item["operation_key"]] = item["component"]
    if len(merged) > left.max_retries:
        raise ValueError("merged retry budget exceeds task limit")
    return TaskRetryBudget(left.max_retries, charges=merged).snapshot()


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
                "graph_digest", "crew_plan_digest", "task_id",
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


class ProviderBackedCrewAdapter(ApprovedToolCrewAdapter):
    """Proposal-only crew adapter backed by a host-injected structured provider.

    The host owns provider credentials and process/client lifecycle. The shared
    bounded-call boundary limits caller wait even if an invoker ignores its timeout.
    Python cannot kill a timed-out thread, so late completion remains possible and
    consumes one of the globally bounded provider-worker slots until it exits.
    """

    def __init__(
        self,
        crew_id: str,
        provider_id: str,
        provider_version: str,
        invoke_provider: Callable[[str, int], str],
        tools: ApprovedToolRegistry,
        *,
        allowed_tools: Sequence[str],
        timeout_seconds: int = 120,
        max_response_chars: int = 100_000,
        finalizer: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        max_tool_calls: int = 4,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("provider_id is required")
        if not isinstance(provider_version, str) or not provider_version.strip():
            raise ValueError("provider_version is required")
        if provider_version.strip().lower() in {"unknown", "unversioned", "latest"}:
            raise ValueError("provider_version must be pinned, not unknown/latest")
        if not callable(invoke_provider):
            raise TypeError("invoke_provider must be a host-injected callable")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 600:
            raise ValueError("timeout_seconds must be an integer between 1 and 600")
        if type(max_response_chars) is not int or not 256 <= max_response_chars <= 1_000_000:
            raise ValueError("max_response_chars must be between 256 and 1000000")
        self.provider_id = provider_id.strip()
        self.provider_version = provider_version.strip()
        self.timeout_seconds = timeout_seconds
        self.max_response_chars = max_response_chars
        self._invoke_provider = invoke_provider
        if progress_callback is not None and not callable(progress_callback):
            raise TypeError("progress_callback must be callable")
        self._progress_callback = progress_callback

        def planner(request: Mapping[str, Any]) -> Mapping[str, Any]:
            # task_id is supervisor telemetry context, not model input.
            task_request = {key: value for key, value in request.items() if key != "task_id"}
            # Internal task identity can appear in evidence for checkpoint lineage,
            # but must not be forwarded into provider/model input.
            task_evidence = task_request.get("evidence")
            if isinstance(task_evidence, Mapping) and "task_id" in task_evidence:
                task_request["evidence"] = {
                    key: value for key, value in task_evidence.items() if key != "task_id"
                }
            prompt_payload = {
                "task_request": task_request,
                "approved_tools": [
                    {"name": name, "description": tools.description(name)}
                    for name in sorted(set(allowed_tools))
                ],
                "response_contract": {
                    "top_level_keys": ["tool_calls", "proposal"],
                    "tool_call_keys": ["name", "arguments"],
                    "proposal_schema_version": "auren-crew-proposal-v1",
                    "proposal_required_keys": [
                        "schema_version", "recommendation", "artifacts", "evidence_refs",
                        "uncertainty", "blockers", "acceptance_check_results",
                    ],
                    "output": "one JSON object only; no Markdown fences or surrounding prose",
                },
            }
            prompt = (
                "You are a specialist proposal agent. Return only the JSON object matching "
                "response_contract. Tool calls are proposals for the host-approved read-only "
                "tool boundary, not permission to execute side effects.\n"
                + json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            )
            task_id = request.get("task_id")
            if self._progress_callback is not None and (not isinstance(task_id, str) or not task_id.strip()):
                raise ValueError("provider progress integration requires a supervisor-issued task_id")

            def emit_provider_event(event: str, **fields: Any) -> None:
                if self._progress_callback is None:
                    return
                # Explicit allowlist: never forward prompt, response, request payload,
                # exception text, or arbitrary provider metadata into the journal.
                safe_fields = {
                    key: value for key, value in fields.items()
                    if key in {"provider", "timeout_seconds", "late_result_possible", "retry_blocked", "max_workers"}
                }
                safe_fields.setdefault("provider", self.provider_id)
                self._progress_callback({"task_id": task_id, "event": event, **safe_fields})

            outcome, raw = invoke_provider_with_bounded_wait(
                self._invoke_provider, (prompt, self.timeout_seconds),
                timeout_seconds=self.timeout_seconds, provider_id=self.provider_id,
                on_event=emit_provider_event if self._progress_callback is not None else None,
            )
            if outcome == "capacity":
                raise RuntimeError("bounded provider worker capacity exhausted; retry after capacity is available")
            if outcome == "timeout":
                raise TimeoutError("provider call exceeded its bounded wait; a late response may still arrive")
            if outcome == "error":
                emit_provider_event("provider_call_failed", error_type=type(raw).__name__)
                raise raw
            if not isinstance(raw, str):
                raise TypeError("provider response must be text")
            if len(raw) > self.max_response_chars:
                raise ValueError("provider response exceeds configured character budget")

            def reject_duplicate_keys(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError(f"provider response contains duplicate JSON key: {key}")
                    result[key] = value
                return result

            try:
                parsed = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError("provider response must be one valid JSON object") from exc
            if not isinstance(parsed, Mapping) or set(parsed) != {"tool_calls", "proposal"}:
                raise ValueError("provider response must contain exactly tool_calls and proposal")
            emit_provider_event("provider_result_ready")
            return parsed

        super().__init__(
            crew_id,
            planner,
            tools,
            allowed_tools=allowed_tools,
            finalizer=finalizer,
            max_tool_calls=max_tool_calls,
            implementation_version=f"{self.provider_id}@{self.provider_version}",
        )

    @property
    def configuration_digest(self) -> str:
        payload = {
            "schema_version": "auren-provider-backed-crew-adapter-v1",
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "timeout_seconds": self.timeout_seconds,
            "max_response_chars": self.max_response_chars,
            "progress_callback_enabled": self._progress_callback is not None,
            "approved_tool_configuration": super().configuration_digest,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


def build_provider_backed_specialist_crew_adapters(
    *,
    provider_invokers: Mapping[str, Callable[[str, int], str]],
    provider_ids: Mapping[str, str],
    provider_versions: Mapping[str, str],
    tools: ApprovedToolRegistry,
    allowed_tools_by_crew: Mapping[str, Sequence[str]],
    finalizers: Mapping[str, Callable[[Mapping[str, Any]], Mapping[str, Any]]] | None = None,
    timeout_seconds_by_crew: Mapping[str, int] | None = None,
    max_response_chars: int = 100_000,
    max_tool_calls: int = 4,
    progress_callbacks_by_crew: Mapping[str, Callable[[Mapping[str, Any]], None]] | None = None,
) -> dict[str, ProviderBackedCrewAdapter]:
    """Build all eight host-backed adapters with pinned provider identity and tool scope."""
    if not isinstance(tools, ApprovedToolRegistry):
        raise TypeError("tools must be an ApprovedToolRegistry")
    mappings = {
        "provider_invokers": provider_invokers,
        "provider_ids": provider_ids,
        "provider_versions": provider_versions,
        "allowed_tools_by_crew": allowed_tools_by_crew,
    }
    expected = set(SPECIALIST_CREW_IDS)
    for name, mapping in mappings.items():
        if not isinstance(mapping, Mapping):
            raise TypeError(f"{name} must be a mapping")
        unknown = set(mapping) - expected
        if unknown:
            raise ValueError(f"{name} contains unknown crew IDs: " + ", ".join(sorted(unknown)))
        missing = expected - set(mapping)
        if missing:
            raise ValueError(f"{name} is missing specialist crew IDs: " + ", ".join(sorted(missing)))
    finalizers = {} if finalizers is None else finalizers
    timeouts = {} if timeout_seconds_by_crew is None else timeout_seconds_by_crew
    progress_callbacks_by_crew = {} if progress_callbacks_by_crew is None else progress_callbacks_by_crew
    if not all(isinstance(value, Mapping) for value in (finalizers, timeouts, progress_callbacks_by_crew)):
        raise TypeError("finalizers, timeout_seconds_by_crew, and progress_callbacks_by_crew must be mappings")
    if set(finalizers) - expected or set(timeouts) - expected or set(progress_callbacks_by_crew) - expected:
        raise ValueError("finalizers, timeout_seconds_by_crew, or progress_callbacks_by_crew contains unknown crew IDs")
    if any(not callable(callback) for callback in progress_callbacks_by_crew.values()):
        raise TypeError("every provider progress callback must be callable")
    return {
        crew_id: ProviderBackedCrewAdapter(
            crew_id,
            provider_ids[crew_id],
            provider_versions[crew_id],
            provider_invokers[crew_id],
            tools,
            allowed_tools=allowed_tools_by_crew[crew_id],
            timeout_seconds=timeouts.get(crew_id, 120),
            max_response_chars=max_response_chars,
            finalizer=finalizers.get(crew_id),
            max_tool_calls=max_tool_calls,
            progress_callback=progress_callbacks_by_crew.get(crew_id),
        )
        for crew_id in SPECIALIST_CREW_IDS
    }


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


SOFTWARE_TEAM_PERSONA_IDS = (
    "ceo_sponsor",
    "cto_sponsor",
    "engineering_manager",
    "release_operations",
    "research_evaluation",
    "solution_architect",
)


def build_software_team_persona_adapters(
    *,
    planners: Mapping[str, Callable[[Mapping[str, Any]], Mapping[str, Any]]],
    tools: ApprovedToolRegistry,
    allowed_tools_by_persona: Mapping[str, Sequence[str]],
    finalizers: Mapping[str, Callable[[Mapping[str, Any]], Mapping[str, Any]]] | None = None,
    max_tool_calls: int = 4,
    implementation_versions: Mapping[str, str] | None = None,
) -> dict[str, ApprovedToolCrewAdapter]:
    """Build the six leadership/research/architecture adapters with explicit tool scopes.

    This is separate from the eight core specialist adapters so existing provider
    integrations remain compatible. Returned adapters are proposals only; the
    supervisor retains execution, approval, merge, promotion, and release authority.
    """
    if not isinstance(tools, ApprovedToolRegistry):
        raise TypeError("tools must be an ApprovedToolRegistry")
    finalizers = {} if finalizers is None else finalizers
    implementation_versions = {} if implementation_versions is None else implementation_versions
    for name, mapping in (
        ("planners", planners), ("allowed_tools_by_persona", allowed_tools_by_persona),
        ("finalizers", finalizers), ("implementation_versions", implementation_versions),
    ):
        if not isinstance(mapping, Mapping):
            raise TypeError(f"{name} must be a mapping")
        unknown = set(mapping) - set(SOFTWARE_TEAM_PERSONA_IDS)
        if unknown:
            raise ValueError(f"{name} contains unknown persona IDs: " + ", ".join(sorted(unknown)))
    missing_planners = set(SOFTWARE_TEAM_PERSONA_IDS) - set(planners)
    missing_scopes = set(SOFTWARE_TEAM_PERSONA_IDS) - set(allowed_tools_by_persona)
    if missing_planners:
        raise ValueError("missing software-team persona planners: " + ", ".join(sorted(missing_planners)))
    if missing_scopes:
        raise ValueError("missing software-team persona tool scopes: " + ", ".join(sorted(missing_scopes)))
    return {
        persona_id: ApprovedToolCrewAdapter(
            persona_id, planners[persona_id], tools,
            allowed_tools=allowed_tools_by_persona[persona_id],
            finalizer=finalizers.get(persona_id),
            max_tool_calls=max_tool_calls,
            implementation_version=implementation_versions.get(persona_id, "unversioned"),
        )
        for persona_id in SOFTWARE_TEAM_PERSONA_IDS
    }


def build_provider_backed_software_team_persona_adapters(
    *,
    provider_invokers: Mapping[str, Callable[[str, int], str]],
    provider_ids: Mapping[str, str],
    provider_versions: Mapping[str, str],
    tools: ApprovedToolRegistry,
    allowed_tools_by_persona: Mapping[str, Sequence[str]],
    finalizers: Mapping[str, Callable[[Mapping[str, Any]], Mapping[str, Any]]] | None = None,
    timeout_seconds_by_persona: Mapping[str, int] | None = None,
    max_response_chars: int = 100_000,
    max_tool_calls: int = 4,
    progress_callbacks_by_persona: Mapping[str, Callable[[Mapping[str, Any]], None]] | None = None,
) -> dict[str, ProviderBackedCrewAdapter]:
    """Build all six host-backed leadership/research/architecture persona adapters.

    Provider clients, credentials, and network policy remain host-owned. Every
    persona requires a pinned provider identity/version and an explicit read-only
    tool scope; returned adapters can only make structured proposals.
    """
    if not isinstance(tools, ApprovedToolRegistry):
        raise TypeError("tools must be an ApprovedToolRegistry")
    mappings = {
        "provider_invokers": provider_invokers,
        "provider_ids": provider_ids,
        "provider_versions": provider_versions,
        "allowed_tools_by_persona": allowed_tools_by_persona,
    }
    expected = set(SOFTWARE_TEAM_PERSONA_IDS)
    for name, mapping in mappings.items():
        if not isinstance(mapping, Mapping):
            raise TypeError(f"{name} must be a mapping")
        unknown = set(mapping) - expected
        missing = expected - set(mapping)
        if unknown:
            raise ValueError(f"{name} contains unknown persona IDs: " + ", ".join(sorted(unknown)))
        if missing:
            raise ValueError(f"{name} is missing software-team persona IDs: " + ", ".join(sorted(missing)))
    finalizers = {} if finalizers is None else finalizers
    timeouts = {} if timeout_seconds_by_persona is None else timeout_seconds_by_persona
    callbacks = {} if progress_callbacks_by_persona is None else progress_callbacks_by_persona
    for name, mapping in (("finalizers", finalizers), ("timeout_seconds_by_persona", timeouts),
                          ("progress_callbacks_by_persona", callbacks)):
        if not isinstance(mapping, Mapping):
            raise TypeError(f"{name} must be a mapping")
        unknown = set(mapping) - expected
        if unknown:
            raise ValueError(f"{name} contains unknown persona IDs: " + ", ".join(sorted(unknown)))
    if any(not callable(callback) for callback in callbacks.values()):
        raise TypeError("every software-team progress callback must be callable")
    return {
        persona_id: ProviderBackedCrewAdapter(
            persona_id,
            provider_ids[persona_id],
            provider_versions[persona_id],
            provider_invokers[persona_id],
            tools,
            allowed_tools=allowed_tools_by_persona[persona_id],
            timeout_seconds=timeouts.get(persona_id, 120),
            max_response_chars=max_response_chars,
            finalizer=finalizers.get(persona_id),
            max_tool_calls=max_tool_calls,
            progress_callback=callbacks.get(persona_id),
        )
        for persona_id in SOFTWARE_TEAM_PERSONA_IDS
    }


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
    handoff_id: str = ""


class GraphHandoffController:
    """Create/claim handoffs for graph nodes; never invokes a crew itself."""

    MAX_PENDING_HANDOFFS = 64

    def __init__(
        self, *, max_attempts: int = 3, retry_budget: TaskRetryBudget | None = None,
        persist: Callable[[Mapping[str, Any]], None] | None = None,
        require_persistence: bool = False,
    ) -> None:
        if type(max_attempts) is not int or max_attempts < 1 or max_attempts > 10:
            raise ValueError("max_attempts must be between 1 and 10")
        if type(require_persistence) is not bool:
            raise ValueError("require_persistence must be a boolean")
        if persist is not None and not callable(persist):
            raise TypeError("persist must be callable")
        if require_persistence and persist is None:
            raise ValueError("durable handoff checkpoint writer is required by task policy")
        self.max_attempts = max_attempts
        self.retry_budget = retry_budget
        self._persist_callback = persist
        self.require_persistence = require_persistence
        self._queue: list[CrewHandoff] = []
        self._in_flight: dict[str, CrewHandoff] = {}
        self._seen_ids: set[str] = set()
        self._completed: set[tuple[str, str, str]] = set()
        self._completed_ids: set[str] = set()
        self._completion_receipts: dict[str, dict[str, Any]] = {}
        self._lock = RLock()
        # A failed checkpoint write makes this controller unusable. In-memory
        # state may have advanced, but callers must not continue as if it were
        # durable or silently retry a duplicate handoff.
        self._persistence_failure: Exception | None = None

    def _ensure_healthy(self) -> None:
        if self._persistence_failure is not None:
            raise RuntimeError(
                "handoff controller is fail-closed after a checkpoint persistence failure"
            ) from self._persistence_failure

    @staticmethod
    def _handoff_id(source_crew: str, target_crew: str, task: str, source_plan_digest: str) -> str:
        payload = [source_crew, target_crew, task.strip(), source_plan_digest]
        return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode("utf-8")).hexdigest()

    @classmethod
    def handoff_id_for(cls, source_crew: str, target_crew: str, task: str, source_plan_digest: str) -> str:
        """Return the stable idempotency key used by enqueue and checkpoint receipts."""
        return cls._handoff_id(source_crew, target_crew, task, source_plan_digest)

    def _persist(self) -> None:
        self._ensure_healthy()
        if self.require_persistence and self._persist_callback is None:
            error = RuntimeError("durable handoff checkpoint writer is required by task policy")
            self._persistence_failure = error
            raise error
        if self._persist_callback is not None:
            try:
                self._persist_callback(self.snapshot())
            except Exception as exc:
                self._persistence_failure = exc
                raise

    def enqueue(
        self,
        *,
        source_crew: str,
        target_crew: str,
        task: str,
        source_plan_digest: str,
        allowed_handoffs: Sequence[str],
        selected_crews: Sequence[str],
    ) -> CrewHandoff | None:
        if target_crew not in allowed_handoffs:
            raise PermissionError(f"handoff {source_crew}->{target_crew} is not declared by crew contract")
        if target_crew not in selected_crews:
            raise PermissionError(f"handoff target crew is not assigned to the graph plan: {target_crew}")
        if not task.strip() or not source_plan_digest.strip():
            raise ValueError("handoff task and source plan digest are required")
        handoff_id = self._handoff_id(source_crew, target_crew, task, source_plan_digest)
        with self._lock:
            self._ensure_healthy()
            for queued in self._queue:
                if queued.handoff_id == handoff_id:
                    return queued
            if handoff_id in self._in_flight:
                return self._in_flight[handoff_id]
            # A completed handoff replayed from the original proposal after a
            # restart is idempotently acknowledged, not treated as a new dispatch.
            if handoff_id in self._completed_ids:
                if handoff_id not in self._completion_receipts:
                    raise ValueError("completed handoff lacks a durable independent review receipt")
                return None
            handoff = CrewHandoff(source_crew, target_crew, task.strip(), source_plan_digest, 0, handoff_id)
            if handoff_id in self._seen_ids:
                raise ValueError(
                    "handoff was already claimed or completed; duplicate dispatch is blocked"
                )
            if len(self._queue) + len(self._in_flight) >= self.MAX_PENDING_HANDOFFS:
                raise OverflowError("specialist handoff queue is at its configured bound")
            self._queue.append(handoff)
            self._seen_ids.add(handoff_id)
            self._persist()
            return handoff

    def claim_next(self, *, graph_node_active: bool) -> CrewHandoff | None:
        if not graph_node_active:
            raise RuntimeError("handoffs may only be claimed by an active graph node")
        with self._lock:
            self._ensure_healthy()
            if not self._queue:
                return None
            item = self._queue.pop(0)
            self._in_flight[item.handoff_id] = item
            self._persist()
            return item

    def retry(self, handoff: CrewHandoff, *, retryable: bool) -> CrewHandoff | None:
        with self._lock:
            self._ensure_healthy()
            # A retry is a state transition on an existing claim, never a way
            # to inject a new handoff into the queue. Validate every identity
            # field before consuming budget or mutating queue/in-flight state.
            self.validate_claimed(handoff)
            handoff_id = handoff.handoff_id or self._handoff_id(
                handoff.source_crew, handoff.target_crew, handoff.task, handoff.source_plan_digest
            )
            if not retryable or handoff.attempt + 1 >= self.max_attempts:
                self._in_flight.pop(handoff_id, None)
                self._persist()
                return None
            if self.retry_budget is not None and not self.retry_budget.consume(
                component="crew_handoff",
                operation_key=f"handoff:{handoff_id}:retry:{handoff.attempt + 1}",
            ):
                self._in_flight.pop(handoff_id, None)
                self._persist()
                return None
            retry = CrewHandoff(
                handoff.source_crew, handoff.target_crew, handoff.task,
                handoff.source_plan_digest, handoff.attempt + 1, handoff_id,
            )
            self._in_flight.pop(handoff_id, None)
            self._queue.append(retry)
            self._persist()
            return retry

    def validate_claimed(self, handoff: CrewHandoff) -> None:
        """Require a completion/review request to match an active claimed handoff."""
        with self._lock:
            self._ensure_healthy()
            handoff_id = handoff.handoff_id or self._handoff_id(
                handoff.source_crew, handoff.target_crew, handoff.task, handoff.source_plan_digest
            )
            claimed = self._in_flight.get(handoff_id)
            if claimed is None:
                raise ValueError("handoff must be claimed before independent review or completion")
            if (
                claimed.source_crew != handoff.source_crew
                or claimed.target_crew != handoff.target_crew
                or claimed.task != handoff.task
                or claimed.source_plan_digest != handoff.source_plan_digest
                or claimed.attempt != handoff.attempt
            ):
                raise ValueError("handoff completion record does not match the claimed handoff")

    @staticmethod
    def _validate_independent_review_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "schema_version", "reviewer_id", "reviewer_version", "status",
            "evidence_refs", "reason", "digest",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("independent handoff review receipt fields are invalid")
        refs = value.get("evidence_refs")
        if (
            value.get("schema_version") != "auren-handoff-review-receipt-v1"
            or not isinstance(value.get("reviewer_id"), str) or not value["reviewer_id"].strip()
            or not isinstance(value.get("reviewer_version"), str) or not value["reviewer_version"].strip()
            or value.get("status") not in {"pass", "reject"}
            or not isinstance(refs, list) or not refs or len(refs) > 64
            or any(not isinstance(ref, str) or not ref.strip() or len(ref) > 1024 for ref in refs)
            or not isinstance(value.get("reason"), str) or not value["reason"].strip()
            or len(value["reason"]) > 1000
            or not isinstance(value.get("digest"), str)
        ):
            raise ValueError("independent handoff review receipt failed schema or evidence validation")
        payload = {key: item for key, item in value.items() if key != "digest"}
        expected = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if value["digest"] != expected:
            raise ValueError("independent handoff review receipt digest mismatch")
        return dict(value)

    def mark_completed(
        self,
        handoff: CrewHandoff,
        *,
        independent_review_passed: bool,
        review_receipt: Mapping[str, Any] | None = None,
    ) -> None:
        if not independent_review_passed:
            raise PermissionError("handoff cannot complete before independent review passes")
        with self._lock:
            self._ensure_healthy()
            handoff_id = handoff.handoff_id or self._handoff_id(
                handoff.source_crew, handoff.target_crew, handoff.task, handoff.source_plan_digest
            )
            self.validate_claimed(handoff)
            if not isinstance(review_receipt, Mapping):
                raise PermissionError("handoff completion requires a durable independent review receipt")
            validated_receipt = self._validate_independent_review_receipt(review_receipt)
            if validated_receipt["status"] != "pass":
                raise PermissionError("a rejected review receipt cannot mark a handoff successful")
            key = (handoff.source_crew, handoff.target_crew, hashlib.sha256(handoff.task.encode()).hexdigest())
            self._completed.add(key)
            self._completed_ids.add(handoff_id)
            self._completion_receipts[handoff_id] = validated_receipt
            self._in_flight.pop(handoff_id, None)
            self._seen_ids.add(handoff_id)
            self._persist()

    def mark_rejected(
        self,
        handoff: CrewHandoff,
        *,
        review_receipt: Mapping[str, Any],
    ) -> None:
        """Persist a terminal reviewer rejection so restart cannot replay it as pending."""
        with self._lock:
            self._ensure_healthy()
            handoff_id = handoff.handoff_id or self._handoff_id(
                handoff.source_crew, handoff.target_crew, handoff.task, handoff.source_plan_digest
            )
            self.validate_claimed(handoff)
            if not isinstance(review_receipt, Mapping):
                raise PermissionError("terminal rejection requires an independent review receipt")
            validated_receipt = self._validate_independent_review_receipt(review_receipt)
            if validated_receipt["status"] != "reject":
                raise PermissionError("mark_rejected requires a reject review receipt")
            key = (handoff.source_crew, handoff.target_crew, hashlib.sha256(handoff.task.encode()).hexdigest())
            self._completed.add(key)
            self._completed_ids.add(handoff_id)
            self._completion_receipts[handoff_id] = validated_receipt
            self._in_flight.pop(handoff_id, None)
            self._seen_ids.add(handoff_id)
            self._persist()

    def terminal_receipt(self, handoff_id: str) -> dict[str, Any] | None:
        """Return the validated terminal receipt for an idempotent replay."""
        with self._lock:
            self._ensure_healthy()
            receipt = self._completion_receipts.get(handoff_id)
            return dict(receipt) if receipt is not None else None

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe checkpoint for the supervisor's canonical task store."""
        with self._lock:
            payload = {
                "schema_version": "auren-crew-handoff-checkpoint-v3",
                "max_attempts": self.max_attempts,
                "queued": [
                    {"source_crew": h.source_crew, "target_crew": h.target_crew,
                     "task": h.task, "source_plan_digest": h.source_plan_digest,
                     "attempt": h.attempt, "handoff_id": h.handoff_id}
                    for h in self._queue
                ],
                "in_flight": [
                    {"source_crew": h.source_crew, "target_crew": h.target_crew,
                     "task": h.task, "source_plan_digest": h.source_plan_digest,
                     "attempt": h.attempt, "handoff_id": h.handoff_id}
                    for h in sorted(self._in_flight.values(), key=lambda item: item.handoff_id)
                ],
                "seen_ids": sorted(self._seen_ids),
                "completed": [list(key) for key in sorted(self._completed)],
                "completed_ids": sorted(self._completed_ids),
                "completion_receipts": {key: self._completion_receipts[key] for key in sorted(self._completion_receipts)},
                "retry_budget": self.retry_budget.snapshot() if self.retry_budget is not None else None,
            }
            payload["digest"] = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            return payload

    @classmethod
    def resume(
        cls,
        checkpoint: Mapping[str, Any],
        *,
        retry_budget: TaskRetryBudget | None = None,
        persist: Callable[[Mapping[str, Any]], None] | None = None,
        require_persistence: bool = False,
    ) -> "GraphHandoffController":
        if not isinstance(checkpoint, Mapping) or checkpoint.get("schema_version") not in {
            "auren-crew-handoff-checkpoint-v1", "auren-crew-handoff-checkpoint-v2",
            "auren-crew-handoff-checkpoint-v3"
        }:
            raise ValueError("unsupported handoff checkpoint")
        supplied_digest = checkpoint.get("digest")
        if checkpoint.get("schema_version") in {"auren-crew-handoff-checkpoint-v2", "auren-crew-handoff-checkpoint-v3"} and not supplied_digest:
            raise ValueError("handoff checkpoint digest is required for schema v2/v3")
        if supplied_digest is not None:
            payload = {key: value for key, value in checkpoint.items() if key != "digest"}
            expected_digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if supplied_digest != expected_digest:
                raise ValueError("handoff checkpoint digest mismatch")
        shared_budget = retry_budget
        saved_budget = checkpoint.get("retry_budget")
        if isinstance(saved_budget, Mapping):
            if shared_budget is None:
                shared_budget = TaskRetryBudget.resume(saved_budget)
            else:
                # The outer graph checkpoint is canonical for the task, but a
                # handoff snapshot may contain newer retry charges. Merge
                # monotonically so neither checkpoint can refund consumed budget.
                shared_budget.merge_snapshot(saved_budget)
        controller = cls(
            max_attempts=checkpoint.get("max_attempts", 3),
            retry_budget=shared_budget,
            persist=persist,
            require_persistence=require_persistence,
        )
        queued = checkpoint.get("queued")
        completed = checkpoint.get("completed")
        in_flight = checkpoint.get("in_flight", [])
        if not isinstance(queued, list) or not isinstance(completed, list) or not isinstance(in_flight, list):
            raise ValueError("checkpoint queue, in_flight, and completed records must be lists")
        if len(queued) + len(in_flight) > cls.MAX_PENDING_HANDOFFS:
            raise ValueError("handoff checkpoint exceeds the configured pending queue bound")

        def restore(raw: Mapping[str, Any]) -> CrewHandoff:
            source = raw.get("source_crew")
            target = raw.get("target_crew")
            task = raw.get("task")
            plan_digest = raw.get("source_plan_digest")
            attempt = raw.get("attempt", 0)
            if any(not isinstance(value, str) or not value.strip() for value in (source, target, task, plan_digest)):
                raise ValueError("invalid handoff checkpoint fields")
            if type(attempt) is not int or not 0 <= attempt < controller.max_attempts:
                raise ValueError("checkpoint handoff attempt is outside retry budget")
            expected_id = cls._handoff_id(source, target, task, plan_digest)
            handoff_id = raw.get("handoff_id", expected_id)
            if handoff_id != expected_id:
                raise ValueError("handoff checkpoint ID does not match its payload")
            return CrewHandoff(source, target, task, plan_digest, attempt, expected_id)

        for raw in queued:
            if not isinstance(raw, Mapping):
                raise ValueError("invalid queued handoff")
            item = restore(raw)
            if item.handoff_id in controller._seen_ids:
                raise ValueError("duplicate handoff ID in checkpoint queue")
            controller._queue.append(item)
            controller._seen_ids.add(item.handoff_id)
        for raw in in_flight:
            if not isinstance(raw, Mapping):
                raise ValueError("invalid in-flight handoff")
            item = restore(raw)
            if item.handoff_id in controller._seen_ids:
                raise ValueError("duplicate handoff ID in checkpoint in-flight records")
            # Specialist proposal adapters are read-only; an interrupted claim
            # can be requeued with the same idempotency key without repeating effects.
            controller._queue.append(item)
            controller._seen_ids.add(item.handoff_id)
        saved_seen = checkpoint.get("seen_ids", [])
        if not isinstance(saved_seen, list) or any(not isinstance(value, str) or not value.strip() for value in saved_seen):
            raise ValueError("invalid handoff seen_ids")
        controller._seen_ids.update(saved_seen)
        for item in completed:
            if not isinstance(item, list) or len(item) != 3 or any(not isinstance(value, str) for value in item):
                raise ValueError("invalid completed handoff key")
            controller._completed.add(tuple(item))
        completed_ids = checkpoint.get("completed_ids", [])
        if not isinstance(completed_ids, list) or any(
            not isinstance(value, str) or not value.strip() for value in completed_ids
        ):
            raise ValueError("invalid completed handoff IDs")
        if checkpoint.get("schema_version") == "auren-crew-handoff-checkpoint-v3":
            if len(completed_ids) != len(set(completed_ids)):
                raise ValueError("duplicate completed handoff ID")
            if not set(completed_ids).issubset(set(saved_seen)):
                raise ValueError("completed handoff IDs must be included in seen_ids")
            active_ids = {item.handoff_id for item in controller._queue} | set(controller._in_flight)
            if set(completed_ids) & active_ids:
                raise ValueError("completed handoff ID cannot also be queued or in-flight")
        completion_receipts = checkpoint.get("completion_receipts", {})
        if not isinstance(completion_receipts, Mapping):
            raise ValueError("invalid completed handoff review receipts")
        if checkpoint.get("schema_version") == "auren-crew-handoff-checkpoint-v3":
            if set(completion_receipts) != set(completed_ids):
                raise ValueError("every completed handoff must retain its independent review receipt")
            for handoff_id, receipt in completion_receipts.items():
                if not isinstance(handoff_id, str) or not handoff_id.strip() or not isinstance(receipt, Mapping):
                    raise ValueError("invalid completed handoff review receipt entry")
                controller._completion_receipts[handoff_id] = cls._validate_independent_review_receipt(receipt)
        controller._completed_ids.update(completed_ids)
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
