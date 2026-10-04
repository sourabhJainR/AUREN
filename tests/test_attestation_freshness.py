from portable.attestation_freshness import (
    AttestationFreshnessPolicy,
    AttestationReplayRegistry,
    FreshEvaluationAttestation,
    FreshnessAwareAttestor,
)
from portable.attestation_key_separation import AttestationTrustPolicy
from portable.independent_evaluation_attestation import EvaluationAttestation


def make_attestation(issued_at=100):
    return FreshEvaluationAttestation(
        EvaluationAttestation("campaign", "corpus", "oracle", "v1", "signer", "sig"),
        issued_at,
    )


def make_verifier():
    return FreshnessAwareAttestor(
        lambda _: True,
        AttestationTrustPolicy(frozenset({"signer"})),
        AttestationFreshnessPolicy(max_age_seconds=10, max_future_skew_seconds=2),
        AttestationReplayRegistry(),
    )


def test_fresh_attestation_is_trustworthy_once():
    verifier = make_verifier()
    result = verifier.verify(
        make_attestation(),
        evaluator_id="evaluator",
        oracle_id="oracle-service",
        expected_campaign_digest="campaign",
        expected_corpus_digest="corpus",
        expected_oracle_digest="oracle",
        now=105,
    )
    assert result.trustworthy


def test_replay_is_rejected():
    verifier = make_verifier()
    attestation = make_attestation()
    kwargs = dict(
        evaluator_id="evaluator",
        oracle_id="oracle-service",
        expected_campaign_digest="campaign",
        expected_corpus_digest="corpus",
        expected_oracle_digest="oracle",
        now=105,
    )
    assert verifier.verify(attestation, **kwargs).trustworthy
    replay = verifier.verify(attestation, **kwargs)
    assert not replay.trustworthy
    assert "already been consumed" in replay.reasons[-1]


def test_expired_attestation_is_rejected():
    result = make_verifier().verify(
        make_attestation(issued_at=100),
        evaluator_id="evaluator",
        oracle_id="oracle-service",
        expected_campaign_digest="campaign",
        expected_corpus_digest="corpus",
        expected_oracle_digest="oracle",
        now=111,
    )
    assert not result.trustworthy
    assert "expired" in result.reasons


def test_future_skew_is_rejected():
    result = make_verifier().verify(
        make_attestation(issued_at=103),
        evaluator_id="evaluator",
        oracle_id="oracle-service",
        expected_campaign_digest="campaign",
        expected_corpus_digest="corpus",
        expected_oracle_digest="oracle",
        now=100,
    )
    assert not result.trustworthy
    assert "future" in result.reasons[0]


def test_replay_registry_can_be_preseeded():
    attestation = make_attestation()
    registry = AttestationReplayRegistry({attestation.attestation_id})
    verifier = FreshnessAwareAttestor(
        lambda _: True,
        AttestationTrustPolicy(frozenset({"signer"})),
        AttestationFreshnessPolicy(),
        registry,
    )
    result = verifier.verify(
        attestation,
        evaluator_id="evaluator",
        oracle_id="oracle-service",
        expected_campaign_digest="campaign",
        expected_corpus_digest="corpus",
        expected_oracle_digest="oracle",
        now=100,
    )
    assert not result.trustworthy
