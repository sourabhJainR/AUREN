"""Provider-neutral bounded state-graph execution runtime.

The runtime keeps execution semantics small and explicit: JSON-compatible state,
canonical digests, validated checkpoints, deterministic merge order, bounded
retries, interrupts and bounded parallel supersteps. Provider/model/tool
execution remains outside this module.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
import json
from hashlib import sha256
from threading import Event
from typing import Any, Callable, Mapping, MutableMapping, Protocol, Sequence

State = MutableMapping[str, Any]
Node = Callable[[Mapping[str, Any]], Mapping[str, Any]]
Router = Callable[[Mapping[str, Any]], str | Sequence[str]]
Reducer = Callable[[Any, Any], Any]
_STATE_TYPES = (str, int, float, bool, type(None))


def canonical_json_bytes(value: object) -> bytes:
    try:
        return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TypeError("state and checkpoint payloads must be JSON-compatible") from exc


def state_digest(value: object) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()[:16]


def validate_state(value: object, *, label: str = "state") -> None:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")

    def walk(item: object, path: str) -> None:
        if isinstance(item, _STATE_TYPES):
            if isinstance(item, float) and (item != item or item in (float("inf"), float("-inf"))):
                raise ValueError(f"{label} contains non-finite number at {path}")
            return
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str) or not key:
                    raise TypeError(f"{label} keys must be non-empty strings at {path}")
                walk(child, f"{path}.{key}")
            return
        if isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                walk(child, f"{path}[{index}]")
            return
        raise TypeError(f"{label} contains unsupported value at {path}: {type(item).__name__}")

    walk(dict(value), label)


class CheckpointStore(Protocol):
    def save(self, checkpoint: "Checkpoint") -> None: ...
    def load(self, run_id: str) -> "Checkpoint | None": ...


@dataclass(frozen=True)
class NodeContract:
    """Optional bounded input/output contract for a graph node."""
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(set(self.inputs)) != len(self.inputs) or len(set(self.outputs)) != len(self.outputs):
            raise ValueError("node contract keys must be unique")
        if any(not key.strip() for key in (*self.inputs, *self.outputs)):
            raise ValueError("node contract keys must be non-empty")


@dataclass(frozen=True)
class EdgeContract:
    """Data contract carried by a graph edge."""
    source: str
    target: str
    data_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.source or not self.target:
            raise ValueError("edge contract endpoints are required")
        if any(not key.strip() for key in self.data_keys):
            raise ValueError("edge data keys must be non-empty")


@dataclass(frozen=True)
class ConvergenceGuard:
    """Bounded loop guard that deduplicates against everything observed."""
    max_dry_rounds: int = 2
    max_seen: int = 10000

    def __post_init__(self) -> None:
        if self.max_dry_rounds < 1 or self.max_seen < 1:
            raise ValueError("convergence limits must be positive")

    def unique_rounds(self, rounds: Sequence[Sequence[object]]) -> tuple[tuple[object, ...], ...]:
        seen: set[str] = set()
        accepted: list[tuple[object, ...]] = []
        dry = 0
        import hashlib
        for current in rounds:
            fresh: list[object] = []
            for item in current:
                key = hashlib.sha256(canonical_json_bytes(item)).hexdigest()
                if key not in seen:
                    seen.add(key)
                    if len(seen) > self.max_seen:
                        raise RuntimeError("convergence guard exceeded max_seen")
                    fresh.append(item)
            if fresh:
                dry = 0
                accepted.append(tuple(fresh))
            else:
                dry += 1
                if dry >= self.max_dry_rounds:
                    break
        return tuple(accepted)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    retryable_exceptions: tuple[type[BaseException], ...] = (Exception,)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if not self.retryable_exceptions:
            raise ValueError("retryable_exceptions must not be empty")

    def allows(self, exc: BaseException) -> bool:
        return isinstance(exc, self.retryable_exceptions)


@dataclass(frozen=True)
class Checkpoint:
    run_id: str
    step: int
    state: dict[str, Any]
    next_nodes: tuple[str, ...]
    trace: tuple[str, ...] = ()
    state_digest: str = ""

    def __post_init__(self) -> None:
        validate_state(self.state, label="checkpoint.state")
        expected = state_digest(self.state)
        if self.state_digest and self.state_digest != expected:
            raise ValueError("checkpoint state digest mismatch")
        object.__setattr__(self, "state_digest", expected)
        if self.step < 0:
            raise ValueError("checkpoint step cannot be negative")
        if not self.run_id:
            raise ValueError("checkpoint run_id is required")


@dataclass(frozen=True)
class GraphInterrupt(Exception):
    run_id: str
    step: int
    state: dict[str, Any]
    next_nodes: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        validate_state(self.state, label="interrupt.state")

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True)
class GraphEvent:
    step: int
    node: str
    status: str
    attempts: int = 1
    detail: str = ""


@dataclass(frozen=True)
class GraphRun:
    run_id: str
    state: dict[str, Any]
    events: tuple[GraphEvent, ...]
    trace: tuple[str, ...]
    steps: int
    interrupted: bool = False

    @property
    def digest(self) -> str:
        return state_digest({"run_id": self.run_id, "state": self.state, "events": [event.__dict__ for event in self.events], "trace": list(self.trace), "steps": self.steps, "interrupted": self.interrupted})

    def as_dict(self) -> dict[str, object]:
        return {"run_id": self.run_id, "state": self.state, "events": [event.__dict__ for event in self.events], "trace": list(self.trace), "steps": self.steps, "interrupted": self.interrupted, "digest": self.digest}


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self._items: dict[str, Checkpoint] = {}

    def save(self, checkpoint: Checkpoint) -> None:
        self._items[checkpoint.run_id] = checkpoint

    def load(self, run_id: str) -> Checkpoint | None:
        return self._items.get(run_id)


class StateGraph:
    START = "__start__"
    END = "__end__"
    EFFECTS = {"pure", "idempotent", "external"}

    def __init__(self, *, reducers: Mapping[str, Reducer] | None = None) -> None:
        self._nodes: dict[str, Node] = {}
        self._effects: dict[str, str] = {}
        self._edges: dict[str, tuple[str, ...]] = {}
        self._routers: dict[str, Router] = {}
        self._contracts: dict[str, NodeContract] = {}
        self._edge_contracts: dict[tuple[str, str], EdgeContract] = {}
        self._reducers = dict(reducers or {})
        self._retry: dict[str, RetryPolicy] = {}
        self._before: set[str] = set()
        self._after: set[str] = set()

    def add_node(self, name: str, node: Node, *, retry_policy: RetryPolicy | None = None, effect: str = "pure", contract: NodeContract | None = None) -> "StateGraph":
        if not name.strip() or name in {self.START, self.END}: raise ValueError("invalid node name")
        if name in self._nodes: raise ValueError(f"duplicate node: {name}")
        if effect not in self.EFFECTS: raise ValueError(f"unsupported node effect: {effect}")
        if retry_policy and retry_policy.max_attempts > 1 and effect == "external": raise ValueError("external nodes are not retryable; model the operation as idempotent before enabling retry")
        self._nodes[name] = node; self._effects[name] = effect
        if contract is not None: self._contracts[name] = contract
        if retry_policy: self._retry[name] = retry_policy
        return self

    def add_edge(self, source: str, target: str, *, data_keys: Sequence[str] = ()) -> "StateGraph":
        self._validate_source(source); self._validate_target(target)
        keys = tuple(data_keys)
        source_contract = self._contracts.get(source)
        target_contract = self._contracts.get(target)
        if keys and source_contract and not set(keys).issubset(source_contract.outputs):
            raise ValueError(f"edge {source}->{target} references undeclared source outputs")
        if keys and target_contract and not set(keys).issubset(target_contract.inputs):
            raise ValueError(f"edge {source}->{target} references undeclared target inputs")
        self._edges[source] = self._edges.get(source, ()) + (target,)
        if keys: self._edge_contracts[(source, target)] = EdgeContract(source, target, keys)
        return self

    def add_join(self, source: str, target: str) -> "StateGraph":
        """Declare an explicit join edge while retaining normal StateGraph semantics."""
        return self.add_edge(source, target)

    def add_conditional_edges(self, source: str, router: Router) -> "StateGraph":
        if source not in self._nodes: raise ValueError(f"unknown source node: {source}")
        self._routers[source] = router; return self

    def interrupt_before(self, *nodes: str) -> "StateGraph":
        for node in nodes: self._validate_target(node, allow_end=False)
        self._before.update(nodes); return self

    def interrupt_after(self, *nodes: str) -> "StateGraph":
        for node in nodes: self._validate_target(node, allow_end=False)
        self._after.update(nodes); return self

    def compile(self) -> "CompiledStateGraph":
        if not self._nodes: raise ValueError("graph must contain at least one node")
        if self.START not in self._edges: raise ValueError("graph must define an entry edge from START")
        return CompiledStateGraph(self)

    def _validate_source(self, source: str) -> None:
        if source != self.START and source not in self._nodes: raise ValueError(f"unknown source node: {source}")

    def _validate_target(self, target: str, *, allow_end: bool = True) -> None:
        if target != self.END and target not in self._nodes: raise ValueError(f"unknown graph node: {target}")
        if not allow_end and target == self.END: raise ValueError("END is not a node")


class CompiledStateGraph:
    def __init__(self, graph: StateGraph) -> None: self._g = graph

    def invoke(self, state: Mapping[str, Any], *, run_id: str = "run", checkpoint: CheckpointStore | None = None, resume: bool = False, max_steps: int = 100, parallel_nodes: Callable[[str], bool] | None = None, max_parallel_nodes: int = 1, node_timeout_seconds: float | None = None, cancellation: Event | None = None, parallel_failure_mode: str = "fail_fast") -> GraphRun:
        validate_state(state)
        if max_steps < 1: raise ValueError("max_steps must be positive")
        if max_parallel_nodes < 1: raise ValueError("max_parallel_nodes must be positive")
        if parallel_failure_mode not in {"fail_fast", "isolate"}: raise ValueError("parallel_failure_mode must be fail_fast or isolate")
        if node_timeout_seconds is not None and node_timeout_seconds <= 0: raise ValueError("node_timeout_seconds must be positive")
        existing = checkpoint.load(run_id) if resume and checkpoint else None
        current: dict[str, Any] = dict(existing.state if existing else state)
        next_nodes = list(existing.next_nodes if existing else self._g._edges[StateGraph.START])
        events: list[GraphEvent] = []
        trace = list(existing.trace if existing else ())
        step = existing.step if existing else 0
        skip_before_once = existing is not None
        while next_nodes:
            if cancellation and cancellation.is_set():
                self._checkpoint(checkpoint, run_id, step, current, next_nodes, trace)
                return GraphRun(run_id, dict(current), tuple(events), tuple(trace), step, True)
            if step >= max_steps: raise RuntimeError(f"graph exceeded max_steps={max_steps}")
            step += 1; snapshot = dict(current)
            if not skip_before_once:
                for name in next_nodes:
                    if name != StateGraph.END and name in self._g._before:
                        self._checkpoint(checkpoint, run_id, step - 1, current, next_nodes, trace)
                        raise GraphInterrupt(run_id, step - 1, dict(current), tuple(next_nodes), f"interrupted before {name}")
            skip_before_once = False
            parallel = [name for name in next_nodes if name != StateGraph.END and parallel_nodes and parallel_nodes(name)]
            sequential = [name for name in next_nodes if name != StateGraph.END and name not in parallel]
            results_by_name: dict[str, tuple[Mapping[str, Any], int]] = {}
            isolated_failures: set[str] = set()
            if cancellation and cancellation.is_set():
                self._checkpoint(checkpoint, run_id, step - 1, current, next_nodes, trace)
                return GraphRun(run_id, dict(current), tuple(events), tuple(trace), step - 1, True)
            if parallel:
                executor = ThreadPoolExecutor(max_workers=min(max_parallel_nodes, len(parallel)))
                futures = {name: executor.submit(self._run_node, name, snapshot) for name in parallel}
                try:
                    for name in parallel:
                        if cancellation and cancellation.is_set():
                            for future in futures.values(): future.cancel()
                            executor.shutdown(wait=False, cancel_futures=True)
                            self._checkpoint(checkpoint, run_id, step - 1, current, next_nodes, trace)
                            return GraphRun(run_id, dict(current), tuple(events), tuple(trace), step - 1, True)
                        try:
                            results_by_name[name] = futures[name].result(timeout=node_timeout_seconds)
                        except Exception as exc:
                            if parallel_failure_mode == "fail_fast":
                                raise
                            isolated_failures.add(name)
                            events.append(GraphEvent(step, name, "failed-isolated", 1, type(exc).__name__))
                except FutureTimeoutError as exc:
                    for future in futures.values(): future.cancel()
                    executor.shutdown(wait=False, cancel_futures=True)
                    if parallel_failure_mode == "fail_fast":
                        raise TimeoutError("parallel node exceeded timeout") from exc
                else:
                    executor.shutdown(wait=True)
            for name in sequential:
                if cancellation and cancellation.is_set():
                    self._checkpoint(checkpoint, run_id, step - 1, current, next_nodes, trace)
                    return GraphRun(run_id, dict(current), tuple(events), tuple(trace), step - 1, True)
                results_by_name[name] = self._run_node_with_timeout(name, snapshot, node_timeout_seconds)
            for name in next_nodes:
                if name == StateGraph.END or name in isolated_failures: continue
                output, attempts = results_by_name[name]
                self._merge(current, output); events.append(GraphEvent(step, name, "completed", attempts)); trace.append(name)
                if name in self._g._after:
                    next_after = self._next(name, current); self._checkpoint(checkpoint, run_id, step, current, next_after, trace)
                    raise GraphInterrupt(run_id, step, dict(current), tuple(next_after), f"interrupted after {name}")
            next_set: list[str] = []
            for name in next_nodes:
                if name != StateGraph.END and name not in isolated_failures: next_set.extend(self._next(name, current))
            next_nodes = list(dict.fromkeys(next_set)); self._checkpoint(checkpoint, run_id, step, current, next_nodes, trace)
            if StateGraph.END in next_nodes: next_nodes = []
        return GraphRun(run_id, dict(current), tuple(events), tuple(trace), step)

    def _checkpoint(self, store: CheckpointStore | None, run_id: str, step: int, state: Mapping[str, Any], next_nodes: Sequence[str], trace: Sequence[str]) -> None:
        if store: store.save(Checkpoint(run_id, step, dict(state), tuple(next_nodes), tuple(trace)))

    def _run_node_with_timeout(self, name: str, state: Mapping[str, Any], timeout: float | None) -> tuple[Mapping[str, Any], int]:
        if timeout is None: return self._run_node(name, state)
        executor = ThreadPoolExecutor(max_workers=1); future = executor.submit(self._run_node, name, state)
        try:
            result = future.result(timeout=timeout)
        except FutureTimeoutError as exc:
            future.cancel(); executor.shutdown(wait=False, cancel_futures=True)
            raise TimeoutError(f"node {name} exceeded timeout") from exc
        else:
            executor.shutdown(wait=True); return result

    def _run_node(self, name: str, state: Mapping[str, Any]) -> tuple[Mapping[str, Any], int]:
        policy = self._g._retry.get(name, RetryPolicy()); last: BaseException | None = None
        for attempt in range(1, policy.max_attempts + 1):
            try:
                output = self._g._nodes[name](dict(state))
                if not isinstance(output, Mapping): raise TypeError(f"node {name} must return a mapping")
                validate_state(output, label=f"node {name} output")
                return dict(output), attempt
            except BaseException as exc:
                last = exc
                if attempt >= policy.max_attempts or not policy.allows(exc): raise
        assert last is not None; raise last

    def _next(self, name: str, state: Mapping[str, Any]) -> tuple[str, ...]:
        if name in self._g._routers:
            routed = self._g._routers[name](dict(state)); values = (routed,) if isinstance(routed, str) else tuple(routed)
        else: values = self._g._edges.get(name, ())
        for value in values: self._g._validate_target(value)
        return tuple(values)

    def _merge(self, state: MutableMapping[str, Any], update: Mapping[str, Any]) -> None:
        for key, value in update.items():
            state[key] = self._g._reducers[key](state[key], value) if key in state and key in self._g._reducers else value
            validate_state({key: state[key]}, label=f"state.{key}")


__all__ = ["Checkpoint", "ConvergenceGuard", "EdgeContract", "NodeContract", "CompiledStateGraph", "GraphEvent", "GraphInterrupt", "GraphRun", "InMemoryCheckpointStore", "RetryPolicy", "StateGraph", "canonical_json_bytes", "state_digest", "validate_state"]
