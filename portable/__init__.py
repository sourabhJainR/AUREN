"""Portable AER runtime package."""

from .adaptive_runtime import AdaptiveRuntime
from .adaptive_learning import AdaptiveLearningStore, DeferredLearningJob, WorkStyleProfile
from .adaptive_tuning import AdaptivePolicy, AdaptiveTuner, ExperienceRecord, MaintenanceReceipt, TuningDecision
from .empirical_improvement import EmpiricalImprovement, ImprovementObservation, ImprovementReport
from .aer_runtime import main
from .impact_analysis import ImpactRecord, ImpactReport, analyze
from .lifecycle_hooks import HookBus, HookDecision, HookEvent, HookPhase, HookedExecution
from .provider_fabric import CapabilityRequest, ProviderCapability, ProviderFabric, RoutingDecision
from .session_state import SessionCheckpoint, SessionStore
from .task_planner import PRIORITIES, STATUSES, Task, TaskPlan
from .capability_fabric import CAPABILITIES, Capability, CapabilityFabric, ProviderAdapter, ProviderAdapterRegistry
from .persistent_memory import MemoryRecord, PersistentMemory
from .agent_memory import AgentMemory
from .context_engine import ContextEngine, ContextItem, ContextPolicy, Handoff, handoff_from_output
from .dream_memory import DreamMemory
from .automation_scheduler import AutomationScheduler, Schedule
from .output_quality import OutputQualityGate, QualityResult
from .feedback_loop import BoundedLoop, LoopAction, LoopDefinition, LoopPass, LoopRunReceipt, VerificationResult
from .agency_provenance import ProvenanceLedger, ProvenanceRecord
from .engineering_design_guard import DesignDimension, DesignFinding, DesignReviewReceipt, EngineeringDesignGuard, FindingSeverity
from .agency_state_graph import Checkpoint, CompiledStateGraph, GraphEvent, GraphInterrupt, GraphRun, InMemoryCheckpointStore, RetryPolicy, StateGraph
from .decision_fabric import ChoiceDecision, DecisionBatch, DecisionFabric, DecisionPolicy, DecisionQuestion, NoulDecision, PolicyDecision, ScoreDecision
from .adaptive_decision import AdaptiveInferencePolicy, HttpDecisionProvider, InferenceDecision
from .experience_router import ExperienceRouter, ExperienceSummary
from .recursive_agency import AgencyReceipt, CycleObservation, RecursiveAgency, WorkItem
from .repository_intelligence import (
    PackedFile, RepositoryAnswer, RepositoryEvidence, RepositoryIntelligence, RepositoryMap, RepositoryPack, render_compact,
)
from .agent_patterns import (
    ConsensusResult, EvidenceItem, MixtureOfAgents, OneChangeOptimizer, OptimizationResult, OptimizationRound,
    ResearchPlan, ResearchPlanner, ResearchTask, RouteDecision, RouteRequest, ScopeChecker, ScopeFinding, ScopeReport,
    Specialist, SpecialistRouter,
)
from .world_model import Observation, PredictionError, WorldFact, WorldModel, WorldPrediction
from .general_intelligence_cycle import CognitiveCycleResult, GeneralIntelligenceCycle
from .hypothesis_engine import BeliefEvidence, Hypothesis, HypothesisEngine
from .reasoning_ledger import ReasoningLedger, ReasoningRecord
from .causal_model import CausalLink, CausalModel
from .causal_learning import CausalLearner, Intervention, InterventionOutcome
from .counterfactual import CounterfactualEngine, CounterfactualQuery, CounterfactualResult
from .goal_manager import Goal, GoalManager
from .information_planner import InformationAction, InformationPlan, InformationPlanner
from .active_information import ActiveInformationLoop, InformationExecution, InformationReceipt
from .self_model import CapabilityProfile, SelfModel
from .agi_evaluation import CapabilityCase, CapabilityEvaluator, CaseResult, EvaluationReport, KINDS
from .cognitive_runtime import CognitiveRuntime
from .cognitive_loop import CognitiveEpisode, CognitiveEpisodeReceipt, CognitiveLoop
from .cognitive_controller import CognitiveController, CognitivePlan
from .cognitive_learning import BeliefContext, CognitiveLearningLoop, LearningSignal
from .learning_transfer import ConsolidationReceipt, LearningExperience, LearningTransfer, TransferCandidate
from .transfer_validation import TransferValidation, TransferValidationReceipt, TransferValidator
from .generalization import Abstraction, AnalogyCandidate, GeneralizationEngine
from .skill_graph import SkillGraph, SkillNode
from .curiosity import CuriosityEngine, LearningChoice, LearningNeed
from .interactive_benchmark import BenchmarkSuiteResult, EpisodeResult, InteractiveBenchmark, InteractiveEnvironment, KeyDoorEnvironment
from .continual_learning import BenchmarkObservation, ContinualLearningGuard, RegressionResult
from .capability_acquisition import CapabilityAcquirer, CapabilityNeed, CapabilityProposal, GraduationReceipt as CapabilityGraduationReceipt, PracticeResult, ValidationEvidence, ValidationReceipt
from .autonomy_graduation import AutonomyEvidence, AutonomyGraduator, GraduationPolicy, GraduationReceipt, LEVELS
from .deep_evaluation import BenchmarkCase, DeepBenchmarkReport, DeepEvaluator
from .adaptive_trigger import AdaptiveTrigger, AdaptiveTriggerRequest, TriggerOutcome, TriggerReceipt, trigger_adaptive_runtime
from .trigger_runtime import TriggerClaim, TriggerEvent, TriggerRuntime, TriggerStatus
from .local_workbench import LocalWorkbench, WorkPacket, WorkReceipt, WorkbenchReceipt, packet
from .world_mega_model import EvolutionReceipt, MegaPlan, MegaPromotion, AutonomousGeneralizationCycle, AutonomousCapabilityEvolutionCycle, WorldMegaModel
from .autonomous_capability_invention import AutonomousCapabilityInvention, CapabilityComposition, HoldoutResult, InventionCandidate, InventionReceipt, SafetyResult
from .autonomous_evolution_controller import AutonomousEvolutionController, EvolutionTrigger
from .capability_lifecycle import CapabilityLifecycle, CapabilityLifecycleReceipt
from .generalization_curriculum import GeneralizationCurriculum, GeneralizationExperiment, ExperimentResult, GeneralizationReport
from .autonomous_curriculum import AutonomousCurriculumDiscovery, CurriculumCandidate, CurriculumDecision
from .whole_system_engineering import ENGINEERING_DOMAINS, EngineeringTask, EngineeringEvaluation, EngineeringCoverage, WholeSystemEngineeringEvaluator
from .requirement_contract import Requirement, RequirementContract, RequirementContractEngine
from .end_to_end_engineering_episode import EngineeringEpisodeRequest, EngineeringEpisodeResult, EndToEndEngineeringEpisode

