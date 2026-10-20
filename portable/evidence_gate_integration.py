"""Bridge bounded revision-bound checks into AUREN's durable evidence graph.

Receipt/check nodes are content-addressed. A separate per-run node preserves
invocation identity so identical checks across runs do not lose provenance.
This adapter is not a sandbox and does not replace supervisor or CI gates.
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
    run_node_id: str
    receipt_node_id: str
    check_node_ids: tuple[str, ...]


class EvidenceGateIntegration:
    """Persist revision, invocation, receipt and check-level evidence lineage."""

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
        run_id = run_id.strip()
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
            "repository_revision", expected_revision, {"project": self.project}
        )
        run_node = self.graph.add_node(
            "evidence_gate_run",
            _canonical_digest({"run_id": run_id, "receipt_digest": receipt.digest}),
            {
                "project": self.project,
                "run_id": run_id,
                "receipt_digest": receipt.digest,
                "status": receipt.status,
            },
        )
        receipt_json = json.dumps(receipt.as_dict(), sort_keys=True, separators=(",", ":"))
        receipt_node = self.graph.add_node(
            "evidence_gate_receipt",
            receipt.digest,
            {
                "project": self.project,
                "status": receipt.status,
                "expected_revision": receipt.expected_revision,
                "observed_revision": receipt.observed_revision or "",
                "schema_version": receipt.schema_version,
                "receipt_json": receipt_json,
            },
        )
        self.graph.add_edge(revision_node, "expected_revision_for", run_node)
        self.graph.add_edge(run_node, "records_receipt", receipt_node)

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
            result_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            node = self.graph.add_node(
                "evidence_gate_check",
                _canonical_digest(payload),
                {
                    "project": self.project,
                    "receipt_digest": receipt.digest,
                    "name": check.name,
                    "status": check.status,
                    "returncode": "" if check.returncode is None else str(check.returncode),
                    "duration_seconds": str(check.duration_seconds),
                    "result_json": result_json,
                },
            )
            self.graph.add_edge(receipt_node, "contains_check", node)
            check_node_ids.append(node.node_id)

        # Fail closed if any canonical evidence was not durably readable.
        persisted_revision = self.graph.get_node(revision_node.node_id)
        persisted_run = self.graph.get_node(run_node.node_id)
        persisted_receipt = self.graph.get_node(receipt_node.node_id)
        if persisted_revision is None or persisted_run is None or persisted_receipt is None:
            raise RuntimeError("evidence lineage was not durably persisted")
        if dict(persisted_run.metadata).get("run_id") != run_id:
            raise RuntimeError("persisted evidence run identity does not match")
        if dict(persisted_receipt.metadata).get("receipt_json") != receipt_json:
            raise RuntimeError("persisted evidence receipt failed read-back verification")
        for node_id in check_node_ids:
            persisted_check = self.graph.get_node(node_id)
            if persisted_check is None or "result_json" not in dict(persisted_check.metadata):
                raise RuntimeError("persisted evidence check details are missing")

        return IntegratedEvidenceReceipt(
            receipt,
            revision_node.node_id,
            run_node.node_id,
            receipt_node.node_id,
            tuple(check_node_ids),
        )


__all__ = ["IntegratedEvidenceReceipt", "EvidenceGateIntegration"]
