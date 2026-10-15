"""Bridge the AI coding orchestrator to the deterministic Agency Runtime."""
from __future__ import annotations
from dataclasses import dataclass, replace
import posixpath
from typing import Callable, Iterable, Mapping
from .agency_adaptive_planning import AdaptiveRecommendation, BenchmarkHistory, apply_recommendation, observe_run, recommend_plan
from .agency_artifact_regression import ArtifactSnapshot, RegressionDecision, compare_artifacts
from .agency_codebase_context import CodebaseContext, retrieve_from_path
from .agency_multi_specialist import SpecialistResourceProfile, build_specialist_plan
from .agency_provenance import ProvenanceLedger
from .agency_release import ReleaseDecision, decide_release
from .agency_runtime import Assignment, ExecutionResult, TaskProfile, execute
from .agency_quality import ReviewFinding
from .action_governance import ActionGovernanceGateway, ActionRequest, ApprovalReceipt

@dataclass(frozen=True)
class CodingTask:
    task_id: str
    goal: str
    acceptance: tuple[str,...]=()
    technologies: tuple[str,...]=()
    risk: str="normal"
    mutation_mode: str="bounded"
    support_limit: int=2
    protected_paths: tuple[str,...]=()
    allowed_artifact_changes: tuple[str,...]=()
    workspace_root: str|None=None
    context_query: str|None=None
    context_token_budget: int=4000
    context_max_files: int=12
    context_graph_hops: int=2

@dataclass
class OrchestratorRun:
    task: CodingTask
    execution: ExecutionResult
    execution_plan: object
    regression: RegressionDecision|None
    release: ReleaseDecision
    provenance: ProvenanceLedger
    adaptive_recommendation: AdaptiveRecommendation|None=None
    benchmark: object|None=None
    codebase_context: CodebaseContext|None=None
    @property
    def ready(self)->bool: return self.release.releasable
    def as_dict(self)->dict[str,object]:
        return {"task_id":self.task.task_id,"execution":self.execution.as_dict(),"execution_plan":self.execution_plan.as_dict(),"regression":self.regression.as_dict() if self.regression else None,"release":self.release.as_dict(),"provenance":self.provenance.as_dict(),"adaptive_recommendation":self.adaptive_recommendation.as_dict() if self.adaptive_recommendation else None,"benchmark":self.benchmark.as_dict() if self.benchmark else None,"codebase_context":self.codebase_context.as_dict() if self.codebase_context else None,"ready":self.ready}

def _safe_repo_path(value: str) -> str:
    """Normalize a repository-relative path and reject traversal/absolute scopes."""
    if not isinstance(value, str) or not value.strip() or "\\x00" in value:
        raise ValueError("write scope paths must be non-empty strings without NUL bytes")
    raw = value.replace("\\\\", "/").strip()
    if raw.startswith("/") or (len(raw) >= 2 and raw[1] == ":"):
        raise ValueError("write scope paths must be repository-relative")
    parts = raw.split("/")
    if any(part == ".." for part in parts):
        raise ValueError("write scope paths must not contain parent traversal")
    normalized = posixpath.normpath(raw)
    if normalized in {"", "."}:
        raise ValueError("write scope path must identify a repository path")
    return normalized


def _paths_overlap(left: str, right: str) -> bool:
    """Return true if either path is the other or lies beneath it."""
    return (
        left == right
        or left.startswith(right.rstrip("/") + "/")
        or right.startswith(left.rstrip("/") + "/")
    )


def _artifact_map(items:Iterable[ArtifactSnapshot])->dict[str,ArtifactSnapshot]: return {x.path:x for x in items}

def _build_codebase_context(task:CodingTask)->CodebaseContext|None:
    if not task.workspace_root: return None
    return retrieve_from_path(task.workspace_root,task.context_query or task.goal,token_budget=task.context_token_budget,max_files=task.context_max_files,graph_hops=task.context_graph_hops)

