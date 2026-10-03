"""Provider-native capability discovery and routing for AUREN.

The fabric keeps AUREN provider-neutral while preferring capabilities that a
local coding agent already exposes. It never assumes a provider has a feature:
capabilities are discovered from explicit environment/command evidence and can
be overridden by a provider manifest. The result is a small routing contract
that works across repositories and survives new sessions.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping


CAPABILITIES = (
    "agent",
    "subagent",
    "hooks",
    "session_resume",
    "structured_output",
    "tool_interception",
    "mcp",
    "background_execution",
)


@dataclass(frozen=True)
class ProviderCapability:
    provider: str
    command: str | None
    version: str | None
    capabilities: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities


@dataclass(frozen=True)
class CapabilityRequest:
    capability: str
    preferred_providers: tuple[str, ...] = ()
    allow_fallback: bool = True


@dataclass(frozen=True)
class RoutingDecision:
    provider: str
    capability: str
    native: bool
    reason: str
    command: str | None = None


DEFAULT_PROVIDERS: dict[str, tuple[str, ...]] = {
    "claude": ("agent", "subagent", "hooks", "session_resume", "structured_output", "tool_interception", "mcp", "background_execution"),
    "codex": ("agent", "subagent", "session_resume", "structured_output", "mcp"),
    "gemini": ("agent", "subagent", "hooks", "session_resume", "structured_output", "mcp"),
    "copilot": ("agent", "subagent", "hooks", "structured_output", "mcp"),
}


class ProviderFabric:
    """Discover provider capabilities and select native execution when possible."""

    def __init__(self, manifest_dir: Path | str | None = None) -> None:
        self.manifest_dir = Path(manifest_dir or (Path.home() / ".auren" / "providers")).expanduser()

    def discover(self, *, refresh: bool = False) -> dict[str, ProviderCapability]:
        discovered: dict[str, ProviderCapability] = {}
        for provider, capabilities in DEFAULT_PROVIDERS.items():
            command = shutil.which(provider)
            version = self._version(command) if command else None
            evidence: list[str] = []
            if command:
                evidence.append(f"command:{command}")
                evidence.append(f"version:{version or 'unknown'}")
            manifest = self._manifest(provider)
            if manifest:
                capabilities = tuple(sorted(set(capabilities) | set(manifest.get("capabilities", []))))
                evidence.append("manifest")
            if command or manifest:
                discovered[provider] = ProviderCapability(provider, command, version, tuple(sorted(capabilities)), tuple(evidence))
        if refresh:
            self.persist(discovered)
        return discovered

    def route(self, request: CapabilityRequest, providers: Mapping[str, ProviderCapability] | None = None) -> RoutingDecision:
        if request.capability not in CAPABILITIES:
            raise ValueError(f"unsupported capability: {request.capability}")
        available = providers or self.discover()
        candidates = request.preferred_providers or tuple(available.keys())
        for name in candidates:
            capability = available.get(name)
            if capability and capability.supports(request.capability):
                return RoutingDecision(name, request.capability, True, "native provider capability discovered", capability.command)
        if request.allow_fallback:
            return RoutingDecision("auren", request.capability, False, "no native capability discovered; use AUREN fallback")
        raise RuntimeError(f"no provider supports capability: {request.capability}")

    def persist(self, providers: Mapping[str, ProviderCapability]) -> Path:
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        target = self.manifest_dir / "capabilities.json"
        temp = target.with_suffix(".tmp")
        payload = {name: asdict(value) for name, value in sorted(providers.items())}
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, target)
        return target

    def _manifest(self, provider: str) -> dict[str, Any] | None:
        path = self.manifest_dir / f"{provider}.json"
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _version(command: str | None) -> str | None:
        if not command:
            return None
        try:
            result = subprocess.run([command, "--version"], capture_output=True, text=True, timeout=3, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        text = (result.stdout or result.stderr).strip().splitlines()
        return text[0][:200] if text else None


__all__ = ["CAPABILITIES", "CapabilityRequest", "ProviderCapability", "ProviderFabric", "RoutingDecision"]
