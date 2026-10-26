"""Compatibility import for the canonical portable extension policy."""
from portable.extension_policy import (
    ExtensionInvocationDenied,
    evaluate_invocation,
    invoke_extension,
)

__all__ = ["ExtensionInvocationDenied", "evaluate_invocation", "invoke_extension"]
