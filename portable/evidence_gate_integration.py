"""Bridge bounded revision-bound checks into AUREN's durable evidence graph.

This adapter is deliberately explicit: it never executes untrusted command text,
and it does not replace supervisor authorization, sandboxing, review, or CI gates.
A receipt is returned only after its receipt/check nodes and lineage edges persist.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .evidence_gate import EvidenceCheck, EvidenceReceipt, run_evidence_gate
from .persistent_evidence_graph import PersistentEvidenceGraph
from .persistent_memory import PersistentMemory


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class IntegratedEvidenceReceipt:
    receipt: EvidenceReceipt
    revision_node_id: str
    receipt_node_id: str
    check_node_ids: tuple[str, ...]


class EvidenceGateIntegration:
    """Persist gate receipts and check-level lineage in the canonical evidence graph."""

    def __init__(self, memory: PersistentMemory, project: str) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be PersistentMemory")
        if not isinstance(project, str) or not project.strip():
            raise ValueError("project is required")
        self.project = project.strip()
        self.graph = PersistentEvidenceGraph(memory, self.project)

    def run_and_record(
        self,
        project_root: str | Path,
        *,
        run_id: str,
        expected_revision: str,
        checks: Sequence[EvidenceCheck],
        total_timeout_seconds: float = 600.0,
        output_limit_chars: int = 4000,
        command_runner: Callable[..., object] | None = None,
        revision_reader: Callable[[Path], str] | None = None,
    ) -> IntegratedEvidenceReceipt:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id is required")
        kwargs: dict[str, object] = {
            "expected_revision": expected_revision,
            "checks": checks,
            "total_timeout_seconds": total_timeout_seconds,
            "output_limit_chars": output_limit_chars,
        }
        if command_runner is not None:
            kwargs["command_runner"] = command_runner
        if revision_reader is not None:
            kwargs["revision_reader"] = revision_reader

        receipt = run_evidence_gate(project_root, **kwargs)  # type: ignore[arg-type]
        revision_node = self.graph.add_node(
            "repository_revision",
            expected_revision,
            {"project": self.project, "run_id": run_id.strip()},
        )
        receipt_node = self.graph.add_node(
            "evidence_gate_receipt",
            receipt.digest,
            {
                "project": self.project,
                "run_id": run_id.strip(),
                "status": receipt.status,
                "expected_revision": receipt.expected_revision,
                "observed_revision": receipt.observed_revision or "",
                "schema_version": receipt.schema_version,
                "receipt_json": json.dumps(
                    receipt.as_dict(), sort_keys=True, separators=(",", ":")
                ),
            },
        )
        self.graph.add_edge(revision_node, "verified_by", receipt_node)

        check_node_ids: list[str] = []
        for check in receipt.checks:
            payload = {
                "receipt_digest": receipt.digest,
                "name": check.name,
                "status": check.status,
                "returncode": check.returncode,
                "duration_seconds": check.duration_seconds,
                "stdout": check.stdout,
                "stderr": check.stderr,
            }
            node = self.graph.add_node(
                "evidence_gate_check",
                _canonical_digest(payload),
                {
                    "project": self.project,
                    "run_id": run_id.strip(),
                    "receipt_digest": receipt.digest,
                    "name": check.name,
                    "status": check.status,
                    "returncode": "" if check.returncode is None else str(check.returncode),
                    "duration_seconds": str(check.duration_seconds),
                    "result_json": json.dumps(payload, sort_keys=True, separators=(",", ":")),
                },
            )
            self.graph.add_edge(receipt_node, "contains_check", node)
            check_node_ids.append(node.node_id)

        # Verify that persistence is readable before handing the receipt to a caller.
        persisted_receipt = self.graph.get_node(receipt_node.node_id)
        if persisted_receipt is None:
            raise RuntimeError("evidence receipt was not durably persisted")
        receipt_metadata = dict(persisted_receipt.metadata)
        if receipt_metadata.get("receipt_json") != json.dumps(
            receipt.as_dict(), sort_keys=True, separators=(",", ":")
        ):
            raise RuntimeError("persisted evidence receipt failed read-back verification")
        for node_id in check_node_ids:
            persisted_check = self.graph.get_node(node_id)
            if persisted_check is None:
                raise RuntimeError("evidence check was not durably persisted")
            if "result_json" not in dict(persisted_check.metadata):
                raise RuntimeError("persisted evidence check details are missing")

        return IntegratedEvidenceReceipt(
            receipt,
            revision_node.node_id,
            receipt_node.node_id,
            tuple(check_node_ids),
        )


__all__ = ["IntegratedEvidenceReceipt", "EvidenceGateIntegration"]
