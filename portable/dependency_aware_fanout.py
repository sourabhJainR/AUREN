"""Dependency-aware bounded fan-out using AUREN's canonical TaskPlan and guard.

Only explicitly declared read-only tasks may run concurrently. All other tasks
run serially. The caller supplies the existing task-wide RuntimeExecutionGuard;
this module never creates or resets a budget.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Callable, Mapping

from .runtime_recovery import ExecutionBudgetExceeded, RuntimeExecutionGuard
from .task_planner import Task, TaskPlan


@dataclass
class FanoutResult:
    statuses: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    dispatch_order: list[str] = field(default_factory=list)
    max_observed_concurrency: int = 0

    @property
    def succeeded(self) -> bool:
        return bool(self.statuses) and all(value == "done" for value in self.statuses.values())


class DependencyAwareFanout:
    """Execute a TaskPlan while respecting dependencies and one shared budget."""

    def __init__(
        self,
        plan: TaskPlan,
        execute: Callable[[Task], Any],
        *,
        execution_guard: RuntimeExecutionGuard,
        max_workers: int = 4,
    ) -> None:
        if not isinstance(plan, TaskPlan):
            raise TypeError("plan must be the canonical TaskPlan")
        if not callable(execute):
            raise TypeError("execute must be callable")
        if not isinstance(execution_guard, RuntimeExecutionGuard):
            raise TypeError("execution_guard must be the shared RuntimeExecutionGuard")
        if type(max_workers) is not int or not 1 <= max_workers <= 16:
            raise ValueError("max_workers must be an integer between 1 and 16")
        self.plan = plan
        self.execute = execute
        self.guard = execution_guard
        self.max_workers = max_workers

    @staticmethod
    def _is_read_only(task: Task) -> bool:
        return task.metadata.get("execution_mode") == "read_only"

    def run(self) -> FanoutResult:
        result = FanoutResult()
        for task_id, task in self.plan.tasks.items():
            result.statuses[task_id] = "done" if task.status == "done" else (
                "blocked" if task.status in {"blocked", "cancelled"} else "pending"
            )
            if task.status == "done":
                result.outputs[task_id] = task.metadata.get("execution_output")

        active: dict[Future[Any], Task] = {}
        lock = Lock()
        active_count = 0

        def run_one(task: Task) -> Any:
            nonlocal active_count
            self.guard.check()
            self.guard.charge_step()
            if not self.guard.charge_attempt("task:" + task.id):
                raise ExecutionBudgetExceeded(f"task attempt already charged: {task.id}")
            with lock:
                active_count += 1
                result.max_observed_concurrency = max(
                    result.max_observed_concurrency, active_count
                )
            try:
                self.guard.check()
                return self.execute(task)
            finally:
                with lock:
                    active_count -= 1

        with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="auren-fanout") as pool:
            while True:
                cancelled = self.guard.cancellation_event.is_set()
                if cancelled:
                    for task in self.plan.tasks.values():
                        if result.statuses[task.id] == "pending":
                            result.statuses[task.id] = "blocked"
                            result.errors[task.id] = self.guard.cancellation_reason or "task cancelled"
                    if not active:
                        break

                # Propagate dependency failure without launching downstream nodes.
                for task in self.plan.tasks.values():
                    if result.statuses[task.id] != "pending":
                        continue
                    if any(result.statuses.get(dep) in {"failed", "blocked"} for dep in task.dependencies):
                        result.statuses[task.id] = "blocked"
                        result.errors[task.id] = "dependency did not complete successfully"

                ready = [
                    task for task in self.plan.ready()
                    if result.statuses[task.id] == "pending"
                    and all(result.statuses.get(dep) == "done" for dep in task.dependencies)
                ]
                # Do not launch two mutable/serial tasks together. Read-only
                # tasks may fan out only up to the remaining worker capacity.
                available = self.max_workers - len(active)
                if not cancelled and available > 0 and ready:
                    read_only = [task for task in ready if self._is_read_only(task)]
                    if read_only:
                        for task in read_only[:available]:
                            result.statuses[task.id] = "running"
                            result.dispatch_order.append(task.id)
                            active[pool.submit(run_one, task)] = task
                    elif not active:
                        task = ready[0]
                        result.statuses[task.id] = "running"
                        result.dispatch_order.append(task.id)
                        active[pool.submit(run_one, task)] = task

                if not active:
                    pending = [key for key, value in result.statuses.items() if value == "pending"]
                    if not pending:
                        break
                    # No runnable task means the plan is blocked by non-success
                    # prerequisites or a pre-existing nonterminal state.
                    for task_id in pending:
                        result.statuses[task_id] = "blocked"
                        result.errors[task_id] = "no runnable dependency-ready task"
                    break

                done, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in done:
                    task = active.pop(future)
                    try:
                        output = future.result()
                    except BaseException as exc:
                        result.statuses[task.id] = "failed"
                        result.errors[task.id] = f"{type(exc).__name__}: {str(exc)[:500]}"
                        # Budget exhaustion is a task-wide stop condition.
                        if isinstance(exc, ExecutionBudgetExceeded):
                            self.guard.cancel(f"fan-out budget exhausted at {task.id}")
                    else:
                        result.statuses[task.id] = "done"
                        result.outputs[task.id] = output
                        task.status = "done"
                        task.metadata["execution_output"] = output

        return result


__all__ = ["DependencyAwareFanout", "FanoutResult"]
