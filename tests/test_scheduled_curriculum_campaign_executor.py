import pytest
from dataclasses import dataclass
from portable.autonomous_curriculum_evolution import AutonomousCurriculumEvolution
from portable.open_ended_task_environment_discovery import DiscoverySignal, OpenEndedTaskEnvironmentDiscovery
from portable.capability_invention_validation import CapabilityValidationPlan, ValidationProbe
from portable.capability_invention_validation_runner import CapabilityInventionValidationRunner
from portable.external_environment_protocol import EnvironmentContract, EpisodeTrace
from portable.external_curriculum_campaign_orchestrator import ExternalCurriculumCampaignOrchestrator
from portable.resource_aware_curriculum_scheduler import ResourceAwareCurriculumScheduler
from portable.scheduled_curriculum_campaign_executor import ScheduledCurriculumCampaignExecutor

def curriculum():
    targets=OpenEndedTaskEnvironmentDiscovery().discover((
        DiscoverySignal("a","science","long-horizon",.9),
        DiscoverySignal("b","planning","adversarial",.8)))
    return AutonomousCurriculumEvolution().build(targets,max_targets=2)

@dataclass
class Executor:
    def run(self, probe, contract):
        return EpisodeTrace(probe.probe_id,contract.contract_digest,("ok",),("done",),1,True,True)
@dataclass
class Oracle:
    independent: bool=True
    def verify(self, probe, trace): return True
class Generator:
    def build(self,target_id,*,plan_digest):
        p=CapabilityValidationPlan("proposal-"+target_id,(
            ValidationProbe("p1-"+target_id,"long-horizon","a",True,True),
            ValidationProbe("p2-"+target_id,"adversarial","b",True,True)),True,True,.75,.02)
        c=EnvironmentContract("env-"+target_id,"1",("text",),("read",),3,True,True)
        return p,c,"g-"+target_id,"o-"+target_id

def test_schedule_drives_exact_campaign_targets():
    c=curriculum()
    orch=ExternalCurriculumCampaignOrchestrator(Generator(),CapabilityInventionValidationRunner(Executor(),Oracle()))
    result=ScheduledCurriculumCampaignExecutor(ResourceAwareCurriculumScheduler(),orch).run(c)
    assert result.resource_budget_respected
    assert result.scheduled_target_ids==tuple(x.target_id for x in result.campaign.target_results)
    assert result.execution_digest

def test_rejects_schedule_for_other_curriculum():
    c=curriculum()
    orch=ExternalCurriculumCampaignOrchestrator(Generator(),CapabilityInventionValidationRunner(Executor(),Oracle()))
    ex=ScheduledCurriculumCampaignExecutor(ResourceAwareCurriculumScheduler(),orch)
    schedule=ex.scheduler.build(c)
    with pytest.raises(ValueError,match="does not belong"):
        ex.run(curriculum(),schedule=schedule)
