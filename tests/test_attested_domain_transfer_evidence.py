from portable.attested_domain_transfer_evidence import AttestedDomainTransferEvidenceBuilder
from portable.attestation_freshness import FreshEvaluationAttestation
from portable.independent_evaluation_attestation import EvaluationAttestation
from portable.sealed_arena_boundary import SealedCaseEnvelope, SealedCampaignRequest
from portable.sealed_cross_domain_arena import CrossDomainCampaign, SealedDomain, TargetDomainProvenance


def campaign():
    req = SealedCampaignRequest(
        "campaign", "corpus",
        tuple(SealedCaseEnvelope(f"c{i}", f"t{i}", f"i{i}", f"e{i}", True) for i in range(1, 7)),
        run_nonce="transfer-evidence-test-nonce",
    )
    trusted = attestations()
    def provenance(domain_id, attestation_index, overlap_index):
        return TargetDomainProvenance(
            domain_id=domain_id,
            origin="https://benchmarks.example.org/transfer/" + domain_id,
            manifest_digest=req.manifest_digest,
            split="sealed_holdout",
            development_corpus_digest="development-corpus-v0",
            development_overlap_status="no_overlap",
            overlap_attestation_digest=trusted[overlap_index].evidence_digest,
            independent_operator_id="independent-operator",
            attestation_digest=trusted[attestation_index].evidence_digest,
        )
    return CrossDomainCampaign(
        "x", req,
        (
            SealedDomain("domain-a", ("c1","c2","c3"), provenance=provenance("domain-a", 6, 7)),
            SealedDomain("domain-b", ("c4","c5","c6"), provenance=provenance("domain-b", 8, 9)),
        ),
        "oracle", "evaluator",
    )

def attestations():
    out=[]
    for i in range(1,11):
        base=EvaluationAttestation("campaign","corpus","oracle","v1","signer",f"sig{i}")
        fresh=FreshEvaluationAttestation(base,100)
        out.append(type("A",(),{"trustworthy":True,"evidence_digest":fresh.attestation_id})())
    return out


def test_builds_transfer_evidence_from_attested_cases():
    result = AttestedDomainTransferEvidenceBuilder().build(
        campaign(),
        results={f"c{i}": (True, False, attestations()[i-1].evidence_digest) for i in range(1,7)},
        attestations=attestations(),
        source_project="source",
        target_project="target",
        capability="reasoning",
        min_samples=5,
    )
    assert result.assessment.trustworthy
    assert result.assessment.samples == 6


def test_missing_attestation_fails_closed():
    try:
        AttestedDomainTransferEvidenceBuilder().build(
            campaign(),
            results={"c1": (True, False, "missing")},
            attestations=attestations(),
            source_project="source",
            target_project="target",
            capability="reasoning",
            min_samples=1,
        )
    except ValueError as exc:
        assert "attestation" in str(exc)
    else:
        raise AssertionError("expected missing attestation failure")
