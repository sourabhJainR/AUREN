from portable.external_environment_protocol import EnvironmentContract, EpisodeTrace
from portable.external_evaluation_campaign import ExternalEvaluationCampaign
from portable.external_generalization_runner import ExternalGeneralizationCampaignRunner


class Env:
    def __init__(self, digest):
        self.digest = digest
    def run(self, case_id, contract):
        return EpisodeTrace(case_id, contract.contract_digest, 12, True, True, (case_id+"-evidence",), ("search",))


class Oracle:
    def verify(self, trace):
        return True


def make():
    c=EnvironmentContract(
        "world","1",("text","image"),("read","tool_call"),20,
        external_tools=("search",), unfamiliar_tools=("search",)
    )
    campaign=ExternalEvaluationCampaign(
        "campaign","1","1",c.contract_digest,"generator",
        "oracle","eval","runtime",("a","b","c"),("c",),
        novel_domain_case_ids=("c",),external_attestation="attested"
    )
    return c,campaign


def test_runner_executes_only_external_adapter_and_oracle():
    c,campaign=make()
    result=ExternalGeneralizationCampaignRunner(Env(c.contract_digest),Oracle()).run(campaign,c)
    assert result.outcome.holdout_pass_rate == 1.0
    assert result.outcome.holdout_verification_rate == 1.0
    assert result.environment.long_horizon_success


def test_runner_rejects_wrong_environment_identity():
    c,campaign=make()
    other=EnvironmentContract(
        "other","1",("text",),("read",),20,
        external_tools=("search",), unfamiliar_tools=("search",)
    )
    try:
        ExternalGeneralizationCampaignRunner(Env(c.contract_digest),Oracle()).run(campaign,other)
    except ValueError as exc:
        assert "corpus digest" in str(exc)
    else:
        raise AssertionError("expected identity rejection")


def test_runner_rejects_oracle_disagreement():
    c,campaign=make()
    class BadOracle:
        def verify(self, trace): return not trace.verified
    try:
        ExternalGeneralizationCampaignRunner(Env(c.contract_digest),BadOracle()).run(campaign,c)
    except ValueError as exc:
        assert "disagrees" in str(exc)
    else:
        raise AssertionError("expected oracle disagreement")


def test_runner_does_not_allow_missing_holdout_execution():
    c,campaign=make()
    empty=ExternalEvaluationCampaign(
        "campaign2","1","1",c.contract_digest,"generator","oracle","eval","runtime",
        ("a",),("a",),external_attestation="attested"
    )
    class Wrong:
        def run(self, case_id, contract):
            return EpisodeTrace("other",contract.contract_digest,1,True,True,("e",))
    try:
        ExternalGeneralizationCampaignRunner(Wrong(),Oracle()).run(empty,c)
    except ValueError as exc:
        assert "wrong case" in str(exc)
    else:
        raise AssertionError("expected case-boundary rejection")
