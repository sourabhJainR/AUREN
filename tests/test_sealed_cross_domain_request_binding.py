import unittest

from portable.sealed_arena_boundary import SealedCampaignRequest, SealedCaseEnvelope
from portable.sealed_cross_domain_arena import CrossDomainCampaign, SealedDomain


def make_campaign(input_digest):
    request = SealedCampaignRequest(
        "campaign", "corpus",
        (
            SealedCaseEnvelope("case-a", "task-a", input_digest, "env-a", True, ("novel-domain",)),
            SealedCaseEnvelope("case-b", "task-b", "input-b", "env-b", True, ("source-domain",)),
        ),
        "unique-run-nonce",
    )
    return CrossDomainCampaign(
        "campaign-id", request,
        (SealedDomain("novel-domain", ("case-a",), True),
         SealedDomain("source-domain", ("case-b",), False)),
        "independent-oracle", "independent-evaluator",
    )


class SealedCrossDomainRequestBindingTests(unittest.TestCase):
    def test_campaign_digest_changes_when_any_case_input_changes(self):
        original = make_campaign("input-a")
        tampered = make_campaign("tampered-input-a")
        self.assertNotEqual(original.request.request_digest, tampered.request.request_digest)
        self.assertNotEqual(original.campaign_digest, tampered.campaign_digest)


if __name__ == "__main__":
    unittest.main()
