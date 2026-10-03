"""Compatibility exports for the canonical AUREN bounded loop runtime.

The implementation now lives in ``portable.feedback_loop`` so the portable
bundle has one ownership model. Existing callers importing this legacy path
continue to work without maintaining a second implementation.
"""

from portable.feedback_loop import (  # noqa: F401
    BoundedLoop,
    FeedbackLoop,
    FeedbackPolicy,
    LoopAction,
    LoopDefinition,
    LoopPass,
    LoopRunReceipt,
    LOOP_TERMINAL_STATES,
    VerificationResult,
)

__all__ = [
    "BoundedLoop",
    "FeedbackLoop",
    "FeedbackPolicy",
    "LoopAction",
    "LoopDefinition",
    "LoopPass",
    "LoopRunReceipt",
    "LOOP_TERMINAL_STATES",
    "VerificationResult",
]
