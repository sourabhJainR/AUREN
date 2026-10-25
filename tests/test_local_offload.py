import time
from threading import Event, Thread
from portable.local_offload import LocalOffloadBroker, OffloadJob, ResourceBudget


def test_local_offload_runs_safe_command(tmp_path):
    broker = LocalOffloadBroker(tmp_path, budget=ResourceBudget(max_workers=1, timeout_seconds=10))
    result = broker.run(OffloadJob("one", ("python", "-c", "print('ok')")))
    assert result.status == "passed"
    assert "ok" in result.output


def test_local_offload_blocks_mutating_git(tmp_path):
    broker = LocalOffloadBroker(tmp_path, budget=ResourceBudget(max_workers=1, timeout_seconds=10))
    result = broker.run(OffloadJob("one", ("git", "push")))
    assert result.status == "rejected"
    assert "blocked" in (result.error or "")


def test_local_offload_runs_independent_jobs_in_parallel(tmp_path):
    broker = LocalOffloadBroker(tmp_path, budget=ResourceBudget(max_workers=2, timeout_seconds=10))
    jobs = [
        OffloadJob("one", ("python", "-c", "print('one')")),
        OffloadJob("two", ("python", "-c", "print('two')")),
    ]
    results = broker.run_many(jobs)
    assert [r.status for r in results] == ["passed", "passed"]
    assert {r.job_id for r in results} == {"one", "two"}


def test_local_offload_isolates_workspace(tmp_path):
    (tmp_path / "input.txt").write_text("original", encoding="utf-8")
    broker = LocalOffloadBroker(tmp_path, budget=ResourceBudget(max_workers=1, timeout_seconds=10))
    result = broker.run(
        OffloadJob(
            "one",
            ("python", "-c", "from pathlib import Path; Path('input.txt').write_text('changed')"),
            isolate=True,
            allow_write=True,
        )
    )
    assert result.status == "passed"
    assert (tmp_path / "input.txt").read_text(encoding="utf-8") == "original"

def test_local_offload_supervisor_cancellation_terminates_running_command(tmp_path):
    marker = tmp_path / "started.txt"
    broker = LocalOffloadBroker(tmp_path, budget=ResourceBudget(max_workers=1, timeout_seconds=10))
    job = OffloadJob(
        "cancel-running",
        ("python", "-c", f"from pathlib import Path; import time; Path({str(marker)!r}).write_text('started'); time.sleep(30)"),
    )
    cancellation = Event()
    results = []
    worker = Thread(
        target=lambda: results.append(
            broker.run_supervised(job, execution_payload={"auren_cancel_event": cancellation})
        ),
        daemon=True,
    )
    worker.start()
    end = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < end:
        time.sleep(0.01)
    assert marker.exists(), "offload command did not start"
    cancellation.set()
    worker.join(timeout=5)
    assert not worker.is_alive(), "cancelled offload command did not stop"
    assert len(results) == 1
    assert results[0].status == "cancelled"
    assert "terminated" in (results[0].error or "")
    capacity = broker.capacity()
    assert capacity["active_jobs"] == 0
    assert capacity["queued_jobs"] == 0


def test_local_offload_supervisor_deadline_caps_command_runtime(tmp_path):
    marker = tmp_path / "started.txt"
    broker = LocalOffloadBroker(tmp_path, budget=ResourceBudget(max_workers=1, timeout_seconds=10))
    job = OffloadJob(
        "deadline-running",
        ("python", "-c", f"from pathlib import Path; import time; Path({str(marker)!r}).write_text('started'); time.sleep(30)"),
    )
    result = broker.run_supervised(
        job, execution_payload={"auren_deadline_epoch": time.time() + 0.3},
    )
    assert result.status == "timeout"
    assert result.duration_seconds < 3
    assert marker.exists()
    assert broker.capacity()["active_jobs"] == 0


def test_local_offload_supervisor_stops_before_launch_when_cancelled_or_expired(tmp_path):
    marker = tmp_path / "must-not-run.txt"
    command = ("python", "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')")
    broker = LocalOffloadBroker(tmp_path, budget=ResourceBudget(max_workers=1, timeout_seconds=10))
    cancellation = Event()
    cancellation.set()
    cancelled = broker.run_supervised(
        OffloadJob("pre-cancel", command),
        execution_payload={"auren_cancel_event": cancellation},
    )
    expired = broker.run_supervised(
        OffloadJob("pre-expired", command),
        execution_payload={"auren_deadline_epoch": time.time() - 1},
    )
    assert cancelled.status == "cancelled"
    assert expired.status == "timeout"
    assert not marker.exists()
    capacity = broker.capacity()
    assert capacity["active_jobs"] == 0
    assert capacity["queued_jobs"] == 0
    assert capacity["completed_jobs"] == 2
