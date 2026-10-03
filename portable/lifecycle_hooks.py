"""Provider-neutral lifecycle hooks for AUREN.

Hooks are intentionally small and composable. Providers may map their native
hook system onto these phases, while AUREN can always run the same hooks itself.
A hook can observe or veto an action, but a non-authoritative hook cannot weaken
security, verification, or promotion policy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping


class HookPhase(str, Enum):
    SESSION_START = "session_start"
    PLAN_START = "plan_start"
    BEFORE_AGENT = "before_agent"
    AFTER_AGENT = "after_agent"
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    BEFORE_VERIFY = "before_verify"
    AFTER_VERIFY = "after_verify"
    BEFORE_PROMOTION = "before_promotion"
    AFTER_PROMOTION = "after_promotion"
    SESSION_END = "session_end"
    RECOVERY = "recovery"


@dataclass(frozen=True)
class HookEvent:
    phase: HookPhase
    session_id: str
    task_id: str | None = None
    provider: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HookDecision:
    allow: bool = True
    reason: str = ""
    annotations: Mapping[str, Any] = field(default_factory=dict)


HookHandler = Callable[[HookEvent], HookDecision | None]


class HookBus:
    """Ordered lifecycle hooks with fail-closed veto semantics."""

    def __init__(self) -> None:
        self._handlers: dict[HookPhase, list[HookHandler]] = {phase: [] for phase in HookPhase}

    def register(self, phase: HookPhase, handler: HookHandler) -> None:
        if not callable(handler):
            raise TypeError("hook handler must be callable")
        self._handlers[phase].append(handler)

    def emit(self, event: HookEvent) -> HookDecision:
        annotations: dict[str, Any] = {}
        for handler in tuple(self._handlers[event.phase]):
            try:
                decision = handler(event)
            except Exception as exc:
                return HookDecision(False, f"hook {getattr(handler, '__name__', repr(handler))} failed: {exc}")
            if decision is None:
                continue
            annotations.update(decision.annotations)
            if not decision.allow:
                return HookDecision(False, decision.reason, annotations)
        return HookDecision(True, "", annotations)

    def phases(self) -> tuple[HookPhase, ...]:
        return tuple(phase for phase, handlers in self._handlers.items() if handlers)


class HookedExecution:
    """Adapter used by orchestrators/providers to enforce lifecycle hooks."""

    def __init__(self, hooks: HookBus, session_id: str, provider: str | None = None) -> None:
        self.hooks = hooks
        self.session_id = session_id
        self.provider = provider

    def gate(self, phase: HookPhase, *, task_id: str | None = None, payload: Mapping[str, Any] | None = None) -> HookDecision:
        return self.hooks.emit(HookEvent(phase, self.session_id, task_id, self.provider, payload or {}))


__all__ = ["HookBus", "HookDecision", "HookEvent", "HookPhase", "HookedExecution"]
