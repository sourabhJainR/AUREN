"""Expose AUREN's local Streamable HTTP MCP endpoint over stdio."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import BinaryIO, TextIO

_MAX_REQUEST_BYTES = 64 * 1024
_MAX_RESPONSE_BYTES = 2_097_152


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def validate_local_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or not parsed.port
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or parsed.path != "/mcp"
    ):
        raise ValueError("stdio proxy URL must be the IPv4 loopback HTTP /mcp endpoint with an explicit port")
    return value


def read_token_file(path: Path) -> str:
    from .local_host import _read_token_file

    return _read_token_file(path.expanduser())


def _rpc_error(request_id, code: int, message: str) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
        separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _decode_sse_messages(body: bytes) -> bytes:
    """Convert Streamable HTTP SSE data events into newline-delimited JSON-RPC."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("local MCP endpoint returned invalid UTF-8 SSE data") from exc
    messages: list[bytes] = []
    data_lines: list[str] = []

    def flush_event() -> None:
        if not data_lines:
            return
        data = "\n".join(data_lines)
        data_lines.clear()
        if data == "[DONE]":
            return
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as exc:
            raise RuntimeError("local MCP endpoint returned malformed SSE JSON") from exc
        if not isinstance(parsed, dict) or parsed.get("jsonrpc") != "2.0":
            raise RuntimeError("local MCP SSE event is not a JSON-RPC message")
        messages.append(json.dumps(parsed, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))

    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line:
            flush_event()
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if field == "data":
            data_lines.append(value[1:] if separator and value.startswith(" ") else value)
    flush_event()
    if not messages:
        raise RuntimeError("local MCP endpoint returned an SSE stream without JSON-RPC messages")
    return b"\n".join(messages)


def _decode_http_response(body: bytes, content_type: str) -> bytes:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type == "text/event-stream":
        return _decode_sse_messages(body)
    if media_type not in {"application/json", "application/json-rpc"}:
        raise RuntimeError("local MCP endpoint returned an unsupported content type")
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("local MCP endpoint returned malformed JSON") from exc
    if not isinstance(parsed, dict) or parsed.get("jsonrpc") != "2.0":
        raise RuntimeError("local MCP endpoint response is not a JSON-RPC message")
    return json.dumps(parsed, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def post_message(
    url: str, token: str, payload: bytes, *,
    protocol_version: str | None = None, timeout_seconds: float = 60.0,
) -> bytes | None:
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if protocol_version:
        headers["MCP-Protocol-Version"] = protocol_version
    request = urllib.request.Request(url, data=payload, method="POST", headers=headers)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            if response.status == 202:
                return None
            body = response.read(_MAX_RESPONSE_BYTES + 1)
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        body = exc.read(_MAX_RESPONSE_BYTES + 1)
        if len(body) <= _MAX_RESPONSE_BYTES:
            try:
                return _decode_http_response(body, exc.headers.get("Content-Type", "application/json"))
            except RuntimeError:
                pass
        raise RuntimeError("local MCP endpoint rejected the request") from None
    except (OSError, TimeoutError, urllib.error.URLError):
        raise RuntimeError("local AUREN MCP service is unavailable; start 'auren_cli.py local' first") from None
    if len(body) > _MAX_RESPONSE_BYTES:
        raise RuntimeError("local MCP response exceeded the configured byte limit")
    return _decode_http_response(body, content_type) if body else None


def run_proxy(
    url: str,
    token_file: Path,
    *,
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    endpoint = validate_local_url(url)
    token = read_token_file(token_file)
    source = stdin or sys.stdin.buffer
    target = stdout or sys.stdout.buffer
    diagnostics = stderr or sys.stderr
    negotiated_protocol: str | None = None
    while True:
        line = source.readline(_MAX_REQUEST_BYTES + 1)
        if not line:
            break
        request_id = None
        if len(line) > _MAX_REQUEST_BYTES:
            target.write(_rpc_error(None, -32700, "MCP request exceeds the configured byte limit") + b"\n")
            target.flush()
            break
        try:
            payload = json.loads(line.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("MCP stdio messages must be JSON objects")
            request_id = payload.get("id")
            response = post_message(
                endpoint, token,
                json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
                protocol_version=negotiated_protocol if payload.get("method") != "initialize" else None,
            )
            if response is not None:
                for response_line in response.splitlines():
                    if not response_line:
                        continue
                    try:
                        decoded = json.loads(response_line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        raise RuntimeError("local MCP response could not be decoded as JSON-RPC")
                    result = decoded.get("result") if isinstance(decoded, dict) else None
                    version = result.get("protocolVersion") if isinstance(result, dict) else None
                    if payload.get("method") == "initialize" and isinstance(version, str):
                        negotiated_protocol = version
                    target.write(response_line + b"\n")
                target.flush()
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            target.write(_rpc_error(request_id, -32700, str(exc)) + b"\n")
            target.flush()
        except RuntimeError as exc:
            print(str(exc), file=diagnostics, flush=True)
            if request_id is not None:
                target.write(_rpc_error(request_id, -32000, str(exc)) + b"\n")
                target.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bridge a local AUREN MCP HTTP endpoint to MCP stdio.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return run_proxy(args.url, args.token_file)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"AUREN MCP stdio proxy: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
