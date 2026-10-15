"""Explicit production registration for provider-backed specialist crew adapters.

Provider clients and credentials are injected by the host. This module does not
read secrets from environment variables or grant tools beyond each adapter's
declared allowlist. Registration is immutable after freeze and produces a stable
configuration digest for graph/evidence binding.
"""
from __future__ import annotations

import hashlib
import json
from threading import RLock
from typing import Mapping

from portable.crew_adapters import ProviderBackedCrewAdapter


class ProductionCrewAdapterRegistry:
    SCHEMA_VERSION = "auren-production-crew-registry-v1"

    def __init__(self, *, required_crew_ids: tuple[str, ...] = ()) -> None:
        if len(set(required_crew_ids)) != len(required_crew_ids) or any(not x.strip() for x in required_crew_ids):
            raise ValueError("required crew IDs must be unique non-empty strings")
        self._required = frozenset(required_crew_ids)
        self._adapters: dict[str, ProviderBackedCrewAdapter] = {}
        self._frozen = False
        self._lock = RLock()

    def register(self, adapter: ProviderBackedCrewAdapter) -> None:
        with self._lock:
            if self._frozen:
                raise RuntimeError("production adapter registry is frozen")
            if not isinstance(adapter, ProviderBackedCrewAdapter):
                raise TypeError("production registration accepts only provider-backed adapters")
            if adapter.crew_id in self._adapters:
                raise ValueError(f"duplicate production crew registration: {adapter.crew_id}")
            if adapter.provider_version.strip().lower() in {"unknown", "unversioned", "latest"}:
                raise ValueError("production provider versions must be pinned")
            if not adapter.configuration_digest:
                raise ValueError("adapter configuration digest is required")
            self._adapters[adapter.crew_id] = adapter

    def freeze(self) -> str:
        with self._lock:
            missing = self._required - set(self._adapters)
            if missing:
                raise RuntimeError("required production adapters are missing: " + ", ".join(sorted(missing)))
            self._frozen = True
            return self.configuration_digest

    @property
    def configuration_digest(self) -> str:
        with self._lock:
            rows = [
                {
                    "crew_id": crew_id,
                    "provider_id": adapter.provider_id,
                    "provider_version": adapter.provider_version,
                    "adapter_digest": adapter.configuration_digest,
                }
                for crew_id, adapter in sorted(self._adapters.items())
            ]
            body = {"schema_version": self.SCHEMA_VERSION, "adapters": rows}
            return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @property
    def crew_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._adapters))

    def get(self, crew_id: str) -> ProviderBackedCrewAdapter:
        with self._lock:
            if not self._frozen:
                raise RuntimeError("production adapter registry must be frozen before dispatch")
            try:
                return self._adapters[crew_id]
            except KeyError as exc:
                raise PermissionError(f"crew is not registered for production dispatch: {crew_id}") from exc

    def require_all(self, crew_ids: tuple[str, ...]) -> None:
        missing = set(crew_ids) - set(self.crew_ids)
        if missing:
            raise PermissionError("required production adapters missing: " + ", ".join(sorted(missing)))


__all__ = ["ProductionCrewAdapterRegistry"]
