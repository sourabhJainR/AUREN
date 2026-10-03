"""Executable deterministic interactive benchmark environments for AUREN."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence


class InteractiveEnvironment(Protocol):
    def reset(self) -> Mapping[str, Any]: ...
    def actions(self) -> tuple[str, ...]: ...
    def step(self, action: str) -> tuple[Mapping[str, Any], float, bool]: ...


@dataclass(frozen=True)
class EpisodeResult:
    task_id: str
    success: bool
    reward: float
    steps: int
    recovery_count: int
    action_trace: tuple[str, ...]
    observation_trace: tuple[Mapping[str, Any], ...]
    digest: str


@dataclass(frozen=True)
class BenchmarkSuiteResult:
    episodes: tuple[EpisodeResult, ...]
    success_rate: float
    transfer_rate: float | None
    recovery_rate: float
    mean_steps: float
    generalization_gap: float | None
    digest: str


class InteractiveBenchmark:
    """Run bounded policy/environment interaction with deterministic traces."""

    def __init__(self, *, max_steps: int = 32) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.max_steps = max_steps

    def run_episode(self, task_id: str, environment: InteractiveEnvironment,
                    policy: Callable[[Mapping[str, Any], Sequence[str]], str]) -> EpisodeResult:
        if not task_id.strip() or not callable(policy):
            raise ValueError("task_id and policy are required")
        observation = dict(environment.reset())
        observations = [dict(observation)]
        actions: list[str] = []
        reward = 0.0
        recovery_count = 0
        success = False
        for _ in range(self.max_steps):
            available = tuple(environment.actions())
            if not available:
                break
            action = policy(dict(observation), available)
            if action not in available:
                raise ValueError(f"policy selected unavailable action: {action}")
            try:
                observation, step_reward, done = environment.step(action)
            except Exception:
                recovery_count += 1
                observation = {"error": "environment_step_failed", "available_actions": available}
                observations.append(dict(observation))
                continue
            actions.append(action)
            reward += float(step_reward)
            observations.append(dict(observation))
            if bool(observation.get("recovered", False)):
                recovery_count += 1
            if done:
                success = bool(observation.get("success", step_reward > 0))
                break
        canonical = {"task_id": task_id, "success": success, "reward": reward, "steps": len(actions),
                     "recovery_count": recovery_count, "actions": actions, "observations": observations}
        digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
        return EpisodeResult(task_id, success, reward, len(actions), recovery_count,
                             tuple(actions), tuple(observations), digest)

    def run_suite(self, tasks: Sequence[tuple[str, InteractiveEnvironment]],
                  policy: Callable[[Mapping[str, Any], Sequence[str]], str],
                  *, training_count: int = 0) -> BenchmarkSuiteResult:
        if training_count < 0 or training_count > len(tasks):
            raise ValueError("training_count must be between 0 and task count")
        episodes = tuple(self.run_episode(task_id, environment, policy) for task_id, environment in tasks)
        training = episodes[:training_count]
        hidden = episodes[training_count:]
        success_rate = sum(item.success for item in episodes) / len(episodes) if episodes else 0.0
        transfer_rate = sum(item.success for item in hidden) / len(hidden) if hidden else None
        recovery_rate = sum(item.recovery_count > 0 for item in episodes) / len(episodes) if episodes else 0.0
        mean_steps = sum(item.steps for item in episodes) / len(episodes) if episodes else 0.0
        generalization_gap = (sum(item.success for item in training) / len(training) - transfer_rate) if training and transfer_rate is not None else None
        canonical = [{"task_id": item.task_id, "success": item.success, "reward": item.reward,
                      "steps": item.steps, "recovery_count": item.recovery_count, "digest": item.digest}
                     for item in episodes]
        digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return BenchmarkSuiteResult(episodes, round(success_rate, 6), round(transfer_rate, 6) if transfer_rate is not None else None,
                                    round(recovery_rate, 6), round(mean_steps, 6), round(generalization_gap, 6) if generalization_gap is not None else None,
                                    digest)


class KeyDoorEnvironment:
    """Small hidden-layout task family for interactive transfer benchmarking."""

    def __init__(self, layout: tuple[str, ...], *, task_seed: int = 0) -> None:
        if not layout or len(set(layout)) != len(layout):
            raise ValueError("layout must contain unique non-empty actions")
        if any(not isinstance(item, str) or not item for item in layout):
            raise ValueError("layout actions must be non-empty strings")
        self._layout = tuple(layout)
        self._seed = int(task_seed)
        self._index = 0
        self._unlocked = False

    def reset(self) -> Mapping[str, Any]:
        self._index = 0
        self._unlocked = False
        return {"location": "door", "locked": True, "sequence_progress": 0, "seed_hint": self._seed % 3}

    def actions(self) -> tuple[str, ...]:
        candidates = tuple(dict.fromkeys((*self._layout, "open", "wait")))
        return candidates

    def step(self, action: str) -> tuple[Mapping[str, Any], float, bool]:
        if action not in self.actions():
            raise ValueError("invalid action")
        if action == "wait":
            return ({"location": "door", "locked": not self._unlocked, "sequence_progress": self._index, "recovered": True}, -0.1, False)
        if self._index < len(self._layout) and action == self._layout[self._index]:
            self._index += 1
            if self._index == len(self._layout):
                self._unlocked = True
                return ({"location": "door", "locked": False, "sequence_progress": self._index}, 1.0, False)
            return ({"location": "keypad", "locked": True, "sequence_progress": self._index}, 0.2, False)
        if action == "open":
            if self._unlocked:
                return ({"location": "inside", "locked": False, "sequence_progress": self._index, "success": True}, 2.0, True)
            return ({"location": "door", "locked": True, "sequence_progress": self._index}, -0.5, False)
        self._index = max(0, self._index - 1)
        return ({"location": "keypad", "locked": True, "sequence_progress": self._index, "recovered": True}, -0.2, False)


__all__ = ["BenchmarkSuiteResult", "EpisodeResult", "InteractiveBenchmark", "InteractiveEnvironment", "KeyDoorEnvironment"]
