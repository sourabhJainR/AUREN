import unittest

from portable.decision_fabric import (
    EvidenceAssessment, EvidenceTrustLevel, ResourceProfile, ResourceRequest,
    assess_evidence_trust, assess_uncertainty, route_resource,
)


class DecisionFabricSafetyTests(unittest.TestCase):
    def test_model_claim_is_not_verification(self):
        claim = EvidenceAssessment("e1", EvidenceTrustLevel.MODEL_CLAIM, "repo", verified=True)
        deterministic = EvidenceAssessment("e2", EvidenceTrustLevel.DETERMINISTIC_RESULT, "repo", verified=True)
        self.assertFalse(assess_evidence_trust((claim,)))
        self.assertTrue(assess_evidence_trust((deterministic,)))

    def test_disagreement_increases_verification_depth(self):
        low = assess_uncertainty(confidence=0.9)
        high = assess_uncertainty(confidence=0.9, disagreement=0.9)
        self.assertGreater(high.score, low.score)
        self.assertIn(high.verification_depth, {"independent", "independent-plus-human"})

    def test_resource_routing_respects_isolation_and_history(self):
        profile = ResourceProfile(cpu_available=8, memory_mb=16384)
        isolated = route_resource(ResourceRequest(requires_isolation=True), profile)
        self.assertEqual(isolated.lane, "isolated")
        local = route_resource(ResourceRequest(estimated_cpu=2), profile, historical_local_success=0.95)
        cloud = route_resource(ResourceRequest(estimated_cpu=2), profile, historical_local_success=0.4)
        self.assertEqual(local.lane, "local")
        self.assertEqual(cloud.lane, "cloud")


if __name__ == "__main__":
    unittest.main()
