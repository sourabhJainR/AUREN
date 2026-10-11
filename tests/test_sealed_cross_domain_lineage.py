from __future__ import annotations

import unittest

from portable.external_evaluator_gateway import ExternalEvaluatorCommand
from portable.sealed_arena_boundary import SealedArenaEvidence, SealedCampaignRequest, SealedCaseEnvelope
from portable.sealed_cross_domain_arena import CrossDomainCampaign, SealedCrossDomainRunner, SealedDomain, TargetDomainProvenance


def make_campaign():
    request = SealedCampaignRequest(
        "request-digest",
        "corpus-digest",
        (
            SealedCaseEnvelope("case-a", "task-a", "input-a", "env-a", True),
            SealedCaseEnvelope("case-b", "task-b", "input-b", "env-b", True),
        ),
        "run-nonce",
    )
    provenance = TargetDomainProvenance(
        domain_id="domain-a",
        origin="https://benchmarks.example.org/domain-a",
        manifest_digest=request.manifest_digest,
        split="sealed_holdout",
        development_corpus_digest="development-corpus",
        development_overlap_status="no_overlap",
        overlap_attestation_digest="overlap-attestation",
        independent_operator_id="independent-operator",
        attestation_digest="test-attestation",
        attestation_signer_id="independent-operator",
        attestation_signature="fixture-signature",
    )
    return CrossDomainCampaign(
        "campaign-id",
        request,
        (
            SealedDomain("domain-a", ("case-a",), True, provenance=provenance),
            SealedDomain("domain-b", ("case-b",), False),
        ),
        "independent-oracle",
        "external-evaluator",
    )


class RecordingGateway:
    def __init__(self):
        self.request = None

    def evaluate(self, request, command):
        self.request = request
        return SealedArenaEvidence("receipt-digest", 1.0, len(request.cases))


class SealedCrossDomainLineageTests(unittest.TestCase):
    def test_runner_binds_domain_assignment_into_external_receipt_lineage(self):
        campaign = make_campaign()
        gateway = RecordingGateway()
        result = SealedCrossDomainRunner(gateway, target_provenance_verifier=lambda _value: True, trusted_target_operator_ids=frozenset({"independent-operator"})).run(
            campaign, ExternalEvaluatorCommand(("external-evaluator",))
        )
        self.assertEqual(gateway.request.campaign_digest, campaign.campaign_digest)
        self.assertNotEqual(gateway.request.campaign_digest, campaign.request.campaign_digest)
        self.assertEqual(gateway.request.corpus_digest, campaign.request.corpus_digest)
        self.assertEqual(gateway.request.run_nonce, campaign.request.run_nonce)
        self.assertEqual(result.receipt_digest, "receipt-digest")

    def test_domain_novel_flag_requires_exact_boolean(self):
        with self.assertRaisesRegex(ValueError, "novel must be a boolean"):
            SealedDomain("domain-a", ("case-a",), "false")

    def test_domain_identifiers_and_case_ids_must_be_non_empty_strings(self):
        with self.assertRaisesRegex(ValueError, "domain_id"):
            SealedDomain(17, ("case-a",))
        with self.assertRaisesRegex(ValueError, "case_ids"):
            SealedDomain("domain-a", (" ",))

    def test_target_operator_must_differ_from_evaluator_and_oracle(self):
        campaign = make_campaign()
        conflicting = CrossDomainCampaign(
            campaign.campaign_id,
            campaign.request,
            (
                SealedDomain(
                    "domain-a",
                    ("case-a",),
                    True,
                    provenance=TargetDomainProvenance(
                        domain_id="domain-a",
                        origin="https://benchmarks.example.org/domain-a",
                        manifest_digest=campaign.request.manifest_digest,
                        split="sealed_holdout",
                        development_corpus_digest="development-corpus",
                        development_overlap_status="no_overlap",
                        overlap_attestation_digest="overlap-attestation",
                        independent_operator_id=campaign.evaluator_id,
                        attestation_digest="test-attestation",
                        attestation_signer_id=campaign.evaluator_id,
                        attestation_signature="fixture-signature",
                    ),
                ),
                SealedDomain("domain-b", ("case-b",), False),
            ),
            campaign.independent_oracle_id,
            campaign.evaluator_id,
        )
        runner = SealedCrossDomainRunner(RecordingGateway(), target_provenance_verifier=lambda _value: True, trusted_target_operator_ids=frozenset({"independent-operator"}))
        with self.assertRaisesRegex(ValueError, "independent of evaluator and oracle"):
            runner.run(conflicting, ExternalEvaluatorCommand(("external-evaluator",)))

    def test_runner_rejects_operator_not_in_out_of_band_allowlist(self):
        campaign = make_campaign()
        runner = SealedCrossDomainRunner(
            RecordingGateway(),
            target_provenance_verifier=lambda _value: True,
            trusted_target_operator_ids=frozenset({"different-operator"}),
        )
        with self.assertRaisesRegex(ValueError, "not in the out-of-band trusted allowlist"):
            runner.run(campaign, ExternalEvaluatorCommand(("external-evaluator",)))

    def test_runner_requires_signed_operator_provenance(self):
        campaign = make_campaign()
        provenance = campaign.domains[0].provenance
        from dataclasses import replace
        altered = replace(provenance, attestation_signer_id="", attestation_signature="")
        domains = (replace(campaign.domains[0], provenance=altered), campaign.domains[1])
        unsigned = CrossDomainCampaign(
            campaign.campaign_id, campaign.request, domains,
            campaign.independent_oracle_id, campaign.evaluator_id,
        )
        runner = SealedCrossDomainRunner(
            RecordingGateway(),
            target_provenance_verifier=lambda _value: True,
            trusted_target_operator_ids=frozenset({"independent-operator"}),
        )
        with self.assertRaisesRegex(ValueError, "missing a matching operator signature"):
            runner.run(unsigned, ExternalEvaluatorCommand(("external-evaluator",)))

    def test_campaign_digest_changes_when_domain_assignment_changes(self):
        campaign = make_campaign()
        altered = CrossDomainCampaign(
            campaign.campaign_id,
            campaign.request,
            (
                SealedDomain("domain-a", ("case-b",), True),
                SealedDomain("domain-b", ("case-a",), False),
            ),
            campaign.independent_oracle_id,
            campaign.evaluator_id,
        )
        self.assertNotEqual(campaign.campaign_digest, altered.campaign_digest)


if __name__ == "__main__":
    unittest.main()
