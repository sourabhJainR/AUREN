"""Supervisor-invoked CI repair/watchdog helpers; no independent polling loop.

The existing ExecutionController remains the only task queue and worker owner.
The existing EngineeringRecoveryLedger owns finite attempts and evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from typing import Any, Mapping

from .engineering_recovery import EngineeringRecoveryLedger, RecoveryAction, RecoveryLimitReached


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _safe_ref(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 500 or not re.match(r"^https?://", value):
        raise ValueError("evidence references must be bounded HTTP(S) URLs")
    if re.search(r"(?i)(token|secret|password|api[_-]?key)=", value):
        raise ValueError("credential-bearing evidence references are not allowed")
    return value


class CIRepairWatchdog:
    """Idempotently enqueue repair work from the existing supervisor poll cycle."""

    def __init__(self, controller: Any, recovery: EngineeringRecoveryLedger):
        if not callable(getattr(controller, "submit", None)) or not hasattr(controller, "db_path"):
            raise TypeError("existing ExecutionController instance is required")
        if not isinstance(recovery, EngineeringRecoveryLedger):
            raise TypeError("canonical EngineeringRecoveryLedger is required")
        self.controller = controller
        self.recovery = recovery

    def observe_failed_check(
        self, *, repository: str, head_sha: str, check_identity: str,
        failure_fingerprint: str, evidence_refs: list[str], handler: str,
        diagnosis: str,
    ) -> Mapping[str, Any]:
        for value, name in ((repository, "repository"), (head_sha, "head_sha"),
                            (check_identity, "check_identity"), (failure_fingerprint, "failure_fingerprint"),
                            (diagnosis, "diagnosis"), (handler, "handler")):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", head_sha):
            raise ValueError("head_sha must be an immutable full commit SHA")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            raise ValueError("bounded evidence references are required")
        refs = list(dict.fromkeys(_safe_ref(ref) for ref in evidence_refs))[:8]
        identity = {"repository": repository, "head_sha": head_sha.lower(),
                    "check_identity": check_identity, "failure_fingerprint": failure_fingerprint}
        stable_key = "ci-repair-" + _digest(identity)
        intent = "repair failed required CI check " + _digest(identity)
        self.recovery.start_run(stable_key, intent)

        # The controller's SQLite tasks table is the deduplication authority;
        # repeated polling must not create another task for the same head/check.
        with sqlite3.connect(self.controller.db_path) as db:
            rows = db.execute("SELECT id,payload,state FROM tasks").fetchall()
        for task_id, raw_payload, state in rows:
            try:
                payload = json.loads(raw_payload)
            except (TypeError, json.JSONDecodeError):
                continue
            repair = payload.get("auren_ci_repair")
            if isinstance(repair, Mapping) and repair.get("stable_key") == stable_key:
                return {"status": "already_queued", "task_id": task_id, "state": state, "stable_key": stable_key}

        try:
            attempt_id = self.recovery.begin_attempt(
                stable_key, "ci_repair", failure_fingerprint, diagnosis,
                action="reproduce", head_sha=head_sha.lower(),
            )
        except RecoveryLimitReached as exc:
            return {"status": "blocked", "stable_key": stable_key, "reason": str(exc)}
        payload = {
            "auren_ci_repair": {"stable_key": stable_key, **identity},
            "evidence_refs": refs,
            "diagnosis": diagnosis[:1000],
            "repair_attempt_id": attempt_id,
            "require_new_evidence": True,
            "max_repair_attempts": 2,
        }
        try:
            task = self.controller.submit(handler, payload, goal=f"Repair CI failure {stable_key}")
        except Exception:
            self.recovery.finish_attempt(attempt_id, outcome="blocked", evidence=refs)
            raise
        # Keep the recovery attempt running until the supervisor records its actual validation outcome.
        return {"status": "queued", "task_id": task.id, "stable_key": stable_key,
                "attempt_id": attempt_id, "head_sha": head_sha.lower()}

    def finish_repair_attempt(self, *, attempt_id: int, outcome: str,
                              prior_evidence_refs: list[str], outcome_evidence_refs: list[str]) -> Mapping[str, Any]:
        """Close a queued repair only with outcome evidence; no-progress becomes blocked."""
        if outcome not in {"passed", "failed", "blocked", "rolled_back"}:
            raise ValueError("invalid recovery outcome")
        prior = {_safe_ref(item) for item in prior_evidence_refs}
        current = list(dict.fromkeys(_safe_ref(item) for item in outcome_evidence_refs))
        new_evidence = [item for item in current if item not in prior]
        if not new_evidence:
            self.recovery.finish_attempt(attempt_id, outcome="blocked", evidence=sorted(prior))
            return {"status": "blocked", "reason": "no new evidence or measurable progress; retry stopped"}
        self.recovery.finish_attempt(attempt_id, outcome=outcome, evidence=current)
        return {"status": outcome, "new_evidence": new_evidence}

    def finalize_terminal_tasks(self) -> list[Mapping[str, Any]]:
        """Close repair budgets from terminal supervisor state; missing validation evidence blocks."""
        with sqlite3.connect(self.controller.db_path) as db:
            rows = db.execute("SELECT id,payload,state FROM tasks WHERE state IN ('completed','failed','cancelled','blocked_reconciliation')").fetchall()
        journal_events = self.controller.progress_journal._events()
        finalized = []
        for task_id, raw_payload, state in rows:
            try:
                payload = json.loads(raw_payload)
            except (TypeError, json.JSONDecodeError):
                continue
            repair = payload.get("auren_ci_repair")
            attempt_id = payload.get("repair_attempt_id")
            if not isinstance(repair, Mapping) or not isinstance(attempt_id, int):
                continue
            stable_key = repair.get("stable_key")
            if not isinstance(stable_key, str):
                continue
            attempts = self.recovery.attempts(stable_key)
            attempt = next((item for item in attempts if item.attempt_id == attempt_id), None)
            if attempt is None or attempt.outcome != "running":
                continue
            prior_refs = payload.get("evidence_refs", [])
            if not isinstance(prior_refs, list):
                prior_refs = []
            validation_refs = [
                event["evidence_ref"] for event in journal_events
                if event.get("task_id") == task_id
                and event.get("stage") in {"validation", "verification", "ci_repair_validation"}
                and isinstance(event.get("evidence_ref"), str)
                and event["evidence_ref"].startswith("https://")
            ]
            outcome = "passed" if state == "completed" else ("failed" if state == "failed" else "blocked")
            result = self.finish_repair_attempt(
                attempt_id=attempt_id, outcome=outcome,
                prior_evidence_refs=prior_refs, outcome_evidence_refs=validation_refs,
            )
            finalized.append({"task_id": task_id, "attempt_id": attempt_id, **dict(result)})
        return finalized

    def stale_tasks(self, *, stale_after_seconds: float, now: float | None = None) -> list[Mapping[str, Any]]:
        """Report stale heartbeat candidates; supervisor decides whether to recover/block."""
        import time
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        current = time.time() if now is None else now
        with sqlite3.connect(self.controller.db_path) as db:
            rows = db.execute(
                "SELECT id,state,heartbeat_at,lease_until,updated_at FROM tasks "
                "WHERE state IN ('pending','running','recovering')"
            ).fetchall()
        findings = []
        def epoch(value: Any) -> float | None:
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                try:
                    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                    if parsed.tzinfo is None or parsed.utcoffset() is None:
                        return None
                    return parsed.timestamp()
                except ValueError:
                    return None

        for task_id, state, heartbeat, lease_until, updated in rows:
            stamp = epoch(heartbeat) or epoch(updated)
            lease = epoch(lease_until)
            if (stamp is not None and current - stamp >= stale_after_seconds) or (lease is not None and lease < current):
                findings.append({"task_id": task_id, "state": state, "heartbeat_at": heartbeat,
                                 "lease_until": lease_until, "reason": "stale_heartbeat_or_expired_lease"})
        return findings
