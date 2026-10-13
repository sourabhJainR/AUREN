"""Trusted live repository context provider for consequential execution gates.

Repository identity and candidate SHA are read from Git, never task payloads or
self-reported decision records. Decision records and approver policy are loaded
from operator-controlled paths; evidence IDs are resolved against the canonical
ledger for that exact repository/head.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from .canonical_evidence_ledger import CanonicalEvidenceLedger
from .engineering_evidence_capture import EvidenceCapture


_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class TrustedRepositoryContextProvider:
    """Callable provider registered as the execution controller's context handler.

    The configured repository slug, decision-record directory, approver-policy
    file, and evidence ledger are operator-owned. It refuses dirty worktrees,
    ambiguous remotes, malformed policies, and records for stale heads.
    """

    def __init__(
        self,
        repository_root: str | Path,
        *,
        expected_repository: str,
        decision_record_dir: str | Path,
        authorized_approvers_file: str | Path,
        evidence_ledger: CanonicalEvidenceLedger,
        evidence_capture: EvidenceCapture,
        receipt_verifier: Any,
        require_clean_worktree: bool = True,
    ) -> None:
        self.root = Path(repository_root).expanduser().resolve()
        self.expected_repository = expected_repository.strip()
        if not _REPOSITORY_RE.fullmatch(self.expected_repository):
            raise ValueError("expected_repository must be an owner/repository slug")
        self.decision_record_dir = Path(decision_record_dir).expanduser().resolve()
        self.authorized_approvers_file = Path(authorized_approvers_file).expanduser().resolve()
        self.evidence_ledger = evidence_ledger
        self.evidence_capture = evidence_capture
        self.receipt_verifier = receipt_verifier
        if self.evidence_capture.evidence_ledger is not evidence_ledger:
            raise ValueError("evidence capture and trusted context must share the same canonical ledger instance")
        if self.receipt_verifier is None:
            raise ValueError("trusted receipt verifier is required")
        self.require_clean_worktree = bool(require_clean_worktree)

    def _git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return completed.stdout.strip()

    @staticmethod
    def _remote_slug(remote: str) -> str:
        remote = remote.strip()
        if remote.startswith("git@") and ":" in remote:
            slug = remote.split(":", 1)[1]
        elif remote.startswith("ssh://"):
            path = remote.split("/", 3)[-1]
            slug = path
        elif remote.startswith("https://") or remote.startswith("http://"):
            from urllib.parse import urlparse
            slug = urlparse(remote).path.lstrip("/")
        else:
            raise ValueError("origin remote uses an unsupported or ambiguous URL form")
        slug = slug.removesuffix(".git").strip("/")
        if not _REPOSITORY_RE.fullmatch(slug):
            raise ValueError("origin remote does not resolve to an owner/repository slug")
        return slug

    def __call__(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise ValueError("trusted context request must be a mapping")
        task_id = request.get("task_id")
        if not isinstance(task_id, str) or not _TASK_ID_RE.fullmatch(task_id):
            raise ValueError("trusted context requires a safe task identifier")
        # Caller-provided repository/head fields are intentionally ignored.
        remote = self._git("remote", "get-url", "origin")
        actual_repository = self._remote_slug(remote)
        if actual_repository.casefold() != self.expected_repository.casefold():
            raise ValueError("live origin repository does not match operator policy")
        head = self._git("rev-parse", "--verify", "HEAD^{commit}")
        if not _COMMIT_RE.fullmatch(head):
            raise ValueError("live HEAD is not a full commit SHA")
        if self.require_clean_worktree:
            status = self._git("status", "--porcelain", "--untracked-files=all")
            if status:
                raise ValueError("promotion blocked because the live worktree is dirty")

        record_path = (self.decision_record_dir / f"{task_id}.json").resolve()
        if record_path.parent != self.decision_record_dir:
            raise ValueError("decision record path escaped operator-controlled directory")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("task_id") != task_id:
            raise ValueError("operator decision record is missing or belongs to another task")

        approvers_data = json.loads(self.authorized_approvers_file.read_text(encoding="utf-8"))
        if not isinstance(approvers_data, list) or not approvers_data or any(
            not isinstance(item, str) or not item.strip() for item in approvers_data
        ):
            raise ValueError("operator approver policy must be a non-empty string list")
        known_evidence_ids = self.evidence_ledger.known_evidence_ids(
            repository=self.expected_repository, head_sha=head
        )
        def require_evidence(evidence_id: Any, label: str, allowed_types: set[str]) -> None:
            if not isinstance(evidence_id, str) or not evidence_id.strip():
                raise ValueError(f"{label} evidence ID is missing")
            item = self.evidence_ledger.resolve(
                evidence_id, repository=self.expected_repository, head_sha=head
            )
            if not item or item["status"] not in {"passed", "verified"}:
                raise ValueError(f"{label} evidence is missing, stale, or not passing")
            if item["evidence_type"] not in allowed_types:
                raise ValueError(f"{label} evidence has an incompatible evidence type")
            retained = self.evidence_capture.signed_receipts(evidence_id=evidence_id)
            verified_receipt = False
            for stored in retained:
                envelope = stored["envelope"]
                if not isinstance(envelope, Mapping) or set(envelope) != {"payload", "signer_id", "signature"}:
                    continue
                payload = envelope.get("payload")
                if not isinstance(payload, Mapping):
                    continue
                signer_id = envelope.get("signer_id")
                if not isinstance(signer_id, str) or signer_id != item["signer_id"]:
                    continue
                if (
                    payload.get("evidence_id") != evidence_id
                    or payload.get("repository") != self.expected_repository
                    or payload.get("head_sha") != head
                    or payload.get("evidence_type") != item["evidence_type"]
                    or payload.get("status") != item["status"]
                    or payload.get("payload_digest") != item["payload_digest"]
                    or signer_id in {payload.get("evaluator_id"), payload.get("oracle_id"), payload.get("runtime_id")}
                ):
                    continue
                receipt_bytes = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                if hashlib.sha256(receipt_bytes).hexdigest() != stored["receipt_digest"]:
                    continue
                try:
                    issued = datetime.fromisoformat(str(payload.get("issued_at", "")).replace("Z", "+00:00"))
                    if issued.tzinfo is None or issued.utcoffset() is None:
                        continue
                    age = (datetime.now(timezone.utc) - issued.astimezone(timezone.utc)).total_seconds()
                    max_age = 90 * 24 * 3600 if item["evidence_type"] == "rollback_rehearsal" else 7 * 24 * 3600
                    if age > max_age or age < -300:
                        continue
                    message = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                    if hasattr(self.receipt_verifier, "verify_detached"):
                        valid_signature = self.receipt_verifier.verify_detached(signer_id, envelope["signature"], message)
                    else:
                        valid_signature = self.receipt_verifier(signer_id, envelope["signature"], message)
                    if valid_signature is True:
                        verified_receipt = True
                        break
                except Exception:
                    continue
            if not verified_receipt:
                raise ValueError(f"{label} evidence lacks a fresh trusted signed receipt")

        checks = record.get("checks")
        if not isinstance(checks, list) or not checks:
            raise ValueError("operator decision record must contain checks")
        for index, check in enumerate(checks):
            if not isinstance(check, Mapping):
                raise ValueError(f"decision record check {index} is malformed")
            if check.get("status") == "passed":
                require_evidence(
                    check.get("evidence_id"), f"check[{index}]",
                    {"ci_check", "ci_run", "regression_check", "test_result",
                     "independent_evaluator_receipt", "external_evaluator_receipt", "arena_receipt"},
                )
        promotion = record.get("promotion")
        if isinstance(promotion, Mapping) and promotion.get("approval_evidence_id"):
            require_evidence(
                promotion["approval_evidence_id"], "promotion approval",
                {"approval", "promotion_approval", "operator_approval"},
            )
        if record.get("rollback_test_evidence_id"):
            require_evidence(
                record["rollback_test_evidence_id"], "rollback rehearsal",
                {"rollback_rehearsal", "rollback_test"},
            )
        return {
            "decision_record": record,
            "expected_repository": self.expected_repository,
            "expected_head": head,
            "authorized_approvers": tuple(approvers_data),
            "known_evidence_ids": known_evidence_ids,
        }


def trusted_repository_context(request: Mapping[str, Any]) -> dict[str, Any]:
    """Importable, environment-configured adapter for ExecutionController.

    Set deployment paths and repository identity in service configuration. This
    module-level function is durable across controller restarts; task payload
    values are never used to select the repository, head, decision file, or policy.
    """
    root = os.environ.get("AUREN_REPOSITORY_ROOT", "").strip()
    repository = os.environ.get("AUREN_REPOSITORY_SLUG", "").strip()
    record_dir = os.environ.get("AUREN_DECISION_RECORD_DIR", "").strip()
    approvers_file = os.environ.get("AUREN_AUTHORIZED_APPROVERS_FILE", "").strip()
    ledger_path = os.environ.get("AUREN_CANONICAL_EVIDENCE_DB", "").strip()
    if not all((root, repository, record_dir, approvers_file)):
        raise ValueError(
            "trusted context requires AUREN_REPOSITORY_ROOT, AUREN_REPOSITORY_SLUG, "
            "AUREN_DECISION_RECORD_DIR, and AUREN_AUTHORIZED_APPROVERS_FILE"
        )
    ledger = CanonicalEvidenceLedger(
        ledger_path or str(Path(root).expanduser().resolve() / ".auren" / "canonical-evidence.sqlite3")
    )
    capture_path = os.environ.get(
        "AUREN_ENGINEERING_EVIDENCE_DB",
        str(Path(root).expanduser().resolve() / ".auren" / "engineering-evidence.sqlite3"),
    )
    capture = EvidenceCapture(capture_path, evidence_ledger=ledger)
    public_keys_path = os.environ.get("AUREN_TRUSTED_EVIDENCE_PUBLIC_KEYS_FILE", "").strip()
    if not public_keys_path:
        raise ValueError("AUREN_TRUSTED_EVIDENCE_PUBLIC_KEYS_FILE is required for trusted promotion context")
    encoded_keys = json.loads(Path(public_keys_path).read_text(encoding="utf-8"))
    if not isinstance(encoded_keys, dict) or not encoded_keys:
        raise ValueError("trusted evidence public-key file must be a non-empty JSON object")
    public_keys = {}
    for signer_id, encoded in encoded_keys.items():
        if not isinstance(signer_id, str) or not isinstance(encoded, str):
            raise ValueError("trusted evidence public keys must map signer IDs to base64url strings")
        raw = base64.urlsafe_b64decode(encoded + "=" * ((4 - len(encoded) % 4) % 4))
        if len(raw) != 32:
            raise ValueError(f"trusted Ed25519 public key for {signer_id} must be 32 bytes")
        public_keys[signer_id] = raw
    revoked_path = os.environ.get("AUREN_REVOKED_EVIDENCE_SIGNERS_FILE", "").strip()
    revoked = json.loads(Path(revoked_path).read_text(encoding="utf-8")) if revoked_path else []
    from .sealed_arena_signature import Ed25519ReceiptVerifier
    verifier = Ed25519ReceiptVerifier(public_keys, revoked_signers=revoked)
    provider = TrustedRepositoryContextProvider(
        root,
        expected_repository=repository,
        decision_record_dir=record_dir,
        authorized_approvers_file=approvers_file,
        evidence_ledger=ledger,
        evidence_capture=capture,
        receipt_verifier=verifier,
    )
    return provider(request)


__all__ = ["TrustedRepositoryContextProvider", "trusted_repository_context"]
