from portable.external_transfer_evidence_coordinator import ExternalTransferEvidenceCoordinator
from portable.attestation_freshness import AttestationFreshnessPolicy, FreshEvaluationAttestation
from portable.attestation_key_separation import AttestationTrustPolicy
from portable.independent_evaluation_attestation import EvaluationAttestation


def test_coordinator_rejects_nonfresh_attestation_before_transfer():
    class Gateway:
        def evaluate(self, request, command):
            return object()
    coordinator = ExternalTransferEvidenceCoordinator(
        Gateway(),
        verify_signature=lambda _: True,
        attestation_policy=AttestationTrustPolicy(frozenset({"signer"})),
        freshness_policy=AttestationFreshnessPolicy(max_age_seconds=1),
    )
    att = FreshEvaluationAttestation(
        EvaluationAttestation("campaign","corpus","oracle","v1","signer","sig"),
        1,
    )
    try:
        coordinator.execute_and_attribute(
            type("C", (), {
                "request": type("R", (), {"campaign_digest":"campaign","corpus_digest":"corpus"})(),
                "evaluator_id":"evaluator","independent_oracle_id":"oracle",
                "campaign_digest":"x",
            })(),
            object(),
            attestation=att,
            now=10,
            results={},
            source_project="a", target_project="b", capability="x",
            min_samples=1,
        )
    except ValueError as exc:
        assert "not trustworthy" in str(exc)
    else:
        raise AssertionError("expected freshness rejection")
