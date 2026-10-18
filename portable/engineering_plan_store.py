"""Transactional SQLite persistence for spec-driven engineering plans.

This store persists the plan contract; it does not execute work or replace the
canonical RuntimeExecutionGuard/checkpoint ledger used for runtime budgets.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .engineering_plan import EngineeringPlan, EngineeringPlanRun, new_plan_run


class PlanRevisionConflict(RuntimeError):
    """A concurrent writer advanced the plan run after this caller loaded it."""


class EngineeringPlanStore:
    SCHEMA_VERSION = 2

    def __init__(self, database_path: str | Path, *, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds
        db = self._connect()
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS engineering_plan_runs (
                    run_id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    plan_digest TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    run_json TEXT NOT NULL,
                    run_digest TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            db.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")
        finally:
            db.close()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path, timeout=self.timeout_seconds, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(f"PRAGMA busy_timeout={int(self.timeout_seconds * 1000)}")
        return db

    @staticmethod
    def _json(payload: dict[str, Any]) -> str:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)

    def create(self, plan: EngineeringPlan, run: EngineeringPlanRun | None = None) -> EngineeringPlanRun:
        run = run or new_plan_run(plan)
        run.verify_against(plan)
        if run.source_revision != plan.source_revision:
            raise ValueError("plan run source revision mismatch")
        plan_json = self._json(plan.to_dict())
        run_json = self._json(run.to_dict())
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO engineering_plan_runs "
                "(run_id, schema_version, plan_digest, plan_json, run_json, run_digest, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (plan.run_id, self.SCHEMA_VERSION, plan.digest, plan_json, run_json, run.digest, time.time()),
            )
            db.commit()
        except sqlite3.IntegrityError as exc:
            db.rollback()
            raise ValueError(f"engineering plan run {plan.run_id!r} already exists") from exc
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return run

    def load(self, run_id: str) -> tuple[EngineeringPlan, EngineeringPlanRun]:
        db = self._connect()
        try:
            row = db.execute(
                "SELECT schema_version, plan_digest, plan_json, run_json, run_digest "
                "FROM engineering_plan_runs WHERE run_id = ?", (run_id,),
            ).fetchone()
        finally:
            db.close()
        if row is None:
            raise KeyError(f"engineering plan run {run_id!r} does not exist")
        if row["schema_version"] != self.SCHEMA_VERSION:
            raise ValueError("unsupported engineering plan store schema")
        try:
            plan_payload = json.loads(row["plan_json"])
            run_payload = json.loads(row["run_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("corrupt engineering plan JSON") from exc
        plan = EngineeringPlan.from_dict(plan_payload)
        run = EngineeringPlanRun.from_dict(run_payload)
        if run.digest != row["run_digest"]:
            raise ValueError("stored run digest mismatch")
        if plan.run_id != run_id or plan.digest != row["plan_digest"]:
            raise ValueError("stored plan identity or digest mismatch")
        run.verify_against(plan)
        return plan, run

    def save_run(
        self, run_id: str, run: EngineeringPlanRun, *, expected_revision: int,
    ) -> None:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT schema_version, plan_digest, plan_json, run_json, run_digest "
                "FROM engineering_plan_runs WHERE run_id = ?", (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"engineering plan run {run_id!r} does not exist")
            if row["schema_version"] != self.SCHEMA_VERSION:
                raise ValueError("unsupported engineering plan store schema")
            plan = EngineeringPlan.from_dict(json.loads(row["plan_json"]))
            current = EngineeringPlanRun.from_dict(json.loads(row["run_json"]))
            if current.digest != row["run_digest"]:
                raise ValueError("stored run digest mismatch")
            if plan.digest != row["plan_digest"]:
                raise ValueError("stored plan digest mismatch")
            current.verify_against(plan)
            run.verify_against(plan)
            if current.revision != expected_revision:
                raise PlanRevisionConflict(
                    f"expected run revision {expected_revision}, found {current.revision}"
                )
            if run.revision != expected_revision + 1:
                raise ValueError("saved run must advance exactly one revision")
            db.execute(
                "UPDATE engineering_plan_runs SET run_json = ?, run_digest = ?, updated_at = ? "
                "WHERE run_id = ? AND plan_digest = ?",
                (self._json(run.to_dict()), run.digest, time.time(), run_id, plan.digest),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
