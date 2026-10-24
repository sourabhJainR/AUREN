"""Zero-dependency local AUREN engineering dashboard server."""
from __future__ import annotations
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from .engineering_dashboard import EngineeringDashboard
from .muxin_session_adapter import LocalModelDiscovery, MuxinSessionAdapter
from .hybrid_model_router import HybridModelRouter, TaskRouteRequest
from .agency_observability_ops import observability_snapshot
from .identity_compat import canonical_runtime_home

UI_ROOT = Path(__file__).resolve().parent.parent / "dashboard"
CONTROL_CENTER = Path(__file__).resolve().parent.parent / "site" / "control-center.html"


class _Handler(BaseHTTPRequestHandler):
    dashboard = None
    control_center = None
    ui_root = UI_ROOT
    control_center_path = CONTROL_CENTER

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _origin_is_local(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True  # CLI and same-origin legacy clients may omit Origin.
        try:
            parsed = urlparse(origin)
            port = parsed.port
        except ValueError:
            return False
        return (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost"}
            and port == self.server.server_port
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )

    def _guard_api_origin(self, path):
        if path.startswith("/api/") and not self._origin_is_local():
            self._send(403, b'{"error":"untrusted origin"}', "application/json; charset=utf-8")
            return False
        return True

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._guard_api_origin(path):
            return
        if path != "/api/route":
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 65536:
                raise ValueError("request body size is outside the allowed limit")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("task"), str) or not payload["task"].strip():
                raise ValueError("task must be a non-empty string")
            request = TaskRouteRequest(
                task=payload["task"][:60000],
                capability=str(payload.get("capability", "general"))[:80],
                privacy=str(payload.get("privacy", "private"))[:40],
                max_attempts=min(3, max(1, int(payload.get("max_attempts", 2)))),
                timeout_seconds=min(120.0, max(0.1, float(payload.get("timeout_seconds", 30.0)))),
            )
            result = HybridModelRouter().route(request)
            body = json.dumps({
                "provider": result.provider, "model": result.model, "success": result.success,
                "attempts": result.attempts, "latency_ms": result.latency_ms,
                "resource_usage": result.resource_usage, "fallback_reason": result.fallback_reason,
                "result": result.result, "error": result.error,
            }, separators=(",", ":")).encode()
            self._send(200 if result.success else 503, body, "application/json; charset=utf-8")
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._send(400, json.dumps({"error": str(exc)}).encode(), "application/json; charset=utf-8")
        except Exception:
            self._send(503, b'{"error":"routing request failed"}', "application/json; charset=utf-8")

    def do_GET(self):
        path = urlparse(self.path).path
        if not self._guard_api_origin(path):
            return
        if path == "/api/dashboard":
            body = json.dumps(self.dashboard.snapshot().as_dict(), separators=(",", ":")).encode()
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path == "/api/observability":
            try:
                trace_path = canonical_runtime_home() / "observability" / "traces.jsonl"
                body = json.dumps(observability_snapshot(trace_path), separators=(",", ":")).encode()
                self._send(200, body, "application/json; charset=utf-8")
            except Exception:
                self._send(503, b'{"error":"observability snapshot unavailable"}', "application/json; charset=utf-8")
            return
        if path == "/api/control-center":
            try:
                snapshot = self.control_center.snapshot()
                snapshot["models"] = LocalModelDiscovery().discover()
                body = json.dumps(snapshot, separators=(",", ":")).encode()
                self._send(200, body, "application/json; charset=utf-8")
            except Exception:
                # Keep the local console responsive; report a bounded error without internals.
                self._send(503, b'{"error":"control center snapshot unavailable"}', "application/json; charset=utf-8")
            return
        if path == "/api/health":
            self._send(200, b'{"status":"ok"}', "application/json; charset=utf-8")
            return
        if path == "/control-center":
            self._serve(self.control_center_path, "text/html; charset=utf-8")
            return
        if path in {"/", "/index.html"}:
            self._serve(self.ui_root / "index.html", "text/html; charset=utf-8")
            return
        if path.startswith("/assets/"):
            target = self.ui_root / path.removeprefix("/assets/")
            if target.is_file() and target.resolve().is_relative_to(self.ui_root.resolve()):
                self._serve(target, self._content_type(target))
                return
        self._send(404, b'{"error":"not found"}', "application/json")

    def _serve(self, path, content_type):
        try:
            body = path.read_bytes()
        except OSError:
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        self._send(200, body, content_type)

    @staticmethod
    def _content_type(path):
        return {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".svg": "image/svg+xml", ".json": "application/json"}.get(path.suffix.lower(), "application/octet-stream")

    def log_message(self, format, *args):
        return


def serve(project_root=".", host="127.0.0.1", port=8765):
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("dashboard server only binds to loopback; remote access requires a separately secured deployment")
    dashboard = EngineeringDashboard(project_root)
    control_center = MuxinSessionAdapter(project_root, dashboard=dashboard)
    handler = type("AERDashboardHandler", (_Handler,), {
        "dashboard": dashboard, "control_center": control_center,
        "ui_root": UI_ROOT, "control_center_path": CONTROL_CENTER,
    })
    server = ThreadingHTTPServer((host, port), handler)
    print(f"AUREN Engineering Console: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="Run the local AUREN engineering dashboard.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(args.project_root, args.host, args.port)


if __name__ == "__main__":
    main()
