"""Provider-neutral dependency-aware task planning."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

STATUSES = {"pending", "in-progress", "blocked", "done", "cancelled"}
PRIORITIES = {"high", "medium", "low"}

@dataclass
class Task:
    id: str
    title: str
    description: str = ""
    status: str = "pending"
    priority: str = "medium"
    dependencies: list[str] = field(default_factory=list)
    subtasks: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    parallel_group: str = ""
    checkpoint: str = ""
    verification_strategy: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not self.id or not self.title:
            raise ValueError("task id and title are required")
        if self.status not in STATUSES:
            raise ValueError(f"invalid task status: {self.status}")
        if self.priority not in PRIORITIES:
            raise ValueError(f"invalid task priority: {self.priority}")

class TaskPlan:
    def __init__(self, tasks: Iterable[Task] = ()) -> None:
        rows = list(tasks)
        self.tasks = {task.id: task for task in rows}
        if len(self.tasks) != len(rows):
            raise ValueError("duplicate task id")
        self.validate()

    def validate(self) -> None:
        for task in self.tasks.values():
            task.validate()
            missing = sorted(set(task.dependencies) - self.tasks.keys())
            if missing:
                raise ValueError(f"task {task.id} has missing dependencies: {missing}")
            if task.id in task.dependencies:
                raise ValueError(f"task {task.id} depends on itself")
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError("task dependency cycle detected")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dep in self.tasks[task_id].dependencies:
                visit(dep)
            visiting.remove(task_id)
            visited.add(task_id)
        for task_id in self.tasks:
            visit(task_id)

    def ready(self, tag: str | None = None) -> list[Task]:
        result = []
        for task in self.tasks.values():
            if task.status != "pending" or (tag and tag not in task.tags):
                continue
            if all(self.tasks[d].status == "done" for d in task.dependencies):
                result.append(task)
        priority = {"high": 0, "medium": 1, "low": 2}
        return sorted(result, key=lambda t: (priority[t.priority], t.id))

    def parallel_ready(self, tag: str | None = None) -> list[Task]:
        ready = self.ready(tag)
        groups: dict[str, list[Task]] = {}
        for task in ready:
            if task.parallel_group:
                groups.setdefault(task.parallel_group, []).append(task)
        return [task for group in sorted(groups) for task in sorted(groups[group], key=lambda t: t.id)]

    def next(self, tag: str | None = None) -> Task | None:
        ready = self.ready(tag)
        return ready[0] if ready else None

    def to_dict(self) -> dict:
        return {"tasks": [asdict(task) for task in sorted(self.tasks.values(), key=lambda t: t.id)]}

    def digest(self) -> str:
        """Return a stable digest of task definitions, excluding mutable run state."""
        self.validate()
        rows = []
        for task in sorted(self.tasks.values(), key=lambda item: item.id):
            metadata = {key: value for key, value in task.metadata.items() if key != "execution_output"}
            rows.append({
                "id": task.id, "title": task.title, "description": task.description,
                "priority": task.priority, "dependencies": list(task.dependencies),
                "subtasks": list(task.subtasks), "tags": list(task.tags),
                "acceptance": list(task.acceptance), "files": list(task.files),
                "metadata": metadata, "parallel_group": task.parallel_group,
                "checkpoint": task.checkpoint,
                "verification_strategy": list(task.verification_strategy),
            })
        try:
            payload = json.dumps(
                {"tasks": rows}, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("TaskPlan definitions must be finite JSON-compatible values") from exc
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def from_dict(cls, raw: dict) -> "TaskPlan":
        rows = raw.get("tasks", []) if isinstance(raw, dict) else []
        if not isinstance(rows, list):
            raise ValueError("tasks must be an array")
        return cls([Task(**row) for row in rows])

    @classmethod
    def load(cls, path: Path | str) -> "TaskPlan":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path: Path | str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

__all__ = ["Task", "TaskPlan", "STATUSES", "PRIORITIES"]
