"""Public facade for the AUREN final-output quality gate."""
from typing import Any

from .agent_capabilities import OutputQualityGate as _OutputQualityGate, QualityResult


class OutputQualityGate(_OutputQualityGate):
    def evaluate(self, report: dict[str, Any] | None = None, **kwargs: Any) -> QualityResult:
        # ``report`` is evidence/context for compatibility; the deterministic
        # gate scores the explicit acceptance/verification inputs.
        return super().evaluate(**kwargs)


__all__ = ["OutputQualityGate", "QualityResult"]
