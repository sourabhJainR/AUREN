from __future__ import annotations

import unittest

from portable.external_evaluator_gateway import ExternalEvaluatorCommand
from portable.sealed_arena_boundary import SealedArenaEvidence, SealedCampaignRequest, SealedCaseEnvelope
from portable.sealed_cross_domain_arena import CrossDomainCampaign, SealedCrossDomainRunner, SealedDomain


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
    return CrossDomainCampaign(
        "campaign-id",
        request,
        (
            SealedDomain("domain-a", ("case-a",), True),
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
        result = SealedCrossDomainRunner(gateway).run(
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
