from portable.capability_invention_validation import CapabilityInventionValidator
from portable.capability_invention_validation_runner import CapabilityInventionValidationRunner
from portable.external_environment_protocol import EnvironmentContract, EpisodeTrace
from portable.open_ended_capability_discovery import CapabilityGap, OpenEndedCapabilityDiscovery


class Executor:
    def run(self, probe, contract):
        return EpisodeTrace(probe.probe_id, contract.contract_digest, 3, True, True, (f"evidence-{probe.probe_id}",))


class Oracle:
    independent = True
    def verify(self, probe, trace):
        return trace.verified


def make_plan():
    gap = CapabilityGap("gap-1", ("failure-1",), ("science", "planning"), ("tool-selection",), 0.9, 0.8)
    proposal = OpenEndedCapabilityDiscovery().discover((gap,))[0]
    return CapabilityInventionValidator().plan(proposal, ("science", "planning"))


def make_contract():
    return EnvironmentContract("external-validation", "1", ("text", "structured"), ("read", "tool_call"), 10)


def test_runs_fresh_multidomain_holdout():
    plan, contract = make_plan(), make_contract()
    result = CapabilityInventionValidationRunner(Executor(), Oracle()).run(plan, contract, expected_plan_digest=plan.plan_digest)
    assert result.evidence.trustworthy
    assert result.evidence.holdout_pass_rate == 1.0
    assert result.evidence.task_families == ("science", "planning")
    assert len(result.traces) == len(plan.probes)


def test_rejects_plan_digest_mismatch():
    plan, contract = make_plan(), make_contract()
    try:
        CapabilityInventionValidationRunner(Executor(), Oracle()).run(plan, contract, expected_plan_digest="bad")
    except ValueError as exc:
        assert "digest" in str(exc)
    else:
        raise AssertionError("expected digest failure")


def test_rejects_non_independent_oracle():
    class NonIndependent(Oracle):
        independent = False
    try:
        CapabilityInventionValidationRunner(Executor(), NonIndependent()).run(make_plan(), make_contract())
    except ValueError as exc:
        assert "independent" in str(exc)
    else:
        raise AssertionError("expected independence failure")


def test_rejects_wrong_probe_id():
    class WrongExecutor(Executor):
        def run(self, probe, contract):
            return EpisodeTrace("wrong", contract.contract_digest, 1, True, True, ("evidence",))
    try:
        CapabilityInventionValidationRunner(WrongExecutor(), Oracle()).run(make_plan(), make_contract())
    except ValueError as exc:
        assert "wrong probe" in str(exc)
    else:
        raise AssertionError("expected probe identity failure")


def test_rejects_oracle_disagreement():
    class DisagreeingOracle(Oracle):
        def verify(self, probe, trace):
            return not trace.verified
    try:
        CapabilityInventionValidationRunner(Executor(), DisagreeingOracle()).run(make_plan(), make_contract())
    except ValueError as exc:
        assert "oracle" in str(exc)
    else:
        raise AssertionError("expected oracle disagreement")


def test_rejects_contamination_and_budget():
    plan, contract = make_plan(), make_contract()
    runner = CapabilityInventionValidationRunner(Executor(), Oracle(), max_probes=1)
    try:
        runner.run(plan, contract, contaminated=True)
    except ValueError as exc:
        assert "contaminated" in str(exc)
    else:
        raise AssertionError("expected contamination failure")
    try:
        runner.run(plan, contract)
    except ValueError as exc:
        assert "probe budget" in str(exc)
    else:
        raise AssertionError("expected probe budget failure")
