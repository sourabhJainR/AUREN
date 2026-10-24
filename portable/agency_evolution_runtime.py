"""Durable, bounded orchestration for AUREN agency evolution Gates F-I.

This layer coordinates experiments and evidence. It does not execute providers or
grant release authority. External effects must be dispatched by the trusted
ExecutionController with stable operation IDs and provider reconciliation.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from .agency_evolution_gates import (
    ExperimentManifest,
    ImprovementJournal,
    evaluate_promotion_gates,
    extract_signals_from_trace,
    prioritize_signals,
    detect_drift,
    evaluate_evaluator_calibration,
)

TERMINAL = frozenset({"completed", "failed", "cancelled", "rolled_back"})
NON_SUCCESS = frozenset({"unknown", "unscored", "inconclusive"})
OUTCOMES = frozenset({"pass", "fail", "unknown", "unscored", "inconclusive"})
_STATES = frozenset({
    "queued", "running", "blocked", "evaluated", "awaiting_approval",
    "canary", "completed", "failed", "cancelled", "rolled_back", "inconclusive",
})


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _safe_summary(value: Any) -> Any:
    """Persist bounded metadata only; reject arbitrary provider payloads."""
    if value is None or type(value) in {bool, int}:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite evidence values are forbidden")
        return value
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, Mapping):
        allowed = {}
        for key, item in list(value.items())[:80]:
            name = str(key)[:80]
            if any(word in name.lower() for word in ("prompt", "response", "payload", "secret", "token", "password", "credential", "holdout_data")):
                raise ValueError(f"sensitive field is not allowed in evidence summary: {name}")
            allowed[name] = _safe_summary(item)
        return allowed
    if isinstance(value, (list, tuple)):
        return [_safe_summary(item) for item in value[:80]]
    raise ValueError(f"unsupported evidence value type: {type(value).__name__}")


class EvolutionConflict(RuntimeError):
    """A stable operation identifier was reused with different immutable intent."""


class AgencyEvolutionRuntime:
    """SQLite-backed experiment lifecycle, reconciliation, and release evidence.

    SQLite serializes local processes through BEGIN IMMEDIATE. This does not
    replace provider idempotency, distributed locks, independent holdout custody,
    or the trusted controller's authority checks.
    """

    def __init__(self, database_path: str | Path, *, max_attempts: int = 3,
                 max_cycles: int = 2, improvement_journal: ImprovementJournal | None = None):
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if isinstance(max_cycles, bool) or not isinstance(max_cycles, int) or max_cycles < 1:
            raise ValueError("max_cycles must be a positive integer")
        self.path = Path(database_path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_attempts = max_attempts
        self.max_cycles = max_cycles
        self.journal = improvement_journal
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS evolution_operations(
                    operation_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL UNIQUE,
                    manifest_digest TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    parent_operation_id TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    outcome TEXT NOT NULL DEFAULT '',
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    next_action TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evolution_events(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    operation_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_digest TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evolution_cycles(
                    cycle_number INTEGER PRIMARY KEY,
                    operation_id TEXT NOT NULL UNIQUE,
                    parent_operation_id TEXT NOT NULL DEFAULT '',
                    evidence_digest TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
            """)

    def _event(self, db: sqlite3.Connection, operation_id: str, event_type: str,
               payload: Mapping[str, Any], event_id: str | None = None) -> None:
        safe = _safe_summary(dict(payload))
        encoded = _canonical(safe)
        event_id = event_id or f"evt-{uuid4().hex}"
        existing = db.execute(
            "SELECT operation_id,event_type,payload_json,digest FROM evolution_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if existing:
            if existing[:3] != (operation_id, event_type, encoded):
                raise EvolutionConflict("event ID reused with conflicting content")
            return
        previous = db.execute("SELECT digest FROM evolution_events ORDER BY sequence DESC LIMIT 1").fetchone()
        body = {
            "sequence": db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM evolution_events").fetchone()[0],
            "event_id": event_id, "operation_id": operation_id, "event_type": event_type,
            "payload_json": encoded, "previous_digest": previous[0] if previous else "0" * 64,
            "created_at": time.time(),
        }
        db.execute(
            "INSERT INTO evolution_events(sequence,event_id,operation_id,event_type,payload_json,previous_digest,digest,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (body["sequence"], event_id, operation_id, event_type, encoded, body["previous_digest"], _digest(body), body["created_at"]),
        )

    def _operation(self, db: sqlite3.Connection, operation_id: str) -> dict[str, Any]:
        row = db.execute("SELECT operation_id,experiment_id,manifest_digest,manifest_json,parent_operation_id,state,attempts,outcome,evidence_json,result_json,next_action,created_at,updated_at FROM evolution_operations WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None:
            raise KeyError(operation_id)
        keys = ("operation_id","experiment_id","manifest_digest","manifest_json","parent_operation_id","state","attempts","outcome","evidence_json","result_json","next_action","created_at","updated_at")
        item = dict(zip(keys, row))
        for key, target in (("manifest_json", "manifest"), ("evidence_json", "evidence_refs"), ("result_json", "result")):
            item[target] = json.loads(item.pop(key))
        return item

    def create_experiment(self, manifest: ExperimentManifest, *, evidence_refs: tuple[str, ...],
                          parent_operation_id: str = "") -> dict[str, Any]:
        if not isinstance(manifest, ExperimentManifest):
            raise TypeError("manifest must be an ExperimentManifest")
        if not evidence_refs or any(not isinstance(ref, str) or not ref.strip() for ref in evidence_refs):
            raise ValueError("source evidence references are required")
        if len(set(evidence_refs)) != len(evidence_refs):
            raise ValueError("source evidence references must be unique")
        encoded = _canonical(asdict(manifest))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        operation_id = "evo-" + hashlib.sha256((manifest.experiment_id + ":" + digest).encode()).hexdigest()[:32]
        now = time.time()
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT operation_id,manifest_digest FROM evolution_operations WHERE experiment_id=?", (manifest.experiment_id,)).fetchone()
            if prior:
                if prior[1] != digest:
                    raise EvolutionConflict("experiment ID already binds different immutable manifest")
                result = self._operation(db, prior[0])
                evidence_refs = tuple(result["evidence_refs"])
            else:
                cycle_count = db.execute("SELECT COUNT(*) FROM evolution_cycles").fetchone()[0]
                if cycle_count >= self.max_cycles:
                    raise ValueError("self-improvement cycle budget exhausted")
                if parent_operation_id:
                    parent = self._operation(db, parent_operation_id)
                    if parent["state"] != "completed" or parent["outcome"] != "pass":
                        raise ValueError("a follow-on cycle requires verified successful evidence from its parent")
                    evidence_refs = tuple(dict.fromkeys((*evidence_refs, f"operation:{parent_operation_id}")))
                db.execute(
                    "INSERT INTO evolution_operations(operation_id,experiment_id,manifest_digest,manifest_json,parent_operation_id,state,evidence_json,next_action,created_at,updated_at) VALUES(?,?,?,?,?,'queued',?,?,?,?)",
                    (operation_id, manifest.experiment_id, digest, encoded, parent_operation_id, _canonical(list(evidence_refs)), "claim operation before dispatch", now, now),
                )
                cycle = db.execute("SELECT COALESCE(MAX(cycle_number),0)+1 FROM evolution_cycles").fetchone()[0]
                db.execute("INSERT INTO evolution_cycles(cycle_number,operation_id,parent_operation_id,evidence_digest,created_at) VALUES(?,?,?,?,?)",
                           (cycle, operation_id, parent_operation_id, _digest(list(evidence_refs)), now))
                self._event(db, operation_id, "experiment_queued", {"manifest_digest": digest, "evidence_refs": list(evidence_refs), "parent_operation_id": parent_operation_id})
                result = self._operation(db, operation_id)
        # Retrying this projection is safe because the journal uses a stable event ID.
        # The SQLite event is canonical if the secondary journal is temporarily unavailable.
        if self.journal:
            self.journal.append(event_id=f"evolution:{operation_id}:queued", work_id=operation_id, state="queued",
                                payload={"manifest_digest": digest, "evidence_refs": list(evidence_refs)})
        return result

    def claim(self, operation_id: str) -> dict[str, Any]:
        """Atomically claim a queued operation. Never re-dispatch a running/unknown one."""
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            item = self._operation(db, operation_id)
            if item["state"] != "queued":
                raise ValueError(f"operation cannot be claimed from state={item['state']}; reconcile first")
            if item["attempts"] >= self.max_attempts:
                db.execute("UPDATE evolution_operations SET state='blocked',next_action='operator review; retry budget exhausted',updated_at=? WHERE operation_id=?", (time.time(), operation_id))
                self._event(db, operation_id, "retry_budget_exhausted", {"attempts": item["attempts"]})
                return self._operation(db, operation_id)
            attempts = item["attempts"] + 1
            db.execute("UPDATE evolution_operations SET state='running',attempts=?,next_action='await durable experiment result or reconcile external operation',updated_at=? WHERE operation_id=?", (attempts, time.time(), operation_id))
            self._event(db, operation_id, "operation_claimed", {"attempt": attempts})
            return self._operation(db, operation_id)

    def recover_interrupted(self, *, older_than_seconds: float = 60.0) -> list[str]:
        """Block stale running work after startup/lease recovery; never auto-retry live work."""
        if isinstance(older_than_seconds, bool) or not isinstance(older_than_seconds, (int, float)) or not math.isfinite(older_than_seconds) or older_than_seconds < 0:
            raise ValueError("older_than_seconds must be finite and non-negative")
        cutoff = time.time() - float(older_than_seconds)
        blocked: list[str] = []
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT operation_id FROM evolution_operations WHERE state='running' AND updated_at<=?", (cutoff,)).fetchall()
            for (operation_id,) in rows:
                db.execute("UPDATE evolution_operations SET state='blocked',outcome='unknown',next_action='reconcile stable operation ID with trusted controller/provider before any retry',updated_at=? WHERE operation_id=?", (time.time(), operation_id))
                self._event(db, operation_id, "interrupted_outcome_unknown", {"next_action": "reconcile before retry"})
                blocked.append(operation_id)
        return blocked

    def reconcile(self, operation_id: str, *, remote_state: str, evidence_ref: str) -> dict[str, Any]:
        remote_state = _text(remote_state, "remote_state").lower()
        evidence_ref = _text(evidence_ref, "evidence_ref")
        if remote_state not in {"not_started", "completed", "failed", "unknown"}:
            raise ValueError("remote_state must be not_started, completed, failed or unknown")
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            item = self._operation(db, operation_id)
            if item["state"] != "blocked" or item["outcome"] != "unknown":
                raise ValueError("only an ambiguous interrupted operation can be reconciled")
            if remote_state == "unknown":
                self._event(db, operation_id, "reconciliation_still_unknown", {"evidence_ref": evidence_ref})
                return self._operation(db, operation_id)
            if remote_state == "not_started":
                state, outcome, next_action = "queued", "", "safe to claim again after trusted controller confirms no side effect started"
            elif remote_state == "completed":
                state, outcome, next_action = "evaluated", "unknown", "validate recovered result and attach independent evaluation evidence"
            else:
                state, outcome, next_action = "failed", "fail", "inspect failure evidence; do not auto-retry"
            refs = list(dict.fromkeys([*item["evidence_refs"], evidence_ref]))
            db.execute("UPDATE evolution_operations SET state=?,outcome=?,evidence_json=?,next_action=?,updated_at=? WHERE operation_id=?",
                       (state, outcome, _canonical(refs), next_action, time.time(), operation_id))
            self._event(db, operation_id, "operation_reconciled", {"remote_state": remote_state, "evidence_ref": evidence_ref, "state": state})
            return self._operation(db, operation_id)

    def record_evaluation(self, operation_id: str, *, outcome: str, evidence_refs: tuple[str, ...],
                          evaluator_revision: str, independent: bool, holdout_isolated: bool,
                          metrics: Mapping[str, Any], domain: str) -> dict[str, Any]:
        outcome = _text(outcome, "outcome").lower()
        evaluator_revision = _text(evaluator_revision, "evaluator_revision")
        domain = _text(domain, "domain")
        if outcome not in OUTCOMES:
            raise ValueError("outcome must be pass, fail, unknown, unscored or inconclusive")
        if type(independent) is not bool or type(holdout_isolated) is not bool:
            raise ValueError("independence and holdout isolation must be explicit booleans")
        if not evidence_refs or any(not isinstance(ref, str) or not ref.strip() for ref in evidence_refs):
            raise ValueError("evaluation evidence references are required")
        safe_metrics = _safe_summary(dict(metrics))
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            item = self._operation(db, operation_id)
            if item["state"] not in {"running", "evaluated"}:
                raise ValueError(f"cannot evaluate operation in state={item['state']}")
            success = outcome == "pass" and independent and holdout_isolated
            state = "evaluated" if success else ("failed" if outcome == "fail" else "inconclusive" if outcome in NON_SUCCESS else "blocked")
            next_action = "check all promotion gates and request trusted approval" if success else "collect independent evidence or stop; non-pass is not success"
            result = {"outcome": outcome, "evaluator_revision": evaluator_revision, "independent": independent,
                      "holdout_isolated": holdout_isolated, "metrics": safe_metrics, "domain": domain,
                      "evidence_refs": list(evidence_refs), "verified_pass": success}
            refs = list(dict.fromkeys([*item["evidence_refs"], *evidence_refs]))
            db.execute("UPDATE evolution_operations SET state=?,outcome=?,evidence_json=?,result_json=?,next_action=?,updated_at=? WHERE operation_id=?",
                       (state, outcome, _canonical(refs), _canonical(result), next_action, time.time(), operation_id))
            self._event(db, operation_id, "evaluation_recorded", result)
            return self._operation(db, operation_id)

    def promotion_decision(self, operation_id: str, *, gates: Mapping[str, bool],
                           regression_passed: bool, risk_approved: bool) -> dict[str, Any]:
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            item = self._operation(db, operation_id)
            result = item["result"]
            if item["state"] != "evaluated" or item["outcome"] != "pass" or result.get("verified_pass") is not True:
                return {"action": "block", "reason": "independent passing evaluation is required", "operation_id": operation_id}
            decision = evaluate_promotion_gates(
                gates, candidate_passed=True, regression_passed=regression_passed,
                risk_approved=risk_approved,
            )
            action = "awaiting_approval" if decision.action == "eligible_for_controller_approval" else "block"
            with db:
                db.execute("UPDATE evolution_operations SET state=?,next_action=?,updated_at=? WHERE operation_id=?",
                           (action, "trusted controller must verify approver, exact-head checks, and release authority" if action == "awaiting_approval" else decision.reason, time.time(), operation_id))
                self._event(db, operation_id, "promotion_gate_checked", {"action": action, "reason": decision.reason, "missing_gates": list(decision.missing_gates)})
            return {"action": action, "reason": decision.reason, "missing_gates": list(decision.missing_gates), "operation_id": operation_id}

    def record_release_event(self, operation_id: str, *, event: str, evidence_ref: str,
                             controller_authorized: bool, verified: bool = False) -> dict[str, Any]:
        """Record controller-produced release/rollback evidence; never perform release itself."""
        event = _text(event, "event")
        evidence_ref = _text(evidence_ref, "evidence_ref")
        if event not in {"shadow_passed", "approval_verified", "canary_started", "canary_passed", "promoted", "rollback_started", "rollback_verified"}:
            raise ValueError("unsupported release lifecycle event")
        if type(controller_authorized) is not bool or type(verified) is not bool:
            raise ValueError("controller_authorized and verified must be explicit booleans")
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            item = self._operation(db, operation_id)
            if not controller_authorized:
                db.execute("UPDATE evolution_operations SET state='blocked',next_action='trusted controller authorization missing; no release action permitted',updated_at=? WHERE operation_id=?", (time.time(), operation_id))
                self._event(db, operation_id, "release_event_rejected", {"event": event, "evidence_ref": evidence_ref})
                return self._operation(db, operation_id)
            allowed = {
                "shadow_passed": {"awaiting_approval"},
                "approval_verified": {"awaiting_approval"},
                "canary_started": {"awaiting_approval"},
                "canary_passed": {"canary"},
                "promoted": {"canary"},
                "rollback_started": {"canary", "completed", "failed"},
                "rollback_verified": {"canary", "completed", "failed", "rolled_back"},
            }
            if item["state"] not in allowed[event]:
                raise ValueError(f"release event {event} is invalid from state={item['state']}")
            if event in {"shadow_passed", "approval_verified", "canary_passed", "promoted", "rollback_verified"} and not verified:
                raise ValueError(f"{event} requires independently verified controller evidence")
            flags = dict(item["result"].get("release_flags", {}))
            prerequisites = {
                "approval_verified": "shadow_passed",
                "canary_started": "approval_verified",
                "canary_passed": "canary_started",
                "promoted": "canary_passed",
                "rollback_verified": "rollback_started",
            }
            required_flag = prerequisites.get(event)
            if required_flag and flags.get(required_flag) is not True:
                raise ValueError(f"{event} requires prior verified event: {required_flag}")
            if event == "canary_started":
                state, next_action = "canary", "observe canary against pinned metrics and bounded window"
            elif event == "promoted":
                state, next_action = "completed", "verify release health and preserve evidence"
            elif event == "rollback_verified":
                state, next_action = "rolled_back", "confirm restored revision and runtime health"
            else:
                state, next_action = item["state"], "continue only with trusted controller evidence and bounded observation"
            flags[event] = True
            result = dict(item["result"])
            result["release_flags"] = flags
            refs = list(dict.fromkeys([*item["evidence_refs"], evidence_ref]))
            db.execute("UPDATE evolution_operations SET state=?,evidence_json=?,result_json=?,next_action=?,updated_at=? WHERE operation_id=?",
                       (state, _canonical(refs), _canonical(result), next_action, time.time(), operation_id))
            self._event(db, operation_id, "release_lifecycle_event", {"event": event, "evidence_ref": evidence_ref, "verified": verified})
            return self._operation(db, operation_id)

    def cancel(self, operation_id: str, *, evidence_ref: str) -> dict[str, Any]:
        evidence_ref = _text(evidence_ref, "evidence_ref")
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            item = self._operation(db, operation_id)
            if item["state"] in TERMINAL:
                return item
            if item["state"] == "running":
                state, outcome, next_action = "blocked", "unknown", "cancellation requested; reconcile external outcome before retry or terminal claim"
            else:
                state, outcome, next_action = "cancelled", item["outcome"], "cancelled; preserve evidence and verify no external work remains"
            refs = list(dict.fromkeys([*item["evidence_refs"], evidence_ref]))
            db.execute("UPDATE evolution_operations SET state=?,outcome=?,evidence_json=?,next_action=?,updated_at=? WHERE operation_id=?",
                       (state, outcome, _canonical(refs), next_action, time.time(), operation_id))
            self._event(db, operation_id, "cancellation_requested", {"state": state, "evidence_ref": evidence_ref})
            return self._operation(db, operation_id)

    def snapshot(self, operation_id: str) -> dict[str, Any]:
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            item = self._operation(db, operation_id)
            rows = db.execute("SELECT event_type,payload_json,digest FROM evolution_events WHERE operation_id=? ORDER BY sequence", (operation_id,)).fetchall()
            domains = {}
            if item["result"].get("domain"):
                domains[item["result"]["domain"]] = {
                    "outcome": item["outcome"], "metrics": item["result"].get("metrics", {}),
                    "evaluator_revision": item["result"].get("evaluator_revision"),
                }
            return {
                "operation_id": item["operation_id"], "experiment_id": item["experiment_id"],
                "state": item["state"], "outcome": item["outcome"] or "unknown",
                "attempts": item["attempts"], "max_attempts": self.max_attempts,
                "evidence_refs": item["evidence_refs"], "result": item["result"],
                "domain_scorecards": domains, "event_count": len(rows),
                "event_digests": [row[2] for row in rows],
                "next_action": item["next_action"], "updated_at": item["updated_at"],
                "release_authority": "trusted-controller-only",
            }

    def verify_event_chain(self) -> bool:
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            rows = db.execute("SELECT sequence,event_id,operation_id,event_type,payload_json,previous_digest,digest,created_at FROM evolution_events ORDER BY sequence").fetchall()
        previous = "0" * 64
        for sequence, event_id, operation_id, event_type, payload_json, previous_digest, supplied, created_at in rows:
            body = {"sequence": sequence, "event_id": event_id, "operation_id": operation_id, "event_type": event_type, "payload_json": payload_json, "previous_digest": previous_digest, "created_at": created_at}
            if previous_digest != previous or _digest(body) != supplied:
                return False
            previous = supplied
        return True

    def calibrate_evaluator(self, operation_id: str, *, evaluator_revision: str,
                            adjudicated_cases: tuple[tuple[bool, bool], ...],
                            adjudication_evidence_refs: tuple[str, ...],
                            independent_adjudication: bool,
                            minimum_samples: int = 20, minimum_accuracy: float = 0.95,
                            maximum_false_pass_rate: float = 0.02) -> dict[str, Any]:
        """Record calibration only when independent adjudication is explicitly attested."""
        if type(independent_adjudication) is not bool:
            raise ValueError("independent_adjudication must be an explicit boolean")
        if not independent_adjudication:
            raise ValueError("evaluator calibration requires independent adjudication")
        if not adjudication_evidence_refs or any(not isinstance(ref, str) or not ref.strip() for ref in adjudication_evidence_refs):
            raise ValueError("adjudication evidence references are required")
        result = evaluate_evaluator_calibration(
            evaluator_revision, adjudicated_cases, minimum_samples=minimum_samples,
            minimum_accuracy=minimum_accuracy, maximum_false_pass_rate=maximum_false_pass_rate,
        )
        payload = {
            "evaluator_revision": result.evaluator_revision, "sample_count": result.sample_count,
            "accuracy": result.accuracy, "false_pass_rate": result.false_pass_rate,
            "false_fail_rate": result.false_fail_rate, "accepted": result.accepted,
            "reason": result.reason, "independent_adjudication": True,
            "adjudication_evidence_refs": list(adjudication_evidence_refs),
        }
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            item = self._operation(db, operation_id)
            refs = list(dict.fromkeys([*item["evidence_refs"], *adjudication_evidence_refs]))
            result_json = dict(item["result"])
            calibrations = list(result_json.get("evaluator_calibrations", []))
            calibrations.append(payload)
            result_json["evaluator_calibrations"] = calibrations
            next_action = "evaluator calibration accepted; retain with promotion evidence" if result.accepted else "block promotion; improve evaluator or collect more independent adjudications"
            db.execute("UPDATE evolution_operations SET evidence_json=?,result_json=?,next_action=?,updated_at=? WHERE operation_id=?",
                       (_canonical(refs), _canonical(result_json), next_action, time.time(), operation_id))
            self._event(db, operation_id, "evaluator_calibrated", payload)
        return payload

    def assess_drift(self, operation_id: str, *, metric: str, observations: tuple[float, ...],
                     fixed_release_baseline: float, adaptive_baseline: float,
                     absolute_tolerance: float, adaptive_tolerance: float,
                     evidence_ref: str) -> dict[str, Any]:
        """Compare operational drift against fixed release and adaptive workload baselines."""
        evidence_ref = _text(evidence_ref, "evidence_ref")
        result = detect_drift(
            metric, observations, fixed_baseline=fixed_release_baseline,
            adaptive_baseline=adaptive_baseline, absolute_tolerance=absolute_tolerance,
            adaptive_tolerance=adaptive_tolerance,
        )
        payload = {
            "metric": result.metric, "sample_count": result.sample_count,
            "observed_mean": result.observed_mean, "fixed_baseline": result.fixed_baseline,
            "adaptive_baseline": result.adaptive_baseline, "absolute_delta": result.absolute_delta,
            "adaptive_delta": result.adaptive_delta, "drift_detected": result.drift_detected,
            "reason": result.reason, "evidence_ref": evidence_ref,
        }
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            item = self._operation(db, operation_id)
            refs = list(dict.fromkeys([*item["evidence_refs"], evidence_ref]))
            result_json = dict(item["result"])
            drift_results = list(result_json.get("drift_assessments", []))
            drift_results.append(payload)
            result_json["drift_assessments"] = drift_results
            next_action = "drift detected; block promotion and investigate" if result.drift_detected else item["next_action"]
            db.execute("UPDATE evolution_operations SET evidence_json=?,result_json=?,next_action=?,updated_at=? WHERE operation_id=?",
                       (_canonical(refs), _canonical(result_json), next_action, time.time(), operation_id))
            self._event(db, operation_id, "drift_assessed", payload)
        return payload

    def ingest_trace(self, trace: Mapping[str, Any]) -> dict[str, Any]:
        """Convert observed trace metadata into prioritized signals, not ground truth."""
        signals = prioritize_signals(extract_signals_from_trace(trace))
        return {"trace_id": trace.get("trace_id"), "signal_count": len(signals),
                "signals": [{"signal_id": s.signal_id, "category": s.category, "summary": s.summary,
                             "source_ref": s.source_ref, "severity": s.severity,
                             "expected_impact": s.expected_impact, "security_relevance": s.security_relevance}
                            for s in signals],
                "next_action": "independently validate signals before creating or promoting a hypothesis"}
