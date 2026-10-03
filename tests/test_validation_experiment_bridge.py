from portable.capability_invention_validation import CapabilityInventionValidator
from portable.capability_invention_validation_runner import CapabilityInventionValidationRunner, ValidationEvidence
from portable.external_evaluation_campaign import LearningIntervention
from portable.external_environment_protocol import EnvironmentContract, EpisodeTrace
from portable.open_ended_capability_discovery import CapabilityGap, OpenEndedCapabilityDiscovery
from portable.validation_experiment_bridge import ValidationExperimentBridge


class Executor:
    def run(self, probe, contract):
        return EpisodeTrace(probe.probe_id, contract.contract_digest, 3, True, True, ("evidence",))


class Oracle:
    independent = True
    def verify(self, probe, trace):
        return trace.verified


def evidence():
    gap = CapabilityGap("gap", ("failure",), ("science", "planning"), ("tool-selection",), .9, .8)
    proposal = OpenEndedCapabilityDiscovery().discover((gap,))[0]
    plan = CapabilityInventionValidator().plan(proposal, ("science", "planning"))
    contract = EnvironmentContract("env", "1", ("text", "structured"), ("read",), 10)
    return CapabilityInventionValidationRunner(Executor(), Oracle()).run(plan, contract).evidence


def intervention():
    return LearningIntervention("i1", "adaptive-capability", "hypothesis", "improve transfer")


def test_builds_causal_experiment_proposal():
    e = evidence()
    p = ValidationExperimentBridge().propose(
        capability="adaptive-capability",
        evidence=e,
        intervention=intervention(),
        prior_campaign_digest="prior",
    )
    assert p.ready_for_execution
    assert p.required_domains == ("science", "planning")
    assert p.retest_contract.new_holdout_required
    assert p.retest_contract.independence_required
    assert p.minimum_replications == 3
    assert p.proposal_digest


def test_rejects_untrustworthy_validation():
    e = ValidationEvidence(
        "proposal", "plan", ("p1", "p2"), ("p1", "p2"), ("p1",), ("p1",),
        ("p2",), ("a", "b"), True, True, True, 10, "digest"
    )
    try:
        ValidationExperimentBridge().propose(
            capability="capability", evidence=e, intervention=intervention(),
            prior_campaign_digest="prior",
        )
    except ValueError as exc:
        assert "trustworthy" in str(exc)
    else:
        raise AssertionError("expected trust boundary failure")
