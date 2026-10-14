import hashlib
import unittest
import time
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from dataclasses import replace

from portable.arena_run_receipt import ArenaRunReceipt, oracle_registry_digest
from portable.arena_isolation_attestation import ArenaIsolationAttestation
from portable.attestation_key_separation import AttestationTrustPolicy, SeparationAwareAttestor
from portable.causal_capability_promotion import CausalCapabilityPromotionGate, CausalPromotionEvidence
from portable.external_evaluation_campaign import ExternalEvaluationCampaign
from portable.independent_evaluation_attestation import AttestedEvaluation, EvaluationAttestation
from portable.promotion_evidence_chain import ArenaEvidenceObservationLedger, PromotionEvidenceChain, PromotionEvidenceChainBuilder


_REQUIRED_ISOLATION_CONTROLS = (
    "network_disabled",
    "no_host_mounts",
    "read_only_rootfs",
    "non_root_execution",
    "credentials_not_exposed",
    "resource_limits_enforced",
    "termination_observed",
)
_TEST_IMAGE_DIGEST = "sha256:" + "a" * 64


def isolation_kwargs(campaign, receipt):
    now = time.time()
    unsigned = ArenaIsolationAttestation(
        campaign_digest=campaign.campaign_digest,
        run_receipt_digest=receipt.receipt_digest,
        image_digest=_TEST_IMAGE_DIGEST,
        sandbox_id="external-sandbox-17",
        signer_id="independent-sandbox-auditor",
        signer_key_id="sandbox-auditor-key-v3",
        signature="placeholder",
        issued_at_epoch=now - 10,
        expires_at_epoch=now + 300,
        controls=_REQUIRED_ISOLATION_CONTROLS,
        observed_host_identity="host-audit-17",
        observation_started_at_epoch=now - 9,
        observation_ended_at_epoch=now - 8,
    )
    signature = "signed:" + hashlib.sha256(unsigned.signing_message()).hexdigest()
    attestation = replace(unsigned, signature=signature)

    def verify(signer_id, supplied_signature, message):
        return (
            signer_id == "independent-sandbox-auditor"
            and supplied_signature == "signed:" + hashlib.sha256(message).hexdigest()
        )

    return dict(
        isolation_attestation=attestation,
        verify_isolation_signature=verify,
        expected_image_digest=_TEST_IMAGE_DIGEST,
        evaluator_id="runtime-evaluator",
        oracle_id="independent-oracle",
        policy_runtime_id="policy-runtime",
        trusted_isolation_signers=frozenset({"independent-sandbox-auditor"}),
        trusted_isolation_signer_key_ids=frozenset({"sandbox-auditor-key-v3"}),
        revoked_isolation_signer_key_ids=frozenset(),
        isolation_now=now,
        expected_sandbox_identity="host-audit-17",
        revoked_isolation_signers=frozenset(),
        campaign_started_at_epoch=now - 30,
        campaign_ended_at_epoch=now - 1,
    )


def make_campaign(**overrides):
    oracle = oracle_registry_digest(("oracle-1",))
    values = dict(
        campaign_id="c1", campaign_version="1", arena_version="1", corpus_digest="c" * 64,
        generator_digest="g" * 64, oracle_digest=oracle, evaluator_version="eval",
        runtime_snapshot="runtime", case_ids=("a", "b"), holdout_case_ids=("b",),
        external_attestation="attested",
    )
    values.update(overrides)
    return ExternalEvaluationCampaign(**values)


