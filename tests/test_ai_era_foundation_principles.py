"""Regression tests for AUREN's 20-principle AI-era foundation contract."""
import hashlib
import json
from pathlib import Path
from portable.ai_era_principles import PRINCIPLES, build_contract_receipt, canonical_registry, registry_digest
from portable.orchestration import Graph, Node, NodeKind, Orchestrator, RunStatus

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_IDS = ["IPACT","VERA","PACT","INVAR","TRACE","R3","RAIL","CIG","BLAST","INDEP","OWN","LEARN","DELTA","HANDOFF","DEGRADE","TRUST","PROMOTE","ECONOMY","MEASURE","CONTEXT"]

def test_registry_has_twenty_unique_principles_with_existing_owner_and_test():
    registry = canonical_registry()
    assert registry["schema_version"] == "auren-ai-era-principles-v1"
    assert [p["id"] for p in PRINCIPLES] == EXPECTED_IDS
    assert len({p["id"] for p in PRINCIPLES}) == 20
    for p in registry["principles"]:
        assert p["meaning"] and p["name"] and p["exception_policy"]
        assert (ROOT / p["owner"]).is_file(), p["id"]
        assert (ROOT / p["test"]).is_file(), p["id"]

def test_receipt_is_deterministic_and_binds_intent_paths_risk_revision_and_registry():
    receipt = build_contract_receipt("Implement principles", ["portable/orchestration.py"], "HIGH", "abc123")
    assert receipt == build_contract_receipt("Implement principles", ["portable/orchestration.py"] * 2, "high", "abc123")
    assert receipt["registry_digest"] == registry_digest()
    assert receipt["principles"] == EXPECTED_IDS
    digest = receipt.pop("digest")
    assert digest == hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert build_contract_receipt("other intent", ["portable/orchestration.py"], "high", "abc123")["digest"] != digest
    assert build_contract_receipt("Implement principles", ["portable/other.py"], "high", "abc123")["digest"] != digest
    assert build_contract_receipt("Implement principles", ["portable/orchestration.py"], "critical", "abc123")["digest"] != digest
    assert build_contract_receipt("Implement principles", ["portable/orchestration.py"], "high", "def456")["digest"] != digest

def test_receipt_rejects_missing_intent_and_unknown_risk():
    for args, expected in [((" ", [], "low"), "non-empty intent"), (("task", [], "unbounded"), "risk must be one of")]:
        try:
            build_contract_receipt(*args)
        except ValueError as error:
            assert expected in str(error)
        else:
            raise AssertionError(f"expected ValueError containing {expected!r}")

def test_orchestrator_records_principle_receipt_before_graph_nodes():
    observed = []
    graph = Graph([Node("inspect", NodeKind.DETERMINISTIC,
        lambda state: observed.append(state["ai_era_principles_contract"]["digest"]) or {"ok": True})])
    run = Orchestrator(graph).run("principle-contract-test", "Run a bounded documentation task",
        context={"changed_paths": ["docs/guide.md"], "risk": "low", "code_revision": "test-revision"})
    assert run.status == RunStatus.ACCEPTED
    event = next(e for e in run.evidence if e.kind == "ai-era-principles-contract")
    payload = json.loads(event.summary)
    assert payload["code_revision"] == "test-revision"
    assert payload["risk"] == "low" and len(payload["principles"]) == 20
    assert observed == [payload["digest"]]
    trace = next(e for e in run.trajectory if e.get("event") == "ai_era_principles_contract")
    assert trace["receipt_digest"] == payload["digest"]
    assert trace["registry_digest"] == payload["registry_digest"]

def test_high_risk_guard_still_blocks_before_side_effects_and_keeps_principle_receipt():
    calls = []
    graph = Graph([Node("mutate", NodeKind.DETERMINISTIC, lambda state: calls.append("ran") or {"ok": True}, risk="high")])
    run = Orchestrator(graph).run("blocked-high-risk", "Change authentication for external tool execution",
        context={"changed_paths": ["portable/security/tool_runner.py"], "risk": "high"})
    assert run.status == RunStatus.BLOCKED
    assert calls == []
    assert any(e.kind == "ai-era-principles-contract" for e in run.evidence)
    assert any(e.kind == "engineering-design-review" for e in run.evidence)
