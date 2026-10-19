"""Dependency-aware planning for parallel Jira work without another task database.

This module validates a supplied Jira batch and derives safe ready/parallel slices.
The caller remains responsible for sourcing Jira records and persisting canonical
execution state through AUREN's Engineering State Ledger.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


class JiraPlanError(ValueError):
    """A Jira batch is ambiguous, invalid, or has cyclic dependencies."""


@dataclass(frozen=True, slots=True)
class JiraWorkItem:
    key: str
    acceptance_criteria: tuple[str, ...]
    depends_on: tuple[str, ...] = ()
    likely_paths: tuple[str, ...] = ()
    risk: str = "normal"

    def __post_init__(self) -> None:
        key = self.key.strip().upper()
        if not key or "-" not in key or key.startswith("-") or key.endswith("-"):
            raise JiraPlanError(f"invalid Jira key: {self.key!r}")
        if not self.acceptance_criteria or any(not item.strip() for item in self.acceptance_criteria):
            raise JiraPlanError(f"{key} requires non-empty acceptance criteria")
        if self.risk not in {"low", "normal", "high", "critical"}:
            raise JiraPlanError(f"{key} has unsupported risk tier: {self.risk}")
        dependencies = tuple(dict.fromkeys(item.strip().upper() for item in self.depends_on))
        if key in dependencies:
            raise JiraPlanError(f"{key} cannot depend on itself")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "depends_on", dependencies)
        object.__setattr__(self, "acceptance_criteria", tuple(item.strip() for item in self.acceptance_criteria))
        object.__setattr__(self, "likely_paths", tuple(sorted({
            item.strip().replace("\\", "/").strip("/")
            for item in self.likely_paths if item.strip()
        })))


@dataclass(frozen=True, slots=True)
class JiraExecutionWave:
    index: int
    parallel_groups: tuple[tuple[str, ...], ...]
    blocked: Mapping[str, tuple[str, ...]]
    shared_path_conflicts: Mapping[str, tuple[str, ...]]


class JiraBatchPlanner:
    """Compute dependency-ready work and conservative file ownership groups."""

    def __init__(self, items: Iterable[JiraWorkItem]) -> None:
        materialized = tuple(items)
        self.items = {item.key: item for item in materialized}
        if len(self.items) != len(materialized):
            raise JiraPlanError("duplicate Jira keys in batch")
        for item in self.items.values():
            unknown = tuple(dep for dep in item.depends_on if dep not in self.items)
            if unknown:
                raise JiraPlanError(f"{item.key} references missing dependencies: {', '.join(unknown)}")
        self._assert_acyclic()

    def _assert_acyclic(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise JiraPlanError(f"dependency cycle detected at {key}")
            if key in visited:
                return
            visiting.add(key)
            for dependency in self.items[key].depends_on:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

        for key in sorted(self.items):
            visit(key)

    def blocked_dependencies(self, completed: Iterable[str]) -> dict[str, tuple[str, ...]]:
        done = {item.strip().upper() for item in completed}
        unknown = done - self.items.keys()
        if unknown:
            raise JiraPlanError(f"completed keys are not in this batch: {', '.join(sorted(unknown))}")
        return {
            key: tuple(dep for dep in item.depends_on if dep not in done)
            for key, item in sorted(self.items.items())
            if key not in done and any(dep not in done for dep in item.depends_on)
        }

    def ready_items(self, completed: Iterable[str] = ()) -> tuple[str, ...]:
        done = {item.strip().upper() for item in completed}
        if done - self.items.keys():
            raise JiraPlanError(f"completed keys are not in this batch: {', '.join(sorted(done - self.items.keys()))}")
        return tuple(
            key for key, item in sorted(self.items.items())
            if key not in done and all(dep in done for dep in item.depends_on)
        )

    def shared_path_conflicts(self, keys: Sequence[str] | None = None) -> dict[str, tuple[str, ...]]:
        selected = tuple(sorted(set(key.strip().upper() for key in keys))) if keys is not None else tuple(sorted(self.items))
        unknown = set(selected) - self.items.keys()
        if unknown:
            raise JiraPlanError(f"unknown Jira keys: {', '.join(sorted(unknown))}")
        owners: dict[str, list[str]] = {}
        for key in selected:
            for path in self.items[key].likely_paths:
                owners.setdefault(path, []).append(key)
        return {
            path: tuple(sorted(path_owners))
            for path, path_owners in sorted(owners.items())
            if len(path_owners) > 1
        }

    def parallel_groups(self, keys: Sequence[str] | None = None) -> tuple[tuple[str, ...], ...]:
        """Greedily pack independent work into groups with disjoint known paths.

        Missing path estimates do not establish independence; such items are isolated
        in their own group until impact analysis supplies a better ownership estimate.
        """
        selected = tuple(sorted(set(key.strip().upper() for key in keys))) if keys is not None else tuple(sorted(self.items))
        unknown = set(selected) - self.items.keys()
        if unknown:
            raise JiraPlanError(f"unknown Jira keys: {', '.join(sorted(unknown))}")
        groups: list[list[str]] = []
        path_sets: list[set[str]] = []
        for key in selected:
            paths = set(self.items[key].likely_paths)
            if not paths:
                groups.append([key])
                path_sets.append(set())
                continue
            target = next((i for i, existing in enumerate(path_sets) if existing and not (existing & paths)), None)
            if target is None:
                groups.append([key])
                path_sets.append(paths)
            else:
                groups[target].append(key)
                path_sets[target].update(paths)
        return tuple(tuple(group) for group in groups)

    def next_wave(self, completed: Iterable[str] = ()) -> JiraExecutionWave:
        done = {item.strip().upper() for item in completed}
        ready = self.ready_items(done)
        return JiraExecutionWave(
            index=1,
            parallel_groups=self.parallel_groups(ready),
            blocked=self.blocked_dependencies(done),
            shared_path_conflicts=self.shared_path_conflicts(ready),
        )


__all__ = ["JiraBatchPlanner", "JiraExecutionWave", "JiraPlanError", "JiraWorkItem"]
