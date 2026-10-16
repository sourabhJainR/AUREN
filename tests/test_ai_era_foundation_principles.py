"""Regression tests for AUREN's 20-principle AI-era foundation contract."""
import hashlib
import json
from pathlib import Path

import pytest

from portable.ai_era_principles import (
    ALLOWED_RISKS,
    PRINCIPLES,
    build_contract_receipt,
    canonical_registry,
    registry_digest,
)
from portable.orchestration import Graph, Node, NodeKind, Orchestrator, RunStatus

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_IDS = [
    "IPACT", "VERA", "PACT", "INVAR", "TRACE", "R3", "RAIL", "CIG", "BLAST", "INDEP",
    "OWN", "LEARN", "DELTA", "HANDOFF", "DEGRADE", "TRUST", "PROMOTE", "ECONOMY",
    "MEASURE", "CONTEXT",
]


def test_registry_has_all_twenty_unique_principles_and_traceable_controls():
    registry = canonical_registry()
    assert registry["schema_version"] == "auren-ai-era-principles-v1"
    assert [item["id"] for item in PRINCIPLES] == EXPECTED_IDS
    assert len({item["id"] for item in PRINCIPLES}) == 20
    for item in PRINCIPLES:
        assert item["name"].strip()
        assert item["meaning"].strip()
        assert item["control"].strip()
        assert item["family"].strip()
        assert item["owners"] and item["tests"]
        assert all((ROOT / path).is_file() for path in item["owners"]), item["id"]
        assert all((ROOT / path).is_file() for path in item["tests"]), item["id"]


def test_receipt_digest_binds_intent_paths_risk_revision_and_registry():
    base = build_contract_receipt("Implement AI-era principles", ["portable/orchestration.py"], "high", "abc123")
    reordered = build_contract_receipt("Implement AI-era principles", ["portable/orchestration.py", "portable/orchestration.py"], " HIGH ", "abc123")
    assert base == reordered
    assert base["registry_digest"] == registry_digest()
    assert len(base["principles"]) == 20
    for key in ("intent_digest", "changed_paths", "risk", "code_revision", "registry_digest"):
        changed = {
            "intent_digest": ("different intent", ["portable/orchestration.py"], "high", "abc123"),
            "changed_paths": ("Implement AI-era principles", ["portable/other.py"], "high", "abc123"),
            "risk": ("Implement AI-era principles", ["portable/orchestration.py"], "critical", "abc123"),
            "code_revision": ("Implement AI-era principles", ["portable/orchestration.py"], "high", "def456"),
            "registry_digest": ("Implement AI-era principles", ["portable/orchestration.py"], "high", "abc123"),
        }[key]
        candidate = build_contract_receipt(*changed)
        if key == "registry_digest":
            # The registry digest is deterministic and changes only with the registry itself.
            assert candidate[key] == base[key]
        else:
            assert candidate["digest"] != base["digest"]
    digest = base.pop("digest")
    assert digest == hashlib.sha256(json.dumps(base, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_receipt_rejects_missing_intent_and_unknown_risk():
    with pytest.raises(ValueError, match="non-empty intent"):
        build_contract_receipt(" ", [], "low")
    with pytest.raises(ValueError, match="risk must be one of"):
        build_contract_receipt("task", [], "unbounded")


def test_orchestrator_records_principle_receipt_before_graph_side_effects():
    observed = []
    graph = Graph([
        Node("inspect", NodeKind.DETERMINISTIC, lambda state: observed.append(state["ai_era_principles_contract"]["digest"]) or {"ok": True})
    ])
    run = Orchestrator(graph).run(
        "principle-contract-test",
        "Run a bounded and verified documentation task",
        context={"changed_paths": ["docs/guide.md"], "risk": "low", "code_revision": "test-revision"},
    )
    assert run.status == RunStatus.ACCEPTED
    receipt = next(e for e in run.evidence if e.kind == "ai-era-principles-contract")
    payload = json.loads(receipt.summary)
    assert payload["code_revision"] == "test-revision"
    assert payload["risk"] == "low"
    assert len(payload["principles"]) == 20
    assert observed == [payload["digest"]]
    event = next(e for e in run.trajectory if e.get("event") == "ai_era_principles_contract")
    assert event["receipt_digest"] == payload["digest"]
    assert event["registry_digest"] == payload["registry_digest"]


def test_high_risk_guard_still_blocks_before_graph_side_effects_and_keeps_principle_evidence():
    calls = []
    graph = Graph([
        Node("mutate", NodeKind.DETERMINISTIC, lambda state: calls.append("ran") or {"ok": True}, risk="high")
    ])
    run = Orchestrator(graph).run(
        "blocked-high-risk",
        "Change authentication for external tool execution",
        context={"changed_paths": ["portable/security/tool_runner.py"], "risk": "high"},
    )
    assert run.status == RunStatus.BLOCKED
    assert calls == []
    assert any(e.kind == "ai-era-principles-contract" for e in run.evidence)
    assert any(e.kind == "engineering-design-review" for e in run.evidence)
