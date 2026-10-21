"""Versioned, data-only extension contracts for standalone AUREN integrations.

Registration describes a provider. It does not grant authority, load code, expose
secrets, or prove that a provider's claimed trust level is valid.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping

_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
_HEALTH = frozenset({"healthy", "degraded", "unavailable"})
_COST = frozenset({"low", "medium", "high"})
_TRUST = frozenset({"native", "verified", "external"})
_TRUST_ORDER = {"native": 0, "verified": 1, "external": 2}
_ALLOWED_FIELDS = frozenset({
    "name", "version", "capabilities", "health", "cost_class", "trust",
    "permissions", "contract_version", "output_limit_bytes", "timeout_seconds",
})


class ExtensionContractError(ValueError):
    """Raised when an extension manifest is invalid or conflicts with a registration."""


def _token_tuple(name: str, values: Any, *, required: bool) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise ExtensionContractError(f"{name} must be a tuple of strings")
    if required and not values:
        raise ExtensionContractError(f"{name} must not be empty")
    if any(isinstance(value, str) and value in {"*", "all", "admin", "unrestricted"} for value in values):
        raise ExtensionContractError(f"{name} contains a forbidden wildcard")
    if any(not isinstance(value, str) or not _NAME.fullmatch(value) for value in values):
        raise ExtensionContractError(f"{name} contains an invalid token")
    if len(set(values)) != len(values):
        raise ExtensionContractError(f"{name} values must be unique")
    return values


@dataclass(frozen=True, slots=True)
class ExtensionManifest:
    name: str
    version: str
    capabilities: tuple[str, ...]
    health: str = "healthy"
    cost_class: str = "medium"
    trust: str = "external"
    permissions: tuple[str, ...] = ()
    contract_version: str = "auren-extension-v1"
    output_limit_bytes: int = 65536
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME.fullmatch(self.name):
            raise ExtensionContractError("name must be a lowercase identifier")
        if not isinstance(self.version, str) or not _VERSION.fullmatch(self.version):
            raise ExtensionContractError("version must use semantic version syntax")
        if self.contract_version != "auren-extension-v1":
            raise ExtensionContractError("unsupported extension contract version")
        _token_tuple("capabilities", self.capabilities, required=True)
        _token_tuple("permissions", self.permissions, required=False)
        if self.health not in _HEALTH:
            raise ExtensionContractError("health must be healthy, degraded, or unavailable")
        if self.cost_class not in _COST:
            raise ExtensionContractError("cost_class must be low, medium, or high")
        if self.trust not in _TRUST:
            raise ExtensionContractError("trust must be native, verified, or external")
        if type(self.output_limit_bytes) is not int or not 1 <= self.output_limit_bytes <= 1_048_576:
            raise ExtensionContractError("output_limit_bytes must be between 1 and 1048576")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 300
        ):
            raise ExtensionContractError("timeout_seconds must be finite and in (0, 300]")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ExtensionManifest":
        if not isinstance(data, Mapping):
            raise ExtensionContractError("manifest must be a mapping")
        unknown = set(data) - _ALLOWED_FIELDS
        if unknown:
            raise ExtensionContractError("unknown manifest fields: " + ", ".join(sorted(unknown)))
        missing = {"name", "version", "capabilities"} - set(data)
        if missing:
            raise ExtensionContractError("missing manifest fields: " + ", ".join(sorted(missing)))
        values = dict(data)
        for key in ("capabilities", "permissions"):
            if key in values and isinstance(values[key], list):
                values[key] = tuple(values[key])
        return cls(**values)

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "name": self.name,
            "version": self.version,
            "capabilities": list(self.capabilities),
            "health": self.health,
            "cost_class": self.cost_class,
            "trust": self.trust,
            "permissions": list(self.permissions),
            "output_limit_bytes": self.output_limit_bytes,
            "timeout_seconds": float(self.timeout_seconds),
        }

    @property
    def digest(self) -> str:
        payload = json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ExtensionRegistry:
    """Deterministic registry for data-only manifests; it never invokes providers."""

    def __init__(self) -> None:
        self._manifests: dict[str, ExtensionManifest] = {}

    def register(self, manifest: ExtensionManifest | Mapping[str, Any]) -> str:
        if isinstance(manifest, Mapping):
            manifest = ExtensionManifest.from_mapping(manifest)
        if not isinstance(manifest, ExtensionManifest):
            raise TypeError("manifest must be an ExtensionManifest or mapping")
        previous = self._manifests.get(manifest.name)
        if previous is not None and previous.digest != manifest.digest:
            raise ExtensionContractError(
                f"extension {manifest.name!r} is already registered with a different digest"
            )
        self._manifests[manifest.name] = manifest
        return manifest.digest

    def get(self, name: str) -> ExtensionManifest | None:
        return self._manifests.get(name)

    def providers_for(
        self, capability: str, *, allow_external: bool = True
    ) -> tuple[ExtensionManifest, ...]:
        if not isinstance(capability, str) or not _NAME.fullmatch(capability):
            raise ExtensionContractError("capability must be a lowercase identifier")
        if type(allow_external) is not bool:
            raise ExtensionContractError("allow_external must be a boolean")
        return tuple(
            item for item in sorted(self._manifests.values(), key=lambda x: (_TRUST_ORDER[x.trust], x.name))
            if item.health == "healthy"
            and capability in item.capabilities
            and (allow_external or item.trust != "external")
        )

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {**item.as_dict(), "digest": item.digest}
            for item in sorted(self._manifests.values(), key=lambda x: x.name)
        )


__all__ = ["ExtensionContractError", "ExtensionManifest", "ExtensionRegistry"]
