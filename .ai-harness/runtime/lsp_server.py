#!/usr/bin/env python3
"""Small stdio JSON-RPC/LSP server for repository-aware navigation.

It provides initialize, shutdown, textDocument/documentSymbol, definition and
references using lightweight source scanning. It is deliberately dependency-free
and is a navigation aid; language-specific LSPs remain preferred when present.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


SYMBOL_RE = re.compile(r"^\s*(?:class|def|function|interface|struct|enum|type)\s+([A-Za-z_][\w]*)")
WORD_RE = re.compile(r"\b[A-Za-z_][\w]*\b")


def _send(value: dict) -> None:
    payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)
    sys.stdout.buffer.flush()


def _read() -> dict | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        text = line.decode("utf-8", "replace").strip()
        if not text:
            break
        if ":" in text:
            key, value = text.split(":", 1)
            headers[key.lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


def _path(root: Path, uri: str) -> Path:
    value = uri.replace("file://", "", 1)
    candidate = Path(value).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("document is outside workspace") from exc
    return candidate


def _symbols(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    result = []
    for number, line in enumerate(lines):
        match = SYMBOL_RE.match(line)
        if match:
            name = match.group(1)
            result.append({"name": name, "kind": 12, "range": {"start": {"line": number, "character": 0}, "end": {"line": number, "character": len(line)}}})
    return result


def serve(root: Path) -> None:
    root = root.resolve()
    while True:
        message = _read()
        if message is None:
            return
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"capabilities": {"documentSymbolProvider": True, "definitionProvider": True, "referencesProvider": True}, "serverInfo": {"name": "auren-lsp", "version": "1.0"}}})
        elif method == "shutdown":
            _send({"jsonrpc": "2.0", "id": request_id, "result": None})
            return
        elif method == "exit":
            return
        elif method == "textDocument/documentSymbol":
            uri = params.get("textDocument", {}).get("uri", "")
            _send({"jsonrpc": "2.0", "id": request_id, "result": _symbols(_path(root, uri))})
        elif method in {"textDocument/definition", "textDocument/references"}:
            uri = params.get("textDocument", {}).get("uri", "")
            path = _path(root, uri)
            line_no = int((params.get("position") or {}).get("line", 0))
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            line = lines[line_no] if 0 <= line_no < len(lines) else ""
            names = WORD_RE.findall(line)
            target = names[0] if names else ""
            matches = []
            for candidate in root.rglob("*"):
                if not candidate.is_file() or any(part in {".git", ".auren", "node_modules", "__pycache__"} for part in candidate.parts):
                    continue
                try:
                    for index, content in enumerate(candidate.read_text(encoding="utf-8", errors="replace").splitlines()):
                        if target and re.search(rf"\b{re.escape(target)}\b", content):
                            matches.append({"uri": candidate.as_uri(), "range": {"start": {"line": index, "character": max(0, content.find(target))}, "end": {"line": index, "character": max(0, content.find(target)) + len(target)}}})
                except OSError:
                    continue
            _send({"jsonrpc": "2.0", "id": request_id, "result": matches[:200]})
        else:
            if request_id is not None:
                _send({"jsonrpc": "2.0", "id": request_id, "result": None})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=".")
    args = parser.parse_args()
    serve(Path(args.workspace))
