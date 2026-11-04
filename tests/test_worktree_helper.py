from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / ".ai-harness" / "worktree.py"
SPEC = importlib.util.spec_from_file_location("auren_worktree_helper", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
worktree = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worktree)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True, capture_output=True, check=False, shell=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def setup_repo(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    git(root, "config", "user.name", "AUREN Test")
    git(root, "config", "user.email", "auren-test@example.invalid")
    (root / "src").mkdir()
    (root / "src" / "base.py").write_text("value = 1\n", encoding="utf-8")
    git(root, "add", "src/base.py")
    git(root, "commit", "-qm", "initial")
    return root


def configure(root: Path, tmp_path: Path):
    worktree.ROOT = root
    worktree.WORKTREES = root / ".ai-harness" / "worktrees"
    worktree.STATE = root / ".ai-harness" / "worktrees.json"


def test_create_persists_full_base_and_task_scope_then_resumes(tmp_path):
    root = setup_repo(tmp_path)
    configure(root, tmp_path)
    intent = "a" * 64
    budget = "b" * 64
    record = worktree.create(
        "worker-a", task_id="task-1", intent_digest=intent,
        budget_digest=budget, owned_paths=("src/feature.py",),
    )
    assert record["status"] == "active"
    assert len(record["base_sha"]) == 40
    persisted = json.loads(worktree.STATE.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == 2
    assert persisted["worktrees"]["worker-a"]["base_sha"] == record["base_sha"]

    workspace = Path(record["path"])
    (workspace / "src" / "feature.py").write_text("answer = 42\n", encoding="utf-8")
    resumed = worktree.resume(
        "worker-a", task_id="task-1", intent_digest=intent,
        budget_digest=budget, owned_paths=("src/feature.py",),
    )
    assert resumed["base_sha"] == record["base_sha"]
    assert resumed["status"] == "active"


def test_resume_rejects_changed_budget_scope_or_out_of_scope_files(tmp_path):
    root = setup_repo(tmp_path)
    configure(root, tmp_path)
    intent, budget = "a" * 64, "b" * 64
    record = worktree.create(
        "worker-a", task_id="task-1", intent_digest=intent,
        budget_digest=budget, owned_paths=("src/feature.py",),
    )
    workspace = Path(record["path"])
    (workspace / "README.md").write_text("outside scope\n", encoding="utf-8")

    with pytest.raises(PermissionError, match="budget binding"):
        worktree.resume(
            "worker-a", task_id="task-1", intent_digest=intent,
            budget_digest="c" * 64, owned_paths=("src/feature.py",),
        )
    with pytest.raises(PermissionError, match="out-of-scope"):
        worktree.resume(
            "worker-a", task_id="task-1", intent_digest=intent,
            budget_digest=budget, owned_paths=("src/feature.py",),
        )


def test_duplicate_identity_and_cleanup_require_explicit_worker_stop(tmp_path):
    root = setup_repo(tmp_path)
    configure(root, tmp_path)
    record = worktree.create(
        "worker-a", task_id="task-1", intent_digest="a" * 64,
        budget_digest="b" * 64, owned_paths=("src/feature.py",),
    )
    with pytest.raises(ValueError, match="already exists"):
        worktree.create(
            "worker-a", task_id="task-2", intent_digest="a" * 64,
            budget_digest="b" * 64, owned_paths=("src/other.py",),
        )
    with pytest.raises(PermissionError, match="worker-stop confirmation"):
        worktree.remove("worker-a", force=True)
    removed = worktree.remove("worker-a", worker_stopped=True)
    assert removed["status"] == "cleaned"
    assert not Path(record["path"]).exists()


def test_tampered_registry_cannot_remove_a_worktree_outside_managed_root(tmp_path):
    root = setup_repo(tmp_path)
    configure(root, tmp_path)
    record = worktree.create(
        "worker-a", task_id="task-1", intent_digest="a" * 64,
        budget_digest="b" * 64, owned_paths=("src/feature.py",),
    )
    outside = tmp_path / "outside-worktree"
    outside.mkdir()
    records = worktree.load()
    records["worker-a"]["path"] = str(outside)
    worktree.save(records)

    with pytest.raises(PermissionError, match="escapes configured root"):
        worktree.remove("worker-a", force=True, worker_stopped=True)
    assert outside.is_dir()
    assert Path(record["path"]).exists()


def test_corrupt_registry_fails_closed_instead_of_becoming_empty(tmp_path):
    root = setup_repo(tmp_path)
    configure(root, tmp_path)
    worktree.STATE.parent.mkdir(parents=True)
    worktree.STATE.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt"):
        worktree.load()


def test_reconcile_reports_missing_and_orphaned_managed_worktrees(tmp_path):
    root = setup_repo(tmp_path)
    configure(root, tmp_path)
    record = worktree.create(
        "worker-a", task_id="task-1", intent_digest="a" * 64,
        budget_digest="b" * 64, owned_paths=("src/feature.py",),
    )
    # Remove the Git worktree out of band to simulate a missing directory while
    # retaining the durable owner record.
    git(root, "worktree", "remove", "--force", record["path"])
    report = worktree.reconcile()
    assert report["missing"] == [str(Path(record["path"]).resolve())]
    assert report["orphaned"] == []
