"""Zero-dependency local AUREN engineering dashboard server."""
from __future__ import annotations
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from .engineering_dashboard import EngineeringDashboard
UI_ROOT = Path(__file__).resolve().parent.parent / "dashboard"

class _Handler(BaseHTTPRequestHandler):
    dashboard = None
    ui_root = UI_ROOT
    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/dashboard":
            body = json.dumps(self.dashboard.snapshot().as_dict(), separators=(",", ":")).encode()
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path == "/api/health":
            self._send(200, b'{"status":"ok"}', "application/json")
            return
        if path in {"/", "/index.html"}:
            self._serve(self.ui_root / "index.html", "text/html; charset=utf-8")
            return
        if path.startswith("/assets/"):
            target = self.ui_root / path.removeprefix("/assets/")
            if target.is_file() and target.resolve().is_relative_to(self.ui_root.resolve()):
                self._serve(target, self._content_type(target)); return
        self._send(404, b'{"error":"not found"}', "application/json")
    def _serve(self, path, content_type):
        try: body = path.read_bytes()
        except OSError:
            self._send(404, b'{"error":"not found"}', "application/json"); return
        self._send(200, body, content_type)
    @staticmethod
    def _content_type(path):
        return {".css":"text/css; charset=utf-8",".js":"text/javascript; charset=utf-8",
                ".svg":"image/svg+xml",".json":"application/json"}.get(path.suffix.lower(),"application/octet-stream")
    def log_message(self, format, *args): return

def serve(project_root=".", host="127.0.0.1", port=8765):
    dashboard = EngineeringDashboard(project_root)
    handler = type("AURENDashboardHandler", (_Handler,), {"dashboard": dashboard, "ui_root": UI_ROOT})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"AUREN Engineering Console: http://{host}:{port}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

def main():
    parser = argparse.ArgumentParser(description="Run the local AUREN engineering dashboard.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(args.project_root, args.host, args.port)

if __name__ == "__main__": main()
