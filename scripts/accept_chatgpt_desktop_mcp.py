"""Exercise the exact ChatGPT desktop/Codex stdio MCP command from config.toml.

This validates the configured stdio process and a read-only AUREN MCP call.
It does not inspect or control the ChatGPT desktop UI.
"""
from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import threading
import tomllib
from pathlib import Path
from typing import Any, Mapping

from portable.mcp_local_client import LOCAL_PROTOCOL_VERSION
from portable.mcp_stdio_proxy import validate_local_url

_MAX_LINE = 2 * 1024 * 1024


class AcceptanceError(RuntimeError):
    pass


def _read_stream(stream, output: queue.Queue):
    try:
        while True:
            line = stream.readline(_MAX_LINE + 1)
            if not line:
                output.put(None)
                return
            output.put(line)
    except Exception as exc:
        output.put(exc)


def _rpc(process: subprocess.Popen, lines: queue.Queue, request: Mapping[str, Any],
         request_id: int | None, timeout: float) -> Mapping[str, Any] | None:
    payload = json.dumps(request, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(payload) > _MAX_LINE:
        raise AcceptanceError("MCP request exceeded the configured size limit")
    process.stdin.write(payload + b"\n")
    process.stdin.flush()
    if request_id is None:
        return None
    try:
        line = lines.get(timeout=timeout)
    except queue.Empty as exc:
        raise AcceptanceError("timed out waiting for the configured stdio MCP process") from exc
    if line is None:
        raise AcceptanceError("configured stdio MCP process exited before returning a response")
    if isinstance(line, BaseException):
        raise AcceptanceError("could not read the configured stdio MCP response") from line
    if len(line) > _MAX_LINE:
        raise AcceptanceError("MCP response exceeded the configured size limit")
    try:
        response = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("stdio stdout contained a non-JSON-RPC line; check that logs go to stderr") from exc
    if not isinstance(response, dict) or response.get("jsonrpc") != "2.0" or response.get("id") != request_id:
        raise AcceptanceError("stdio MCP response did not match the request")
    if isinstance(response.get("error"), dict):
        message = response["error"].get("message")
        raise AcceptanceError(message[:400] if isinstance(message, str) else "MCP request was rejected")
    result = response.get("result")
    if not isinstance(result, dict):
        raise AcceptanceError("stdio MCP response did not contain a result object")
    return result


def _load_server(config_path: Path, server_name: str) -> tuple[list[str], Path]:
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AcceptanceError(f"could not read MCP TOML config: {type(exc).__name__}") from exc
    servers = config.get("mcp_servers")
    server = servers.get(server_name) if isinstance(servers, dict) else None
    if not isinstance(server, dict):
        raise AcceptanceError(f"MCP server '{server_name}' is missing from config")
    command = server.get("command")
    args = server.get("args")
    if not isinstance(command, str) or not command.strip():
        raise AcceptanceError("MCP server command must be a non-empty string")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise AcceptanceError("MCP server args must be an array of strings")
    if "mcp-proxy" not in args:
        raise AcceptanceError("configured server is not the AUREN mcp-proxy command")
    try:
        url = args[args.index("--url") + 1]
        token_file = args[args.index("--token-file") + 1]
        validate_local_url(url)
    except (ValueError, IndexError) as exc:
        raise AcceptanceError("AUREN stdio config must use loopback /mcp and a token file") from exc
    token_path = Path(token_file).expanduser()
    if not token_path.is_file():
        raise AcceptanceError("configured AUREN local operator token file does not exist")
    cwd_value = server.get("cwd")
    cwd = Path(cwd_value).expanduser() if isinstance(cwd_value, str) and cwd_value else Path.cwd()
    if not cwd.is_dir():
        raise AcceptanceError("configured MCP working directory does not exist")
    executable = Path(command).expanduser()
    if executable.parent != Path(".") and not executable.is_file():
        raise AcceptanceError("configured MCP executable does not exist")
    return [command, *args], cwd


def run_acceptance(config_path: Path, server_name: str = "auren", timeout: float = 15.0) -> dict[str, Any]:
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0.1 <= timeout <= 120:
        raise AcceptanceError("timeout must be between 0.1 and 120 seconds")
    command, cwd = _load_server(config_path, server_name)
    try:
        process = subprocess.Popen(
            command, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, shell=False, bufsize=0,
        )
    except OSError as exc:
        raise AcceptanceError(f"could not launch configured MCP process: {type(exc).__name__}") from exc

    lines: queue.Queue = queue.Queue()
    def drain_stderr():
        try:
            for _line in iter(process.stderr.readline, b""):
                pass
        except Exception:
            pass

    reader = threading.Thread(target=_read_stream, args=(process.stdout, lines), daemon=True)
    err_reader = threading.Thread(target=drain_stderr, daemon=True)
    reader.start()
    err_reader.start()
    try:
        initialize = _rpc(process, lines, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": LOCAL_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "AUREN ChatGPT desktop acceptance", "version": "1.0"},
            },
        }, 1, timeout)
        negotiated = initialize.get("protocolVersion")
        if not isinstance(negotiated, str):
            raise AcceptanceError("MCP initialize did not negotiate a protocol version")
        _rpc(process, lines, {
            "jsonrpc": "2.0", "method": "notifications/initialized",
        }, None, timeout)
        tools_result = _rpc(process, lines, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        }, 2, timeout)
        tools = tools_result.get("tools")
        if not isinstance(tools, list):
            raise AcceptanceError("MCP tools/list did not return a tool array")
        status_tool = next((item for item in tools if isinstance(item, dict)
                            and item.get("name") == "auren.service_status"), None)
        if status_tool is None:
            raise AcceptanceError("authenticated tool list does not include auren.service_status")
        if status_tool.get("annotations", {}).get("readOnlyHint") is not True:
            raise AcceptanceError("auren.service_status is not marked read-only")
        status_result = _rpc(process, lines, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "auren.service_status", "arguments": {}},
        }, 3, timeout)
        if status_result.get("isError") is True:
            raise AcceptanceError("read-only service_status tool returned an error")
        if not isinstance(status_result.get("content"), list) and not isinstance(
            status_result.get("structuredContent"), dict
        ):
            raise AcceptanceError("service_status did not return structured tool content")
        return {
            "status": "stdio_acceptance_passed",
            "config": str(config_path),
            "server_name": server_name,
            "protocol_version": negotiated,
            "tool_count": len(tools),
            "read_only_tool": "auren.service_status",
            "read_only_call": "passed",
            "task_submitted": False,
            "approval_changed": False,
            "credentials_printed": False,
            "desktop_ui_verified": False,
        }
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                stream.close()
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the configured ChatGPT desktop/Codex MCP stdio acceptance test."
    )
    parser.add_argument(
        "--config", type=Path,
        default=Path.home() / ".codex" / "config.toml",
        help="Shared Codex/ChatGPT desktop config.toml",
    )
    parser.add_argument("--server", default="auren", help="MCP server name (default: auren)")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)
    try:
        report = run_acceptance(args.config, args.server, args.timeout)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    except (AcceptanceError, OSError, ValueError) as exc:
        print(json.dumps({
            "status": "failed",
            "error": str(exc)[:500],
            "credentials_printed": False,
        }, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
