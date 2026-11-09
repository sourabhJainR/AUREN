"""Evidence-gated execution validation for learning-transfer candidates."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
import json
import uuid

from .learning_transfer import LearningTransfer, TransferCandidate


@dataclass(frozen=True)
class TransferValidation:
    success: bool
    evidence_ids: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class TransferValidationReceipt:
    validation_id: str
    candidate_detail: str
    source_projects: tuple[str, ...]
    success: bool
    negative_transfer: bool
    evidence_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    created_at: str


class TransferValidator:
    """Execute a bounded target-project validation and record its outcome."""

    def __init__(self, transfer: LearningTransfer, *, max_validations: int = 100_000) -> None:
        if not isinstance(transfer, LearningTransfer):
            raise TypeError("transfer must be a LearningTransfer instance")
        if max_validations < 1:
            raise ValueError("max_validations must be positive")
        self.transfer = transfer
        self.memory = transfer.memory
        self.project = transfer.target_project
        self.max_validations = max_validations
        with self.memory._lock, self.memory._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS learning_transfer_validations(
                project TEXT NOT NULL, validation_id TEXT NOT NULL, detail TEXT NOT NULL,
                source_projects TEXT NOT NULL, similarity REAL NOT NULL, success INTEGER NOT NULL,
                negative_transfer INTEGER NOT NULL, evidence_ids TEXT NOT NULL,
                detail_text TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(project, validation_id))""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_transfer_validation_lookup ON learning_transfer_validations(project, detail, success, negative_transfer)")

    def validate(self, candidate: TransferCandidate, runner: Callable[[], TransferValidation], *, min_evidence: int = 1) -> TransferValidationReceipt:
        if not isinstance(candidate, TransferCandidate):
            raise TypeError("candidate must be a TransferCandidate")
        if not callable(runner):
            raise TypeError("runner must be callable")
        if min_evidence < 1:
            raise ValueError("min_evidence must be positive")
        validation_id = uuid.uuid4().hex
        evidence: tuple[str, ...] = ()
        detail = ""
        success = False
        reasons: list[str] = []
        try:
            outcome = runner()
            if not isinstance(outcome, TransferValidation):
                raise TypeError("runner must return TransferValidation")
            evidence = tuple(sorted(set(item.strip() for item in outcome.evidence_ids if isinstance(item, str) and item.strip())))
            detail = str(outcome.detail)[:512]
            success = bool(outcome.success)
            if success and len(evidence) < min_evidence:
                success = False
                reasons.append("successful transfer validation requires evidence")
        except Exception as exc:
            reasons.append(f"{type(exc).__name__}: {exc}"[:512])
        negative = not success
        now = datetime.now(timezone.utc).isoformat()
        with self.memory._lock, self.memory._connection() as db:
            count = db.execute("SELECT COUNT(*) FROM learning_transfer_validations WHERE project=?", (self.project,)).fetchone()[0]
            if count >= self.max_validations:
                raise ValueError("transfer validation budget exceeded")
            db.execute(
                "INSERT INTO learning_transfer_validations VALUES(?,?,?,?,?,?,?,?,?,?)",
                (self.project, validation_id, candidate.detail, json.dumps(candidate.source_projects), candidate.similarity,
                 int(success), int(negative), json.dumps(evidence), detail, now),
            )
        return TransferValidationReceipt(validation_id, candidate.detail, candidate.source_projects, success, negative, evidence, tuple(reasons), now)


__all__ = ["TransferValidation", "TransferValidationReceipt", "TransferValidator"]
