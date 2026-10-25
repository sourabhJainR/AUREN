import unittest

from portable.arena_campaign_packet import (
    CampaignPacketError,
    campaign_content_digest,
    validate_campaign_packet,
)


def digest(char):
    return char * 64


def refresh_campaign_digest(data):
    data["attestation"]["campaign_digest"] = campaign_content_digest(data)
    return data


def packet():
    data = {
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
    return refresh_campaign_digest(data)


class ArenaCampaignPacketTests(unittest.TestCase):
    def test_valid_packet_requires_injected_attestation_verifier(self):
        result = validate_campaign_packet(packet(), verify_external_attestation=lambda _: True)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["repeatability_verified"])
        self.assertEqual(len(result["negative_controls_rejected"]), 7)

    def test_attestation_digest_binds_every_campaign_field_before_trust_verification(self):
        data = packet()
        calls = []
        data["resource_cost"]["wall_seconds"] += 1
        with self.assertRaisesRegex(CampaignPacketError, "does not match canonical campaign content"):
            validate_campaign_packet(
                data,
                verify_external_attestation=lambda value: calls.append(value) or True,
            )
        self.assertEqual(calls, [])

    def test_campaign_content_digest_excludes_attestation_envelope_only(self):
        data = packet()
        original = campaign_content_digest(data)
        data["attestation"]["signature"] = "rotated-signature"
        data["attestation"]["signer_id"] = "different-signer"
        self.assertEqual(campaign_content_digest(data), original)
        data["corpus"]["holdout_digest"] = digest("e")
        self.assertNotEqual(campaign_content_digest(data), original)

    def test_missing_or_failed_verifier_fails_closed(self):
        with self.assertRaisesRegex(CampaignPacketError, "verifier is required"):
            validate_campaign_packet(packet(), verify_external_attestation=None)
        with self.assertRaisesRegex(CampaignPacketError, "not trusted"):
            validate_campaign_packet(packet(), verify_external_attestation=lambda _: False)

    def test_role_overlap_is_rejected_before_trust_claim(self):
        data = packet()
        data["operators"]["sandbox_auditor"] = data["operators"]["candidate_owner"]
        refresh_campaign_digest(data)
        with self.assertRaisesRegex(CampaignPacketError, "distinct operators"):
            validate_campaign_packet(data, verify_external_attestation=lambda _: True)

    def test_repeat_run_nonce_and_result_consistency_are_required(self):
        data = packet()
        data["runs"][1]["run_nonce"] = data["runs"][0]["run_nonce"]
        refresh_campaign_digest(data)
        with self.assertRaisesRegex(CampaignPacketError, "distinct nonces"):
            validate_campaign_packet(data, verify_external_attestation=lambda _: True)
        data = packet()
        data["runs"][1]["result_digest"] = digest("9")
        refresh_campaign_digest(data)
        with self.assertRaisesRegex(CampaignPacketError, "result digests differ"):
            validate_campaign_packet(data, verify_external_attestation=lambda _: True)

    def test_repeat_run_identity_must_be_unique_even_when_nonces_differ(self):
        data = packet()
        data["runs"][1]["run_id"] = data["runs"][0]["run_id"]
        refresh_campaign_digest(data)
        with self.assertRaisesRegex(CampaignPacketError, "distinct run identities"):
            validate_campaign_packet(data, verify_external_attestation=lambda _: True)

    def test_all_negative_controls_must_reject(self):
        data = packet()
        data["negative_controls"]["role_overlap"]["outcome"] = "accepted"
        refresh_campaign_digest(data)
        with self.assertRaisesRegex(CampaignPacketError, "was not rejected"):
            validate_campaign_packet(data, verify_external_attestation=lambda _: True)

    def test_unknown_self_asserted_trust_fields_are_rejected(self):
        data = packet()
        data["attestation"]["trusted"] = True
        with self.assertRaisesRegex(CampaignPacketError, "unknown or missing fields"):
            validate_campaign_packet(data, verify_external_attestation=lambda _: True)

    def test_v2_requires_signed_uncertainty_report(self):
        data = packet()
        data["schema_version"] = "auren-external-campaign-packet-v2"
        data["uncertainty"] = {
            "method": "wilson-score-interval",
            "sample_size": 20,
            "confidence_level": 0.95,
            "lower_bound": 0.43,
            "upper_bound": 0.82,
            "unknown_count": 1,
        }
        refresh_campaign_digest(data)
        result = validate_campaign_packet(data, verify_external_attestation=lambda _: True)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["uncertainty_reported"])
        self.assertEqual(result["uncertainty"]["sample_size"], 20)
        self.assertEqual(result["uncertainty"]["unknown_count"], 1)

    def test_v2_rejects_invalid_uncertainty_and_binds_it_to_attestation(self):
        data = packet()
        data["schema_version"] = "auren-external-campaign-packet-v2"
        data["uncertainty"] = {
            "method": "wilson-score-interval",
            "sample_size": 20,
            "confidence_level": 0.95,
            "lower_bound": 0.8,
            "upper_bound": 0.2,
            "unknown_count": 1,
        }
        refresh_campaign_digest(data)
        with self.assertRaisesRegex(CampaignPacketError, "lower_bound must not exceed upper_bound"):
            validate_campaign_packet(data, verify_external_attestation=lambda _: True)

        data["uncertainty"]["lower_bound"] = 0.2
        refresh_campaign_digest(data)
        data["uncertainty"]["unknown_count"] = 21
        refresh_campaign_digest(data)
        with self.assertRaisesRegex(CampaignPacketError, "within sample_size"):
            validate_campaign_packet(data, verify_external_attestation=lambda _: True)

    def test_v2_requires_uncertainty_but_v1_remains_compatible(self):
        data = packet()
        self.assertFalse(validate_campaign_packet(
            data, verify_external_attestation=lambda _: True
        )["uncertainty_reported"])
        data["schema_version"] = "auren-external-campaign-packet-v2"
        refresh_campaign_digest(data)
        with self.assertRaisesRegex(CampaignPacketError, "unknown or missing fields"):
            validate_campaign_packet(data, verify_external_attestation=lambda _: True)

    def test_malformed_digest_and_empty_limitations_fail_closed(self):
        data = packet()
        data["corpus"]["digest"] = "not-a-digest"
        refresh_campaign_digest(data)
        with self.assertRaisesRegex(CampaignPacketError, "SHA-256"):
            validate_campaign_packet(data, verify_external_attestation=lambda _: True)
        data = packet()
        data["limitations"] = []
        refresh_campaign_digest(data)
        with self.assertRaisesRegex(CampaignPacketError, "limitations"):
            validate_campaign_packet(data, verify_external_attestation=lambda _: True)


if __name__ == "__main__":
    unittest.main()
