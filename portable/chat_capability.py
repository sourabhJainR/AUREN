"""Provider-neutral chat capability for durable AdaptiveRuntime triggers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from dataclasses import asdict, dataclass
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portable.adaptive_runtime import AdaptiveRuntime
from portable.adaptive_trigger import AdaptiveTrigger
from portable.agent_capabilities import AutomationScheduler
from portable.orchestration import Graph


CAPABILITY_NAME = "adaptive_runtime.trigger"


@dataclass(frozen=True)
class ChatCapabilityDescriptor:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]


_DESCRIPTOR = ChatCapabilityDescriptor(
    name=CAPABILITY_NAME,
    description=(
        "Durably accept a repository engineering task for the existing "
        "AdaptiveRuntime and return immediately without waiting for execution."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "required": ["task", "project_root"],
        "properties": {
            "task": {"type": "string", "minLength": 1},
            "project_root": {"type": "string", "minLength": 1},
            "context": {"type": "object", "additionalProperties": True},
            "priority": {"type": "string", "enum": ["high", "normal", "low"]},
            "event_id": {"type": "string", "minLength": 1},
            "max_attempts": {"type": "integer", "minimum": 1, "maximum": 16},
        },
    },
    output_schema={
        "type": "object",
        "required": ["trigger_id", "status", "accepted_at"],
        "properties": {
            "trigger_id": {"type": "string"},
            "status": {"type": "string"},
            "accepted_at": {"type": "string"},
        },
    },
)


def descriptor() -> ChatCapabilityDescriptor:
    """Return the stable provider-neutral capability contract."""
    return _DESCRIPTOR


def invoke(runtime: AdaptiveRuntime, arguments: Mapping[str, Any]):
    """Validate and durably enqueue one chat-triggered AdaptiveRuntime task."""
    if not isinstance(arguments, Mapping):
        raise TypeError("capability arguments must be an object")
    task = arguments.get("task")
    project_root = arguments.get("project_root")
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task is required")
    if not isinstance(project_root, str) or not project_root.strip():
        raise ValueError("project_root is required")
    context = arguments.get("context", {})
    if not isinstance(context, Mapping):
        raise TypeError("context must be an object")
    priority = arguments.get("priority", "normal")
    if priority not in {"high", "normal", "low"}:
        raise ValueError("priority must be one of: low, normal, high")
    max_attempts = arguments.get("max_attempts", 3)
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or not 1 <= max_attempts <= 16:
        raise ValueError("max_attempts must be an integer from 1 to 16")
    event_id = arguments.get("event_id")
    if event_id is not None and (not isinstance(event_id, str) or not event_id.strip()):
        raise ValueError("event_id must be a non-empty string when supplied")
    receipt = AdaptiveTrigger.for_runtime(runtime).trigger_adaptive_runtime(
        task,
        project_root,
        context,
        priority=priority,
        event_id=event_id,
        max_attempts=max_attempts,
        fire_and_forget=True,
    )
    return asdict(receipt)


def _default_runtime() -> AdaptiveRuntime:
    """Build a process-local runtime using the canonical AUREN automation store."""
    state = Path(os.environ.get("AUREN_HOME", Path.home() / ".auren")).expanduser()
    scheduler = AutomationScheduler(state / "automation" / "automation.db")
    return AdaptiveRuntime(Graph([]), automation_scheduler=scheduler)


def _jsonrpc_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_definition() -> dict[str, Any]:
    return {
        "name": "adaptive_runtime_trigger",
        "description": _DESCRIPTOR.description,
        "inputSchema": dict(_DESCRIPTOR.input_schema),
    }


def serve_mcp(runtime: AdaptiveRuntime | None = None) -> int:
    """Serve the capability through a minimal stdio MCP JSON-RPC loop."""
    runtime = runtime or _default_runtime()
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params") or {}
            if method == "initialize":
                response = _jsonrpc_response(
                    request_id,
                    {
                        "protocolVersion": str(params.get("protocolVersion") or "2024-11-05"),
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "auren-adaptive-runtime", "version": "1.0.0"},
                    },
                )
            elif method == "notifications/initialized":
                continue
            elif method == "tools/list":
                response = _jsonrpc_response(request_id, {"tools": [_tool_definition()]})
            elif method == "tools/call":
                if params.get("name") != "adaptive_runtime_trigger":
                    raise ValueError("unknown tool")
                result = invoke(runtime, params.get("arguments") or {})
                response = _jsonrpc_response(
                    request_id,
                    {"content": [{"type": "text", "text": json.dumps(result, sort_keys=True)}]},
                )
            elif method == "ping":
                response = _jsonrpc_response(request_id, {})
            else:
                response = _jsonrpc_error(request_id, -32601, f"method not found: {method}")
        except Exception as exc:
            response = _jsonrpc_error(request_id if "request_id" in locals() else None, -32602, str(exc))
        sys.stdout.write(json.dumps(response, sort_keys=True) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(serve_mcp())


__all__ = ["CAPABILITY_NAME", "ChatCapabilityDescriptor", "descriptor", "invoke", "serve_mcp"]