__all__ = [
    "main", "AdaptiveRuntime", "AdaptiveLearningStore", "DeferredLearningJob", "WorkStyleProfile", "AdaptivePolicy", "AdaptiveTuner", "ExperienceRecord", "MaintenanceReceipt", "TuningDecision", "EmpiricalImprovement", "ImprovementObservation", "ImprovementReport",
    "HookBus", "HookDecision", "HookEvent", "HookPhase", "HookedExecution",
    "CapabilityRequest", "ProviderCapability", "ProviderFabric", "RoutingDecision", "SessionCheckpoint", "SessionStore",
    "ImpactRecord", "ImpactReport", "analyze", "Task", "TaskPlan", "STATUSES", "PRIORITIES",
    "CAPABILITIES", "Capability", "CapabilityFabric", "ProviderAdapter", "ProviderAdapterRegistry",
    "MemoryRecord", "PersistentMemory", "AgentMemory", "ContextEngine", "ContextItem", "ContextPolicy",
    "Handoff", "handoff_from_output", "DreamMemory", "AutomationScheduler", "Schedule", "OutputQualityGate", "QualityResult",
    "BoundedLoop", "LoopAction", "LoopDefinition", "LoopPass", "LoopRunReceipt", "VerificationResult",
    "ProvenanceLedger", "ProvenanceRecord", "DesignDimension", "DesignFinding", "DesignReviewReceipt", "EngineeringDesignGuard", "FindingSeverity",
    "Checkpoint", "CompiledStateGraph", "GraphEvent", "GraphInterrupt", "GraphRun", "InMemoryCheckpointStore", "RetryPolicy", "StateGraph",
    "ChoiceDecision", "DecisionBatch", "DecisionFabric", "DecisionPolicy", "DecisionQuestion", "NoulDecision", "PolicyDecision", "ScoreDecision", "AdaptiveInferencePolicy", "HttpDecisionProvider", "InferenceDecision", "ExperienceRouter", "ExperienceSummary",
    "AgencyReceipt", "CycleObservation", "RecursiveAgency", "WorkItem",
    "PackedFile", "RepositoryPack", "RepositoryEvidence", "RepositoryAnswer", "RepositoryIntelligence", "RepositoryMap", "render_compact",
    "Specialist", "SpecialistRouter", "RouteRequest", "RouteDecision", "ResearchTask", "ResearchPlan", "ResearchPlanner",
    "EvidenceItem", "ConsensusResult", "MixtureOfAgents", "OptimizationRound", "OptimizationResult", "OneChangeOptimizer",
    "ScopeFinding", "ScopeReport", "ScopeChecker", "Observation", "WorldFact", "WorldPrediction", "PredictionError", "WorldModel",
    "BeliefEvidence", "Hypothesis", "HypothesisEngine", "ReasoningLedger", "ReasoningRecord", "CausalLink", "CausalModel",
    "CausalLearner", "Intervention", "InterventionOutcome", "CounterfactualEngine", "CounterfactualQuery", "CounterfactualResult",
    "Goal", "GoalManager", "InformationAction", "InformationPlan", "InformationPlanner", "ActiveInformationLoop", "InformationExecution", "InformationReceipt",
    "CapabilityProfile", "SelfModel", "CapabilityCase", "CapabilityEvaluator", "CaseResult", "EvaluationReport", "KINDS", "CognitiveRuntime",
    "CognitiveEpisode", "CognitiveEpisodeReceipt", "CognitiveLoop", "CognitiveController", "CognitivePlan", "BeliefContext", "CognitiveLearningLoop", "LearningSignal",
    "LearningExperience", "TransferCandidate", "ConsolidationReceipt", "LearningTransfer", "TransferValidation", "TransferValidationReceipt", "TransferValidator",
    "Abstraction", "AnalogyCandidate", "GeneralizationEngine", "SkillGraph", "SkillNode", "CuriosityEngine", "LearningChoice", "LearningNeed",
    "BenchmarkSuiteResult", "EpisodeResult", "InteractiveBenchmark", "InteractiveEnvironment", "KeyDoorEnvironment",
    "BenchmarkObservation", "RegressionResult", "ContinualLearningGuard",
    "CapabilityAcquirer", "CapabilityNeed", "CapabilityProposal", "PracticeResult", "CapabilityGraduationReceipt", "ValidationEvidence", "ValidationReceipt",
    "AutonomyEvidence", "AutonomyGraduator", "GraduationPolicy", "GraduationReceipt", "LEVELS", "BenchmarkCase", "DeepBenchmarkReport", "DeepEvaluator",
    "AdaptiveTrigger", "AdaptiveTriggerRequest", "TriggerOutcome", "TriggerReceipt", "trigger_adaptive_runtime",
    "TriggerClaim", "TriggerEvent", "TriggerRuntime", "TriggerStatus",
    "LocalWorkbench", "WorkPacket", "WorkReceipt", "WorkbenchReceipt", "packet",
    "CognitiveCycleResult", "GeneralIntelligenceCycle", "EvolutionReceipt", "MegaPlan", "MegaPromotion", "AutonomousGeneralizationCycle", "AutonomousCapabilityEvolutionCycle", "WorldMegaModel",
    "AutonomousCapabilityInvention", "CapabilityComposition", "HoldoutResult", "InventionCandidate", "InventionReceipt", "SafetyResult",
    "AutonomousEvolutionController", "EvolutionTrigger", "CapabilityLifecycle", "CapabilityLifecycleReceipt", "GeneralizationCurriculum", "GeneralizationExperiment", "ExperimentResult", "GeneralizationReport", "AutonomousCurriculumDiscovery", "CurriculumCandidate", "CurriculumDecision", "ENGINEERING_DOMAINS", "EngineeringTask", "EngineeringEvaluation", "EngineeringCoverage", "WholeSystemEngineeringEvaluator", "Requirement", "RequirementContract", "RequirementContractEngine", "EngineeringEpisodeRequest", "EngineeringEpisodeResult", "EndToEndEngineeringEpisode",
]
