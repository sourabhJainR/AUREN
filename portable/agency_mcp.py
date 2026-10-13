"""Small MCP-compatible JSON-RPC tool contract for AUREN.

This module intentionally stops at the protocol boundary: transport remains the
host application's responsibility. It gives AUREN deterministic tool listing,
argument validation and policy enforcement without adding an SDK dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .agency_agent_capabilities import AllowlistedToolPolicy, ToolRegistry


@dataclass(frozen=True)
class MCPRequest:
    request_id: str | int
    method: str
    params: Mapping[str, Any]


@dataclass(frozen=True)
class MCPResponse:
    request_id: str | int | None
    result: Mapping[str, Any] | None = None
    error: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"jsonrpc": "2.0", "id": self.request_id}
        if self.error is not None:
            value["error"] = dict(self.error)
        else:
            value["result"] = dict(self.result or {})
        return value


class MCPToolEndpoint:
    def __init__(self, registry: ToolRegistry, policy: AllowlistedToolPolicy | None = None):
        self.registry = registry
        self.policy = policy or AllowlistedToolPolicy()

    def handle(self, request: MCPRequest) -> MCPResponse:
        try:
            if request.method == "tools/list":
                return MCPResponse(request.request_id, {"tools": [
                    {"name": t.name, "description": t.description, "category": t.category, "risk": t.risk}
                    for t in self.registry.discover(" ", limit=1000)
                ]})
            if request.method == "tools/call":
                name = str(request.params.get("name", ""))
                arguments = request.params.get("arguments", {})
                if not isinstance(arguments, Mapping):
                    raise TypeError("arguments must be an object")
                result = self.registry.execute(name, arguments, self.policy)
                return MCPResponse(request.request_id, {"content": [{"type": "text", "text": str(result)}]})
            return MCPResponse(request.request_id, error={"code": -32601, "message": "method not found"})
        except PermissionError as exc:
            return MCPResponse(request.request_id, error={"code": -32001, "message": str(exc)})
        except (KeyError, TypeError, ValueError) as exc:
            return MCPResponse(request.request_id, error={"code": -32602, "message": str(exc)})
