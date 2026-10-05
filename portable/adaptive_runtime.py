"""Reusable facade that composes AER orchestration and agent capabilities."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .adaptive_learning import AdaptiveLearningStore, DeferredLearningJob
from .adaptive_tuning import AdaptiveTuner, MaintenanceReceipt
from .automation_scheduler import AutomationScheduler
from .capability_fabric import Capability, CapabilityFabric, ProviderAdapter, ProviderAdapterRegistry
from .context_graph import ContextGraph
from .context_resolver import ContextResolution, ContextResolver
from .cognitive_controller import CognitiveController
from .cognitive_learning import BeliefContext, LearningSignal
from .cognitive_loop import CognitiveEpisodeReceipt, CognitiveLoop
from .cognitive_runtime import CognitiveRuntime
from .empirical_improvement import EmpiricalImprovement, ImprovementReport
from .hypothesis_engine import BeliefEvidence
from .information_planner import InformationAction
from .lifecycle_hooks import HookBus, HookPhase, HookedExecution
from .orchestration import Graph, OrchestrationRun, Orchestrator, execution_strategy
from .output_quality import OutputQualityGate, QualityResult
from .persistent_memory import PersistentMemory
from .provider_fabric import CapabilityRequest, ProviderFabric, RoutingDecision
from .session_state import SessionCheckpoint, SessionStore
from .trigger_runtime import TriggerEvent, TriggerRuntime
from .world_model import PredictionError, WorldPrediction
from .world_mega_model import WorldMegaModel


class AdaptiveRuntime:
    """Single AER composition point for provider, capability and durable state."""

    def __init__(
        self,
        graph: Graph,
        *,
        session_store: SessionStore | None = None,
        provider_fabric: ProviderFabric | None = None,
        hooks: HookBus | None = None,
        capability_fabric: CapabilityFabric | None = None,
        persistent_memory: PersistentMemory | None = None,
        automation_scheduler: AutomationScheduler | None = None,
        output_quality: OutputQualityGate | None = None,
        provider_adapters: ProviderAdapterRegistry | None = None,
        max_total_attempts: int = 32,
        maintenance_interval_seconds: int = 300,
        maintenance_budget: int = 20,
    ) -> None:
        if maintenance_interval_seconds < 1:
            raise ValueError("maintenance_interval_seconds must be positive")
        if maintenance_budget < 1:
            raise ValueError("maintenance_budget must be positive")
        self.orchestrator = Orchestrator(graph, max_total_attempts=max_total_attempts)
        self.session_store = session_store or SessionStore()
        self.provider_fabric = provider_fabric or ProviderFabric()
        self.hooks = hooks or HookBus()
        self.capability_fabric = capability_fabric or CapabilityFabric()
        aer_state = Path.home() / ".aer"
        self.persistent_memory = persistent_memory or PersistentMemory(aer_state / "memory" / "memory.db")
        self.automation_scheduler = automation_scheduler or AutomationScheduler(aer_state / "automation" / "automation.db")
        self.output_quality = output_quality or OutputQualityGate()
        self.provider_adapters = provider_adapters or ProviderAdapterRegistry()
        self.trigger_runtime = TriggerRuntime(self.automation_scheduler)
        self.maintenance_interval_seconds = maintenance_interval_seconds
        self.maintenance_budget = maintenance_budget
        self.last_cognitive_episode: CognitiveEpisodeReceipt | None = None
        self.last_cognitive_plan: dict[str, object] | None = None
        self.last_learning_signal: LearningSignal | None = None
        self.last_deferred_learning_job: DeferredLearningJob | None = None
        self.last_maintenance_receipt: MaintenanceReceipt | None = None
        from .execution_controller import ExecutionController
        self.execution_controller = ExecutionController(Path.home() / ".aer" / "executive")

    def capability(self, name: str, preferred: tuple[str, ...] = ()) -> RoutingDecision:
        return self.provider_fabric.route(CapabilityRequest(name, preferred))

    def plan_capabilities(self, requested: Iterable[str], *, network_allowed: bool,
                          sandbox_available: bool = True, max_risk: str = "high") -> list[Capability]:
        return self.capability_fabric.plan(requested, network_allowed=network_allowed,
                                           sandbox_available=sandbox_available, max_risk=max_risk)

    def register_provider_adapter(self, name: str, capabilities: Iterable[str], *, priority: int = 0) -> None:
        self.provider_adapters.register(ProviderAdapter(name, frozenset(capabilities), priority=priority))

    def resolve_provider_adapter(self, required: Iterable[str], preferred: tuple[str, ...] = ()) -> ProviderAdapter:
        return self.provider_adapters.resolve(required, preferred)

    def quality_check(self, *, acceptance_met: bool, verification_passed: bool,
                      evidence_count: int, diff_clean: bool, scope_clean: bool,
                      unresolved: int = 0) -> QualityResult:
        return self.output_quality.evaluate(acceptance_met=acceptance_met, verification_passed=verification_passed,
                                            evidence_count=evidence_count, diff_clean=diff_clean,
                                            scope_clean=scope_clean, unresolved=unresolved)

    def resolve_context(self, project_root: Path | str, task: str, *, node_id: str | None = None,
                        workspace_id: str | None = None, required: Sequence[str] = (),
                        memory_limit: int = 12, graph_limit: int = 24) -> ContextResolution:
        project_key = self.session_store.project_key(project_root)
        graph = ContextGraph(self.persistent_memory, project_key)
        resolver = ContextResolver(self.persistent_memory, graph)
        return resolver.resolve(task, node_id=node_id, workspace_id=workspace_id, required=required,
                                memory_limit=memory_limit, graph_limit=graph_limit)

    def cognition(self, project_root: Path | str) -> CognitiveRuntime:
        project_key = self.session_store.project_key(project_root)
        return CognitiveRuntime.create(self.persistent_memory, project_key)

    def mega_model(self, project_root: Path | str) -> WorldMegaModel:
        """Return the project-scoped world/cognition/self-evolution control plane."""
        project_key = self.session_store.project_key(project_root)
        return WorldMegaModel(self.persistent_memory, project_key)

    def emit_trigger(self, kind: str, payload: Mapping[str, Any], *, event_id: str | None = None,
                     max_attempts: int = 3) -> TriggerEvent:
        return self.trigger_runtime.emit(kind, payload, event_id=event_id, max_attempts=max_attempts)

    def dispatch_triggers(self, handler: Any, *, limit: int = 20) -> list[Any]:
        return self.trigger_runtime.dispatch_due(handler, limit=limit)

    def ensure_learning_maintenance(self, project_root: Path | str, *, interval_seconds: int | None = None) -> object:
        """Create the project learning schedule once and return its durable receipt."""
        root = Path(project_root).expanduser().resolve()
        interval = interval_seconds or self.maintenance_interval_seconds
        task = json.dumps({"kind": "adaptive_learning", "project_root": str(root)}, sort_keys=True)
        existing = self.automation_scheduler.find_task(task)
        if existing is not None:
            return existing
        start = datetime.now(timezone.utc) - timedelta(seconds=interval)
        return self.automation_scheduler.add(task, interval, max_attempts=3, start=start)

    def maintenance_tick(self, project_root: Path | str, *, budget: int | None = None) -> MaintenanceReceipt | None:
        """Claim and execute one due maintenance cycle without entering orchestration."""
        root = Path(project_root).expanduser().resolve()
        schedule = self.ensure_learning_maintenance(root)
        claim = self.automation_scheduler.claim(schedule.id)
        if claim is None:
            return None
        started = datetime.now(timezone.utc).isoformat()
        tuner = AdaptiveTuner(self.persistent_memory, self.session_store.project_key(root))
        errors: list[str] = []
        strategy_action = "hold"
        policy_version = tuner.current_policy().version
        processed = 0
        status = "success"
        try:
            processed_jobs = self.process_learning(root, limit=budget or self.maintenance_budget, dream=True)
            processed = len(processed_jobs)
            history = tuner.history(scope="global", limit=1000)
            scopes = sorted({record.capability for record in history if record.capability}) or ["global"]
            for scope in scopes:
                policy = tuner.current_policy(scope)
                strategies = sorted({record.strategy for record in history if record.capability == scope and record.strategy != policy.strategy})
                candidate = None
                if strategies:
                    candidate = max(strategies, key=lambda item: sum(1 for record in history if record.capability == scope and record.strategy == item))
                decision = tuner.evaluate(scope, candidate_strategy=candidate)
                if decision.action != "hold":
                    strategy_action = decision.action
                    policy_version = decision.policy_version
        except Exception as exc:
            status = "retryable"
            errors.append(f"{type(exc).__name__}: {exc}")
        receipt = tuner.record_maintenance_receipt(
            started_at=started,
            jobs_processed=processed,
            strategy_action=strategy_action,
            policy_version=policy_version,
            errors=errors,
        )
        self.last_maintenance_receipt = receipt
        self.automation_scheduler.finish(schedule.id, claim, status, receipt.digest)
        return receipt

    def recent_maintenance(self, project_root: Path | str, limit: int = 20) -> tuple[MaintenanceReceipt, ...]:
        project_key = self.session_store.project_key(project_root)
        return AdaptiveTuner(self.persistent_memory, project_key).recent_receipts(limit)

    def current_adaptive_policy(self, project_root: Path | str, scope: str = "global"):
        project_key = self.session_store.project_key(project_root)
        return AdaptiveTuner(self.persistent_memory, project_key).current_policy(scope)

    def register_task_handler(self, name: str, handler: Any) -> str:
        """Register a restart-safe module-level task handler."""
        return self.execution_controller.register_handler(name, handler)

    def submit_task(self, handler: str, payload: Mapping[str, Any], *, goal: str | None = None) -> Any:
        """Persist a task so the service can resume it after a restart."""
        return self.execution_controller.submit(handler, payload, goal=goal)

    def resume_tasks(self, *, limit: int = 20) -> list[Any]:
        """Resume pending/running durable tasks after process or host restart."""
        return self.execution_controller.resume_pending(limit=limit)

    def run(self, **kwargs: Any) -> OrchestrationRun:
        """Default execution path: durable executive supervision around graph execution."""
        session_id = str(kwargs["session_id"])
        task_id = str(kwargs["task_id"])
        intent = str(kwargs["intent"])
        work_id = self.execution_controller.work_unit_for_session(session_id, f"{task_id}: {intent}")
        attempts = {"count": 0}
        last = {"result": None}

        def execute(iteration: int):
            attempts["count"] = iteration
            context = dict(kwargs.get("context") or {})
            context["auren_supervisor_iteration"] = iteration
            if iteration > 1:
                context["auren_recovery_mode"] = "fresh-retry"
            call = dict(kwargs)
            call["context"] = context
            try:
                result = self._run_once(**call)
                last["result"] = result
                status = "completed" if result.status.value == "accepted" else "failed"
                return status, result.stop_reason or result.status.value, tuple(item.digest for item in result.evidence)
            except Exception as exc:
                last["result"] = None
                return "failed", f"{type(exc).__name__}: {exc}", (f"exception:{type(exc).__name__}",)

        def review(_iteration: int):
            result = last["result"]
            ok = result is not None and result.status.value == "accepted"
            refs = tuple(item.digest for item in result.evidence) if result is not None else ()
            return ok, result.stop_reason if result is not None else "no result", refs

        max_iterations = int((kwargs.get("context") or {}).get("auren_max_iterations", 3))
        unit = self.execution_controller.company.resume(work_id)
        if unit.max_iterations != max_iterations:
            pass
        score = self.execution_controller.company.iterate(
            work_id, execute=execute, review=review, recover=lambda detail: (attempts["count"] < max_iterations, "fresh retry selected", (detail,)),
        )
        result = last["result"]
        if result is None:
            raise RuntimeError("AUREN supervisor completed without an execution result")
        if result.status.value != "accepted":
            raise RuntimeError(result.stop_reason or "AUREN execution failed")
        return result
    def _run_once(
        self,
        *,
        session_id: str,
        task_id: str,
        project_root: Path | str,
        intent: str,
        provider: str | None = None,
        context: Mapping[str, Any] | None = None,
        context_node_id: str | None = None,
        workspace_id: str | None = None,
        required_context: Sequence[str] = (),
        enrich_context: bool = False,
        enrich_cognition: bool = True,
        cognitive_capability: str | None = None,
        cognitive_uncertainty: float = 0.5,
        cognitive_information_actions: tuple[InformationAction, ...] = (),
        cognitive_entity_id: str | None = None,
        cognitive_predicate: str | None = None,
        cognitive_action: str | None = None,
        cognitive_current_value: object | None = None,
        cognitive_beliefs: tuple[BeliefContext, ...] = (),
        cognitive_belief_limit: int = 8,
        cognitive_belief_evidence: tuple[BeliefEvidence, ...] = (),
        cognitive_actual_value: object | None = None,
        learning_evidence: tuple[str, ...] = (),
        learning_context: str | None = None,
        learning_quality: float | None = None,
        learning_iterations: int | None = None,
        learning_verified: bool | None = None,
        learning_strategy: str | None = None,
        learning_confidence: float = 0.5,
    ) -> OrchestrationRun:
        project_key = self.session_store.project_key(project_root)
        provider_name = provider or "aer"
        execution = HookedExecution(self.hooks, session_id, provider_name)
        start = execution.gate(HookPhase.SESSION_START, task_id=task_id, payload={"project_key": project_key})
        if not start.allow:
            raise RuntimeError(f"session_start vetoed: {start.reason}")
        self.ensure_learning_maintenance(project_root)
        tuner = AdaptiveTuner(self.persistent_memory, project_key)
        policy_scope = cognitive_capability or "global"
        policy = tuner.current_policy(policy_scope)
        checkpoint = SessionCheckpoint(
            session_id=session_id,
            task_id=task_id,
            project_key=project_key,
            stage="execute",
            remaining_batches=["verify", "review", "learn"],
            active_provider=provider_name,
            project_root=str(Path(project_root).expanduser().resolve()),
            intent=intent,
        )
        self.session_store.save(checkpoint)
        cognitive_runtime = self.cognition(project_root)
        cognitive_loop = CognitiveLoop(cognitive_runtime)
        learning_store = AdaptiveLearningStore(self.persistent_memory, project_key, dream_root=project_root)
        episode = cognitive_loop.begin(project_key, task_id, intent)
        try:
            before = execution.gate(HookPhase.BEFORE_AGENT, task_id=task_id)
            if not before.allow:
                raise RuntimeError(f"before_agent vetoed: {before.reason}")
            cognitive_loop.observe(episode, {"event": "before_agent", "status": "running", "policy_version": policy.version})
            effective_context = dict(context or {})
            effective_context["aer_workstyle_guidance"] = learning_store.guidance()
            selected_strategy = execution_strategy(policy.strategy)
            effective_context["aer_adaptive_policy"] = {
                "version": policy.version,
                "strategy": policy.strategy,
                "confidence_adjustment": policy.confidence_adjustment,
                "iteration_target": policy.iteration_target,
            }
            effective_context["aer_execution_strategy"] = {
                "name": selected_strategy.name,
                "known": selected_strategy.known,
                "verification_depth": selected_strategy.verification_depth,
                "resource_lane": selected_strategy.resource_lane,
                "capability_bias": list(selected_strategy.capability_bias),
                "minimum_evidence": selected_strategy.minimum_evidence,
            }
            if enrich_context:
                resolution = self.resolve_context(project_root, intent, node_id=context_node_id,
                                                  workspace_id=workspace_id, required=required_context)
                effective_context["aer_context_pack"] = resolution.pack
                effective_context["aer_context_digest"] = resolution.digest
                effective_context["aer_context_omitted"] = list(resolution.omitted)
                cognitive_loop.observe(episode, {"event": "context_resolved", "digest": resolution.digest})
            cognitive_plan_payload: dict[str, object] | None = None
            if enrich_cognition:
                try:
                    effective_context = CognitiveController(cognitive_runtime).enrich_context(
                        intent, capability=cognitive_capability, uncertainty=cognitive_uncertainty,
                        information_actions=cognitive_information_actions, entity_id=cognitive_entity_id,
                        predicate=cognitive_predicate, action=cognitive_action,
                        current_value=cognitive_current_value, beliefs=cognitive_beliefs,
                        belief_limit=cognitive_belief_limit, context=effective_context,
                    )
                    cognitive_plan_payload = dict(effective_context["aer_cognitive_plan"])  # type: ignore[arg-type]
                    self.last_cognitive_plan = dict(cognitive_plan_payload)
                    cognitive_loop.observe(episode, {
                        "event": "cognitive_plan_created",
                        "belief_count": len(cognitive_plan_payload.get("belief_ids", [])),
                        "prediction_id": (cognitive_plan_payload.get("prediction") or {}).get("prediction_id")
                        if isinstance(cognitive_plan_payload.get("prediction"), dict) else None,
                    })
                except Exception as exc:
                    cognitive_loop.observe(episode, {"event": "cognitive_enrichment_error", "error": f"{type(exc).__name__}: {exc}"})
            result = self.orchestrator.run(task_id, intent, effective_context)
            cognitive_loop.observe(episode, {"event": "execution_completed", "status": result.status.value})
            prediction = cognitive_plan_payload.get("prediction") if cognitive_plan_payload else None
            prediction_error: PredictionError | None = None
            if cognitive_actual_value is not None and isinstance(prediction, dict):
                prediction_obj = WorldPrediction(
                    project=project_key,
                    prediction_id=str(prediction["prediction_id"]),
                    entity_id=str(prediction["entity_id"]),
                    predicate=str(prediction["predicate"]),
                    action=str(prediction["action"]),
                    from_value=prediction.get("from_value"),
                    predicted_value=prediction.get("predicted_value"),
                    confidence=float(prediction["confidence"]),
                    evidence_observation_ids=tuple(prediction.get("evidence_observation_ids", ())),
                    created_at=str(prediction.get("created_at", "")) or datetime.now(timezone.utc).isoformat(),
                )
                prediction_error = cognitive_runtime.world.score_prediction(prediction_obj, cognitive_actual_value)
                cognitive_loop.observe(episode, {
                    "event": "prediction_scored",
                    "prediction_id": prediction_error.prediction_id,
                    "correct": prediction_error.absolute_match,
                })
            after = execution.gate(HookPhase.AFTER_AGENT, task_id=task_id, payload={"status": result.status.value})
            if not after.allow:
                raise RuntimeError(f"after_agent vetoed: {after.reason}")
            checkpoint.stage = "complete"
            checkpoint.completed_batches = ["execute", "verify", "review"]
            checkpoint.remaining_batches = ["learn"]
            checkpoint.last_error = None
            self.session_store.save(checkpoint)
            iterations = learning_iterations
            if iterations is None:
                iterations = sum(max(1, item.attempts) + item.repair_count for item in result.results.values()) or 1
            quality = learning_quality if learning_quality is not None else (1.0 if result.status.value == "accepted" else 0.0)
            verified = learning_verified if learning_verified is not None else result.status.value == "accepted"
            evidence = tuple(sorted(set(learning_evidence) | {item.digest for item in result.evidence}))
            try:
                self.last_deferred_learning_job = learning_store.record_outcome(
                    task_id=task_id, intent=intent, status=result.status.value,
                    quality=quality, iterations=iterations, context=learning_context,
                    evidence=evidence, verified=verified, capability=cognitive_capability,
                    belief_evidence=cognitive_belief_evidence,
                    prediction_id=prediction_error.prediction_id if prediction_error else None,
                    prediction_correct=prediction_error.absolute_match if prediction_error else None,
                )
                tuner.record_experience(
                    task_id=task_id,
                    capability=cognitive_capability or "general",
                    strategy=(learning_strategy or policy.strategy),
                    quality=quality,
                    iterations=iterations,
                    confidence=learning_confidence,
                    verified=verified,
                    evidence=evidence,
                    evaluation_class="experience",
                )
                cognitive_loop.observe(episode, {"event": "learning_deferred", "job_id": self.last_deferred_learning_job.job_id,
                                                 "policy_version": policy.version})
            except Exception as exc:
                cognitive_loop.observe(episode, {"event": "learning_defer_error", "error": f"{type(exc).__name__}: {exc}"})
            self.last_learning_signal = None
            self.last_cognitive_episode = cognitive_loop.complete(episode, result.status.value)
            execution.gate(HookPhase.SESSION_END, task_id=task_id, payload={"status": result.status.value})
            return result
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            cognitive_loop.observe(episode, {"event": "execution_error", "status": "failed", "error": error})
            try:
                self.last_deferred_learning_job = learning_store.record_outcome(
                    task_id=task_id, intent=intent, status="failed", quality=0.0,
                    iterations=1, context=learning_context, evidence=learning_evidence, verified=False,
                    capability=cognitive_capability,
                )
                tuner.record_experience(
                    task_id=task_id,
                    capability=cognitive_capability or "general",
                    strategy=(learning_strategy or policy.strategy),
                    quality=0.0,
                    iterations=1,
                    confidence=learning_confidence,
                    verified=False,
                    evidence=learning_evidence,
                    evaluation_class="experience",
                    realized_success=False,
                )
            except Exception as learning_exc:
                cognitive_loop.observe(episode, {"event": "learning_defer_error", "error": f"{type(learning_exc).__name__}: {learning_exc}"})
            checkpoint.last_error = error
            checkpoint.attempt += 1
            self.session_store.save(checkpoint)
            self.last_cognitive_episode = cognitive_loop.complete(episode, "failed", error)
            execution.gate(HookPhase.RECOVERY, task_id=task_id, payload={"error": checkpoint.last_error})
            raise

    def process_learning(self, project_root: Path | str, *, limit: int = 20, dream: bool = True) -> list[dict[str, object]]:
        """Run deferred learning/maintenance outside the active worker path."""
        project_key = self.session_store.project_key(project_root)
        store = AdaptiveLearningStore(self.persistent_memory, project_key, dream_root=project_root)
        jobs = store.process(limit=limit, dream=dream)
        return [
            {"job_id": job.job_id, "task_id": job.task_id, "kind": job.kind, "status": job.status}
            for job in jobs
        ]

    def benchmark_improvement(
        self,
        project_root: Path | str,
        cases: Iterable[object],
        baseline_strategy: str,
        candidate_strategy: str,
        evaluator,
        **thresholds: object,
    ) -> ImprovementReport:
        """Replay a bounded benchmark outside execution authority and gate a candidate empirically."""
        self.session_store.project_key(project_root)
        return EmpiricalImprovement.run(cases, baseline_strategy, candidate_strategy, evaluator, **thresholds)

    def recover(self, session_id: str, next_stage: str = "execute") -> SessionCheckpoint | None:
        return self.session_store.recover(session_id, next_stage=next_stage)


__all__ = ["AdaptiveRuntime"]
