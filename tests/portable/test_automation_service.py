from __future__ import annotations

import json
from datetime import datetime, timezone

from portable.automation_scheduler import AutomationScheduler
from portable.maintenance_service import MaintenanceServiceConfig, _launchd_plist, _linux_unit


def test_last_day_datetime_handles_month_length() -> None:
    now = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
    assert AutomationScheduler.last_day_datetime(now=now, timezone_name="UTC", at_time="02:00") == datetime(2026, 9, 30, 2, tzinfo=timezone.utc)

    now_after = datetime(2026, 9, 30, 3, tzinfo=timezone.utc)
    assert AutomationScheduler.last_day_datetime(now=now_after, timezone_name="UTC", at_time="02:00") == datetime(2026, 10, 31, 2, tzinfo=timezone.utc)

    february = datetime(2028, 2, 1, 12, tzinfo=timezone.utc)
    assert AutomationScheduler.last_day_datetime(now=february, timezone_name="UTC", at_time="02:00").day == 29


def test_legacy_learning_schedule_migrates_once(tmp_path) -> None:
    scheduler = AutomationScheduler(tmp_path / "automation.db")
    task = json.dumps({"kind": "adaptive_learning", "project_root": "/repo"}, sort_keys=True)
    legacy = scheduler.add(task, 300, start=datetime(2026, 9, 17, tzinfo=timezone.utc))

    migrated = scheduler.find_task(task)
    assert migrated is not None
    assert migrated.id != legacy.id
    assert scheduler.calendar_spec(migrated) == {"at_time": "02:00", "timezone": "local"}

    migrated_again = scheduler.find_task(task)
    assert migrated_again is not None
    assert migrated_again.id == migrated.id


def test_monthly_finish_advances_calendar_date(tmp_path) -> None:
    scheduler = AutomationScheduler(tmp_path / "automation.db")
    task = json.dumps({"kind": "adaptive_learning", "project_root": "/repo"}, sort_keys=True)
    scheduled = scheduler.add_last_day_of_month(task, at_time="02:00", timezone_name="UTC")
    when = datetime(2026, 9, 30, 2, tzinfo=timezone.utc)
    assert datetime.fromisoformat(scheduled.next_run) == when

    claim = scheduler.claim(scheduled.id, now=when)
    assert claim
    scheduler.finish_calendar(scheduled.id, claim, "success", "ok", now=when)
    refreshed = scheduler.find_task(json.dumps({"schedule": "last_day_of_month", "task": task, "at_time": "02:00", "timezone": "UTC"}, sort_keys=True))
    assert refreshed is None or refreshed.id != scheduled.id  # exact task lookup is intentionally scoped to base tasks
    next_row = scheduler.due(datetime(2026, 10, 31, 2, tzinfo=timezone.utc))
    assert any(item.id == scheduled.id for item in next_row)


def test_native_service_definitions_use_foreground_host(tmp_path) -> None:
    config = MaintenanceServiceConfig(project_root=tmp_path, scope="user")
    linux = _linux_unit(config)
    plist = _launchd_plist(config)
    assert "portable.maintenance_service" in linux
    assert "last_day_of_month" not in linux  # calendar truth lives in the durable scheduler
    assert "com.auren.AURENMaintenance" in plist
    assert "AUREN_MAINTENANCE_TIME" in plist
