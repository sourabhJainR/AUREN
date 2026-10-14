from __future__ import annotations

import json
from pathlib import Path

from portable.engineering_design_guard import PRINCIPLE_FAMILY_IDS


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "docs" / "engineering-principle-traceability.json"
DOC_PATH = ROOT / "docs" / "ENGINEERING_PRINCIPLE_TRACEABILITY.md"


def test_traceability_matrix_covers_every_canonical_family_with_existing_owners_and_tests():
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    assert matrix["schema_version"] == "auren-engineering-principle-traceability-v1"
    entries = matrix["families"]
    assert {entry["id"] for entry in entries} == set(PRINCIPLE_FAMILY_IDS)
    assert len(entries) == len(PRINCIPLE_FAMILY_IDS)
    allowed_statuses = {"enforced", "evidence-gated", "operator-controlled", "not_applicable"}
    for entry in entries:
        assert entry["status"] in allowed_statuses
        assert entry["owner_files"], entry["id"]
        assert entry["test_files"], entry["id"]
        assert entry["enforced_controls"], entry["id"]
        assert entry["remaining_gaps"], entry["id"]
        for relative in [*entry["owner_files"], *entry["test_files"]]:
            assert (ROOT / relative).is_file(), (entry["id"], relative)


def test_deployment_decisions_state_consistency_and_partition_tradeoffs():
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    decisions = matrix["deployment_decisions"]
    assert len(decisions) >= 5
    for decision in decisions:
        assert decision["boundary"].strip()
        assert decision["consistency"].strip()
        assert decision["cap_pacelc"].strip()
        assert decision["status"] in {"enforced", "evidence-gated", "operator-controlled", "not_applicable"}

    documentation = DOC_PATH.read_text(encoding="utf-8")
    assert "CAP/PACELC" in documentation
    assert "no transport-level exactly-once" in documentation.lower()
    assert "fencing" in documentation
