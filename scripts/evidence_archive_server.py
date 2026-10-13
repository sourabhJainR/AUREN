"""Minimal authenticated receiver for AUREN evidence events.

Bind to loopback and place a TLS-terminating reverse proxy in front of this server.
Never expose this plain-HTTP listener directly to an untrusted network.
"""
from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import hmac

from portable.evidence_archive import ArchiveConflict, EvidenceArchive


MAX_BODY_BYTES = 1_000_000


def load_verifier():
    path = os.environ.get("AUREN_ARCHIVE_TRUSTED_PUBLIC_KEYS_FILE", "").strip()
    if not path:
        raise RuntimeError("AUREN_ARCHIVE_TRUSTED_PUBLIC_KEYS_FILE is required")
    encoded_keys = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(encoded_keys, dict) or not encoded_keys:
        raise RuntimeError("trusted public-key file must be a non-empty JSON object")
    public_keys = {}
    for signer_id, encoded in encoded_keys.items():
        if not isinstance(signer_id, str) or not isinstance(encoded, str):
            raise RuntimeError("trusted public keys must map signer IDs to base64url strings")
        raw = base64.urlsafe_b64decode(encoded + "=" * ((4 - len(encoded) % 4) % 4))
        if len(raw) != 32:
            raise RuntimeError(f"trusted Ed25519 public key for {signer_id} must be 32 bytes")
        public_keys[signer_id] = raw
    revoked_path = os.environ.get("AUREN_ARCHIVE_REVOKED_SIGNERS_FILE", "").strip()
    revoked = json.loads(Path(revoked_path).read_text(encoding="utf-8")) if revoked_path else []
    from portable.sealed_arena_signature import Ed25519ReceiptVerifier
    return Ed25519ReceiptVerifier(public_keys, revoked_signers=revoked)


def main() -> None:
    token = os.environ.get("AUREN_ARCHIVE_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("AUREN_ARCHIVE_TOKEN must be at least 32 characters from a secret manager")
    principal = os.environ.get("AUREN_ARCHIVE_PRINCIPAL", "auren-runtime")
    database = os.environ.get("AUREN_ARCHIVE_DB", ".auren-archive/evidence.sqlite3")
    host = os.environ.get("AUREN_ARCHIVE_BIND", "127.0.0.1")
    port = int(os.environ.get("AUREN_ARCHIVE_PORT", "8765"))
    archive = EvidenceArchive(database, receipt_verifier=load_verifier())

    class Handler(BaseHTTPRequestHandler):
        server_version = "AUREN-Evidence-Archive/1"

        def _reply(self, status: int, body: dict) -> None:
            raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path != "/healthz":
                self._reply(404, {"error": "not_found"})
                return
            self._reply(200, {"status": "ok"})

        def do_POST(self) -> None:
            if self.path != "/v1/auren/events":
                self._reply(404, {"error": "not_found"})
                return
            supplied = self.headers.get("Authorization", "")
            expected = "Bearer " + token
            if not hmac.compare_digest(supplied, expected):
                self._reply(401, {"error": "unauthorized"})
                return
            if self.headers.get_content_type() != "application/json":
                self._reply(415, {"error": "application_json_required"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._reply(400, {"error": "invalid_content_length"})
                return
            if size <= 0 or size > MAX_BODY_BYTES:
                self._reply(413, {"error": "body_size_invalid"})
                return
            try:
                event = json.loads(self.rfile.read(size))
                result = archive.ingest(event, authenticated_principal=principal)
            except ArchiveConflict as exc:
                self._reply(409, {"error": "immutable_event_conflict", "detail": str(exc)})
                return
            except (ValueError, json.JSONDecodeError) as exc:
                self._reply(400, {"error": "invalid_evidence_event", "detail": str(exc)})
                return
            except Exception:
                self._reply(500, {"error": "archive_failure"})
                return
            self._reply(201 if result["status"] == "retained" else 200, result)

        def log_message(self, format: str, *args) -> None:
            # Do not log headers, bearer credentials, or event bodies.
            print(f"{self.log_date_time_string()} {self.address_string()} {format % args}")

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"AUREN evidence archive listening on {host}:{port}; TLS must terminate at a trusted reverse proxy")
    server.serve_forever()


if __name__ == "__main__":
    main()