def make_attested_campaign(campaign, receipt=None, *, signature=None, issued_at_epoch=None, expires_at_epoch=None, run_id=None, receipt_digest=None, runtime_digest=None):
    signature = campaign.external_attestation if signature is None else signature
    receipt = make_receipt(campaign) if receipt is None else receipt
    now = time.time()
    attestation = EvaluationAttestation(
        campaign.campaign_digest, campaign.corpus_digest, campaign.oracle_digest,
        campaign.evaluator_version, "external-signer", signature,
        key_id="external-signer-key-v1",
        run_id=receipt.run_id if run_id is None else run_id,
        receipt_digest=receipt.receipt_digest if receipt_digest is None else receipt_digest,
        runtime_digest=receipt.runtime_snapshot if runtime_digest is None else runtime_digest,
        issued_at_epoch=now - 10 if issued_at_epoch is None else issued_at_epoch,
        expires_at_epoch=now + 300 if expires_at_epoch is None else expires_at_epoch,
    )
    verifier = SeparationAwareAttestor(
        lambda value: value.signature == campaign.external_attestation,
        AttestationTrustPolicy(frozenset({"external-signer"}), trusted_key_ids=frozenset({"external-signer-key-v1"})),
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


def make_receipt(campaign, **overrides):
    values = dict(
        run_id="run", arena_version="1", corpus_digest=campaign.corpus_digest,
        manifest_digest="m" * 64, oracle_digest=campaign.oracle_digest,
        runtime_snapshot=campaign.runtime_snapshot, evaluator_version=campaign.evaluator_version,
        case_ids=campaign.case_ids, holdout_case_ids=campaign.holdout_case_ids,
        passed_case_ids=("a", "b"), verified_case_ids=("a", "b"), duration_ms=100,
        campaign_digest=campaign.campaign_digest,
    )
    values.update(overrides)
    return ArenaRunReceipt(**values)


def make_causal(receipt, **overrides):
    values = dict(
        capability_id="planner", intervention_id="i1", baseline_score=0.70, control_score=0.70,
        treatment_score=0.80, holdout_score=0.74, attribution_confidence=0.9,
        fresh_holdout=True, independent_oracle=True, contamination_detected=False,
        evidence_ids=(receipt.receipt_digest, "e2"),
    )
    values.update(overrides)
    return CausalPromotionEvidence(**values)


class PromotionEvidenceChainTests(unittest.TestCase):
    def test_chain_requires_verified_attestation_and_consistent_lineage(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=make_attested_campaign(campaign),
            **isolation_kwargs(campaign, receipt),
        )
        self.assertTrue(chain.complete, chain.reasons)
        self.assertTrue(chain.isolation_evidence_digest)
        self.assertEqual(chain.campaign_digest, campaign.campaign_digest)
        self.assertTrue(chain.attestation_evidence_digest)

    def test_production_builder_rejects_protocol_and_structurally_forged_attestations(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        protocol_attestation = make_attested_campaign(campaign)
        self.assertTrue(protocol_attestation.trustworthy)
        self.assertFalse(protocol_attestation.production_trustworthy)

        production_chain = PromotionEvidenceChainBuilder().build(
            campaign, receipt, causal, decision,
            attested_campaign=protocol_attestation,
            **isolation_kwargs(campaign, receipt),
        )
        self.assertFalse(production_chain.complete)
        self.assertTrue(any("production trust boundary" in reason for reason in production_chain.reasons))

        forged = AttestedEvaluation(
            protocol_attestation.attestation,
            True, True, True, (), "caller-controlled-evidence-digest",
        )
        self.assertFalse(forged.trustworthy)
        test_chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=forged,
            **isolation_kwargs(campaign, receipt),
        )
        self.assertFalse(test_chain.complete)
        self.assertTrue(any("not independently verified" in reason for reason in test_chain.reasons))

    def test_chain_blocks_mismatched_or_untrusted_evidence(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign, corpus_digest="different", contamination_detected=True)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision, attested_campaign=make_attested_campaign(campaign)
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("corpus" in reason for reason in chain.reasons))

    def test_chain_rejects_receipt_without_campaign_binding(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign, campaign_digest="")
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision, attested_campaign=make_attested_campaign(campaign)
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("campaign digests differ" in reason for reason in chain.reasons))

    def test_chain_requires_receipt_digest_in_causal_evidence(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt, evidence_ids=("unrelated-evidence", "e2"))
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision, attested_campaign=make_attested_campaign(campaign)
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("does not reference this run receipt" in reason for reason in chain.reasons))

    def test_decision_must_reference_exact_causal_evidence(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal_a = make_causal(receipt)
        decision_a = CausalCapabilityPromotionGate().evaluate(causal_a)
        causal_b = make_causal(receipt, treatment_score=0.85)
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal_b, decision_a, attested_campaign=make_attested_campaign(campaign)
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("does not reference this causal evidence" in reason for reason in chain.reasons))

    def test_chain_rejects_bad_attestation_even_with_nonempty_signature(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        attestation = make_attested_campaign(campaign, signature="forged")
        self.assertFalse(attestation.trustworthy)
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision, attested_campaign=attestation
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("not independently verified" in reason for reason in chain.reasons))


    def test_chain_rejects_attestation_bound_to_another_receipt_or_runtime(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        attestation = make_attested_campaign(campaign, receipt, receipt_digest="wrong-receipt")
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=attestation, **isolation_kwargs(campaign, receipt),
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("receipt digest does not match" in reason for reason in chain.reasons))

    def test_chain_rejects_expired_or_future_attestation(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        now = time.time()
        expired = make_attested_campaign(
            campaign, receipt, issued_at_epoch=now - 100, expires_at_epoch=now - 1
        )
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision, attested_campaign=expired,
            attestation_now=now, **isolation_kwargs(campaign, receipt),
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("attestation has expired" in reason for reason in chain.reasons))

    def test_chain_blocks_missing_isolation_attestation(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=make_attested_campaign(campaign),
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("isolation attestation is required" in reason for reason in chain.reasons))

    def test_chain_rejects_isolation_attestation_bound_to_another_image(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        kwargs = isolation_kwargs(campaign, receipt)
        kwargs["isolation_attestation"] = replace(
            kwargs["isolation_attestation"], image_digest="sha256:" + "b" * 64
        )
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=make_attested_campaign(campaign), **kwargs
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("image digest mismatch" in reason for reason in chain.reasons))

    def test_chain_rejects_isolation_signer_overlap(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        kwargs = isolation_kwargs(campaign, receipt)
        kwargs["isolation_attestation"] = replace(
            kwargs["isolation_attestation"], signer_id="runtime-evaluator"
        )
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=make_attested_campaign(campaign), **kwargs
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("signer must be independent" in reason for reason in chain.reasons))

    def test_chain_rejects_untrusted_isolation_signer(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        kwargs = isolation_kwargs(campaign, receipt)
        kwargs["isolation_attestation"] = replace(
            kwargs["isolation_attestation"], signer_id="unconfigured-sandbox-auditor"
        )
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=make_attested_campaign(campaign), **kwargs
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("not in the out-of-band trusted signer allowlist" in reason for reason in chain.reasons))

    def test_chain_rejects_missing_trusted_isolation_signer_allowlist(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        kwargs = isolation_kwargs(campaign, receipt)
        kwargs["trusted_isolation_signers"] = None
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=make_attested_campaign(campaign), **kwargs
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("out-of-band trusted isolation signer allowlist is required" in reason for reason in chain.reasons))

    def test_chain_rejects_revoked_isolation_signer(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        kwargs = isolation_kwargs(campaign, receipt)
        kwargs["revoked_isolation_signers"] = frozenset({"independent-sandbox-auditor"})
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=make_attested_campaign(campaign), **kwargs
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("signer is revoked" in reason for reason in chain.reasons))

    def test_chain_rejects_revoked_isolation_signer_key_id(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        kwargs = isolation_kwargs(campaign, receipt)
        kwargs["revoked_isolation_signer_key_ids"] = frozenset({"sandbox-auditor-key-v3"})
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=make_attested_campaign(campaign), **kwargs
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("signer key ID is revoked" in reason for reason in chain.reasons))

    def test_chain_rejects_missing_trusted_isolation_signer_key_policy(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        kwargs = isolation_kwargs(campaign, receipt)
        kwargs["trusted_isolation_signer_key_ids"] = None
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=make_attested_campaign(campaign), **kwargs
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("trusted isolation signer key-ID allowlist is required" in reason for reason in chain.reasons))

    def test_chain_rejects_mismatched_observed_sandbox_identity(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        kwargs = isolation_kwargs(campaign, receipt)
        kwargs["expected_sandbox_identity"] = "different-host"
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=make_attested_campaign(campaign), **kwargs
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("observed sandbox identity mismatch" in reason for reason in chain.reasons))

    def test_chain_rejects_observation_outside_campaign_window(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        kwargs = isolation_kwargs(campaign, receipt)
        kwargs["campaign_started_at_epoch"] = kwargs["isolation_now"] - 5
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=make_attested_campaign(campaign), **kwargs
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("outside the campaign run interval" in reason for reason in chain.reasons))

    def test_chain_rejects_expired_isolation_attestation(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        kwargs = isolation_kwargs(campaign, receipt)
        now = kwargs["isolation_now"]
        kwargs["isolation_attestation"] = replace(
            kwargs["isolation_attestation"], issued_at_epoch=now - 120, expires_at_epoch=now - 1
        )
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=make_attested_campaign(campaign), **kwargs
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("stale or expired" in reason for reason in chain.reasons))

    def test_chain_rejects_missing_isolation_control(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        kwargs = isolation_kwargs(campaign, receipt)
        kwargs["isolation_attestation"] = replace(
            kwargs["isolation_attestation"], controls=_REQUIRED_ISOLATION_CONTROLS[:-1]
        )
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=make_attested_campaign(campaign), **kwargs
        )
        self.assertFalse(chain.complete)
        self.assertTrue(any("missing required controls" in reason for reason in chain.reasons))



    def test_structurally_forged_chain_cannot_become_learning_input(self):
        # Constructing a dataclass with plausible digest strings is not proof
        # that the campaign/receipt/attestation/isolation chain was verified.
        forged = PromotionEvidenceChain(
            campaign_digest="campaign-digest",
            run_receipt_digest="receipt-digest",
            causal_evidence_digest="causal-digest",
            capability_id="planner",
            intervention_id="intervention",
            eligible=True,
            reasons=(),
            attestation_evidence_digest="attestation-digest",
            causal_evidence_ids=("receipt-digest",),
            isolation_evidence_digest="isolation-digest",
        )
        self.assertTrue(forged.complete)
        self.assertFalse(forged.builder_verified)
        ledger = ArenaEvidenceObservationLedger()
        observation = ledger.record(forged)
        self.assertFalse(observation.accepted)
        self.assertEqual(ledger.learning_inputs, ())

    def test_accepted_evidence_is_immutable_and_replay_is_rejected(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=make_attested_campaign(campaign, receipt),
            **isolation_kwargs(campaign, receipt),
        )
        self.assertTrue(chain.complete, chain.reasons)
        ledger = ArenaEvidenceObservationLedger()
        record = ledger.record(chain)
        self.assertTrue(record.accepted)
        self.assertEqual(ledger.learning_inputs, (record,))
        with self.assertRaisesRegex(ValueError, "replayed Arena receipt evidence"):
            ledger.record(chain)
        with self.assertRaisesRegex(ValueError, "observation_digest"):
            replace(record, observation_digest="tampered")

    def test_rejected_evidence_is_preserved_but_never_becomes_learning_input(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=make_attested_campaign(campaign, receipt),
        )
        self.assertFalse(chain.complete)
        ledger = ArenaEvidenceObservationLedger()
        record = ledger.record(chain)
        self.assertFalse(record.accepted)
        self.assertTrue(record.reasons)
        self.assertEqual(ledger.records, (record,))
        self.assertEqual(ledger.learning_inputs, ())

    def test_conflicting_observation_for_same_receipt_is_rejected(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=make_attested_campaign(campaign, receipt),
            **isolation_kwargs(campaign, receipt),
        )
        ledger = ArenaEvidenceObservationLedger()
        ledger.record(chain)
        conflicting = replace(chain, eligible=False, reasons=("conflicting evidence",))
        with self.assertRaisesRegex(ValueError, "conflicting Arena observations"):
            ledger.record(conflicting)


    def test_in_memory_ledger_allows_only_one_concurrent_receipt_claim(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=make_attested_campaign(campaign, receipt),
            **isolation_kwargs(campaign, receipt),
        )
        ledger = ArenaEvidenceObservationLedger()

        def record(_):
            try:
                ledger.record(chain)
                return "accepted"
            except ValueError:
                return "rejected"

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(record, range(8)))
        self.assertEqual(results.count("accepted"), 1)
        self.assertEqual(results.count("rejected"), 7)
        self.assertEqual(len(ledger.records), 1)

    def test_durable_ledger_survives_restart_and_rejects_replay(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=make_attested_campaign(campaign, receipt),
            **isolation_kwargs(campaign, receipt),
        )
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "evidence.sqlite3"
            first = ArenaEvidenceObservationLedger(database)
            record = first.record(chain)
            reopened = ArenaEvidenceObservationLedger(database)
            self.assertEqual(reopened.records, (record,))
            self.assertEqual(reopened.learning_inputs, (record,))
            with self.assertRaisesRegex(ValueError, "replayed Arena receipt evidence"):
                reopened.record(chain)

    def test_durable_ledger_allows_only_one_concurrent_receipt_claim(self):
        campaign = make_campaign()
        receipt = make_receipt(campaign)
        causal = make_causal(receipt)
        decision = CausalCapabilityPromotionGate().evaluate(causal)
        chain = PromotionEvidenceChainBuilder(require_production_attestation=False).build(
            campaign, receipt, causal, decision,
            attested_campaign=make_attested_campaign(campaign, receipt),
            **isolation_kwargs(campaign, receipt),
        )
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "evidence.sqlite3"
            def record(_):
                try:
                    ArenaEvidenceObservationLedger(database).record(chain)
                    return "accepted"
                except ValueError:
                    return "rejected"
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(record, range(8)))
            self.assertEqual(results.count("accepted"), 1)
            self.assertEqual(results.count("rejected"), 7)
            self.assertEqual(len(ArenaEvidenceObservationLedger(database).records), 1)

if __name__ == "__main__":
    unittest.main()
