"""Versioned requirement decisions bound to the canonical engineering plan store.

Decision history shares the plan database. Records are append-only and bind the
selected option, rejected alternatives, affected tasks and actor to an exact
plan digest and run revision. This module records decisions; it does not grant
execution authority or silently rewrite plan state.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import sqlite3
from contextlib import closing
import time
from typing import Any, Callable, Iterable, Mapping

from .engineering_plan_store import EngineeringPlanStore, PlanRevisionConflict


_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_TYPES = frozenset({"conflict_resolution", "assumption", "scope_change"})


def _text(value: Any, field: str, *, limit: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{field} must be non-empty text of at most {limit} characters")
    return value.strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True)
class EngineeringDecisionRecord:
    run_id: str
    decision_id: str
    revision: int
    plan_digest: str
    run_revision: int
    decision_type: str
    question: str
    options: tuple[str, ...]
    selected_option: str
    rationale: str
    rejected_alternatives: tuple[tuple[str, str], ...]
    affected_task_ids: tuple[str, ...]
    actor_id: str
    supersedes_digest: str | None
    created_at: float
    record_digest: str
    current_binding: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "decision_id": self.decision_id,
            "revision": self.revision,
            "plan_digest": self.plan_digest,
            "run_revision": self.run_revision,
            "decision_type": self.decision_type,
            "question": self.question,
            "options": list(self.options),
            "selected_option": self.selected_option,
            "rationale": self.rationale,
            "rejected_alternatives": [
                {"option": option, "reason": reason}
                for option, reason in self.rejected_alternatives
            ],
            "affected_task_ids": list(self.affected_task_ids),
            "actor_id": self.actor_id,
            "supersedes_digest": self.supersedes_digest,
            "created_at": self.created_at,
            "record_digest": self.record_digest,
            "current_binding": self.current_binding,
        }


class EngineeringDecisionStore:
    """Append-only decision history in the same SQLite database as the plan store."""

    def __init__(
        self,
        plan_store: EngineeringPlanStore,
        *,
        actor_authorizer: Callable[[Mapping[str, Any]], str | None],
    ) -> None:
        if not isinstance(plan_store, EngineeringPlanStore):
            raise TypeError("plan_store must be the canonical EngineeringPlanStore")
        if not callable(actor_authorizer):
            raise TypeError("actor_authorizer must be a trusted host callback")
        self.plan_store = plan_store
        self.actor_authorizer = actor_authorizer
        with closing(plan_store._connect()) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS engineering_decision_records (
                    run_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    plan_digest TEXT NOT NULL,
                    run_revision INTEGER NOT NULL,
                    record_json TEXT NOT NULL,
                    record_digest TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (run_id, decision_id, revision)
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_engineering_decisions_run "
                "ON engineering_decision_records(run_id, decision_id, revision)"
            )

    @staticmethod
    def _validate(
        *, decision_id: str, decision_type: str, question: str,
        options: Iterable[str], selected_option: str, rationale: str,
        rejected_alternatives: Iterable[tuple[str, str]],
        affected_task_ids: Iterable[str], actor_id: str,
    ) -> dict[str, Any]:
        if not isinstance(decision_id, str) or not _TOKEN.fullmatch(decision_id):
            raise ValueError("decision_id must be a bounded identifier token")
        if not isinstance(decision_type, str) or decision_type not in _TYPES:
            raise ValueError(f"decision_type must be one of {sorted(_TYPES)}")
        if isinstance(options, (str, bytes)):
            raise ValueError("options must be a sequence of option strings")
        normalized_options = tuple(_text(item, "option", limit=500) for item in options)
        if len(normalized_options) < 2 or len(normalized_options) > 20:
            raise ValueError("a decision requires between two and twenty options")
        if len(set(normalized_options)) != len(normalized_options):
            raise ValueError("decision options must be unique")
        selected = _text(selected_option, "selected_option", limit=500)
        if selected not in normalized_options:
            raise ValueError("selected_option must be one of the declared options")
        rejected = tuple(
            (_text(option, "rejected option", limit=500), _text(reason, "rejection reason", limit=1000))
            for option, reason in rejected_alternatives
        )
        if len(rejected) > 20:
            raise ValueError("at most twenty rejected alternatives may be recorded")
        if len({option for option, _ in rejected}) != len(rejected):
            raise ValueError("rejected alternatives must be unique")
        if any(option not in normalized_options or option == selected for option, _ in rejected):
            raise ValueError("rejected alternatives must be declared options other than the selected option")
        if isinstance(affected_task_ids, (str, bytes)):
            raise ValueError("affected_task_ids must be a sequence of task IDs")
        affected = tuple(_text(item, "affected task ID", limit=128) for item in affected_task_ids)
        if len(affected) > 100:
            raise ValueError("at most one hundred affected task IDs may be recorded")
        if len(set(affected)) != len(affected):
            raise ValueError("affected task IDs must be unique")
        actor = _text(actor_id, "actor_id", limit=128)
        if not _TOKEN.fullmatch(actor):
            raise ValueError("actor_id must be a bounded identifier token")
        return {
            "decision_id": decision_id,
            "decision_type": decision_type,
            "question": _text(question, "question"),
            "options": list(normalized_options),
            "selected_option": selected,
            "rationale": _text(rationale, "rationale"),
            "rejected_alternatives": [
                {"option": option, "reason": reason} for option, reason in rejected
            ],
            "affected_task_ids": list(affected),
            "actor_id": actor,
        }

    @staticmethod
    def _decode(row: sqlite3.Row, *, current_run_revision: int) -> EngineeringDecisionRecord:
        try:
            payload = json.loads(row["record_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("stored engineering decision JSON is corrupt") from exc
        digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
        if digest != row["record_digest"]:
            raise ValueError("stored engineering decision digest mismatch")
        if (
            payload.get("run_id") != row["run_id"]
            or payload.get("decision_id") != row["decision_id"]
            or payload.get("revision") != row["revision"]
            or payload.get("plan_digest") != row["plan_digest"]
            or payload.get("run_revision") != row["run_revision"]
        ):
            raise ValueError("stored engineering decision identity or binding mismatch")
        return EngineeringDecisionRecord(
            run_id=payload["run_id"], decision_id=payload["decision_id"],
            revision=payload["revision"], plan_digest=payload["plan_digest"],
            run_revision=payload["run_revision"], decision_type=payload["decision_type"],
            question=payload["question"], options=tuple(payload["options"]),
            selected_option=payload["selected_option"], rationale=payload["rationale"],
            rejected_alternatives=tuple(
                (item["option"], item["reason"]) for item in payload["rejected_alternatives"]
            ),
            affected_task_ids=tuple(payload["affected_task_ids"]), actor_id=payload["actor_id"],
            supersedes_digest=payload["supersedes_digest"], created_at=payload["created_at"],
            record_digest=digest, current_binding=payload["run_revision"] == current_run_revision,
        )

    def record(
        self, *, run_id: str, decision_id: str, expected_revision: int,
        plan_digest: str, run_revision: int, decision_type: str, question: str,
        options: Iterable[str], selected_option: str, rationale: str,
        rejected_alternatives: Iterable[tuple[str, str]] = (),
        affected_task_ids: Iterable[str] = (), actor_id: str | None = None,
    ) -> EngineeringDecisionRecord:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        if type(run_revision) is not int or run_revision < 0:
            raise ValueError("run_revision must be a non-negative integer")
        if not isinstance(plan_digest, str) or not _DIGEST.fullmatch(plan_digest):
            raise ValueError("plan_digest must be a lowercase SHA-256 digest")
        if not isinstance(run_id, str) or not _TOKEN.fullmatch(run_id):
            raise ValueError("run_id must be a bounded identifier token")
        if not isinstance(options, (str, bytes)):
            options = tuple(options)
        if not isinstance(rejected_alternatives, (str, bytes)):
            rejected_alternatives = tuple(rejected_alternatives)
        if not isinstance(affected_task_ids, (str, bytes)):
            affected_task_ids = tuple(affected_task_ids)
        actor_payload = {
            "run_id": run_id, "decision_id": decision_id,
            "plan_digest": plan_digest, "run_revision": run_revision,
            "decision_type": decision_type, "question": question,
            "options": list(options) if not isinstance(options, (str, bytes)) else options,
            "selected_option": selected_option, "rationale": rationale,
            "rejected_alternatives": list(rejected_alternatives) if not isinstance(rejected_alternatives, (str, bytes)) else rejected_alternatives,
            "affected_task_ids": list(affected_task_ids) if not isinstance(affected_task_ids, (str, bytes)) else affected_task_ids,
        }
        try:
            verified_actor = self.actor_authorizer(dict(actor_payload))
        except Exception as exc:
            raise PermissionError("decision actor authentication failed") from exc
        if not isinstance(verified_actor, str) or not verified_actor.strip() or len(verified_actor) > 128 or not verified_actor.isprintable():
            raise PermissionError("decision authorizer did not return a verified actor identity")
        if actor_id is not None and actor_id != verified_actor:
            raise PermissionError("caller-supplied actor identity does not match the authenticated actor")
        normalized = self._validate(
            decision_id=decision_id, decision_type=decision_type, question=question,
            options=options, selected_option=selected_option, rationale=rationale,
            rejected_alternatives=rejected_alternatives,
            affected_task_ids=affected_task_ids, actor_id=verified_actor,
        )
        plan, run = self.plan_store.load(run_id)
        if plan.digest != plan_digest:
            raise ValueError("decision plan digest does not match the canonical plan")
        if run.revision != run_revision:
            raise PlanRevisionConflict(
                f"decision run revision {run_revision} is stale; current revision is {run.revision}"
            )
        known_tasks = {task.task_id for task in plan.tasks}
        unknown = set(normalized["affected_task_ids"]) - known_tasks
        if unknown:
            raise ValueError(f"decision references unknown plan tasks: {sorted(unknown)}")

        db = self.plan_store._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            latest = db.execute(
                "SELECT * FROM engineering_decision_records "
                "WHERE run_id=? AND decision_id=? ORDER BY revision DESC LIMIT 1",
                (run_id, decision_id),
            ).fetchone()
            # Re-read the plan/run inside this write transaction. The earlier
            # load validates public input; this closes the race with a concurrent
            # plan-run update before the decision is committed.
            current_plan_row = db.execute(
                "SELECT plan_digest,plan_json,run_json,run_digest FROM engineering_plan_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if current_plan_row is None or current_plan_row["plan_digest"] != plan_digest:
                raise PlanRevisionConflict("canonical plan changed before decision commit")
            try:
                current_plan_payload = json.loads(current_plan_row["plan_json"])
                current_run_payload = json.loads(current_plan_row["run_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("canonical plan/run JSON is corrupt") from exc
            from .engineering_plan import EngineeringPlan
            if EngineeringPlan.from_dict(current_plan_payload).digest != plan_digest:
                raise ValueError("canonical plan digest mismatch")
            current_run_digest = hashlib.sha256(_canonical(current_run_payload).encode("utf-8")).hexdigest()
            if current_run_digest != current_plan_row["run_digest"]:
                raise ValueError("canonical plan run digest mismatch")
            if current_run_payload.get("revision") != run_revision:
                raise PlanRevisionConflict("canonical plan run advanced before decision commit")

            latest_revision = int(latest["revision"]) if latest else 0
            # A retry after a committed insert returns the same receipt instead of
            # appending a duplicate decision version.
            if latest and latest_revision == expected_revision + 1:
                existing = json.loads(latest["record_json"])
                retry_payload = {
                    "run_id": run_id, "revision": latest_revision,
                    "plan_digest": plan_digest, "run_revision": run_revision,
                    **normalized,
                    "supersedes_digest": existing.get("supersedes_digest"),
                    "created_at": existing.get("created_at"),
                }
                if retry_payload == existing and latest["record_digest"] == hashlib.sha256(
                    _canonical(existing).encode("utf-8")
                ).hexdigest():
                    db.commit()
                    return self._decode(latest, current_run_revision=run_revision)
                raise PlanRevisionConflict("decision retry conflicts with the committed decision revision")
            if latest_revision != expected_revision:
                raise PlanRevisionConflict(
                    f"expected decision revision {expected_revision}, found {latest_revision}"
                )
            next_revision = latest_revision + 1
            supersedes = latest["record_digest"] if latest else None
            payload = {
                "run_id": run_id, "revision": next_revision,
                "plan_digest": plan_digest, "run_revision": run_revision,
                **normalized, "supersedes_digest": supersedes,
                "created_at": time.time(),
            }
            canonical = _canonical(payload)
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

            db.execute(
                "INSERT INTO engineering_decision_records "
                "(run_id,decision_id,revision,plan_digest,run_revision,record_json,record_digest,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (run_id, decision_id, next_revision, plan_digest, run_revision, canonical, digest, payload["created_at"]),
            )
            db.commit()
            row = db.execute(
                "SELECT * FROM engineering_decision_records WHERE run_id=? AND decision_id=? AND revision=?",
                (run_id, decision_id, next_revision),
            ).fetchone()
            return self._decode(row, current_run_revision=run_revision)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def history(self, run_id: str, decision_id: str) -> tuple[EngineeringDecisionRecord, ...]:
        if not isinstance(run_id, str) or not _TOKEN.fullmatch(run_id):
            raise ValueError("run_id must be a bounded identifier token")
        if not isinstance(decision_id, str) or not _TOKEN.fullmatch(decision_id):
            raise ValueError("decision_id must be a bounded identifier token")
        plan, run = self.plan_store.load(run_id)
        db = self.plan_store._connect()
        try:
            rows = db.execute(
                "SELECT * FROM engineering_decision_records WHERE run_id=? AND decision_id=? ORDER BY revision",
                (run_id, decision_id),
            ).fetchall()
        finally:
            db.close()
        records = tuple(self._decode(row, current_run_revision=run.revision) for row in rows)
        previous = None
        for expected_revision, record in enumerate(records, start=1):
            if record.revision != expected_revision or record.supersedes_digest != previous:
                raise ValueError("engineering decision history has a missing or broken revision link")
            if record.plan_digest != plan.digest:
                raise ValueError("engineering decision history is bound to a different plan digest")
            previous = record.record_digest
        return records

    def latest(self, run_id: str, decision_id: str) -> EngineeringDecisionRecord:
        records = self.history(run_id, decision_id)
        if not records:
            raise KeyError(f"decision {decision_id!r} does not exist for run {run_id!r}")
        return records[-1]

    def verify_resolution(
        self, *, run_id: str, decision_id: str, selected_option: str,
    ) -> bool:
        """Verify a current, digest-valid conflict decision for a guard callback."""
        try:
            record = self.latest(run_id, decision_id)
        except KeyError:
            return False
        return (
            record.decision_type == "conflict_resolution"
            and record.current_binding is True
            and record.selected_option == selected_option
        )


__all__ = ["EngineeringDecisionRecord", "EngineeringDecisionStore"]
