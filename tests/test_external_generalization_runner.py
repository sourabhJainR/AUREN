import unittest

from portable.attestation_key_separation import AttestationTrustPolicy, SeparationAwareAttestor
from portable.external_environment_protocol import EnvironmentContract, EpisodeTrace
from portable.external_evaluation_campaign import ExternalEvaluationCampaign
from portable.external_generalization_runner import ExternalGeneralizationCampaignRunner
from portable.independent_evaluation_attestation import AttestedEvaluation, EvaluationAttestation


class Env:
    def __init__(self, digest):
        self.digest = digest

    def run(self, case_id, contract):
        return EpisodeTrace(case_id, contract.contract_digest, 12, True, True, (case_id + "-evidence",), ("search",))


class Oracle:
    def verify(self, trace):
        return True


def make():
    contract = EnvironmentContract(
        "world", "1", ("text", "image"), ("read", "tool_call"), 20,
        external_tools=("search",), unfamiliar_tools=("search",),
    )
    campaign = ExternalEvaluationCampaign(
        "campaign", "1", "1", contract.contract_digest, "generator",
        "oracle", "eval", "runtime", ("a", "b", "c"), ("c",),
        novel_domain_case_ids=("c",), external_attestation="attested",
    )
    return contract, campaign


def verify_campaign(campaign):
    attestation = EvaluationAttestation(
        campaign.campaign_digest,
        campaign.corpus_digest,
        campaign.oracle_digest,
        campaign.evaluator_version,
        "external-signer",
        campaign.external_attestation,
    )
    verifier = SeparationAwareAttestor(
        lambda value: value.signature == campaign.external_attestation,
        AttestationTrustPolicy(frozenset({"external-signer"})),
        lambda principal, role: (
            (role == "evaluator" and principal == "runtime-evaluator")
            or (role == "oracle" and principal == "independent-oracle")
        ),
    )
    return verifier.verify(
        attestation,
        evaluator_id="runtime-evaluator",
        oracle_id="independent-oracle",
        expected_campaign_digest=campaign.campaign_digest,
        expected_corpus_digest=campaign.corpus_digest,
        expected_oracle_digest=campaign.oracle_digest,
    )


class ExternalGeneralizationRunnerTests(unittest.TestCase):
    def runner(self, environment=None, oracle=None, verifier=verify_campaign):
        contract, campaign = make()
        return contract, campaign, ExternalGeneralizationCampaignRunner(
            environment or Env(contract.contract_digest),
            oracle or Oracle(),
            attestation_verifier=verifier,
        )

    def test_runner_executes_only_after_verified_attestation(self):
        contract, campaign, runner = self.runner()
        result = runner.run(campaign, contract)
        self.assertTrue(result.attestation.trustworthy)
        self.assertEqual(result.outcome.holdout_pass_rate, 1.0)
        self.assertEqual(result.outcome.holdout_verification_rate, 1.0)
        self.assertTrue(result.environment.long_horizon_success)

    def test_runner_rejects_wrong_environment_identity(self):
        contract, campaign, runner = self.runner()
        other = EnvironmentContract(
            "other", "1", ("text",), ("read",), 20,
            external_tools=("search",), unfamiliar_tools=("search",),
        )
        with self.assertRaisesRegex(ValueError, "corpus digest"):
            runner.run(campaign, other)

    def test_runner_rejects_oracle_disagreement(self):
        contract, campaign, _runner = self.runner()
        class BadOracle:
            def verify(self, trace):
                return not trace.verified
        runner = ExternalGeneralizationCampaignRunner(
            Env(contract.contract_digest), BadOracle(), attestation_verifier=verify_campaign
        )
        with self.assertRaisesRegex(ValueError, "disagrees"):
            runner.run(campaign, contract)

    def test_runner_rejects_wrong_case_trace(self):
        contract, campaign, _runner = self.runner()
        empty = ExternalEvaluationCampaign(
            "campaign2", "1", "1", contract.contract_digest, "generator", "oracle", "eval", "runtime",
            ("a",), ("a",), external_attestation="attested",
        )
        class Wrong:
            def run(self, case_id, contract):
                return EpisodeTrace("other", contract.contract_digest, 1, True, True, ("e",))
        runner = ExternalGeneralizationCampaignRunner(
            Wrong(), Oracle(), attestation_verifier=verify_campaign
        )
        with self.assertRaisesRegex(ValueError, "wrong case"):
            runner.run(empty, contract)

    def test_runner_fails_closed_without_attestation_material(self):
        contract, campaign, _runner = self.runner()
        unattested = ExternalEvaluationCampaign(
            "campaign2", "1", "1", contract.contract_digest, "generator", "oracle", "eval", "runtime",
            ("a",), ("a",), external_attestation="",
        )
        runner = ExternalGeneralizationCampaignRunner(
            Env(contract.contract_digest), Oracle(), attestation_verifier=verify_campaign
        )
        with self.assertRaisesRegex(ValueError, "attestation is required"):
            runner.run(unattested, contract)

    def test_runner_requires_verifier_even_with_nonempty_signature(self):
        contract, campaign = make()
        runner = ExternalGeneralizationCampaignRunner(Env(contract.contract_digest), Oracle())
        with self.assertRaisesRegex(ValueError, "verifier is required"):
            runner.run(campaign, contract)

    def test_runner_rejects_untrusted_attestation(self):
        contract, campaign = make()
        runner = ExternalGeneralizationCampaignRunner(
            Env(contract.contract_digest), Oracle(),
            attestation_verifier=lambda _campaign: AttestedEvaluation(
                EvaluationAttestation(campaign.campaign_digest, campaign.corpus_digest, campaign.oracle_digest,
                                      campaign.evaluator_version, "untrusted", "attested"),
                True, False, True, (), "evidence",
            ),
        )
        with self.assertRaisesRegex(ValueError, "not independently verified"):
            runner.run(campaign, contract)


if __name__ == "__main__":
    unittest.main()
