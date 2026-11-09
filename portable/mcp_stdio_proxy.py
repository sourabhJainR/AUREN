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
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or not parsed.port
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or parsed.path.rstrip("/") != "/mcp"
    ):
        raise ValueError("stdio proxy URL must be a loopback HTTP /mcp endpoint with an explicit port")
    return value


def read_token_file(path: Path) -> str:
    from .local_host import _read_token_file

    return _read_token_file(path.expanduser())


def _rpc_error(request_id, code: int, message: str) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
        separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def post_message(url: str, token: str, payload: bytes, *, timeout_seconds: float = 60.0) -> bytes | None:
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            if response.status == 202:
                return None
            body = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        body = exc.read(_MAX_RESPONSE_BYTES + 1)
        if len(body) <= _MAX_RESPONSE_BYTES:
            try:
                parsed = json.loads(body.decode("utf-8"))
                if isinstance(parsed, dict) and parsed.get("jsonrpc") == "2.0":
                    return body
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        raise RuntimeError("local MCP endpoint rejected the request") from None
    except (OSError, TimeoutError, urllib.error.URLError):
        raise RuntimeError("local AUREN MCP service is unavailable; start 'auren_cli.py local' first") from None
    if len(body) > _MAX_RESPONSE_BYTES:
        raise RuntimeError("local MCP response exceeded the configured byte limit")
    return body or None


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
    for line in source:
        request_id = None
        try:
            if len(line) > _MAX_REQUEST_BYTES:
                raise ValueError("MCP request exceeds the configured byte limit")
            payload = json.loads(line.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("MCP stdio messages must be JSON objects")
            request_id = payload.get("id")
            response = post_message(
                endpoint, token,
                json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
            )
            if response is not None:
                target.write(response.rstrip(b"\r\n") + b"\n")
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
