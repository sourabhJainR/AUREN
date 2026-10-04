#!/usr/bin/env python3
"""Local, read-only AUREN end-user console.

The console is intentionally a view over existing machine-scoped AUREN state.
It does not execute tasks, mutate policy, or create a second state store.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import socket
import sqlite3
import threading
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

_SECRET_KEY = re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization|cookie|credential)")
_SECRET_VALUE = re.compile(
    r"(?i)(api[_-]?key|token|password|secret|authorization|cookie|credential)\s*[:=]\s*[^,\s]+"
)

MAX_JSONL_RECORDS = 25
MAX_FILE_BYTES = 512 * 1024


@dataclass(frozen=True)
class ConsoleSnapshot:
    generated_at: str
    installation: Mapping[str, Any]
    runs: Mapping[str, Any]
    learning: Mapping[str, Any]
    recovery: Mapping[str, Any]
    health: Mapping[str, Any]
    capabilities: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "installation": dict(self.installation),
            "runs": dict(self.runs),
            "learning": dict(self.learning),
            "recovery": dict(self.recovery),
            "health": dict(self.health),
            "capabilities": dict(self.capabilities),
        }


def _redact(value: Any, limit: int = 1200) -> Any:
    if isinstance(value, str):
        value = _SECRET_VALUE.sub(r"\1=[REDACTED]", value)
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else _redact(item, limit)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item, limit) for item in value[:100]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return repr(value)[:limit]


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str]:
    if not path.is_file():
        return None, "missing"
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None, "too_large"
        value = json.loads(path.read_text(encoding="utf-8"))
        return (_redact(value) if isinstance(value, dict) else None), "ok"
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "unknown"


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], str]:
    if not path.is_file():
        return [], "missing"
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    return records, "unknown"
                if isinstance(item, dict):
                    records.append(_redact(item))
                if len(records) >= MAX_JSONL_RECORDS:
                    break
    except (OSError, UnicodeError):
        return records, "unknown"
    return records, "ok"


def _safe_iso_timestamp(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _sqlite_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "missing"}
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1) as conn:
            tables = [
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
                if row and row[0]
            ]
            counts: dict[str, int] = {}
            for table in tables[:20]:
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
                    continue
                try:
                    counts[table] = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                except sqlite3.Error:
                    counts[table] = -1
            return {"status": "ok", "tables": tables[:20], "row_counts": counts}
    except (OSError, sqlite3.Error):
        return {"status": "unknown"}


def _session_summary(root: Path) -> dict[str, Any]:
    sessions = root / "sessions"
    if not sessions.is_dir():
        return {"status": "missing", "count": 0, "recent": []}
    recent: list[dict[str, Any]] = []
    try:
        files = sorted(
            (p for p in sessions.rglob("*") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:25]
        for path in files:
            item: dict[str, Any] = {
                "name": path.relative_to(sessions).as_posix(),
                "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            }
            data, status = _read_json(path)
            item["status"] = status
            if data:
                allowed = {
                    key: data[key]
                    for key in ("project_key", "stage", "attempt", "last_error", "project_root", "intent")
                    if key in data
                }
                item.update(allowed)
                if data.get("project_root") or data.get("intent"):
                    item["resume_hint"] = "reopen the stored project and review the checkpoint before continuing"
            recent.append(item)
    except OSError:
        return {"status": "unknown", "count": 0, "recent": []}
    return {"status": "ok", "count": len(list(sessions.rglob("*"))), "recent": recent}


def _installation_summary(root: Path) -> dict[str, Any]:
    active, status = _read_json(root / "active.json")
    if status == "missing":
        return {"status": "not_installed"}
    if status != "ok" or not active:
        return {"status": "unknown"}
    return {
        "status": "active",
        "version": active.get("version"),
        "source_commit": str(active.get("source_commit") or "")[:12],
        "bundle_sha256": str(active.get("bundle_sha256") or "")[:12],
        "installed_at": active.get("installed_at"),
        "repository_isolated": active.get("repository_isolated"),
    }


def _runs_summary(root: Path) -> dict[str, Any]:
    traces, status = _read_jsonl(root / "observability" / "traces.jsonl")
    if status == "missing":
        return {"status": "not_enabled", "count": 0, "recent": []}
    recent: list[dict[str, Any]] = []
    for trace in reversed(traces):
        recent.append(
            {
                "trace_id": trace.get("trace_id"),
                "name": trace.get("name"),
                "status": trace.get("status"),
                "duration_ms": trace.get("duration_ms"),
                "started_at": _safe_iso_timestamp(trace.get("start")),
                "score_count": len(trace.get("scores") or []) if isinstance(trace.get("scores"), list) else 0,
                "span_count": len(trace.get("spans") or []) if isinstance(trace.get("spans"), list) else 0,
                "metadata": trace.get("metadata", {}),
            }
        )
    return {"status": status, "count": len(traces), "recent": recent}


def _learning_summary(root: Path) -> dict[str, Any]:
    memory = _sqlite_summary(root / "memory" / "memory.db")
    automation = _sqlite_summary(root / "automation" / "automation.db")
    if memory["status"] == "missing" and automation["status"] == "missing":
        return {"status": "unknown", "memory": memory, "automation": automation}
    maintenance_candidates = sorted(
        (p for p in root.rglob("*maintenance*.json") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    receipt = None
    if maintenance_candidates:
        receipt, receipt_status = _read_json(maintenance_candidates[0])
        if receipt_status != "ok":
            receipt = None
    return {"status": "ok", "memory": memory, "automation": automation, "latest_maintenance": receipt}


def _capabilities_summary(root: Path) -> dict[str, Any]:
    skill_dirs = {
        "agent_skills": Path.home() / ".agents" / "skills" / "ai-coding-orchestrator",
        "claude_skill": Path.home() / ".claude" / "skills" / "ai-coding-orchestrator",
        "gemini_skill": Path.home() / ".gemini" / "skills" / "ai-coding-orchestrator",
    }
    installed = {name: path.is_dir() for name, path in skill_dirs.items()}
    providers = root / "providers"
    manifests = []
    if providers.is_dir():
        try:
            manifests = sorted(p.name for p in providers.iterdir() if p.suffix == ".json")[:25]
        except OSError:
            manifests = []
    return {"installed_skills": installed, "provider_manifests": manifests}


def _health_summary(root: Path) -> dict[str, Any]:
    checks = {
        "state_home": root.is_dir(),
        "active_pin": (root / "active.json").is_file(),
        "machine_scoped": root.name == ".auren",
        "observability": (root / "observability" / "traces.jsonl").is_file(),
        "sessions": (root / "sessions").is_dir(),
    }
    return {
        "status": "ok" if checks["state_home"] and checks["machine_scoped"] else "degraded",
        "checks": checks,
    }


def collect_snapshot(auren_home: Path | str | None = None) -> ConsoleSnapshot:
    root = Path(auren_home or (Path.home() / ".auren")).expanduser().resolve()
    return ConsoleSnapshot(
        generated_at=datetime.now(timezone.utc).isoformat(),
        installation=_installation_summary(root),
        runs=_runs_summary(root),
        learning=_learning_summary(root),
        recovery=_session_summary(root),
        health=_health_summary(root),
        capabilities=_capabilities_summary(root),
    )


def render_status(snapshot: ConsoleSnapshot, as_json: bool = False) -> str:
    payload = snapshot.as_dict()
    if as_json:
        return json.dumps(payload, indent=2, sort_keys=True)
    installation = payload["installation"]
    runs = payload["runs"]
    health = payload["health"]
    return "\n".join(
        [
            "AUREN status",
            f"Health: {health.get('status', 'unknown')}",
            f"Installation: {installation.get('status', 'unknown')}",
            f"Version: {installation.get('version', 'n/a')}",
            f"Recent runs: {runs.get('count', 0)}",
            f"Recovery checkpoints: {payload['recovery'].get('count', 0)}",
            f"Generated: {payload['generated_at']}",
        ]
    )


def _status_class(value: Any) -> str:
    text = str(value or "unknown").lower()
    return {
        "ok": "good",
        "active": "good",
        "success": "good",
        "not_installed": "warn",
        "not_enabled": "warn",
        "missing": "warn",
        "degraded": "warn",
    }.get(text, "muted")


def _card(title: str, value: Any, detail: str = "", status: Any = None) -> str:
    cls = _status_class(status if status is not None else value)
    return (
        '<section class="card">'
        f'<div class="eyebrow">{html.escape(title)}</div>'
        f'<div class="metric {cls}">{html.escape(str(value))}</div>'
        f'<div class="detail">{html.escape(detail)}</div>'
        '</section>'
    )


def render_dashboard(snapshot: ConsoleSnapshot) -> str:
    payload = snapshot.as_dict()
    installation = payload["installation"]
    health = payload["health"]
    runs = payload["runs"]
    learning = payload["learning"]
    recovery = payload["recovery"]
    capabilities = payload["capabilities"]
    run_rows = []
    for item in runs.get("recent", []):
        run_rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(item.get('trace_id') or ''))}</code></td>"
            f"<td>{html.escape(str(item.get('name') or 'task'))}</td>"
            f"<td><span class=\"chip {_status_class(item.get('status'))}\">{html.escape(str(item.get('status') or 'unknown'))}</span></td>"
            f"<td>{html.escape(str(item.get('duration_ms') or 'n/a'))} ms</td>"
            f"<td>{html.escape(str(item.get('span_count') or 0))} spans</td>"
            "</tr>"
        )
    if not run_rows:
        run_rows.append('<tr><td colspan="5" class="empty">No trace data yet. Enable observability to see completed work.</td></tr>')
    skill_items = "".join(
        f"<li><span>{html.escape(name.replace('_', ' ').title())}</span><span class=\"chip {'good' if present else 'muted'}\">{'available' if present else 'not found'}</span></li>"
        for name, present in capabilities.get("installed_skills", {}).items()
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AUREN Console</title>
<style>
:root{{font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color-scheme:dark;background:#0b1020;color:#e8edf7;}}
*{{box-sizing:border-box}} body{{margin:0;background:linear-gradient(160deg,#0b1020,#121b2f 60%,#0b1020);min-height:100vh;}}
main{{max-width:1180px;margin:auto;padding:32px 20px 48px}} header{{display:flex;justify-content:space-between;gap:20px;align-items:flex-end;margin-bottom:28px}} h1{{margin:0;font-size:34px;letter-spacing:-.03em}} p{{color:#aeb8ca;margin:6px 0 0}} .meta{{font-size:12px;color:#7f8aa0;text-align:right}}
.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:18px}} .card,.panel{{background:rgba(18,28,48,.82);border:1px solid rgba(145,160,190,.15);border-radius:16px;box-shadow:0 14px 40px rgba(0,0,0,.18)}} .card{{padding:18px}} .eyebrow{{font-size:11px;text-transform:uppercase;letter-spacing:.11em;color:#8591a7}} .metric{{font-size:24px;margin-top:8px;font-weight:700}} .detail{{color:#98a4b8;font-size:12px;margin-top:5px;min-height:17px}} .good{{color:#9ee6b8}} .warn{{color:#ffd596}} .muted{{color:#a7b0c1}}
.panel{{padding:18px;margin-top:14px}} .panel h2{{margin:0 0 14px;font-size:18px}} .two{{display:grid;grid-template-columns:1.5fr 1fr;gap:14px}} table{{width:100%;border-collapse:collapse}} th,td{{padding:10px 8px;border-bottom:1px solid rgba(145,160,190,.12);text-align:left;font-size:13px}} th{{color:#8793a7;font-weight:600}} .chip{{display:inline-flex;align-items:center;border-radius:999px;padding:3px 8px;font-size:11px;background:rgba(145,160,190,.1)}} ul{{list-style:none;padding:0;margin:0}} li{{display:flex;justify-content:space-between;gap:10px;padding:10px 0;border-bottom:1px solid rgba(145,160,190,.12);font-size:13px}} .kv{{display:grid;grid-template-columns:150px 1fr;gap:8px;font-size:13px;margin-top:8px}} .key{{color:#8490a5}} code{{font-size:11px;color:#b7c3d7}} .empty{{color:#7f8aa0;text-align:center;padding:24px}} .footer{{margin-top:20px;color:#748097;font-size:11px}}
@media(max-width:900px){{.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.two{{grid-template-columns:1fr}}header{{align-items:flex-start;flex-direction:column}}.meta{{text-align:left}}}}
@media(max-width:520px){{.grid{{grid-template-columns:1fr}}main{{padding:22px 14px}}h1{{font-size:28px}}}}
</style>
</head>
<body><main>
<header><div><h1>AUREN Console</h1><p>One view over execution evidence, health, learning and recovery.</p></div><div class="meta">Read-only local view<br>{html.escape(payload['generated_at'])}</div></header>
<section class="panel"><h2>Overview</h2><p>The current AUREN control-plane state at a glance.</p></section>
<div class="grid">
{_card('System', health.get('status','unknown'), 'Local AUREN state', health.get('status'))}
{_card('Installation', installation.get('version','not installed'), installation.get('source_commit','') or 'No active pin', installation.get('status'))}
{_card('Recent runs', runs.get('count',0), runs.get('status','unknown'), runs.get('status'))}
{_card('Checkpoints', recovery.get('count',0), recovery.get('status','unknown'), recovery.get('status'))}
</div>
<section class="panel"><h2>Installation</h2><div class="kv"><div class="key">State</div><div>{html.escape(str(installation.get('status','unknown')))}</div><div class="key">Version</div><div>{html.escape(str(installation.get('version','n/a')))}</div><div class="key">Source commit</div><div><code>{html.escape(str(installation.get('source_commit','n/a')))}</code></div><div class="key">Bundle pin</div><div><code>{html.escape(str(installation.get('bundle_sha256','n/a')))}</code></div><div class="key">Repository isolated</div><div>{html.escape(str(installation.get('repository_isolated','unknown')))}</div></div></section>
<div class="two">
<section class="panel"><h2>Runs</h2><table><thead><tr><th>Trace</th><th>Work</th><th>Status</th><th>Duration</th><th>Shape</th></tr></thead><tbody>{''.join(run_rows)}</tbody></table></section>
<section class="panel"><h2>Capabilities</h2><ul>{skill_items or '<li><span>No skill installations detected.</span></li>'}</ul></section>
</div>
<div class="two">
<section class="panel"><h2>Learning</h2><div class="kv"><div class="key">State</div><div>{html.escape(str(learning.get('status','unknown')))}</div><div class="key">Memory</div><div>{html.escape(str(learning.get('memory',{}).get('status','unknown')))}</div><div class="key">Automation</div><div>{html.escape(str(learning.get('automation',{}).get('status','unknown')))}</div></div></section>
<section class="panel"><h2>Recovery</h2><div class="kv"><div class="key">State</div><div>{html.escape(str(recovery.get('status','unknown')))}</div><div class="key">Items</div><div>{html.escape(str(recovery.get('count',0)))}</div></div></section>
</div>
<section class="panel"><h2>Health checks</h2><ul>{''.join(f'<li><span>{html.escape(k.replace("_"," ").title())}</span><span class="chip {"good" if v else "warn"}">{"pass" if v else "missing"}</span></li>' for k,v in health.get("checks",{}).items())}</ul></section>
<div class="footer">AUREN Console never serves repository source files and exposes no mutating API.</div>
</main></body></html>"""


