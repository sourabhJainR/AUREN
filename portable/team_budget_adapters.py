"""Fail-closed shared-budget mediation for provider and tool adapters.

The adapter context and usage receipt are resolved by host-owned callbacks. Request
payloads cannot select a team, member, budget, reservation size, or usage metric.
Unknown outcomes retain their reservation; only the existing trusted settlement
verifier can convert a host receipt into charged usage.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .agent_execution_guard import current_agent_assignment_principal_id


class TeamBudgetAdapterDenied(PermissionError):
    """An assigned adapter call could not prove shared-budget admission or settlement."""


class TeamBudgetAdapterEnforcer:
    """Mediates one external operation against the controller's shared team ledger.

    context_resolver must be a host-owned identity/policy lookup. It receives the
    active authenticated principal and adapter metadata, and returns the canonical
    task/run/member identity, stable operation key, and reservation ceilings.
    usage_evidence_resolver must return a host-authenticated receipt. The controller's
    configured team_budget_settlement_verifier independently validates that receipt.
    """

    _CONTEXT_FIELDS = {
        "task_id", "run_id", "member_id", "principal_id", "operation_key",
        "tokens", "tool_calls", "wall_seconds",
    }

    def __init__(
        self,
        controller: Any,
        *,
        context_resolver: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        usage_evidence_resolver: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> None:
        if not callable(getattr(controller, "reserve_team_budget", None)):
            raise TypeError("controller must provide reserve_team_budget")
        if not callable(getattr(controller, "settle_team_budget", None)):
            raise TypeError("controller must provide settle_team_budget")
        if not callable(context_resolver) or not callable(usage_evidence_resolver):
            raise TypeError("host context and usage evidence resolvers are required")
        self.controller = controller
        self.context_resolver = context_resolver
        self.usage_evidence_resolver = usage_evidence_resolver

    def execute(
        self,
        adapter_kind: str,
        adapter_name: str,
        request: Mapping[str, Any],
        invoke: Callable[[], Any],
    ) -> Any:
        """Reserve before invoking; settle before returning a successful result.

        Non-assigned calls are outside a team member's authority scope and pass
        through unchanged. An assigned call without a complete host binding is
        denied before the adapter runs. Exceptions and uncertain outcomes keep
        their reservation held for explicit reconciliation.
        """
        if not isinstance(adapter_kind, str) or adapter_kind not in {"provider", "tool"}:
            raise ValueError("adapter_kind must be provider or tool")
        if not isinstance(adapter_name, str) or not adapter_name.strip():
            raise ValueError("adapter_name is required")
        if not isinstance(request, Mapping) or not callable(invoke):
            raise TypeError("request mapping and invoke callback are required")

        principal_id = current_agent_assignment_principal_id()
        if principal_id is None:
            return invoke()

        lookup = {
            "adapter_kind": adapter_kind,
            "adapter_name": adapter_name.strip(),
            "request": dict(request),
            "principal_id": principal_id,
        }
        try:
            resolved = self.context_resolver(lookup)
        except Exception as exc:
            raise TeamBudgetAdapterDenied("host team-budget context resolution failed") from exc
        if not isinstance(resolved, Mapping) or set(resolved) != self._CONTEXT_FIELDS:
            raise TeamBudgetAdapterDenied("host team-budget context is incomplete")
        context = dict(resolved)
        if context.get("principal_id") != principal_id:
            raise TeamBudgetAdapterDenied("host team-budget context principal differs from active assignment")
        for field in ("task_id", "run_id", "member_id", "operation_key"):
            value = context.get(field)
            if not isinstance(value, str) or not value.strip():
                raise TeamBudgetAdapterDenied(f"host team-budget {field} is invalid")
        amounts: dict[str, int] = {}
        for field in ("tokens", "tool_calls", "wall_seconds"):
            value = context.get(field)
            if type(value) is not int or value < 0:
                raise TeamBudgetAdapterDenied(f"host team-budget {field} reservation is invalid")
            amounts[field] = value
        if not any(amounts.values()):
            raise TeamBudgetAdapterDenied("host team-budget reservation must be non-zero")

        try:
            reservation = self.controller.reserve_team_budget(
                context["task_id"], context["run_id"], context["member_id"],
                context["operation_key"], tokens=amounts["tokens"],
                tool_calls=amounts["tool_calls"], wall_seconds=amounts["wall_seconds"],
            )
        except Exception as exc:
            raise TeamBudgetAdapterDenied("shared team budget denied adapter dispatch") from exc
        if not isinstance(reservation, Mapping) or reservation.get("state") != "reserved":
            raise TeamBudgetAdapterDenied(
                "operation already has a terminal or uncertain reservation; replay requires its side-effect ledger"
            )

        # Deliberately do not release on exception. A timeout or raised exception
        # does not prove that the remote operation never started.
        result = invoke()
        evidence_request = {
            **context,
            "adapter_kind": adapter_kind,
            "adapter_name": adapter_name.strip(),
            "request": dict(request),
            "result": result,
        }
        try:
            usage_evidence = self.usage_evidence_resolver(evidence_request)
            if not isinstance(usage_evidence, Mapping) or not usage_evidence:
                raise ValueError("host usage evidence is missing")
            self.controller.settle_team_budget(
                context["task_id"], context["run_id"], context["operation_key"],
                usage_evidence=usage_evidence,
            )
        except Exception as exc:
            raise TeamBudgetAdapterDenied(
                "adapter returned but trusted usage settlement failed; reservation remains held"
            ) from exc
        return result
