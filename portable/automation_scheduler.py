"""Public facade for durable AUREN scheduling.

Compatibility methods here adapt the earlier receipt-oriented API to the
unified claim-before-run scheduler without duplicating scheduler state.
"""
from __future__ import annotations

import calendar
import json
import sqlite3
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .agent_capabilities import AutomationScheduler as _AutomationScheduler, Schedule


class AutomationScheduler(_AutomationScheduler):
    def finish(self, schedule_or_claim: str, claim_or_status: str, status_or_detail: str = "", detail: str = "", *, now: datetime | None = None) -> None:
        if claim_or_status in {"success", "retryable", "failed", "cancelled"}:
            claim = schedule_or_claim
            status = claim_or_status
            resolved_detail = status_or_detail
            with sqlite3.connect(self.path) as db:
                row = db.execute("SELECT schedule_id FROM runs WHERE id=?", (claim,)).fetchone()
                if row:
                    schedule_id = row[0]
                else:
                    row = db.execute("SELECT id FROM schedules WHERE claim=?", (claim,)).fetchone()
                    schedule_id = row[0] if row else None
            if schedule_id is None:
                raise KeyError("invalid scheduler claim")
            schedule = self._schedule_by_id(schedule_id, claim)
            if self.calendar_spec(schedule) is not None:
                return self.finish_calendar(schedule.id, claim, status, resolved_detail or detail, now=now)
            return super().finish(schedule.id, claim, status, resolved_detail or detail, now=now)
        schedule = self._schedule_by_id(schedule_or_claim, claim_or_status)
        if self.calendar_spec(schedule) is not None:
            return self.finish_calendar(schedule.id, claim_or_status, status_or_detail, detail, now=now)
        return super().finish(schedule_or_claim, claim_or_status, status_or_detail, detail, now=now)

    def _schedule_by_id(self, schedule_id: str, claim: str | None = None) -> Schedule:
        with sqlite3.connect(self.path) as db:
            query = (
                "SELECT id,task,interval_seconds,max_attempts,next_run,enabled,attempts "
                "FROM schedules WHERE id=?" + (" AND claim=?" if claim is not None else "")
            )
            row = db.execute(query, (schedule_id, claim) if claim is not None else (schedule_id,)).fetchone()
        if row is None:
            raise KeyError("invalid scheduler schedule")
        return Schedule(row[0], row[1], int(row[2]), int(row[3]), row[4], bool(row[5]), int(row[6]))

    def find_task(self, task: str) -> Schedule | None:
        """Return an exact schedule, migrating the legacy learning task once."""
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT id,task,interval_seconds,max_attempts,next_run,enabled,attempts "
                "FROM schedules WHERE task=? ORDER BY id LIMIT 1",
                (task,),
            ).fetchone()
            if row is not None:
                existing = Schedule(row[0], row[1], int(row[2]), int(row[3]), row[4], bool(row[5]), int(row[6]))
                try:
                    payload = json.loads(task)
                except (TypeError, json.JSONDecodeError):
                    payload = None
                if isinstance(payload, dict) and payload.get("kind") == "adaptive_learning":
                    rows = db.execute("SELECT id,task,interval_seconds,max_attempts,next_run,enabled,attempts FROM schedules WHERE enabled=1 ORDER BY id").fetchall()
                    for candidate_row in rows:
                        candidate = Schedule(candidate_row[0], candidate_row[1], int(candidate_row[2]), int(candidate_row[3]), candidate_row[4], bool(candidate_row[5]), int(candidate_row[6]))
                        spec = self.calendar_spec(candidate)
                        if spec is not None and self.task_payload(candidate).get("task") == task:
                            return candidate
                    if existing.enabled:
                        db.execute("UPDATE schedules SET enabled=0,claim=NULL WHERE id=?", (existing.id,))
        try:
            payload = json.loads(task)
        except (TypeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("kind") == "adaptive_learning":
            return self.add_last_day_of_month(task)
        return None

    def disable_task(self, task: str) -> int:
        """Disable all exact-match schedules without deleting their history."""
        with sqlite3.connect(self.path) as db:
            updated = db.execute("UPDATE schedules SET enabled=0,claim=NULL WHERE task=?", (task,)).rowcount
        return int(updated)

    @staticmethod
    def _calendar_timezone(name: str) -> object:
        if not name or name == "local":
            return datetime.now().astimezone().tzinfo or timezone.utc
        try:
            return ZoneInfo(name)
        except Exception as exc:
            raise ValueError(f"invalid timezone: {name}") from exc

    @classmethod
    def last_day_datetime(cls, *, at_time: str = "02:00", timezone_name: str = "local", now: datetime | None = None) -> datetime:
        """Return the next last-calendar-day occurrence as an aware UTC datetime."""
        try:
            hour, minute = (int(part) for part in at_time.split(":", 1))
            scheduled_time = time(hour=hour, minute=minute)
        except (TypeError, ValueError) as exc:
            raise ValueError("at_time must be HH:MM") from exc
        tz = cls._calendar_timezone(timezone_name)
        current = (now or datetime.now(timezone.utc)).astimezone(tz)
        year, month = current.year, current.month
        day = calendar.monthrange(year, month)[1]
        candidate = datetime.combine(date(year, month, day), scheduled_time, tzinfo=tz)
        if candidate <= current:
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1
            day = calendar.monthrange(year, month)[1]
            candidate = datetime.combine(date(year, month, day), scheduled_time, tzinfo=tz)
        return candidate.astimezone(timezone.utc)

    @staticmethod
    def calendar_spec(schedule: Schedule) -> dict[str, str] | None:
        try:
            value = json.loads(schedule.task)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict) or value.get("schedule") != "last_day_of_month":
            return None
        at_time = value.get("at_time", "02:00")
        timezone_name = value.get("timezone", "local")
        if not isinstance(at_time, str) or not isinstance(timezone_name, str):
            return None
        return {"at_time": at_time, "timezone": timezone_name}

    @staticmethod
    def task_payload(schedule: Schedule) -> dict[str, object]:
        try:
            value = json.loads(schedule.task)
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def add_last_day_of_month(
        self,
        task: str,
        *,
        at_time: str = "02:00",
        timezone_name: str = "local",
        max_attempts: int = 3,
        retry_interval_seconds: int = 900,
        start: datetime | None = None,
    ) -> Schedule:
        """Add a durable monthly schedule whose successful runs advance by calendar month."""
        if retry_interval_seconds < 1:
            raise ValueError("retry_interval_seconds must be positive")
        due = start or self.last_day_datetime(at_time=at_time, timezone_name=timezone_name)
        payload = json.dumps({
            "schedule": "last_day_of_month",
            "task": task,
            "at_time": at_time,
            "timezone": timezone_name,
        }, sort_keys=True)
        return super().add(payload, retry_interval_seconds, max_attempts=max_attempts, start=due)

    def next_calendar_run(self, schedule: Schedule, *, now: datetime | None = None) -> datetime | None:
        spec = self.calendar_spec(schedule)
        if spec is None:
            return None
        return self.last_day_datetime(at_time=spec["at_time"], timezone_name=spec["timezone"], now=now)

    def finish_calendar(self, schedule_id: str, claim: str, status: str, detail: str = "", *, now: datetime | None = None) -> None:
        """Finish a run and advance a monthly schedule only after success."""
        finished_at = now or datetime.now(timezone.utc)
        schedule = self._schedule_by_id(schedule_id, claim)
        super().finish(schedule_id, claim, status, detail, now=finished_at)
        if status == "success" and self.calendar_spec(schedule) is not None:
            next_run = self.next_calendar_run(schedule, now=finished_at)
            if next_run is not None:
                with sqlite3.connect(self.path) as db:
                    db.execute("UPDATE schedules SET next_run=?,enabled=1,attempts=0 WHERE id=?", (next_run.isoformat(), schedule_id))

    def recent_runs(self, schedule_id: str, limit: int = 20) -> list[dict[str, str | None]]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT id,schedule_id,started_at,finished_at,status,detail FROM runs WHERE schedule_id=? ORDER BY finished_at DESC LIMIT ?",
                (schedule_id, limit),
            ).fetchall()
        return [dict(id=r[0], schedule_id=r[1], started_at=r[2], finished_at=r[3], status=r[4], detail=r[5]) for r in rows]

    def close(self) -> None:
        return None


__all__ = ["AutomationScheduler", "Schedule"]
