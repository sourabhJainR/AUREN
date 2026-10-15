import unittest

from portable.arena_campaign_packet import CampaignPacketError, validate_campaign_packet


def digest(char):
    return char * 64


def packet():
    return {
        "schema_version": "auren-external-campaign-packet-v1",
        "campaign_id": "campaign-2026-01",
        "candidate": {"repository": "sourabhJainR/AUREN", "commit_sha": "a" * 40, "runtime_digest": digest("b")},
        "source_domain": "repository-engineering",
        "target_domain": "unseen-data-transformation",
        "operators": {
            "candidate_owner": "candidate-team",
            "corpus_owner": "independent-corpus-team",
            "evaluator_owner": "independent-evaluator-team",
            "sandbox_auditor": "independent-sandbox-auditor",
        },
        "corpus": {"digest": digest("c"), "holdout_digest": digest("d"), "provenance_ref": "external://campaign/corpus-provenance"},
        "evaluator": {"id": "external-evaluator-v1", "version": "1.0.0", "digest": digest("e"), "oracle_digest": digest("f")},
        "attestation": {"signer_id": "independent-campaign-signer", "key_id": "campaign-key-v1", "signature": "signature-material", "campaign_digest": digest("1")},
        "runs": [
            {"run_id": "run-1", "run_nonce": "nonce-1", "result_digest": digest("2"), "receipt_digest": digest("3"), "outcome": "completed"},
            {"run_id": "run-2", "run_nonce": "nonce-2", "result_digest": digest("2"), "receipt_digest": digest("4"), "outcome": "completed"},
        ],
        "baseline": {"method": "same-task-control", "sample_size": 20, "pass_rate": 0.65},
        "negative_controls": {
            name: {"outcome": "rejected", "evidence_digest": digest(char)}
            for name, char in (
                ("nonce_replay", "5"), ("receipt_tamper", "6"), ("corpus_mismatch", "7"),
                ("invalid_signature", "8"), ("role_overlap", "9"), ("missing_isolation", "a"),
                ("partial_receipt", "b"),
            )
        },
        "resource_cost": {"wall_seconds": 25.0, "cpu_seconds": 19.0, "peak_memory_bytes": 1048576},
        "limitations": ["One target environment; conclusions do not generalize beyond the sampled cases."],
    }


class ArenaCampaignPacketTests(unittest.TestCase):
    def test_valid_packet_requires_injected_attestation_verifier(self):
        result = validate_campaign_packet(packet(), verify_external_attestation=lambda _: True)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["repeatability_verified"])
        self.assertEqual(len(result["negative_controls_rejected"]), 7)

    def test_missing_or_failed_verifier_fails_closed(self):
        with self.assertRaisesRegex(CampaignPacketError, "verifier is required"):
            validate_campaign_packet(packet(), verify_external_attestation=None)
        with self.assertRaisesRegex(CampaignPacketError, "not trusted"):
            validate_campaign_packet(packet(), verify_external_attestation=lambda _: False)

    def test_role_overlap_is_rejected_before_trust_claim(self):
        data = packet()
        data["operators"]["sandbox_auditor"] = data["operators"]["candidate_owner"]
        with self.assertRaisesRegex(CampaignPacketError, "distinct operators"):
            validate_campaign_packet(data, verify_external_attestation=lambda _: True)

    def test_repeat_run_nonce_and_result_consistency_are_required(self):
        data = packet()
        data["runs"][1]["run_nonce"] = data["runs"][0]["run_nonce"]
        with self.assertRaisesRegex(CampaignPacketError, "distinct nonces"):
            validate_campaign_packet(data, verify_external_attestation=lambda _: True)
        data = packet()
        data["runs"][1]["result_digest"] = digest("9")
        with self.assertRaisesRegex(CampaignPacketError, "result digests differ"):
            validate_campaign_packet(data, verify_external_attestation=lambda _: True)

    def test_all_negative_controls_must_reject(self):
        data = packet()
        data["negative_controls"]["role_overlap"]["outcome"] = "accepted"
        with self.assertRaisesRegex(CampaignPacketError, "was not rejected"):
            validate_campaign_packet(data, verify_external_attestation=lambda _: True)

    def test_unknown_self_asserted_trust_fields_are_rejected(self):
        data = packet()
        data["attestation"]["trusted"] = True
        with self.assertRaisesRegex(CampaignPacketError, "unknown or missing fields"):
            validate_campaign_packet(data, verify_external_attestation=lambda _: True)

    def test_malformed_digest_and_empty_limitations_fail_closed(self):
        data = packet()
        data["corpus"]["digest"] = "not-a-digest"
        with self.assertRaisesRegex(CampaignPacketError, "SHA-256"):
            validate_campaign_packet(data, verify_external_attestation=lambda _: True)
        data = packet()
        data["limitations"] = []
        with self.assertRaisesRegex(CampaignPacketError, "limitations"):
            validate_campaign_packet(data, verify_external_attestation=lambda _: True)


if __name__ == "__main__":
    unittest.main()