def run_coding_task(task:CodingTask,worker:Callable[[TaskProfile,list[Assignment]],Mapping[str,object]],*,baseline_artifacts:Iterable[ArtifactSnapshot]=(),current_artifacts:Iterable[ArtifactSnapshot]=(),resource_profiles:Mapping[str,SpecialistResourceProfile]|None=None,benchmark_history:BenchmarkHistory|Iterable=(),registry:Mapping[str,object]|None=None,rubric:Mapping[str,object]|None=None,governance_gateway:ActionGovernanceGateway|None=None,approval_provider:Callable[[ActionRequest],ApprovalReceipt|None]|None=None,run_id:str|None=None)->OrchestratorRun:
    """Run profile -> adaptive plan -> graph context -> specialists -> verify -> regression -> release -> benchmark."""
    history=benchmark_history if isinstance(benchmark_history,BenchmarkHistory) else BenchmarkHistory(list(benchmark_history))
    recommendation=recommend_plan(history,requested_mutation_mode=task.mutation_mode,requested_support_limit=task.support_limit)
    mutation_mode,support_limit=apply_recommendation(task.mutation_mode,task.support_limit,recommendation)
    effective_task=replace(task,mutation_mode=mutation_mode,support_limit=support_limit)
    codebase_context=_build_codebase_context(effective_task)
    profile=TaskProfile(task_id=effective_task.task_id,request=effective_task.goal,artifact_type="code",risk=effective_task.risk,mutation_mode=effective_task.mutation_mode,acceptance=effective_task.acceptance,technologies=effective_task.technologies,context=codebase_context)
    effective_worker = worker
    if governance_gateway is not None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id is required when governance gateway is enabled")

        def effective_worker(task_profile: TaskProfile, assignments: list[Assignment]) -> Mapping[str, object]:
            mutating = task_profile.mutation_mode != "read-only"
            capability = "repository.write" if mutating else "repository.read"
            task_risk = str(task_profile.risk).strip().lower()
            risk_class = (
                "read" if not mutating
                else "release" if task_risk in {"high", "critical"}
                else "write"
            )
            targets = tuple(effective_task.allowed_artifact_changes) if mutating else (effective_task.task_id,)
            if mutating and not targets:
                return {
                    "deliverables": [], "verification": [],
                    "dimensions": {}, "hard_gates": {"action_governance": False},
                    "findings": [ReviewFinding("blocker", "action_governance:explicit_write_scope_missing")],
                    "evidence": [],
                }

            try:
                normalized_targets = tuple(_safe_repo_path(str(target)) for target in targets)
                protected = tuple(_safe_repo_path(str(path)) for path in effective_task.protected_paths)
            except ValueError as exc:
                return {
                    "deliverables": [], "verification": [],
                    "dimensions": {}, "hard_gates": {"action_governance": False},
                    "findings": [ReviewFinding("blocker", f"action_governance:invalid_write_scope:{exc}")],
                    "evidence": [],
                }

            if mutating and any(
                _paths_overlap(target, root)
                for target in normalized_targets
                for root in protected
            ):
                return {
                    "deliverables": [], "verification": [],
                    "dimensions": {}, "hard_gates": {"action_governance": False},
                    "findings": [ReviewFinding("blocker", "action_governance:task_protected_path")],
                    "evidence": [],
                }

            for index, target in enumerate(normalized_targets):
                request = ActionRequest(
                    run_id=run_id,
                    action_id=f"{effective_task.task_id}:coding-scope:{index}",
                    actor="auren-host",
                    capability=capability,
                    action="specialist.execute",
                    target=target,
                    risk=risk_class,
                )
                approval = approval_provider(request) if approval_provider else None
                verdict = governance_gateway.evaluate(request, approval=approval)
                if not verdict.allowed:
                    return {
                        "deliverables": [], "verification": [],
                        "dimensions": {}, "hard_gates": {"action_governance": False},
                        "findings": [ReviewFinding("blocker", f"action_governance:{verdict.reason}")],
                        "evidence": [],
                    }

            # Bind the actual worker invocation to the complete, preflighted scope.
            # The scope digest is an opaque target; individual paths were separately
            # evaluated above so policy checks cannot be reduced to the first path.
            scope_request = ActionRequest(
                run_id=run_id,
                action_id=f"{effective_task.task_id}:coding-worker",
                actor="auren-host",
                capability=capability,
                action="specialist.execute",
                target=f"task-scope/{effective_task.task_id}",
                risk=risk_class,
                evidence_refs=tuple(f"scope:{path}" for path in normalized_targets),
            )
            scope_approval = approval_provider(scope_request) if approval_provider else None
            scope_verdict = governance_gateway.evaluate(scope_request, approval=scope_approval)
            if not scope_verdict.allowed:
                return {
                    "deliverables": [], "verification": [],
                    "dimensions": {}, "hard_gates": {"action_governance": False},
                    "findings": [ReviewFinding("blocker", f"action_governance:{scope_verdict.reason}")],
                    "evidence": [],
                }

            captured: list[Mapping[str, object]] = []
            result = governance_gateway.execute(
                scope_request,
                lambda: captured.append(worker(task_profile, assignments)) or captured[-1],
                approval=scope_approval,
            )
            if not result.executed or not captured:
                return {
                    "deliverables": [], "verification": [],
                    "dimensions": {}, "hard_gates": {"action_governance": False},
                    "findings": [ReviewFinding("blocker", f"action_governance:{result.verdict.reason}")],
                    "evidence": [],
                }
            return captured[0]

    execution=execute(profile,effective_worker,registry=registry,rubric=rubric,support_limit=effective_task.support_limit)
    execution_plan=build_specialist_plan(execution.assignments,resource_profiles)
    provenance=ProvenanceLedger(); provenance.append(task.task_id,"task-profiled",task.goal); provenance.append(task.task_id,"adaptive-recommendation",str(recommendation.as_dict()))
    if codebase_context:
        provenance.append(task.task_id,"codebase-indexed",codebase_context.snapshot_digest)
        provenance.append(task.task_id,"codebase-context-selected",f"{len(codebase_context.chunks)} chunk(s); {codebase_context.token_estimate} token-estimate; graph-hops={task.context_graph_hops}")
        for unknown in codebase_context.unknowns: provenance.append(task.task_id,"codebase-unknown",unknown)
        for edge in codebase_context.graph_trace.edges:
            provenance.append(task.task_id,"codebase-graph-edge",f"{edge.kind}:{edge.source_path}->{edge.target_path}:{edge.confidence}")
    provenance.append(task.task_id,"specialists-planned",",".join(a.specialist for a in execution.assignments)); provenance.append(task.task_id,"execution-plan",execution_plan.digest())
    for item in execution.evidence: provenance.append(task.task_id,"evidence-recorded",item.claim)
    provenance.append(task.task_id,"verification-recorded",";".join(execution.verification))
    regression=None; regression_status=None
    if baseline_artifacts or current_artifacts:
        regression=compare_artifacts(_artifact_map(baseline_artifacts),_artifact_map(current_artifacts),allowed_changes=task.allowed_artifact_changes); regression_status=regression.status; provenance.append(task.task_id,"artifact-regression",regression.status)
    receipt=execution.receipt
    release=decide_release(0,90,{},(),regression_status=regression_status) if receipt is None else decide_release(receipt.score,receipt.threshold,receipt.hard_gates,receipt.findings,regression_status=regression_status)
    provenance.append(task.task_id,"release-decision",release.status)
    benchmark=observe_run(task_id=task.task_id,plan=execution_plan,provenance=provenance,release_status=release.status,regression_status=regression_status,quality_score=receipt.score if receipt else 0,specialist_results=tuple(f"{a.specialist}:{a.role}" for a in execution.assignments)); provenance.append(task.task_id,"benchmark-observed",benchmark.plan_digest,(benchmark.plan_digest,)); history.add(benchmark)
    return OrchestratorRun(task,execution,execution_plan,regression,release,provenance,recommendation,benchmark,codebase_context)

def verify_provenance(run:OrchestratorRun)->None: run.provenance.verify()
