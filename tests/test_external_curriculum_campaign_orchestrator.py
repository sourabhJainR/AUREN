from dataclasses import dataclass
from contextlib import contextmanager

@contextmanager
def raises(exc, match=None):
    try:
        yield
    except exc as error:
        if match is not None and match not in str(error):
            raise AssertionError(f"expected {match!r} in {error!s}")
    else:
        raise AssertionError(f"expected {exc.__name__} to be raised")
from portable.autonomous_curriculum_evolution import AutonomousCurriculumEvolution
from portable.capability_invention_validation import CapabilityValidationPlan, ValidationProbe
from portable.capability_invention_validation_runner import CapabilityInventionValidationRunner
from portable.external_curriculum_campaign_orchestrator import ExternalCurriculumCampaignOrchestrator
from portable.external_environment_protocol import EnvironmentContract, EpisodeTrace
from portable.open_ended_task_environment_discovery import DiscoverySignal, OpenEndedTaskEnvironmentDiscovery

def curriculum():
    targets = OpenEndedTaskEnvironmentDiscovery().discover((
        DiscoverySignal("e1", "science", "long-horizon", .9),
        DiscoverySignal("e2", "planning", "adversarial", .8, True),
    ))
    return AutonomousCurriculumEvolution().build(targets, max_targets=2)

@dataclass
class Executor:
    def run(self, probe, contract):
        return EpisodeTrace(probe.probe_id, contract.contract_digest, ("ok",), ("done",), 1, True, True)

@dataclass
class Oracle:
    independent: bool = True
    def verify(self, probe, trace): return True

class Generator:
    def __init__(self): self.calls = []
    def build(self, target_id, *, plan_digest):
        self.calls.append((target_id, plan_digest))
        plan = CapabilityValidationPlan("proposal-"+target_id, (
            ValidationProbe("p-"+target_id+"-a", "long-horizon", "family-a", True, True),
            ValidationProbe("p-"+target_id+"-b", "adversarial", "family-b", True, True),
        ), True, True, .75, .02)
        contract = EnvironmentContract("env-"+target_id, "1", ("text",), ("read", "tool_call"), 3, True, True, ("tool-x",), ("tool-x",))
        return plan, contract, "generator-"+target_id, "oracle-"+target_id

def test_runs_multi_domain_curriculum_and_binds_plan():
    g = Generator()
    result = ExternalCurriculumCampaignOrchestrator(g, CapabilityInventionValidationRunner(Executor(), Oracle())).run(curriculum())
    assert result.trustworthy
    assert len(result.target_results) == 2
    assert all(r.plan_digest == result.curriculum_plan_digest for r in result.target_results)
    assert all(r.outcome is not None for r in result.target_results)
    assert all(c[1] == result.curriculum_plan_digest for c in g.calls)

def test_rejects_plan_mismatch():
    with raises(ValueError, match="digest mismatch"):
        ExternalCurriculumCampaignOrchestrator(Generator(), CapabilityInventionValidationRunner(Executor(), Oracle())).run(curriculum(), expected_plan_digest="wrong")

def test_recovery_records_failed_target_without_claiming_trust():
    class Broken(Generator):
        def build(self, target_id, *, plan_digest):
            if target_id.endswith("1"): raise RuntimeError("environment unavailable")
            return super().build(target_id, plan_digest=plan_digest)
    class Recovery:
        def recover(self, target_id, error): return True
    result = ExternalCurriculumCampaignOrchestrator(Broken(), CapabilityInventionValidationRunner(Executor(), Oracle()), recovery=Recovery()).run(curriculum())
    assert not result.trustworthy
    assert any(r.recovered for r in result.target_results)
    assert result.failed_target_ids

def test_rejects_untrusted_curriculum():
    plan = curriculum()
    bad = type(plan)(plan.plan_id, plan.entries, plan.max_targets, "")
    with raises(ValueError, match="trustworthy"):
        ExternalCurriculumCampaignOrchestrator(Generator(), CapabilityInventionValidationRunner(Executor(), Oracle())).run(bad)
