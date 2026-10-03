"""External environment interaction contract.

Defines a modality/tool/horizon-neutral episode boundary so HWS can be tested
against environments it does not control. Environment implementations remain
external and execution authority stays with the host.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Iterable


_ALLOWED_MODALITIES = frozenset({"text", "image", "audio", "video", "structured"})
_ALLOWED_ACTIONS = frozenset({"read", "write", "tool_call", "navigate", "communicate"})


@dataclass(frozen=True, slots=True)
class EnvironmentContract:
    environment_id: str
    version: str
    observation_modalities: tuple[str, ...]
    action_types: tuple[str, ...]
    max_steps: int
    reset_between_cases: bool = True
    hidden_state: bool = True
    external_tools: tuple[str, ...] = ()
    unfamiliar_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.environment_id.strip() or not self.version.strip():
            raise ValueError("environment_id and version are required")
        if self.max_steps < 1:
            raise ValueError("max_steps must be positive")
        if not self.observation_modalities:
            raise ValueError("at least one observation modality is required")
        if not set(self.observation_modalities) <= _ALLOWED_MODALITIES:
            raise ValueError("unsupported observation modality")
        if not self.action_types or not set(self.action_types) <= _ALLOWED_ACTIONS:
            raise ValueError("unsupported action type")
        if len(set(self.observation_modalities)) != len(self.observation_modalities):
            raise ValueError("duplicate observation modality")
        if len(set(self.action_types)) != len(self.action_types):
            raise ValueError("duplicate action type")

    @property
    def contract_digest(self) -> str:
        payload = {
            "environment_id": self.environment_id,
            "version": self.version,
            "observation_modalities": self.observation_modalities,
            "action_types": self.action_types,
            "max_steps": self.max_steps,
            "reset_between_cases": self.reset_between_cases,
            "hidden_state": self.hidden_state,
            "external_tools": self.external_tools,
            "unfamiliar_tools": self.unfamiliar_tools,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class EpisodeTrace:
    episode_id: str
    environment_digest: str
    steps: int
    success: bool
    verified: bool
    evidence_ids: tuple[str, ...]
    tool_calls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.episode_id.strip() or not self.environment_digest.strip():
            raise ValueError("episode identity is required")
        if self.steps < 0:
            raise ValueError("steps cannot be negative")
        if not self.evidence_ids or len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("unique evidence ids are required")

    @property
    def trace_digest(self) -> str:
        payload = {
            "episode_id": self.episode_id,
            "environment_digest": self.environment_digest,
            "steps": self.steps,
            "success": self.success,
            "verified": self.verified,
            "evidence_ids": self.evidence_ids,
            "tool_calls": self.tool_calls,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class EnvironmentEvaluation:
    environment_digest: str
    episodes: tuple[EpisodeTrace, ...]
    long_horizon_success: bool
    multimodal_success: bool
    unfamiliar_tool_success: bool

    @property
    def verified_rate(self) -> float:
        return (
            sum(episode.verified for episode in self.episodes) / len(self.episodes)
            if self.episodes else 0.0
        )


class ExternalEnvironmentEvaluator:
    """Score traces emitted by an externally controlled environment."""

    def evaluate(
        self,
        contract: EnvironmentContract,
        traces: Iterable[EpisodeTrace],
    ) -> EnvironmentEvaluation:
        rows = tuple(traces)
        if not rows:
            raise ValueError("at least one episode trace is required")
        if any(row.environment_digest != contract.contract_digest for row in rows):
            raise ValueError("episode trace does not match environment contract")
        if any(row.steps > contract.max_steps for row in rows):
            raise ValueError("episode exceeds environment step budget")
        if contract.reset_between_cases and len({row.episode_id for row in rows}) != len(rows):
            raise ValueError("episode ids must be unique when reset is required")
        tool_success = bool(contract.external_tools) and any(row.tool_calls for row in rows)
        unfamiliar_tool_success = bool(contract.unfamiliar_tools) and any(
            set(row.tool_calls) & set(contract.unfamiliar_tools) for row in rows
        )
        multimodal_success = len(contract.observation_modalities) > 1 and any(row.success for row in rows)
        long_horizon_success = any(row.success and row.steps >= min(contract.max_steps, 10) for row in rows)
        return EnvironmentEvaluation(
            contract.contract_digest, rows, long_horizon_success,
            multimodal_success, unfamiliar_tool_success,
        )


__all__ = ["EnvironmentContract", "EpisodeTrace", "EnvironmentEvaluation", "ExternalEnvironmentEvaluator"]
