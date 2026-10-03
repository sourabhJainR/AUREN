"""Provider-neutral orchestration primitives for AUREN.

AUREN models Agent -> bounded Loop -> Graph -> Orchestration and can learn changes
to its own executable orchestration. Self-modification is candidate-based: a
candidate is statically validated, evaluated in isolation, regression and
safety gates must both pass, and only then is the executable overlay promoted.

The implementation deliberately treats the harness as part of the system under
evaluation: trajectories, environment fingerprints, evidence, regressions and
repair outcomes are durable learning signals rather than hidden model state.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import py_compile
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping
from .execution_strategy import ExecutionStrategy, execution_strategy


class NodeKind(str, Enum):
    AGENT = "agent"
    DETERMINISTIC = "deterministic"
    EVALUATOR = "evaluator"
    HUMAN = "human"
    ROUTER = "router"
    JOIN = "join"


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class RunStatus(str, Enum):
    RUNNING = "running"
    ACCEPTED = "accepted"
    FAILED = "failed"
    BLOCKED = "blocked"


class PromotionStatus(str, Enum):
    CANDIDATE = "candidate"
    REGRESSION_FAILED = "regression_failed"
    SAFETY_FAILED = "safety_failed"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True)
class Evidence:
    kind: str
    summary: str
    source: str = "runtime"
    digest: str = ""

    def __post_init__(self) -> None:
        if not self.digest:
            payload = f"{self.kind}|{self.source}|{self.summary}".encode()
            object.__setattr__(self, "digest", hashlib.sha256(payload).hexdigest())


@dataclass(frozen=True)
class Node:
    name: str
    kind: NodeKind
    run: Callable[[Mapping[str, Any]], Any]
    depends_on: tuple[str, ...] = ()
    max_attempts: int = 1
    evaluator: Callable[[Any], bool] | None = None
    repair: Callable[[Any, Mapping[str, Any]], Any] | None = None
    critical: bool = True
    risk: str = "medium"
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()


@dataclass
class NodeResult:
    node: str
    status: NodeStatus
    attempts: int = 0
    output: Any = None
    evidence: list[Evidence] = field(default_factory=list)
    error: str | None = None
    repair_count: int = 0


@dataclass
class OrchestrationRun:
    task_id: str
    intent_digest: str
    status: RunStatus = RunStatus.RUNNING
    results: dict[str, NodeResult] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    learned_candidates: list[dict[str, Any]] = field(default_factory=list)
    trajectory: list[dict[str, Any]] = field(default_factory=list)
    environment_fingerprint: str = ""
    stop_reason: str | None = None
    started_at: float = field(default_factory=time.time)

    def record(self, evidence: Evidence) -> None:
        self.evidence.append(evidence)


@dataclass(frozen=True)
class LearningSignal:
    """Evidence used to improve the harness without trusting a single outcome."""

    task_id: str
    intent_digest: str
    repair_count: int
    failure_count: int
    attempt_count: int
    evidence_count: int
    trajectory_digest: str
    environment_fingerprint: str
    transfer_key: str


@dataclass(frozen=True)
class OrchestrationCandidate:
    """Executable orchestration proposed by the learning engine.

    ``source`` is untrusted candidate code. It is never imported during static
    validation. The candidate module must expose ``build_graph()`` returning a
    :class:`Graph`; isolated regression/safety infrastructure owns execution.
    """

    candidate_id: str
    source: str
    parent_digest: str
    source_digest: str
    created_at: float
    reason: str
    status: PromotionStatus = PromotionStatus.CANDIDATE
    regression: bool | None = None
    safety: bool | None = None

    @classmethod
    def create(cls, source: str, parent_digest: str, reason: str) -> "OrchestrationCandidate":
        source_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        candidate_id = hashlib.sha256(f"{parent_digest}:{source_digest}".encode()).hexdigest()[:24]
        return cls(candidate_id, source, parent_digest, source_digest, time.time(), reason)


class SelfModificationEngine:
    """Create and promote executable orchestration only through hard gates.

    Research-informed boundary: candidate generation is cheap and autonomous;
    candidate execution is isolated; activation is impossible without both
    regression and safety evidence. This avoids the unsafe pattern of importing
    untrusted generated code merely to decide whether it is safe.
    """

    _FORBIDDEN_IMPORTS = {
        "subprocess", "socket", "requests", "urllib", "http", "ftplib",
        "ctypes", "multiprocessing", "shutil", "pathlib", "pickle",
    }
    _FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "system", "popen"}

    def __init__(self, active_dir: Path | str | None = None) -> None:
        self.active_dir = Path(active_dir or (Path.home() / ".auren" / "orchestration"))
        self.active_file = self.active_dir / "active.py"
        self.previous_file = self.active_dir / "previous.py"
        self.journal_file = self.active_dir / "promotion.jsonl"

    @classmethod
    def _validate_candidate(cls, source: str) -> None:
        if not source.strip():
            raise ValueError("self-modification candidate is empty")
        tree = ast.parse(source, filename="auren_candidate.py")
        functions = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if "build_graph" not in functions:
            raise ValueError("candidate must expose callable build_graph()")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root in cls._FORBIDDEN_IMPORTS:
                        raise ValueError(f"candidate imports forbidden module: {root}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".", 1)[0]
                if root in cls._FORBIDDEN_IMPORTS:
                    raise ValueError(f"candidate imports forbidden module: {root}")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in cls._FORBIDDEN_CALLS:
                raise ValueError(f"candidate uses forbidden call: {node.func.id}")
        with tempfile.NamedTemporaryFile("w", suffix=".py", encoding="utf-8", delete=False) as handle:
            handle.write(source)
            path = Path(handle.name)
        try:
            py_compile.compile(str(path), doraise=True)
        finally:
            path.unlink(missing_ok=True)

    def propose(self, source: str, parent_digest: str, reason: str) -> OrchestrationCandidate:
        candidate = OrchestrationCandidate.create(source, parent_digest, reason)
        self._validate_candidate(candidate.source)
        self._append({"event": "candidate", **self._record(candidate)})
        return candidate

    def evaluate_and_promote(
        self,
        candidate: OrchestrationCandidate,
        *,
        regression_gate: Callable[[OrchestrationCandidate], bool],
        safety_gate: Callable[[OrchestrationCandidate], bool],
    ) -> OrchestrationCandidate:
        """Run gates in order and atomically activate a passing candidate."""
        if not callable(regression_gate) or not callable(safety_gate):
            raise ValueError("both regression_gate and safety_gate are required")
        self._validate_candidate(candidate.source)

        regression_ok = bool(regression_gate(candidate))
        if not regression_ok:
            rejected = self._with_status(candidate, PromotionStatus.REGRESSION_FAILED, regression=False)
            self._append({"event": "reject", **self._record(rejected)})
            return rejected

        safety_ok = bool(safety_gate(candidate))
        if not safety_ok:
            rejected = self._with_status(candidate, PromotionStatus.SAFETY_FAILED, regression=True, safety=False)
            self._append({"event": "reject", **self._record(rejected)})
            return rejected

        promoted = self._with_status(candidate, PromotionStatus.PROMOTED, regression=True, safety=True)
        self._activate(promoted)
        self._append({"event": "promote", **self._record(promoted)})
        return promoted

    def rollback(self) -> None:
        if not self.previous_file.is_file():
            raise FileNotFoundError("no previous orchestration version is available")
        self.active_dir.mkdir(parents=True, exist_ok=True)
        temp = self.active_dir / ".active.rollback.tmp"
        temp.write_bytes(self.previous_file.read_bytes())
        os.replace(temp, self.active_file)
        self._append({"event": "rollback", "timestamp": time.time()})

    def _activate(self, candidate: OrchestrationCandidate) -> None:
        self.active_dir.mkdir(parents=True, exist_ok=True)
        if self.active_file.is_file():
            os.replace(self.active_file, self.previous_file)
        temp = self.active_dir / ".active.tmp"
        temp.write_text(candidate.source, encoding="utf-8")
        self._validate_candidate(temp.read_text(encoding="utf-8"))
        os.replace(temp, self.active_file)

    def _append(self, value: dict[str, Any]) -> None:
        self.active_dir.mkdir(parents=True, exist_ok=True)
        with self.journal_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, sort_keys=True) + "\n")

    @staticmethod
    def _record(candidate: OrchestrationCandidate) -> dict[str, Any]:
        return {
            "candidate_id": candidate.candidate_id,
            "parent_digest": candidate.parent_digest,
            "source_digest": candidate.source_digest,
            "created_at": candidate.created_at,
            "reason": candidate.reason,
            "status": candidate.status.value,
            "regression": candidate.regression,
            "safety": candidate.safety,
        }

    @staticmethod
    def _with_status(candidate: OrchestrationCandidate, status: PromotionStatus, *, regression: bool | None = None, safety: bool | None = None) -> OrchestrationCandidate:
        return OrchestrationCandidate(candidate.candidate_id, candidate.source, candidate.parent_digest, candidate.source_digest, candidate.created_at, candidate.reason, status, regression, safety)


class Graph:
    """Small dependency graph with deterministic topological execution order."""

    def __init__(self, nodes: list[Node]) -> None:
        self.nodes = {node.name: node for node in nodes}
        if len(self.nodes) != len(nodes):
            raise ValueError("graph contains duplicate node names")
        self._validate()

    def _validate(self) -> None:
        for node in self.nodes.values():
            missing = set(node.depends_on) - self.nodes.keys()
            if missing:
                raise ValueError(f"node {node.name} depends on missing nodes: {sorted(missing)}")
            if node.max_attempts < 1:
                raise ValueError(f"node {node.name} must have a positive attempt budget")
            if node.risk not in {"low", "medium", "high", "critical"}:
                raise ValueError(f"node {node.name} has invalid risk class: {node.risk}")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visiting:
                raise ValueError("graph contains a dependency cycle")
            if name in visited:
                return
            visiting.add(name)
            for dep in self.nodes[name].depends_on:
                visit(dep)
            visiting.remove(name)
            visited.add(name)

        for name in self.nodes:
            visit(name)

    def order(self) -> list[Node]:
        ordered: list[Node] = []
        done: set[str] = set()
        remaining = set(self.nodes)
        while remaining:
            ready = sorted(name for name in remaining if set(self.nodes[name].depends_on) <= done)
            if not ready:
                raise ValueError("graph could not be ordered")
            for name in ready:
                ordered.append(self.nodes[name])
                done.add(name)
                remaining.remove(name)
        return ordered

    def digest(self) -> str:
        payload = [{"name": n.name, "kind": n.kind.value, "depends_on": list(n.depends_on), "max_attempts": n.max_attempts, "risk": n.risk, "inputs": list(n.inputs), "outputs": list(n.outputs)} for n in self.order()]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class Orchestrator:
    """Execute a graph whose agentic nodes contain bounded local loops."""

    def __init__(self, graph: Graph, *, max_total_attempts: int = 32, allow_self_improvement: bool = True) -> None:
        if max_total_attempts < 1:
            raise ValueError("max_total_attempts must be positive")
        self.graph = graph
        self.max_total_attempts = max_total_attempts
        self.allow_self_improvement = allow_self_improvement

    @staticmethod
    def intent_digest(intent: str) -> str:
        return hashlib.sha256(intent.strip().encode()).hexdigest()

    @staticmethod
    def environment_fingerprint(context: Mapping[str, Any] | None) -> str:
        values = {"python": f"{os.sys.version_info.major}.{os.sys.version_info.minor}", "platform": os.name, "context_keys": sorted((context or {}).keys())}
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    def run(self, task_id: str, intent: str, context: Mapping[str, Any] | None = None) -> OrchestrationRun:
        run = OrchestrationRun(task_id=task_id, intent_digest=self.intent_digest(intent), environment_fingerprint=self.environment_fingerprint(context))
        state: dict[str, Any] = dict(context or {})
        state["intent_digest"] = run.intent_digest
        adaptive_policy = self._read_adaptive_policy(state)
        if adaptive_policy is not None:
            strategy_profile = execution_strategy(adaptive_policy["strategy"])
            state["auren_applied_adaptive_policy"] = adaptive_policy
            state["auren_execution_strategy"] = {
                "name": strategy_profile.name,
                "known": strategy_profile.known,
                "attempt_multiplier": strategy_profile.attempt_multiplier,
                "verification_depth": strategy_profile.verification_depth,
                "resource_lane": strategy_profile.resource_lane,
                "capability_bias": list(strategy_profile.capability_bias),
                "minimum_evidence": strategy_profile.minimum_evidence,
            }
            run.trajectory.append({
                "event": "adaptive_policy_applied",
                "version": adaptive_policy["version"],
                "strategy": adaptive_policy["strategy"],
                "iteration_target": adaptive_policy["iteration_target"],
                "strategy_known": strategy_profile.known,
                "verification_depth": strategy_profile.verification_depth,
                "resource_lane": strategy_profile.resource_lane,
                "minimum_evidence": strategy_profile.minimum_evidence,
            })
        total_attempts = 0

        for node in self.graph.order():
            dependency_results = [run.results[name] for name in node.depends_on]
            if any(result.status in {NodeStatus.FAILED, NodeStatus.BLOCKED} for result in dependency_results):
                result = NodeResult(node.name, NodeStatus.SKIPPED, error="dependency failed")
                run.results[node.name] = result
                run.trajectory.append({"node": node.name, "status": result.status.value, "reason": "dependency failed"})
                if node.critical:
                    run.status = RunStatus.BLOCKED
                    run.stop_reason = f"critical dependency failure before {node.name}"
                    return run
                continue

            attempt_limit = self._adaptive_attempt_limit(node, state)
            run.trajectory.append({
                "node": node.name,
                "status": NodeStatus.RUNNING.value,
                "risk": node.risk,
                "attempt_limit": attempt_limit,
            })
            result = self._execute_node(node, state, run, total_attempts, max_attempts_override=attempt_limit)
            run.results[node.name] = result
            total_attempts += result.attempts
            run.trajectory.append({"node": node.name, "status": result.status.value, "attempts": result.attempts, "repairs": result.repair_count})
            if result.evidence:
                run.evidence.extend(result.evidence)
            if result.status in {NodeStatus.FAILED, NodeStatus.BLOCKED} and node.critical:
                run.status = RunStatus.FAILED if result.status == NodeStatus.FAILED else RunStatus.BLOCKED
                run.stop_reason = result.error or f"critical node {node.name} did not pass"
                return run
            state[node.name] = result.output
            if total_attempts >= self.max_total_attempts:
                run.status = RunStatus.BLOCKED
                run.stop_reason = "global attempt budget exhausted"
                return run

        run.status = RunStatus.ACCEPTED
        run.stop_reason = "all critical graph nodes passed"
        if self.allow_self_improvement:
            run.learned_candidates.extend(self._propose_learning(run))
        return run

    def _execute_node(
        self,
        node: Node,
        state: Mapping[str, Any],
        run: OrchestrationRun,
        total_attempts: int,
        *,
        max_attempts_override: int | None = None,
    ) -> NodeResult:
        attempts = 0
        repairs = 0
        evidence: list[Evidence] = []
        last_output: Any = None
        last_error: str | None = None
        max_attempts = max(1, node.max_attempts)
        if max_attempts_override is not None:
            max_attempts = min(max_attempts, max(1, int(max_attempts_override)))

        while attempts < max_attempts and total_attempts + attempts < self.max_total_attempts:
            attempts += 1
            try:
                output = node.run(state)
                last_output = output
                evidence.append(Evidence("node-output", f"{node.name} attempt {attempts} completed", node.name))
                passed = node.evaluator(output) if node.evaluator else True
                if passed:
                    evidence.append(Evidence("evaluation", f"{node.name} passed on attempt {attempts}", node.name))
                    return NodeResult(node.name, NodeStatus.PASSED, attempts, output, evidence, repair_count=repairs)
                last_error = "evaluator rejected node output"
                evidence.append(Evidence("evaluation-failure", last_error, node.name))
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                evidence.append(Evidence("execution-error", last_error, node.name))

            if node.repair is None or attempts >= max_attempts:
                break
            repairs += 1
            try:
                last_output = node.repair(last_output, state)
                evidence.append(Evidence("repair", f"{node.name} produced a repair candidate {repairs}", node.name))
            except Exception as exc:
                last_error = f"repair {type(exc).__name__}: {exc}"
                evidence.append(Evidence("repair-error", last_error, node.name))
                break

        return NodeResult(node.name, NodeStatus.FAILED, attempts, last_output, evidence, last_error, repairs)

    @staticmethod
    def _read_adaptive_policy(state: Mapping[str, Any]) -> dict[str, Any] | None:
        raw = state.get("auren_adaptive_policy")
        if not isinstance(raw, Mapping):
            return None
        version = str(raw.get("version", "")).strip()
        strategy = str(raw.get("strategy", "")).strip()
        try:
            target = float(raw.get("iteration_target", 0.0))
        except (TypeError, ValueError):
            return None
        if not version or not strategy or not 1.0 <= target <= 32.0:
            return None
        return {
            "version": version,
            "strategy": strategy,
            "iteration_target": target,
        }

    @classmethod
    def _adaptive_attempt_limit(cls, node: Node, state: Mapping[str, Any]) -> int | None:
        if node.kind != NodeKind.AGENT:
            return None
        policy = cls._read_adaptive_policy(state)
        if policy is None:
            return None
        profile = execution_strategy(policy["strategy"])
        # The learned target is a ceiling, never an expansion of a node's
        # statically declared safety budget. Ceil keeps fractional targets
        # useful while the node's own max_attempts remains authoritative.
        target = policy["iteration_target"] * profile.attempt_multiplier
        return min(node.max_attempts, max(1, int(target + 0.999999)))

    @staticmethod
    def learning_signal(run: OrchestrationRun) -> LearningSignal:
        repairs = sum(result.repair_count for result in run.results.values())
        failures = sum(1 for result in run.results.values() if result.status == NodeStatus.FAILED)
        attempts = sum(result.attempts for result in run.results.values())
        trajectory_digest = hashlib.sha256(json.dumps(run.trajectory, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        transfer_key = hashlib.sha256(f"{run.intent_digest}:{run.environment_fingerprint}".encode()).hexdigest()[:16]
        return LearningSignal(run.task_id, run.intent_digest, repairs, failures, attempts, len(run.evidence), trajectory_digest, run.environment_fingerprint, transfer_key)

    @staticmethod
    def _propose_learning(run: OrchestrationRun) -> list[dict[str, Any]]:
        signal = Orchestrator.learning_signal(run)
        repairs = signal.repair_count
        failures = signal.failure_count
        if repairs == 0 and failures == 0:
            return []
        return [{
            "type": "strategy-candidate",
            "task_id": run.task_id,
            "intent_digest": run.intent_digest,
            "observations": signal.__dict__,
            "promotion": "candidate -> static-check -> regression -> safety -> shadow -> canary -> active",
            "requires_regression_evaluation": True,
            "requires_safety_evaluation": True,
            "requires_trajectory_evidence": True,
        }]

    def replay(self, run: OrchestrationRun) -> dict[str, Any]:
        return {
            "task_id": run.task_id,
            "intent_digest": run.intent_digest,
            "status": run.status.value,
            "stop_reason": run.stop_reason,
            "environment_fingerprint": run.environment_fingerprint,
            "graph_digest": self.graph.digest(),
            "trajectory": run.trajectory,
            "nodes": {name: {"status": result.status.value, "attempts": result.attempts, "repair_count": result.repair_count, "evidence": [item.digest for item in result.evidence]} for name, result in run.results.items()},
            "evidence": [item.digest for item in run.evidence],
            "learning_signal": self.learning_signal(run).__dict__,
            "learned_candidates": run.learned_candidates,
        }

    def replay_json(self, run: OrchestrationRun) -> str:
        return json.dumps(self.replay(run), sort_keys=True, separators=(",", ":"))
