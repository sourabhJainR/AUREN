from portable.arena_run_receipt import ArenaRunReceipt, oracle_registry_digest
from portable.causal_capability_promotion import CausalCapabilityPromotionGate, CausalPromotionEvidence
from portable.external_evaluation_campaign import ExternalEvaluationCampaign
from portable.promotion_evidence_chain import PromotionEvidenceChainBuilder


def test_chain_requires_consistent_external_lineage():
    oracle = oracle_registry_digest(("oracle-1",))
    campaign = ExternalEvaluationCampaign(
        "c1","1","1","c"*64,"g"*64,oracle,"eval","runtime",
        ("a","b"),("b",),external_attestation="attested",
    )
    receipt = ArenaRunReceipt(
        "run","1","c"*64,"m"*64,oracle,"runtime","eval",
        ("a","b"),("b",),("a","b"),("a","b"),100,
    )
    causal = CausalPromotionEvidence(
        "planner","i1",.70,.70,.80,.74,.9,True,True,False,("e1","e2"),
    )
    decision = CausalCapabilityPromotionGate().evaluate(causal)
    chain = PromotionEvidenceChainBuilder().build(campaign,receipt,causal,decision)
    assert chain.complete
    assert chain.campaign_digest == campaign.campaign_digest


def test_chain_blocks_mismatched_or_untrusted_evidence():
    oracle = oracle_registry_digest(("oracle-1",))
    campaign = ExternalEvaluationCampaign(
        "c1","1","1","c"*64,"g"*64,oracle,"eval","runtime",
        ("a","b"),("b",),external_attestation="attested",
    )
    receipt = ArenaRunReceipt(
        "run","1","different","m"*64,oracle,"runtime","eval",
        ("a","b"),("b",),("a",),("a",),100,contamination_detected=True,
    )
    causal = CausalPromotionEvidence(
        "planner","i1",.70,.70,.80,.74,.9,True,True,False,("e1","e2"),
    )
    decision = CausalCapabilityPromotionGate().evaluate(causal)
    chain = PromotionEvidenceChainBuilder().build(campaign,receipt,causal,decision)
    assert not chain.complete
    assert any("corpus" in reason for reason in chain.reasons)
