from dataclasses import dataclass
import pytest
from portable.autonomous_curriculum_feedback_cycle import AutonomousCurriculumFeedbackCycle
from portable.autonomous_curriculum_evolution import AutonomousCurriculumEvolution
from portable.capability_invention_validation import CapabilityValidationPlan, ValidationProbe
from portable.capability_invention_validation_runner import CapabilityInventionValidationRunner
from portable.external_curriculum_campaign_orchestrator import ExternalCurriculumCampaignOrchestrator
from portable.external_environment_protocol import EnvironmentContract, EpisodeTrace
from portable.open_ended_task_environment_discovery import DiscoverySignal, OpenEndedTaskEnvironmentDiscovery

def curriculum():
    ts = OpenEndedTaskEnvironmentDiscovery().discover((
        DiscoverySignal("seed-a", "science", "constraint-a", .9),
        DiscoverySignal("seed-b", "planning", "constraint-b", .8),
    ))
    return AutonomousCurriculumEvolution().build(ts, max_targets=2)

@dataclass
class Executor:
    def run(self, probe, contract):
        ok = not probe.dimension.endswith("fail")
        return EpisodeTrace(probe.probe_id, contract.contract_digest, ("ok",), ("done",), 1, ok, ok)

@dataclass
class Oracle:
    independent: bool = True
    def verify(self, probe, trace): return trace.verified

class Generator:
    def build(self, target_id, *, plan_digest):
        plan = CapabilityValidationPlan(
            "proposal-"+target_id,
            (ValidationProbe("p-"+target_id+"-a", "fail", "family-a", True, True),
             ValidationProbe("p-"+target_id+"-b", "transfer", "family-b", True, True)),
            True, True, .75, .02)
        contract = EnvironmentContract("env-"+target_id, "1", ("text",), ("read",), 2, True, True)
        return plan, contract, "generator-"+target_id, "oracle-"+target_id

def test_failure_becomes_new_curriculum():
    prior = curriculum()
    campaign = ExternalCurriculumCampaignOrchestrator(
        Generator(), CapabilityInventionValidationRunner(Executor(), Oracle())
    ).run(prior)
    feedback = AutonomousCurriculumFeedbackCycle().derive(prior, campaign)
    assert feedback.discovery_signals
    assert feedback.next_curriculum.trustworthy
    assert feedback.next_curriculum.plan_digest
    assert feedback.feedback_digest

def test_rejects_wrong_lineage():
    prior = curriculum()
    campaign = ExternalCurriculumCampaignOrchestrator(
        Generator(), CapabilityInventionValidationRunner(Executor(), Oracle())
    ).run(prior)
    other = curriculum()
    with pytest.raises(ValueError, match="does not belong"):
        AutonomousCurriculumFeedbackCycle().derive(other, campaign)
