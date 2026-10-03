"""Public facade for the unified AUREN capability fabric.

This facade preserves a small compatibility surface for callers that use the
older ``CapabilityFabric.default()`` construction pattern while keeping the
single implementation in ``agent_capabilities``.
"""
from .agent_capabilities import CAPABILITIES, Capability, CapabilityFabric as _CapabilityFabric, ProviderAdapter, ProviderAdapterRegistry


class CapabilityFabric(_CapabilityFabric):
    @classmethod
    def default(cls) -> "CapabilityFabric":
        return cls()


__all__ = ["CAPABILITIES", "Capability", "CapabilityFabric", "ProviderAdapter", "ProviderAdapterRegistry"]
