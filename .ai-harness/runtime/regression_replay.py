#!/usr/bin/env python3
"""Deterministic replay gate for proposed AUREN policies."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable


@dataclass(frozen=True, slots=True)
class ReplayCase:
    case_id: str
    task_class: str
    expected_success: bool
    expected_verification: bool = True


@dataclass(frozen=True, slots=True)
class ReplayResult:
    passed: bool
    cases: int
    failures: tuple[str, ...]


def replay(cases: Iterable[ReplayCase], runner: Callable[[ReplayCase], tuple[bool, bool]]) -> ReplayResult:
    failures: list[str] = []
    count = 0
    for case in cases:
        count += 1
        success, verified = runner(case)
        if success != case.expected_success or verified != case.expected_verification:
            failures.append(case.case_id)
    return ReplayResult(not failures and count > 0, count, tuple(failures))