def allowed_methods() -> tuple[str, str]:
    return ("GET", "HEAD")


def _serve_once(host: str, port: int, snapshot_provider) -> tuple[ThreadingHTTPServer, str]:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("console host must be loopback")
    bind_host = "127.0.0.1"

    class Handler(BaseHTTPRequestHandler):
        server_version = "AURENConsole/1.0"

        def _write(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            snapshot = snapshot_provider()
            if path == "/":
                self._write(200, "text/html; charset=utf-8", render_dashboard(snapshot).encode("utf-8"))
            elif path == "/api/status":
                self._write(200, "application/json; charset=utf-8", render_status(snapshot, True).encode("utf-8"))
            elif path == "/health":
                self._write(200, "application/json; charset=utf-8", b'{"status":"ok"}')
            else:
                self._write(404, "text/plain; charset=utf-8", b"Not found\n")

        def do_HEAD(self) -> None:  # noqa: N802
            self.do_GET()

        def _method_not_allowed(self) -> None:
            self._write(405, "text/plain; charset=utf-8", b"Method not allowed\n")

        do_POST = do_PUT = do_PATCH = do_DELETE = _method_not_allowed

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer((bind_host, port), Handler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    return server, url


def serve_console(
    auren_home: Path | str | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = False,
    block: bool = True,
) -> str:
    if port < 0 or port > 65535:
        raise ValueError("port must be between 0 and 65535")
    snapshot_provider = lambda: collect_snapshot(auren_home)
    server, url = _serve_once(host, port, snapshot_provider)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    print(f"AUREN Console: {url}")
    if block:
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    return url


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="AUREN local status and read-only console")
    sub = root.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    status.add_argument("--auren-home", type=Path, default=None)
    console = sub.add_parser("console")
    console.add_argument("--host", default="127.0.0.1")
    console.add_argument("--port", type=int, default=0)
    console.add_argument("--open", dest="open_browser", action="store_true")
    console.add_argument("--auren-home", type=Path, default=None)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "status":
        print(render_status(collect_snapshot(args.auren_home), args.json))
        return 0
    if args.command == "console":
        serve_console(args.auren_home, args.host, args.port, args.open_browser, True)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
