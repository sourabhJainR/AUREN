"""End-to-end engineering episode coordinator.

This is the integration layer for the engineering contracts and quality
controls already present in the portable runtime. It coordinates planning,
authorized execution, verification, bounded repair, traceability, and
model/topology-neutral evaluation without granting itself execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .architecture_contract import ArchitectureContract
from .engineering_quality_gate import EngineeringQualityGate, QualityGate, QualityReport
from .engineering_traceability import EngineeringTraceability, TraceLink, TraceabilityReport
from .full_stack_contract import FullStackContract
from .implementation_plan import ImplementationPlan
from .model_topology_benchmark import ModelBenchmarkReport, ModelTopologyBenchmark
from .performance_budget import PerformanceBudget, PerformanceBudgetGate, PerformanceObservation, PerformanceReport
from .requirement_contract import RequirementContract, RequirementContractEngine
from .security_engineering_gate import SecurityEngineeringGate, SecurityReport, Threat
from .verified_repair_loop import RepairAttempt, RepairReport, VerifiedRepairLoop
from .whole_system_engineering import EngineeringCoverage, EngineeringEvaluation, EngineeringTask, WholeSystemEngineeringEvaluator


@dataclass(frozen=True)
class EngineeringEpisodeRequest:
    intent: str
    requirements: RequirementContract
    architecture: ArchitectureContract
    implementation_plan: ImplementationPlan
    full_stack: FullStackContract


@dataclass(frozen=True)
class EngineeringEpisodeResult:
    request: EngineeringEpisodeRequest
    implementation_result: Any
    quality: QualityReport
    security: SecurityReport
    performance: PerformanceReport
    traceability: TraceabilityReport
    engineering: EngineeringCoverage
    repair: RepairReport | None
    benchmark: ModelBenchmarkReport | None
    accepted: bool
    next_action: str
    evidence_ids: tuple[str, ...] = ()
    defects: tuple[str, ...] = ()


class EndToEndEngineeringEpisode:
    """Run one bounded engineering episode using injected authority.

    The executor is the only component allowed to change a repository or
    environment. All evaluators are observation-only. Repair is bounded and
    cannot bypass the same verification gates.
    """

    def __init__(
        self,
        *,
        requirement_engine: RequirementContractEngine | None = None,
        engineering_evaluator: WholeSystemEngineeringEvaluator | None = None,
        repair_loop: VerifiedRepairLoop | None = None,
        benchmark: ModelTopologyBenchmark | None = None,
    ) -> None:
        self.requirements = requirement_engine or RequirementContractEngine()
        self.engineering = engineering_evaluator or WholeSystemEngineeringEvaluator()
        self.repair = repair_loop or VerifiedRepairLoop()
        self.benchmark = benchmark or ModelTopologyBenchmark()
        self.quality = EngineeringQualityGate()
        self.security = SecurityEngineeringGate()
        self.performance = PerformanceBudgetGate()
        self.traceability = EngineeringTraceability()

    def run(
        self,
        request: EngineeringEpisodeRequest,
        *,
        executor: Callable[[EngineeringEpisodeRequest], Any],
        quality_gates: Sequence[QualityGate],
        threats: Sequence[Threat],
        performance_budget: PerformanceBudget,
        performance_observation: PerformanceObservation,
        trace_links: Sequence[TraceLink],
        engineering_evaluator: Callable[[EngineeringTask], EngineeringEvaluation],
        benchmark_models: Sequence[str] = (),
        benchmark_task_ids: Sequence[str] = (),
        benchmark_evaluator: Callable[[str, str], Any] | None = None,
        repair_defect: str = "",
        repair_baseline: float = 0.0,
        repair_attempt: Callable[[str, int], RepairAttempt] | None = None,
        minimum_repair_improvement: float = 0.01,
    ) -> EngineeringEpisodeResult:
        if not isinstance(request, EngineeringEpisodeRequest):
            raise TypeError("request must be an EngineeringEpisodeRequest")
        if not callable(executor):
            raise TypeError("executor must be callable")

        contract_issues = self.requirements.validate(request.requirements)
        if contract_issues:
            raise ValueError("requirement contract is not executable: " + "; ".join(contract_issues))

        implementation_result = executor(request)

        quality = self.quality.evaluate(tuple(quality_gates))
        security = self.security.assess(tuple(threats))
        boundary_issues = self.security.require_boundaries(
            request.architecture.boundaries, threats
        )
        if boundary_issues:
            security = SecurityReport(
                security.threats,
                False,
                tuple(security.unresolved) + tuple(f"unknown-boundary:{x}" for x in boundary_issues),
            )

        performance = self.performance.evaluate(performance_budget, performance_observation)
        traceability = self.traceability.evaluate(
            tuple(trace_links),
            tuple(r.requirement_id for r in request.requirements.requirements),
        )
        engineering = self._evaluate_engineering(request.intent, engineering_evaluator)

        repair = None
        defects = tuple(quality.blocking_defects) + tuple(security.unresolved) + tuple(
            f"performance:{x}" for x in performance.violations
        ) + tuple(f"traceability:{x}" for x in traceability.missing)
        if not engineering.verified:
            defects += ("whole-system engineering evaluation did not pass",)

        if repair_defect and repair_attempt is not None and defects:
            repair = self.repair.run(
                repair_defect,
                repair_baseline,
                repair_attempt,
                minimum_improvement=minimum_repair_improvement,
            )

        benchmark = None
        if benchmark_models and benchmark_task_ids:
            if benchmark_evaluator is None:
                raise ValueError("benchmark_evaluator is required when benchmark inputs are supplied")
            benchmark = self.benchmark.run(
                benchmark_models,
                benchmark_task_ids,
                benchmark_evaluator,
            )

        repair_ok = repair is None or repair.accepted
        benchmark_ok = benchmark is None or benchmark.verified
        accepted = (
            quality.passed
            and security.passed
            and performance.passed
            and traceability.verified
            and engineering.verified
            and repair_ok
            and benchmark_ok
        )
        evidence = tuple(
            dict.fromkeys(
                x
                for x in (
                    *(e for g in quality.gates for e in g.evidence_ids),
                    *(t.threat_id for t in security.threats if t.verified),
                    *[x for x in traceability.missing if not x],
                    *(x.evidence_id for x in (repair.attempts if repair else ()) if x),
                    *(x.evidence_id for x in (benchmark.evaluations if benchmark else ()) if x),
                )
                if x
            )
        )
        if accepted:
            next_action = "learn"
        elif repair is not None and not repair.accepted:
            next_action = "escalate"
        else:
            next_action = "repair"

        return EngineeringEpisodeResult(
            request=request,
            implementation_result=implementation_result,
            quality=quality,
            security=security,
            performance=performance,
            traceability=traceability,
            engineering=engineering,
            repair=repair,
            benchmark=benchmark,
            accepted=accepted,
            next_action=next_action,
            evidence_ids=evidence,
            defects=defects,
        )

    def _evaluate_engineering(
        self,
        intent: str,
        evaluator: Callable[[EngineeringTask], EngineeringEvaluation],
    ) -> EngineeringCoverage:
        tasks = self.engineering.generate(
            intent,
            budget=min(12, len(self.engineering.domains)),
            required_domains=self.engineering.domains,
        )
        return self.engineering.evaluate(tasks, evaluator)


__all__ = ["EngineeringEpisodeRequest", "EngineeringEpisodeResult", "EndToEndEngineeringEpisode"]
