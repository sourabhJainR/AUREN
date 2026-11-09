"""Authenticated, stateless Streamable HTTP MCP service for AUREN operators.

The service is a transport adapter only. Durable task, approval, policy and evidence
authority remains in AUREN's existing runtime stores. It intentionally has no generic
shell, arbitrary file, arbitrary URL-fetch or unrestricted provider passthrough tool.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

from .adaptive_trigger import AdaptiveTrigger
from .durable_approval import DurableApprovalStore
from .identity_compat import canonical_auren_home
from .trigger_runtime import TriggerQueueCapacityError


SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
DEFAULT_ENDPOINT_PATH = "/mcp"
DEFAULT_MAX_BODY_BYTES = 64 * 1024
DEFAULT_MAX_RESPONSE_BYTES = 2_097_152
DEFAULT_MAX_TASK_CHARS = 16_000
DEFAULT_MAX_CONTEXT_BYTES = 16_384
DEFAULT_RATE_LIMIT = 120
DEFAULT_RATE_WINDOW_SECONDS = 60
_SCOPE_READ = "auren:tasks:read"
_SCOPE_WRITE = "auren:tasks:write"
_SCOPE_CANCEL = "auren:tasks:cancel"
_SCOPE_RESUME = "auren:tasks:resume"
_SCOPE_EVIDENCE = "auren:evidence:read"
_SCOPE_APPROVAL_READ = "auren:approvals:read"
_SCOPE_APPROVAL_DECIDE = "auren:approvals:decide"
_SCOPE_ADMIN = "auren:admin"
_ALL_SCOPES = (
    _SCOPE_READ, _SCOPE_WRITE, _SCOPE_CANCEL, _SCOPE_RESUME,
    _SCOPE_EVIDENCE, _SCOPE_APPROVAL_READ, _SCOPE_APPROVAL_DECIDE, _SCOPE_ADMIN,
)
_SECRETISH = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}|"
    r"(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[:=]\s*[^\s,;]+"
)


class MCPServiceError(RuntimeError):
    """A safe, caller-visible operator or protocol error."""

    def __init__(self, message: str, *, code: int = -32000, status: int = 400):
        self.code = code
        self.status = status
        super().__init__(message[:500])


@dataclass(frozen=True)
class MCPPrincipal:
    subject: str
    scopes: frozenset[str]
    roles: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()
    issuer: str = "static"

    def allows(self, scope: str) -> bool:
        return scope in self.scopes or _SCOPE_ADMIN in self.scopes


class TokenVerifier(Protocol):
    def verify(self, token: str) -> MCPPrincipal | None: ...


class StaticBearerTokenVerifier:
    """Small-deployment/dev token verifier. Store only SHA-256 token digests."""

    def __init__(self, records: list[Mapping[str, Any]]):
        parsed: list[tuple[str, MCPPrincipal]] = []
        for record in records:
            token = record.get("token")
            subject = record.get("subject")
            scopes = record.get("scopes", [])
            roles = record.get("roles", [])
            projects = record.get("projects", [])
            issuer = record.get("issuer", "static")
            if not isinstance(token, str) or len(token) < 32:
                raise ValueError("static MCP tokens must contain at least 32 characters")
            if not isinstance(subject, str) or not subject.strip():
                raise ValueError("static MCP token subject is required")
            if not isinstance(issuer, str) or not issuer.strip():
                raise ValueError("static MCP token issuer must be non-empty text")
            if (
                not isinstance(scopes, list)
                or not all(isinstance(x, str) for x in scopes)
                or not set(scopes).issubset(_ALL_SCOPES)
            ):
                raise ValueError("static MCP token scopes are invalid")
            if not isinstance(roles, list) or not all(isinstance(x, str) and x for x in roles):
                raise ValueError("static MCP token roles are invalid")
            if not isinstance(projects, list) or not all(isinstance(x, str) and x for x in projects):
                raise ValueError("static MCP token projects are invalid")
            parsed.append((
                hashlib.sha256(token.encode("utf-8")).hexdigest(),
                MCPPrincipal(subject.strip(), frozenset(scopes), tuple(roles), tuple(projects),
                             issuer.strip()),
            ))
        if not parsed:
            raise ValueError("at least one static MCP token is required")
        self._records = tuple(parsed)

    def verify(self, token: str) -> MCPPrincipal | None:
        candidate = hashlib.sha256(token.encode("utf-8")).hexdigest()
        found = None
        for digest, principal in self._records:
            if hmac.compare_digest(candidate, digest):
                found = principal
        return found


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


class OAuthIntrospectionVerifier:
    """RFC 7662 resource-server adapter with audience checks and bounded caching."""

    def __init__(
        self, *, endpoint: str, client_id: str, client_secret: str,
        audience: str, issuer: str, timeout_seconds: float = 3.0, cache_seconds: int = 15,
    ):
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https" or not parsed.netloc
            or parsed.username or parsed.password or parsed.fragment
        ):
            raise ValueError("OAuth introspection endpoint must use HTTPS without userinfo or fragment")
        issuer_url = urlsplit(issuer)
        if (
            issuer_url.scheme != "https" or not issuer_url.netloc
            or issuer_url.username or issuer_url.password
            or issuer_url.query or issuer_url.fragment
        ):
            raise ValueError("OAuth issuer must be an absolute HTTPS URL without query or fragment")
        if not all(isinstance(x, str) and x for x in (client_id, client_secret, audience, issuer)):
            raise ValueError("OAuth introspection credentials, audience and issuer are required")
        if (
            isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds)) or not 0.1 <= timeout_seconds <= 10
            or type(cache_seconds) is not int or not 0 <= cache_seconds <= 60
        ):
            raise ValueError("OAuth timeout/cache bounds are invalid")
        self.endpoint = endpoint
        self.client_id = client_id
        self.client_secret = client_secret
        self.audience = audience
        self.issuer = issuer.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, MCPPrincipal | None]] = {}
        self._lock = threading.Lock()
        self._opener = urllib.request.build_opener(_NoRedirectHandler())

    def verify(self, token: str) -> MCPPrincipal | None:
        if not token or len(token) > 16_384:
            return None
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(digest)
            if cached and cached[0] > now:
                return cached[1]
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode("utf-8")
        ).decode("ascii")
        body = urllib.parse.urlencode({
            "token": token, "token_type_hint": "access_token", "resource": self.audience,
        }).encode("ascii")
        request = urllib.request.Request(
            self.endpoint, data=body, method="POST",
            headers={
                "Authorization": "Basic " + credentials,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        principal = None
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                if response.status != 200:
                    raise ValueError("OAuth introspection did not return HTTP 200")
                payload = json.loads(response.read(32_768).decode("utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("OAuth introspection response must be an object")
            if payload.get("active") is True:
                audience = payload.get("aud")
                audiences = [audience] if isinstance(audience, str) else audience
                expiry = payload.get("exp")
                if (
                    isinstance(audiences, list) and self.audience in audiences
                    and type(expiry) in {int, float} and expiry > time.time()
                    and isinstance(payload.get("sub"), str) and payload["sub"].strip()
                    and isinstance(payload.get("iss"), str)
                    and payload["iss"].rstrip("/") == self.issuer
                    and payload.get("cnf") is None
                    and (payload.get("nbf") is None or (
                        type(payload.get("nbf")) in {int, float} and payload["nbf"] <= time.time()
                    ))
                ):
                    raw_scopes = payload.get("scope", "")
                    scopes = raw_scopes.split() if isinstance(raw_scopes, str) else raw_scopes
                    roles = payload.get("roles", payload.get("groups", []))
                    projects = payload.get("projects", [])
                    if (
                        isinstance(scopes, list) and all(isinstance(x, str) for x in scopes)
                        and isinstance(roles, list) and all(isinstance(x, str) for x in roles)
                        and isinstance(projects, list) and all(isinstance(x, str) for x in projects)
                    ):
                        principal = MCPPrincipal(
                            payload["sub"], frozenset(set(scopes).intersection(_ALL_SCOPES)),
                            tuple(roles), tuple(projects), self.issuer,
                        )
        except (OSError, urllib.error.URLError, TimeoutError, ValueError, TypeError, json.JSONDecodeError):
            principal = None
        with self._lock:
            self._cache[digest] = (now + self.cache_seconds, principal)
            if len(self._cache) > 4096:
                self._cache = {key: item for key, item in self._cache.items() if item[0] > now}
        return principal


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _safe_text(value: Any, limit: int = 1000) -> str:
    return _SECRETISH.sub("[redacted]", str(value))[:limit]


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "[truncated]"
    if isinstance(value, str):
        return _safe_text(value, 1000)
    if isinstance(value, Mapping):
        hidden = {
            "token", "access_token", "refresh_token", "authorization", "password",
            "secret", "api_key", "prompt", "payload", "raw_request", "raw_response",
            "environment", "headers", "stdout", "stderr",
        }
        return {
            str(key): _safe_value(item, depth=depth + 1)
            for key, item in list(value.items())[:100]
            if str(key).lower() not in hidden
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, depth=depth + 1) for item in value[:100]]
    if value is None or type(value) in {bool, int, float}:
        return value
    return _safe_text(value, 500)


def _bounded_json(value: Any, *, limit: int) -> Any:
    encoded = _canonical_json(value)
    if len(encoded.encode("utf-8")) > limit:
        raise ValueError("JSON input exceeds the configured size limit")
    return json.loads(encoded)


class AurenMCPService:
    """Provider-neutral operator surface over one host-authorized AUREN runtime."""

    def __init__(
        self, runtime: Any, *, project_root: str | Path, token_verifier: TokenVerifier,
        resource_url: str, authorization_server_url: str | None = None,
        allowed_origins: tuple[str, ...] = (), allowed_hosts: tuple[str, ...] = (),
        require_https: bool = True, endpoint_path: str = DEFAULT_ENDPOINT_PATH,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        rate_limit: int = DEFAULT_RATE_LIMIT,
        rate_window_seconds: int = DEFAULT_RATE_WINDOW_SECONDS,
        approval_store: DurableApprovalStore | None = None,
        dispatcher_poll_seconds: float = 2.0,
        control_store_path: str | Path | None = None,
        max_pending_tasks: int = 1000,
    ):
        self.runtime = runtime
        self.controller = getattr(runtime, "execution_controller", None)
        self.trigger_runtime = getattr(runtime, "trigger_runtime", None)
        if self.controller is None or self.trigger_runtime is None:
            raise TypeError("runtime must expose the canonical execution_controller and trigger_runtime")
        graph = getattr(getattr(runtime, "orchestrator", None), "graph", None)
        graph_order = getattr(graph, "order", None)
        if not callable(graph_order) or not graph_order():
            raise ValueError("remote MCP requires a configured runtime with a non-empty reviewed agent graph")
        self.project_root = Path(project_root).expanduser().resolve()
        if not self.project_root.is_dir():
            raise ValueError("configured AUREN project_root must be an existing directory")
        parsed_resource = urlsplit(resource_url)
        if (
            parsed_resource.scheme != "https" or not parsed_resource.netloc
            or parsed_resource.username or parsed_resource.password
            or parsed_resource.query or parsed_resource.fragment
        ):
            raise ValueError("MCP resource URL must be an absolute HTTPS URL without query or fragment")
        if authorization_server_url is not None:
            auth_url = urlsplit(authorization_server_url)
            if (
                auth_url.scheme != "https" or not auth_url.netloc
                or auth_url.username or auth_url.password or auth_url.query or auth_url.fragment
            ):
                raise ValueError("authorization server URL must be an absolute HTTPS issuer URL")
        if not endpoint_path.startswith("/") or "?" in endpoint_path or "#" in endpoint_path:
            raise ValueError("MCP endpoint_path must be an absolute path")
        if parsed_resource.path.rstrip("/") != endpoint_path.rstrip("/"):
            raise ValueError("MCP resource URL path must match the configured endpoint path")
        for origin in allowed_origins:
            parsed_origin = urlsplit(origin)
            local_http = parsed_origin.scheme == "http" and parsed_origin.hostname in {"localhost", "127.0.0.1", "::1"}
            if (
                parsed_origin.scheme != "https" and not local_http
                or not parsed_origin.netloc or parsed_origin.path not in {"", "/"}
                or parsed_origin.query or parsed_origin.fragment
                or parsed_origin.username or parsed_origin.password
            ):
                raise ValueError("allowed origins must be exact HTTPS origins (loopback HTTP is allowed for development)")
        for host in allowed_hosts:
            parsed_host = urlsplit("//" + host)
            if not parsed_host.hostname or parsed_host.path or parsed_host.username or parsed_host.password:
                raise ValueError("allowed hosts must be hostnames, optionally with a port")
        if type(max_body_bytes) is not int or not 1024 <= max_body_bytes <= 1_048_576:
            raise ValueError("max_body_bytes must be an integer between 1 KiB and 1 MiB")
        if type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 2_097_152:
            raise ValueError("max_response_bytes must be an integer between 1 KiB and 2 MiB")
        if (
            type(rate_limit) is not int or not 1 <= rate_limit <= 10_000
            or type(rate_window_seconds) is not int or not 1 <= rate_window_seconds <= 3600
        ):
            raise ValueError("rate limit configuration is invalid")
        if (
            isinstance(dispatcher_poll_seconds, bool)
            or not isinstance(dispatcher_poll_seconds, (int, float))
            or not math.isfinite(float(dispatcher_poll_seconds))
            or not 0.1 <= dispatcher_poll_seconds <= 30
        ):
            raise ValueError("dispatcher poll interval must be between 0.1 and 30 seconds")
        self.token_verifier = token_verifier
        self.resource_url = resource_url.rstrip("/")
        self.authorization_server_url = authorization_server_url
        self.allowed_origins = frozenset(origin.rstrip("/") for origin in allowed_origins)
        allowed_hostnames = frozenset(
            (urlsplit("//" + host).hostname.lower(), urlsplit("//" + host).port)
            for host in allowed_hosts
        )
        resource_host = parsed_resource.hostname.lower()
        resource_port = parsed_resource.port
        if allowed_hostnames and not (
            (resource_host, resource_port) in allowed_hostnames
            or ((resource_host, None) in allowed_hostnames and resource_port in {None, 443})
            or ((resource_host, 443) in allowed_hostnames and resource_port in {None, 443})
        ):
            raise ValueError("MCP resource hostname and port must be included in allowed_hosts")
        self.allowed_hosts = allowed_hostnames
        self.require_https = require_https
        self.endpoint_path = endpoint_path
        self.max_body_bytes = max_body_bytes
        self.max_response_bytes = max_response_bytes
        if type(max_pending_tasks) is not int or not 1 <= max_pending_tasks <= 10_000:
            raise ValueError("max_pending_tasks must be between 1 and 10000")
        self.rate_limit = rate_limit
        self.rate_window_seconds = rate_window_seconds
        self.max_pending_tasks = max_pending_tasks
        self.approval_store = approval_store or DurableApprovalStore(self.controller)
        self.trigger = AdaptiveTrigger.for_runtime(runtime)
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._closed = False
        self._dispatcher_last_error: str | None = None
        self._dispatcher_last_run_at: str | None = None
        self._dispatcher_processed_count = 0
        self.dispatcher_poll_seconds = dispatcher_poll_seconds
        self.control_db = (
            Path(control_store_path).expanduser().resolve()
            if control_store_path is not None
            else canonical_auren_home() / "mcp" / "operator-control.sqlite3"
        )
        self.control_db.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_control_store()

    def _initialize_control_store(self) -> None:
        with closing(sqlite3.connect(self.control_db, timeout=10)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS service_control(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    admissions_paused INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    reason_digest TEXT NOT NULL,
                    sequence INTEGER NOT NULL DEFAULT 0,
                    event_digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS service_control_events(
                    sequence INTEGER PRIMARY KEY,
                    event_json TEXT NOT NULL,
                    previous_digest TEXT NOT NULL,
                    event_digest TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS request_rate_buckets(
                    subject TEXT NOT NULL,
                    bucket INTEGER NOT NULL,
                    request_count INTEGER NOT NULL,
                    PRIMARY KEY(subject,bucket)
                );
                CREATE TABLE IF NOT EXISTS operator_profiles(
                    subject_digest TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS immutable_service_control_event_update
                    BEFORE UPDATE ON service_control_events
                    BEGIN SELECT RAISE(ABORT, 'operator control audit events are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_service_control_event_delete
                    BEFORE DELETE ON service_control_events
                    BEGIN SELECT RAISE(ABORT, 'operator control audit events are immutable'); END;
            """)
            row = db.execute("SELECT singleton FROM service_control WHERE singleton=1").fetchone()
            if row is None:
                now = datetime.now(timezone.utc).isoformat()
                event = {"sequence": 0, "type": "genesis", "timestamp": now}
                encoded = _canonical_json(event)
                digest = hashlib.sha256(("0" * 64 + encoded).encode("utf-8")).hexdigest()
                db.execute(
                    "INSERT INTO service_control VALUES(1,0,?,?,?,?,?)",
                    (now, "system", "0" * 64, 0, digest),
                )
                db.execute(
                    "INSERT INTO service_control_events VALUES(0,?,?,?)",
                    (encoded, "0" * 64, digest),
                )

    def verify_control_audit(self) -> bool:
        previous = "0" * 64
        with closing(sqlite3.connect(self.control_db, timeout=10)) as db:
            rows = db.execute(
                "SELECT sequence,event_json,previous_digest,event_digest "
                "FROM service_control_events ORDER BY sequence"
            ).fetchall()
        for expected_sequence, row in enumerate(rows):
            sequence, encoded, recorded_previous, recorded_digest = row
            digest = hashlib.sha256((previous + encoded).encode("utf-8")).hexdigest()
            if sequence != expected_sequence or recorded_previous != previous or recorded_digest != digest:
                return False
            previous = digest
        control = self._control_row()
        return bool(rows) and int(control["sequence"]) == len(rows) - 1 and control["event_digest"] == previous

    def _control_row(self):
        with closing(sqlite3.connect(self.control_db, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            return db.execute("SELECT * FROM service_control WHERE singleton=1").fetchone()

    def _control(self) -> dict[str, Any]:
        row = self._control_row()
        if row is None:
            raise MCPServiceError("operator control state is unavailable", code=-32050, status=503)
        return {
            "admissions_paused": bool(row["admissions_paused"]),
            "updated_at": row["updated_at"], "updated_by": row["updated_by"],
            "reason_digest": row["reason_digest"], "sequence": int(row["sequence"]),
            "event_digest": row["event_digest"],
            "audit_chain_valid": self.verify_control_audit(),
        }

    def _set_admissions(self, paused: bool, principal: MCPPrincipal, reason: str) -> dict[str, Any]:
        if not self.verify_control_audit():
            raise MCPServiceError("operator control audit is inconsistent; state changes are blocked", code=-32052, status=503)
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ValueError("reason must be non-empty text of at most 500 characters")
        now = datetime.now(timezone.utc).isoformat()
        reason_digest = hashlib.sha256(reason.strip().encode("utf-8")).hexdigest()
        with closing(sqlite3.connect(self.control_db, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT sequence,event_digest FROM service_control WHERE singleton=1").fetchone()
            if row is None:
                raise MCPServiceError("operator control state is unavailable", code=-32050, status=503)
            sequence = int(row[0]) + 1
            event = {
                "sequence": sequence, "paused": paused, "subject": self._principal_key(principal),
                "reason_digest": reason_digest, "timestamp": now,
            }
            encoded = _canonical_json(event)
            digest = hashlib.sha256((str(row[1]) + encoded).encode("utf-8")).hexdigest()
            db.execute(
                "INSERT INTO service_control_events VALUES(?,?,?,?)",
                (sequence, encoded, str(row[1]), digest),
            )
            db.execute(
                "UPDATE service_control SET admissions_paused=?,updated_at=?,updated_by=?,reason_digest=?,sequence=?,event_digest=? WHERE singleton=1",
                (int(paused), now, self._principal_key(principal), reason_digest, sequence, digest),
            )
        return self._control()

    @staticmethod
    def _principal_key(principal: MCPPrincipal) -> str:
        return hashlib.sha256(
            (principal.issuer.rstrip("/") + "\0" + principal.subject).encode("utf-8")
        ).hexdigest()

    def _profile(self, principal: MCPPrincipal) -> dict[str, str]:
        subject_digest = self._principal_key(principal)
        with closing(sqlite3.connect(self.control_db, timeout=10)) as db, db:
            row = db.execute(
                "SELECT profile_id FROM operator_profiles WHERE subject_digest=?",
                (subject_digest,),
            ).fetchone()
            if row is None:
                profile_id = "auren_" + secrets.token_urlsafe(18)
                db.execute(
                    "INSERT INTO operator_profiles VALUES(?,?,?)",
                    (subject_digest, profile_id, datetime.now(timezone.utc).isoformat()),
                )
            else:
                profile_id = str(row[0])
        return {"id": profile_id, "name": "AUREN", "nickname": "AUREN workspace"}

    def _require(self, principal: MCPPrincipal | None, scope: str) -> None:
        if principal is None or not principal.allows(scope):
            raise PermissionError("authenticated principal lacks the required AUREN scope")

    def _principal_has_project_access(self, principal: MCPPrincipal) -> bool:
        if not principal.projects:
            return True
        for project in principal.projects:
            try:
                candidate = Path(project).expanduser().resolve(strict=True)
                if candidate.is_dir() and os.path.samefile(candidate, self.project_root):
                    return True
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
        return False

    def _require_project(self, principal: MCPPrincipal | None) -> None:
        if principal is None:
            raise PermissionError("authentication is required for this AUREN workspace")
        if not self._principal_has_project_access(principal):
            raise PermissionError("authenticated principal is not authorized for this AUREN workspace")

    def _event_for_principal(self, event_id: str, principal: MCPPrincipal):
        normalized_id = event_id.removeprefix("llm-chat:")
        event = self.trigger_runtime.get_event(normalized_id)
        if event is None or event.kind != "adaptive_runtime":
            return None
        if not self._principal_has_project_access(principal):
            raise PermissionError("authenticated principal is not authorized for this AUREN workspace")
        owner = event.payload.get("_auren_mcp_subject")
        if owner != self._principal_key(principal) and not principal.allows(_SCOPE_ADMIN):
            raise PermissionError("task is not visible to this principal")
        return event

    def _trigger_summary(self, event: Any) -> dict[str, Any]:
        runtime_task_id = "llm-chat:" + event.event_id
        try:
            runtime_progress = self.controller.progress_journal.snapshot(
                runtime_task_id, stale_after_seconds=120,
            )
        except Exception as exc:
            runtime_progress = {"status": "unavailable", "error_type": type(exc).__name__}
        execution_status = event.outcome.get("result_status") if isinstance(event.outcome, Mapping) else None
        reported_status = (
            execution_status if event.status == "success" and isinstance(execution_status, str)
            else "dispatched" if event.status == "success" else event.status
        )
        return {
            "task_id": event.event_id, "runtime_task_id": runtime_task_id,
            "kind": "adaptive_runtime",
            "status": reported_status, "dispatch_status": event.status,
            "execution_status": execution_status,
            "completion_verified": str(execution_status).lower() in {"completed", "complete", "success"},
            "cancellation_requested": self.trigger_runtime.is_cancel_requested(event.event_id),
            "attempts": event.attempts,
            "max_attempts": event.max_attempts, "created_at": event.created_at,
            "available_at": event.available_at, "claimed_at": event.claimed_at,
            "lease_until": event.lease_until, "completed_at": event.completed_at,
            "last_detail": _safe_text(event.last_detail, 500),
            "outcome": _safe_value(dict(event.outcome)), "runtime_progress": _safe_value(runtime_progress),
        }

    def _task_summary(self, task_id: str) -> dict[str, Any]:
        task = self.controller.task(task_id)
        if task is None:
            raise KeyError("task not found")
        try:
            progress = self.controller.progress_journal.snapshot(task_id, stale_after_seconds=120)
        except Exception as exc:
            progress = {"status": "unavailable", "error_type": type(exc).__name__}
        return {
            "task_id": task.id, "state": task.state, "attempts": task.attempts,
            "work_unit_id": task.work_unit_id, "deadline_at": task.deadline_at,
            "progress": _safe_value(progress),
        }

    def list_tools(self, principal: MCPPrincipal | None) -> list[dict[str, Any]]:
        definitions = [
            ("auren.service_status", "Read AUREN service health, queue capacity and admission state.", _SCOPE_READ,
             {"type": "object", "properties": {}, "additionalProperties": False}),
            ("auren.submit_task", "Durably submit an engineering task to the configured AUREN workspace. A caller-scoped idempotency key is required. Returns an acceptance receipt, not a completion claim.", _SCOPE_WRITE,
             {"type": "object", "required": ["task", "idempotency_key"], "properties": {
                 "task": {"type": "string", "minLength": 1, "maxLength": DEFAULT_MAX_TASK_CHARS},
                 "context": {"type": "object", "additionalProperties": True},
                 "priority": {"type": "string", "enum": ["high", "normal", "low"]},
                 "idempotency_key": {"type": "string", "minLength": 16, "maxLength": 200},
                 "max_attempts": {"type": "integer", "minimum": 1, "maximum": 8},
             }, "additionalProperties": False}),
            ("auren.list_tasks", "List bounded task summaries for the configured AUREN workspace.", _SCOPE_READ,
             {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "additionalProperties": False}),
            ("auren.get_task", "Read one task or trigger status without exposing its full prompt or payload.", _SCOPE_READ,
             {"type": "object", "required": ["task_id"], "properties": {"task_id": {"type": "string", "minLength": 1, "maxLength": 200}}, "additionalProperties": False}),
            ("auren.cancel_task", "Request cancellation of a controller task or pending/retryable trigger, or signal an active runtime guard. Active work remains nonterminal until the supervisor confirms its outcome.", _SCOPE_CANCEL,
             {"type": "object", "required": ["task_id", "reason"], "properties": {"task_id": {"type": "string"}, "reason": {"type": "string", "minLength": 1, "maxLength": 500}}, "additionalProperties": False}),
            ("auren.get_evidence", "Read bounded progress and evidence references for a task.", _SCOPE_EVIDENCE,
             {"type": "object", "required": ["task_id"], "properties": {"task_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "additionalProperties": False}),
            ("auren.list_approvals", "List durable approval records without exposing approval checkpoints or credentials.", _SCOPE_APPROVAL_READ,
             {"type": "object", "properties": {"status": {"type": "string", "enum": ["pending", "approved", "rejected", "expired", "cancelled", "executing", "completed", "failed", "outcome_unknown"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "additionalProperties": False}),
            ("auren.get_approval", "Read one durable approval record and its immutable digests.", _SCOPE_APPROVAL_READ,
             {"type": "object", "required": ["approval_id"], "properties": {"approval_id": {"type": "string"}}, "additionalProperties": False}),
            ("auren.decide_approval", "Record an approval decision using the authenticated principal identity and host-configured approver authorization. Caller-supplied roles are never accepted.", _SCOPE_APPROVAL_DECIDE,
             {"type": "object", "required": ["approval_id", "decision", "reason"], "properties": {
                 "approval_id": {"type": "string"}, "decision": {"type": "string", "enum": ["approve", "reject"]},
                 "reason": {"type": "string", "minLength": 1, "maxLength": 240},
             }, "additionalProperties": False}),
            ("auren.resume_provider_task", "Authorize read-only provider reconciliation for a blocked task. Host-side provider resume authorization remains mandatory.", _SCOPE_RESUME,
             {"type": "object", "required": ["task_id", "clarification_id", "reason"], "properties": {
                 "task_id": {"type": "string"}, "clarification_id": {"type": "string"},
                 "reason": {"type": "string", "minLength": 1, "maxLength": 500},
             }, "additionalProperties": False}),
            ("auren.pause_admissions", "Pause new MCP task submissions for this service. Existing work is not stopped.", _SCOPE_ADMIN,
             {"type": "object", "required": ["reason"], "properties": {"reason": {"type": "string", "minLength": 1, "maxLength": 500}}, "additionalProperties": False}),
            ("auren.resume_admissions", "Resume new MCP task submissions after an operator pause. Existing task state is unchanged.", _SCOPE_ADMIN,
             {"type": "object", "required": ["reason"], "properties": {"reason": {"type": "string", "minLength": 1, "maxLength": 500}}, "additionalProperties": False}),
        ]
        definitions.insert(1, (
            "auren.get_profile",
            "Return the stable opaque identity associated with the authenticated AUREN connection.",
            _SCOPE_READ,
            {"type": "object", "properties": {}, "additionalProperties": False},
        ))
        tools = []
        for name, description, scope, schema in definitions:
            if principal is not None and not principal.allows(scope):
                continue
            read_only = name in {
                "auren.service_status", "auren.get_profile", "auren.list_tasks",
                "auren.get_task", "auren.get_evidence", "auren.list_approvals",
                "auren.get_approval",
            }
            consequential = name in {
                "auren.submit_task", "auren.cancel_task", "auren.decide_approval",
                "auren.resume_provider_task", "auren.pause_admissions", "auren.resume_admissions",
            }
            title = name.removeprefix("auren.").replace("_", " ").title()[:64]
            tool = {
                "name": name, "title": title, "description": description, "inputSchema": schema,
                "annotations": {
                    "title": title,
                    "readOnlyHint": read_only,
                    "destructiveHint": consequential,
                    "openWorldHint": consequential,
                },
                "_meta": {"securitySchemes": [{"type": "oauth2", "scopes": [scope]}]},
            }
            if name == "auren.get_profile":
                tool["outputSchema"] = {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "name": {"type": "string"},
                        "nickname": {"type": "string"},
                    },
                    "required": ["id"],
                    "additionalProperties": False,
                }
                tool["_meta"]["openai/profile"] = True
            tools.append(tool)
        return tools

    @staticmethod
    def _contains_host_controlled_context(value: Any) -> bool:
        forbidden = {
            "project_root", "root", "handler", "tool", "command", "shell",
            "authorization", "token", "api_key", "environment", "headers",
        }
        if isinstance(value, Mapping):
            return any(
                str(key).lower() in forbidden
                or AurenMCPService._contains_host_controlled_context(item)
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple)):
            return any(AurenMCPService._contains_host_controlled_context(item) for item in value)
        return False

    @staticmethod
    def _validate_schema(value: Any, schema: Mapping[str, Any], path: str = "arguments") -> None:
        expected = schema.get("type")
        valid = {
            "object": isinstance(value, Mapping),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "integer": type(value) is int,
            "number": type(value) in {int, float},
            "boolean": type(value) is bool,
            "null": value is None,
        }
        if expected in valid and not valid[expected]:
            raise ValueError(f"{path} must be {expected}")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError(f"{path} is not an allowed value")
        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0):
                raise ValueError(f"{path} is too short")
            if len(value) > schema.get("maxLength", 1_000_000):
                raise ValueError(f"{path} is too long")
        if type(value) in {int, float}:
            if "minimum" in schema and value < schema["minimum"]:
                raise ValueError(f"{path} is below its minimum")
            if "maximum" in schema and value > schema["maximum"]:
                raise ValueError(f"{path} is above its maximum")
        if isinstance(value, list):
            if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 100_000):
                raise ValueError(f"{path} has an invalid item count")
            item_schema = schema.get("items")
            if isinstance(item_schema, Mapping):
                for index, item in enumerate(value):
                    AurenMCPService._validate_schema(item, item_schema, f"{path}[{index}]")
        if isinstance(value, Mapping):
            properties = schema.get("properties", {})
            required = schema.get("required", ())
            missing = [key for key in required if key not in value]
            if missing:
                raise ValueError(f"{path} is missing required field(s): {', '.join(missing)}")
            if schema.get("additionalProperties") is False:
                unknown = set(value) - set(properties)
                if unknown:
                    raise ValueError(f"{path} contains unsupported field(s): {', '.join(sorted(map(str, unknown)))}")
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, Mapping):
                    AurenMCPService._validate_schema(value[key], child_schema, f"{path}.{key}")

    def call_tool(self, name: str, arguments: Mapping[str, Any], principal: MCPPrincipal | None) -> Any:
        if not isinstance(arguments, Mapping):
            raise TypeError("tool arguments must be an object")
        definition = next(
            (item for item in self.list_tools(None) if item["name"] == name), None,
        )
        if definition is None:
            raise KeyError("unknown or unauthorized AUREN operator tool")
        self._validate_schema(arguments, definition["inputSchema"])
        self._require_project(principal)
        if name == "auren.get_profile":
            self._require(principal, _SCOPE_READ)
            return self._profile(principal)
        if name == "auren.service_status":
            self._require(principal, _SCOPE_READ)
            try:
                queue = self.controller.queue_admission_snapshot()
            except Exception as exc:
                queue = {"status": "unavailable", "error_type": type(exc).__name__}
            try:
                findings = list(self.controller.health_findings())[:50]
            except Exception as exc:
                findings = ["health_check_unavailable:" + type(exc).__name__]
            try:
                control = self._control()
            except Exception as exc:
                control = {
                    "admissions_paused": True, "audit_chain_valid": False,
                    "updated_at": None, "updated_by": None, "reason_digest": None,
                    "sequence": -1, "event_digest": None,
                    "error_type": type(exc).__name__,
                }
            try:
                pending_mcp_tasks = self.trigger_runtime.count_events(
                    kind="adaptive_runtime", statuses=("pending", "claimed", "retryable"),
                )
                trigger_queue_status = "available"
            except Exception as exc:
                pending_mcp_tasks = self.max_pending_tasks
                trigger_queue_status = "unavailable:" + type(exc).__name__
            dispatcher = {
                "running": bool(self._worker and self._worker.is_alive()),
                "last_run_at": self._dispatcher_last_run_at,
                "last_error_type": self._dispatcher_last_error,
                "processed_count": self._dispatcher_processed_count,
                "poll_seconds": self.dispatcher_poll_seconds,
            }
            degraded = (
                bool(findings) or queue.get("status") == "unavailable"
                or not control["audit_chain_valid"] or not dispatcher["running"]
                or dispatcher["last_error_type"] is not None
                or trigger_queue_status != "available"
            )
            return {
                "status": "degraded" if degraded else "ok",
                "workspace_id": hashlib.sha256(str(self.project_root).encode("utf-8")).hexdigest()[:16],
                "admissions": control,
                "queue": queue, "dispatcher": dispatcher,
                "mcp_admission": {
                    "pending_or_active": pending_mcp_tasks,
                    "max_pending": self.max_pending_tasks,
                    "at_capacity": pending_mcp_tasks >= self.max_pending_tasks,
                    "status": trigger_queue_status,
                },
                "health_findings": [_safe_text(x, 300) for x in findings],
                "mcp_protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
            }
        if name == "auren.submit_task":
            self._require(principal, _SCOPE_WRITE)
            control = self._control()
            if not control["audit_chain_valid"]:
                raise MCPServiceError("operator control audit is inconsistent; task admission is blocked", code=-32052, status=503)
            if control["admissions_paused"]:
                raise MCPServiceError("new AUREN task admissions are paused", code=-32051, status=423)
            task = arguments.get("task")
            if not isinstance(task, str) or not task.strip() or len(task) > DEFAULT_MAX_TASK_CHARS:
                raise ValueError("task must be non-empty text within the configured limit")
            if _SECRETISH.search(task):
                raise ValueError("task text appears to contain a credential; use a secret reference instead")
            priority = arguments.get("priority", "normal")
            if priority not in {"high", "normal", "low"}:
                raise ValueError("priority must be high, normal or low")
            max_attempts = arguments.get("max_attempts", 3)
            if type(max_attempts) is not int or not 1 <= max_attempts <= 8:
                raise ValueError("max_attempts must be an integer between 1 and 8")
            context = _bounded_json(arguments.get("context", {}), limit=DEFAULT_MAX_CONTEXT_BYTES)
            if not isinstance(context, Mapping):
                raise TypeError("context must be an object")
            if self._contains_host_controlled_context(context):
                raise ValueError("context contains a host-controlled or credential field")
            if _SECRETISH.search(_canonical_json(context)):
                raise ValueError("task context appears to contain a credential; use a secret reference instead")
            idempotency = arguments.get("idempotency_key")
            if not isinstance(idempotency, str) or not 16 <= len(idempotency) <= 200:
                raise ValueError("idempotency_key must contain 16 to 200 characters")
            event_id = hashlib.sha256(
                (self._principal_key(principal) + "\0" + idempotency).encode("utf-8")
            ).hexdigest()[:40]
            task_context = dict(context)
            task_context["_auren_mcp_subject"] = self._principal_key(principal)
            try:
                receipt = self.trigger.trigger_adaptive_runtime(
                    task.strip(), self.project_root, task_context,
                    priority=priority, event_id=event_id, max_attempts=max_attempts,
                    fire_and_forget=True, max_pending_events=self.max_pending_tasks,
                )
            except TriggerQueueCapacityError as exc:
                raise MCPServiceError(
                    "AUREN MCP task queue is at capacity; retry after an existing task advances",
                    code=-32053, status=429,
                ) from exc
            event = self.trigger_runtime.get_event(receipt.trigger_id)
            summary = self._trigger_summary(event) if event is not None else {}
            trigger_status = event.status if event is not None else receipt.status
            reported_status = (
                "accepted" if trigger_status in {"pending", "claimed"}
                else summary.get("status", trigger_status)
            )
            return {
                "task_id": receipt.trigger_id, "status": reported_status,
                "trigger_status": trigger_status, "accepted_at": receipt.accepted_at,
                "completion_verified": bool(summary.get("completion_verified", False)),
                "cancellation_requested": bool(summary.get("cancellation_requested", False)),
            }
        if name == "auren.list_tasks":
            self._require(principal, _SCOPE_READ)
            limit = arguments.get("limit", 30)
            if type(limit) is not int or not 1 <= limit <= 100:
                raise ValueError("limit must be between 1 and 100")
            events = self.trigger_runtime.list_events(kind="adaptive_runtime", limit=500)
            trigger_tasks = [
                self._trigger_summary(event) for event in events
                if event.payload.get("_auren_mcp_subject") == self._principal_key(principal)
                or principal.allows(_SCOPE_ADMIN)
            ][:limit]
            if principal.allows(_SCOPE_ADMIN):
                try:
                    progress = self.controller.progress_journal.list_snapshots(stale_after_seconds=120)
                    progress_status = "available"
                except Exception as exc:
                    progress = []
                    progress_status = "unavailable:" + type(exc).__name__
            else:
                progress = []
                progress_status = "restricted_to_admin_scope"
            return {
                "trigger_tasks": trigger_tasks,
                "controller_tasks": _safe_value(progress[-limit:][::-1]),
                "controller_tasks_status": progress_status,
            }
        if name == "auren.get_task":
            self._require(principal, _SCOPE_READ)
            task_id = arguments.get("task_id")
            if not isinstance(task_id, str) or not task_id.strip():
                raise ValueError("task_id is required")
            event = self._event_for_principal(task_id.strip(), principal)
            if event is not None:
                return self._trigger_summary(event)
            if not principal.allows(_SCOPE_ADMIN):
                raise PermissionError("controller task is not visible to this principal")
            return self._task_summary(task_id.strip())
        if name == "auren.cancel_task":
            self._require(principal, _SCOPE_CANCEL)
            task_id, reason = arguments.get("task_id"), arguments.get("reason")
            if not isinstance(task_id, str) or not task_id.strip():
                raise ValueError("task_id is required")
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
                raise ValueError("reason is required and must be at most 500 characters")
            event = self._event_for_principal(task_id.strip(), principal)
            if event is not None:
                requester_digest = self._principal_key(principal)
                cancelled = self.trigger_runtime.cancel_pending(
                    event.event_id, reason=reason, requester_digest=requester_digest,
                )
                if cancelled:
                    return {"task_id": task_id, "cancellation_requested": True, "status": "cancelled"}
                signalled = False
                if event.status == "claimed":
                    cancel = getattr(self.runtime, "request_cancel", None)
                    if callable(cancel):
                        signalled = bool(cancel(
                            "llm-chat:" + event.event_id, reason=reason,
                        ))
                    if signalled:
                        signalled = self.trigger_runtime.request_cancel_claimed(
                            event.event_id, reason=reason, requester_digest=requester_digest,
                        )
                return {
                    "task_id": task_id, "cancellation_requested": signalled,
                    "status": "cancellation_requested" if signalled else event.status,
                    "note": "the durable trigger remains nonterminal until the running supervisor records its outcome",
                }
            if not principal.allows(_SCOPE_ADMIN):
                raise PermissionError("controller task is not visible to this principal")
            cancelled = self.controller.request_cancellation(task_id.strip(), reason=reason)
            task = self.controller.task(task_id.strip())
            return {"task_id": task_id, "cancellation_requested": cancelled,
                    "status": task.state if task else "not_found"}
        if name == "auren.get_evidence":
            self._require(principal, _SCOPE_EVIDENCE)
            task_id = arguments.get("task_id")
            limit = arguments.get("limit", 50)
            if not isinstance(task_id, str) or not task_id.strip():
                raise ValueError("task_id is required")
            if type(limit) is not int or not 1 <= limit <= 100:
                raise ValueError("limit must be between 1 and 100")
            event = self._event_for_principal(task_id.strip(), principal)
            if event is not None:
                runtime_task_id = "llm-chat:" + event.event_id
                try:
                    snapshot = self.controller.progress_journal.snapshot(runtime_task_id)
                    events = self.controller.progress_journal.events_for_task(runtime_task_id, limit=limit)
                except Exception as exc:
                    snapshot = {"status": "unavailable", "error_type": type(exc).__name__}
                    events = []
                return {
                    "task_id": task_id, "trigger": self._trigger_summary(event),
                    "snapshot": _safe_value(snapshot), "events": _safe_value(events),
                }
            if not principal.allows(_SCOPE_ADMIN):
                raise PermissionError("controller task evidence is not visible to this principal")
            try:
                events = self.controller.progress_journal.events_for_task(task_id.strip(), limit=limit)
                snapshot = self.controller.progress_journal.snapshot(task_id.strip())
            except Exception as exc:
                events = []
                snapshot = {"status": "unavailable", "error_type": type(exc).__name__}
            return {
                "task_id": task_id, "snapshot": _safe_value(snapshot),
                "events": [{
                    key: _safe_text(value, 1000) if isinstance(value, str) else _safe_value(value)
                    for key, value in row.items()
                    if key in {"timestamp", "event", "state", "stage", "message", "sequence",
                               "evidence_ref", "decision_required", "next_action",
                               "completed_units", "total_units"}
                } for row in events],
            }
        if name == "auren.list_approvals":
            self._require(principal, _SCOPE_APPROVAL_READ)
            limit = arguments.get("limit", 30)
            status = arguments.get("status")
            if type(limit) is not int or not 1 <= limit <= 100:
                raise ValueError("limit must be between 1 and 100")
            events = self.approval_store.events()
            ids = list(dict.fromkeys(str(row["approval_id"]) for row in reversed(events)))
            records = []
            for approval_id in ids:
                try:
                    record = self.approval_store.get(approval_id)
                except KeyError:
                    continue
                if status is None or record.status == status:
                    records.append(self._approval_summary(record))
                if len(records) >= limit:
                    break
            return {"approvals": records}
        if name == "auren.get_approval":
            self._require(principal, _SCOPE_APPROVAL_READ)
            record = self.approval_store.get(str(arguments.get("approval_id", "")))
            return self._approval_summary(record)
        if name == "auren.decide_approval":
            self._require(principal, _SCOPE_APPROVAL_DECIDE)
            approval_id = str(arguments.get("approval_id", ""))
            decision = arguments.get("decision")
            reason = arguments.get("reason")
            if decision not in {"approve", "reject"} or not isinstance(reason, str) or not reason.strip():
                raise ValueError("decision and reason are required")
            record = self.approval_store.get(approval_id)
            decided = self.approval_store.decide(
                approval_id, decision, approver_id=principal.subject,
                roles=principal.roles, scope=record.scope, reason=reason,
            )
            return self._approval_summary(decided)
        if name == "auren.resume_provider_task":
            self._require(principal, _SCOPE_RESUME)
            task_id = arguments.get("task_id")
            clarification_id = arguments.get("clarification_id")
            reason = arguments.get("reason")
            if not all(isinstance(x, str) and x.strip() for x in (task_id, clarification_id, reason)):
                raise ValueError("task_id, clarification_id and reason are required")
            event = self._event_for_principal(task_id.strip(), principal)
            if event is None and not principal.allows(_SCOPE_ADMIN):
                raise PermissionError("controller task is not visible to this principal")
            controller_task_id = "llm-chat:" + event.event_id if event is not None else task_id.strip()
            resumed = self.controller.resume_blocked_provider_task(
                controller_task_id, clarification_id=clarification_id.strip(),
                operator_id=principal.subject, operator_reason=reason.strip(),
            )
            return {"task_id": task_id, "reconciliation_resume_authorized": bool(resumed)}
        if name == "auren.pause_admissions":
            self._require(principal, _SCOPE_ADMIN)
            return self._set_admissions(True, principal, str(arguments.get("reason", "")))
        if name == "auren.resume_admissions":
            self._require(principal, _SCOPE_ADMIN)
            return self._set_admissions(False, principal, str(arguments.get("reason", "")))
        raise KeyError("unknown AUREN operator tool")

    @staticmethod
    def _approval_summary(record: Any) -> dict[str, Any]:
        return {
            "approval_id": record.approval_id, "task_id": record.task_id,
            "operation_id": record.operation_id, "requested_action": record.requested_action,
            "request_fingerprint": record.request_fingerprint, "artifact_digest": record.artifact_digest,
            "evidence_digest": record.evidence_digest, "policy_digest": record.policy_digest,
            "requester_id": record.requester_id, "required_role": record.required_role,
            "scope": record.scope, "expires_at": record.expires_at,
            "required_approvals": record.required_approvals,
            "separation_of_duties": record.separation_of_duties,
            "status": record.status, "version": record.version,
            "created_at": record.created_at, "updated_at": record.updated_at,
            "reason": _safe_text(record.reason, 240), "receipt_digest": record.receipt_digest,
        }

    def _check_rate(self, principal: MCPPrincipal) -> None:
        bucket = int(time.time() // self.rate_window_seconds)
        subject_key = self._principal_key(principal)
        with closing(sqlite3.connect(self.control_db, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO request_rate_buckets(subject,bucket,request_count) VALUES(?,?,1) "
                "ON CONFLICT(subject,bucket) DO UPDATE SET request_count=request_count+1",
                (subject_key, bucket),
            )
            count = db.execute(
                "SELECT request_count FROM request_rate_buckets WHERE subject=? AND bucket=?",
                (subject_key, bucket),
            ).fetchone()[0]
            db.execute("DELETE FROM request_rate_buckets WHERE bucket<?", (bucket - 2,))
        if int(count) > self.rate_limit:
            raise MCPServiceError("rate limit exceeded", code=-32029, status=429)

    def authenticate(self, headers: Mapping[str, str]) -> MCPPrincipal | None:
        authorization = headers.get("authorization", "")
        if not authorization:
            return None
        if not authorization.startswith("Bearer "):
            return None
        token = authorization[7:].strip()
        if not token or len(token) > 16_384:
            return None
        principal = self.token_verifier.verify(token)
        if principal is not None:
            self._check_rate(principal)
        return principal

    def resource_metadata_path(self) -> str:
        resource_path = urlsplit(self.resource_url).path.rstrip("/")
        return "/.well-known/oauth-protected-resource" + resource_path

    def authentication_challenge(self, *, error: str, description: str) -> str:
        resource = urlsplit(self.resource_url)
        metadata = f"{resource.scheme}://{resource.netloc}{self.resource_metadata_path()}"
        safe_description = _safe_text(description, 180).replace('"', "'")
        return (
            f'Bearer resource_metadata="{metadata}", error="{error}", '
            f'error_description="{safe_description}"'
        )

    def protected_resource_metadata(self) -> dict[str, Any]:
        metadata = {
            "resource": self.resource_url,
            "resource_name": "AUREN Remote MCP",
            "resource_documentation": "https://github.com/sourabhJainR/AUREN/blob/main/docs/REMOTE_MCP_OPERATIONS.md",
            "resource_policy_uri": "https://github.com/sourabhJainR/AUREN/blob/main/docs/REMOTE_MCP_PRIVACY.md",
            "bearer_methods_supported": ["header"],
            "scopes_supported": list(_ALL_SCOPES),
        }
        if self.authorization_server_url:
            metadata["authorization_servers"] = [self.authorization_server_url]
        return metadata

    def _rpc(self, request: Mapping[str, Any], principal: MCPPrincipal | None) -> dict[str, Any] | None:
        if not isinstance(request, Mapping) or request.get("jsonrpc") != "2.0":
            raise MCPServiceError("invalid JSON-RPC 2.0 request", code=-32600, status=400)
        if "method" not in request:
            raise MCPServiceError("JSON-RPC method is required", code=-32600, status=400)
        method = request.get("method")
        request_id = request.get("id")
        notification = "id" not in request
        if not isinstance(method, str) or not method:
            raise MCPServiceError("JSON-RPC method is invalid", code=-32600, status=400)
        if not notification and (
            request_id is None or type(request_id) not in {str, int, float}
        ):
            raise MCPServiceError("JSON-RPC id must be a string or number", code=-32600, status=400)
        if notification and method not in {"notifications/initialized", "ping"}:
            raise MCPServiceError("this MCP method requires a JSON-RPC request id", code=-32600, status=400)
        params = request.get("params", {})
        if not isinstance(params, Mapping):
            raise MCPServiceError("JSON-RPC params must be an object", code=-32600, status=400)
        if method == "initialize":
            if notification or request_id is None:
                raise MCPServiceError("initialize must be a JSON-RPC request with an id", code=-32600, status=400)
            version = params.get("protocolVersion", "2025-03-26")
            if version not in SUPPORTED_PROTOCOL_VERSIONS:
                raise MCPServiceError(
                    "unsupported MCP protocol version; supported: " + ", ".join(SUPPORTED_PROTOCOL_VERSIONS),
                    code=-32000, status=400,
                )
            return {"jsonrpc": "2.0", "id": request_id, "result": {
                "protocolVersion": version,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                },
                "serverInfo": {"name": "auren-operator-mcp", "version": "1.0.0"},
                "instructions": (
                    "AUREN is the execution and policy authority. Task acceptance is not task completion. "
                    "Use task status and evidence tools to verify outcomes. Consequential actions remain approval-gated."
                ),
            }}
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return None if notification else {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": self.list_tools(principal)}}
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, Mapping):
                raise MCPServiceError("tools/call requires a name and object arguments", code=-32602, status=400)
            try:
                result = self.call_tool(name, arguments, principal)
                return {"jsonrpc": "2.0", "id": request_id, "result": {
                    "content": [{"type": "text", "text": _canonical_json(result)}],
                    "structuredContent": result,
                }}
            except PermissionError as exc:
                challenge = self.authentication_challenge(
                    error="invalid_token" if principal is None else "insufficient_scope",
                    description="Authenticate and grant the required AUREN permission.",
                )
                return {"jsonrpc": "2.0", "id": request_id, "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": _safe_text(exc, 500)}],
                    "_meta": {"mcp/www_authenticate": [challenge]},
                }}
            except (KeyError, TypeError, ValueError, MCPServiceError) as exc:
                return {"jsonrpc": "2.0", "id": request_id, "result": {
                    "isError": True, "content": [{"type": "text", "text": _safe_text(exc, 500)}],
                }}
            except Exception as exc:
                return {"jsonrpc": "2.0", "id": request_id, "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "AUREN operator operation failed closed (" + type(exc).__name__ + ")."}],
                }}
        if method == "resources/list":
            self._require(principal, _SCOPE_READ)
            return {"jsonrpc": "2.0", "id": request_id, "result": {"resources": [
                {"uri": "auren://service/status", "name": "AUREN service status",
                 "description": "Read-only service health and operator admission state.",
                 "mimeType": "application/json"},
            ]}}
        if method == "resources/read":
            uri = params.get("uri")
            if uri != "auren://service/status":
                raise MCPServiceError("unknown AUREN resource", code=-32602, status=404)
            self._require(principal, _SCOPE_READ)
            result = self.call_tool("auren.service_status", {}, principal)
            return {"jsonrpc": "2.0", "id": request_id, "result": {
                "contents": [{"uri": uri, "mimeType": "application/json", "text": _canonical_json(result)}],
            }}
        if notification:
            return None
        raise MCPServiceError("method not found: " + method, code=-32601, status=404)

    def readiness(self) -> bool:
        worker_ready = bool(self._worker and self._worker.is_alive())
        try:
            audit_ready = self.verify_control_audit()
            self.trigger_runtime.list_events(limit=1)
        except Exception:
            return False
        return worker_ready and audit_ready

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()

        def dispatch_loop():
            while not self._stop.is_set():
                try:
                    dispatched = self.trigger.dispatch_once(limit=10)
                    self._dispatcher_last_error = None
                    self._dispatcher_last_run_at = datetime.now(timezone.utc).isoformat()
                    self._dispatcher_processed_count += len(dispatched or ())
                except Exception as exc:
                    self._dispatcher_last_error = type(exc).__name__
                    self._dispatcher_last_run_at = datetime.now(timezone.utc).isoformat()
                self._stop.wait(self.dispatcher_poll_seconds)

        self._worker = threading.Thread(
            target=dispatch_loop, name="auren-mcp-trigger-dispatcher", daemon=True,
        )
        self._worker.start()

    def close(self) -> None:
        """Gracefully stop dispatch and release service-owned runtime references."""
        if self._closed:
            return
        self._stop.set()
        worker = self._worker
        if worker and worker.is_alive():
            worker.join(timeout=30)
            if worker.is_alive():
                raise TimeoutError("remote MCP dispatcher did not stop within 30 seconds")
        self._worker = None
        trigger = self.trigger
        if trigger is not None:
            trigger.close(wait_for_background=True)
        self.trigger = None
        self.approval_store = None
        self.trigger_runtime = None
        self.controller = None
        self.runtime = None
        self._closed = True


class AurenMCPASGI:
    """ASGI HTTP transport for MCP Streamable HTTP (JSON responses; no SSE stream)."""

    def __init__(self, service: AurenMCPService):
        self.service = service

    @staticmethod
    def _headers(scope: Mapping[str, Any]) -> dict[str, str]:
        return {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in scope.get("headers", ())
        }

    def _origin_allowed(self, origin: str | None) -> bool:
        if origin is None:
            return True
        if origin == "null":
            return False
        return origin in self.service.allowed_origins

    def _host_allowed(self, host: str | None) -> bool:
        if not self.service.allowed_hosts:
            return True
        if not host:
            return False
        parsed = urlsplit("//" + host)
        hostname = parsed.hostname
        try:
            port = parsed.port
        except ValueError:
            return False
        if (
            not hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment
        ):
            return False
        key = (hostname.lower(), port)
        return (
            key in self.service.allowed_hosts
            or ((hostname.lower(), None) in self.service.allowed_hosts and port in {None, 443})
            or ((hostname.lower(), 443) in self.service.allowed_hosts and port in {None, 443})
        )

    async def _body(self, receive) -> bytes:
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                raise MCPServiceError("client disconnected", code=-32000, status=400)
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > self.service.max_body_bytes:
                raise MCPServiceError("request body too large", code=-32000, status=413)
            if not message.get("more_body", False):
                return bytes(body)

    async def _send(self, send, status: int, body: bytes = b"", headers: list[tuple[bytes, bytes]] | None = None):
        response_headers = [
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
            (b"x-frame-options", b"DENY"),
            (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
        ]
        response_headers.extend(headers or [])
        if body:
            response_headers.append((b"content-length", str(len(body)).encode("ascii")))
        await send({"type": "http.response.start", "status": status, "headers": response_headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    self.service.start()
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    self.service.close()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        headers = self._headers(scope)
        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        origin = headers.get("origin")
        cors = []
        if origin is not None and self._origin_allowed(origin):
            cors = [
                (b"access-control-allow-origin", origin.encode("latin1")),
                (b"vary", b"Origin"),
                (b"access-control-allow-headers", b"Authorization, Content-Type, Accept, MCP-Protocol-Version"),
                (b"access-control-allow-methods", b"POST, GET, OPTIONS"),
                (b"access-control-expose-headers", b"WWW-Authenticate, MCP-Protocol-Version"),
            ]
        if not self._origin_allowed(origin):
            await self._send(send, 403, b'{"error":"origin_forbidden"}')
            return
        if not self._host_allowed(headers.get("host")):
            await self._send(send, 400, b'{"error":"host_not_allowed"}')
            return
        if self.service.require_https and scope.get("scheme") != "https":
            await self._send(send, 400, b'{"error":"https_required"}')
            return
        if path in {
            "/.well-known/oauth-protected-resource",
            self.service.resource_metadata_path(),
        }:
            if method != "GET":
                await self._send(send, 405, headers=[(b"allow", b"GET")])
                return
            body = _canonical_json(self.service.protected_resource_metadata()).encode("utf-8")
            await self._send(send, 200, body, cors + [(b"content-type", b"application/json; charset=utf-8")])
            return
        if path in {"/healthz", "/readyz"}:
            if method != "GET":
                await self._send(send, 405, headers=[(b"allow", b"GET")])
                return
            if path == "/readyz" and not self.service.readiness():
                await self._send(send, 503, b'{"status":"not_ready"}', [(b"content-type", b"application/json")])
            else:
                await self._send(send, 200, b'{"status":"ok"}', [(b"content-type", b"application/json")])
            return
        if path != self.service.endpoint_path:
            await self._send(send, 404)
            return
        if method == "OPTIONS":
            if origin is None or not self._origin_allowed(origin):
                await self._send(send, 403)
            else:
                await self._send(send, 204, headers=cors)
            return
        if method == "GET":
            await self._send(send, 405, headers=[(b"allow", b"POST")])
            return
        if method != "POST":
            await self._send(send, 405, headers=[(b"allow", b"POST, GET, OPTIONS")])
            return
        try:
            principal = self.service.authenticate(headers)
            content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                raise MCPServiceError("Content-Type must be application/json", code=-32000, status=415)
            accept = headers.get("accept", "")
            if "application/json" not in accept or "text/event-stream" not in accept:
                raise MCPServiceError("Accept must include application/json and text/event-stream", code=-32000, status=406)
            raw_length = headers.get("content-length")
            if raw_length and (not raw_length.isdigit() or int(raw_length) > self.service.max_body_bytes):
                raise MCPServiceError("request body too large", code=-32000, status=413)
            raw = await self._body(receive)
            try:
                request = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MCPServiceError("request body must be one UTF-8 JSON-RPC message", code=-32700, status=400) from exc
            if isinstance(request, list):
                raise MCPServiceError("JSON-RPC batches are not supported", code=-32600, status=400)
            if not isinstance(request, Mapping):
                raise MCPServiceError("JSON-RPC request must be an object", code=-32600, status=400)
            protocol = headers.get("mcp-protocol-version")
            if request.get("method") != "initialize":
                protocol = protocol or "2025-03-26"
                if protocol not in SUPPORTED_PROTOCOL_VERSIONS:
                    raise MCPServiceError("unsupported MCP-Protocol-Version", code=-32000, status=400)
            elif protocol and protocol not in SUPPORTED_PROTOCOL_VERSIONS:
                raise MCPServiceError("unsupported MCP-Protocol-Version", code=-32000, status=400)
            response = self.service._rpc(request, principal)
            if response is None:
                await self._send(send, 202, headers=cors)
                return
            negotiated = (
                response.get("result", {}).get("protocolVersion")
                if isinstance(response.get("result"), Mapping) else None
            )
            response_protocol = negotiated or protocol or "2025-03-26"
            body = _canonical_json(response).encode("utf-8")
            if len(body) > self.service.max_response_bytes:
                body = _canonical_json({
                    "jsonrpc": "2.0", "id": response.get("id"),
                    "error": {"code": -32054, "message": "MCP response exceeds configured size limit"},
                }).encode("utf-8")
                await self._send(send, 413, body, cors + [
                    (b"content-type", b"application/json; charset=utf-8"),
                ])
                return
            extra = cors + [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"mcp-protocol-version", response_protocol.encode("ascii")),
            ]
            result_meta = response.get("result", {}).get("_meta") if isinstance(response.get("result"), Mapping) else None
            challenge_values = result_meta.get("mcp/www_authenticate") if isinstance(result_meta, Mapping) else None
            if isinstance(challenge_values, list) and challenge_values and isinstance(challenge_values[0], str):
                extra.append((b"www-authenticate", challenge_values[0].encode("latin1")))
            await self._send(send, 200, body, extra)
        except PermissionError as exc:
            challenge = self.service.authentication_challenge(
                error="invalid_token", description="Authenticate to access this AUREN operator resource.",
            )
            request_id = request.get("id") if isinstance(locals().get("request"), Mapping) else None
            body = _canonical_json({
                "jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32001, "message": _safe_text(exc, 300)},
            }).encode("utf-8")
            await self._send(send, 401, body, cors + [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"www-authenticate", challenge.encode("latin1")),
            ])
        except MCPServiceError as exc:
            request_id = request.get("id") if isinstance(locals().get("request"), Mapping) else None
            rpc_error = {
                "jsonrpc": "2.0", "id": request_id,
                "error": {"code": exc.code, "message": _safe_text(exc, 500)},
            }
            body = _canonical_json(rpc_error).encode("utf-8")
            extra = cors + [(b"content-type", b"application/json; charset=utf-8")]
            if exc.status == 401:
                resource = urlsplit(self.service.resource_url)
                metadata = f"{resource.scheme}://{resource.netloc}{self.service.resource_metadata_path()}"
                extra.append((b"www-authenticate", f'Bearer resource_metadata="{metadata}"'.encode("latin1")))
            await self._send(send, exc.status, body, extra)
        except Exception as exc:
            body = _canonical_json({"error": "request failed closed", "type": type(exc).__name__}).encode("utf-8")
            await self._send(send, 500, body, cors + [(b"content-type", b"application/json; charset=utf-8")])


def create_app(runtime: Any, **kwargs: Any) -> AurenMCPASGI:
    """Build the ASGI application from an already trusted AUREN host runtime."""
    return AurenMCPASGI(AurenMCPService(runtime, **kwargs))


def service_from_environment(runtime: Any, *, project_root: str | Path) -> AurenMCPService:
    """Create a fail-closed service from deployment environment configuration."""
    resource_url = os.environ.get("AUREN_MCP_RESOURCE_URL", "").strip()
    authorization_server = os.environ.get("AUREN_MCP_AUTHORIZATION_SERVER", "").strip() or None
    introspection = os.environ.get("AUREN_MCP_INTROSPECTION_URL", "").strip()
    if not resource_url:
        raise RuntimeError("AUREN_MCP_RESOURCE_URL must be configured")
    if introspection:
        if not authorization_server:
            raise RuntimeError("AUREN_MCP_AUTHORIZATION_SERVER is required with OAuth introspection")
        verifier: TokenVerifier = OAuthIntrospectionVerifier(
            endpoint=introspection,
            client_id=os.environ.get("AUREN_MCP_INTROSPECTION_CLIENT_ID", ""),
            client_secret=os.environ.get("AUREN_MCP_INTROSPECTION_CLIENT_SECRET", ""),
            audience=resource_url, issuer=authorization_server,
        )
    else:
        if os.environ.get("AUREN_MCP_ALLOW_STATIC_TOKENS") != "1":
            raise RuntimeError("production remote MCP requires OAuth introspection; static tokens need explicit opt-in")
        try:
            records = json.loads(os.environ.get("AUREN_MCP_STATIC_TOKENS", "[]"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("AUREN_MCP_STATIC_TOKENS must be valid JSON") from exc
        if not isinstance(records, list):
            raise RuntimeError("AUREN_MCP_STATIC_TOKENS must be a JSON array")
        verifier = StaticBearerTokenVerifier(records)
        authorization_server = None
    origins = tuple(x.strip() for x in os.environ.get("AUREN_MCP_ALLOWED_ORIGINS", "").split(",") if x.strip())
    hosts = tuple(x.strip() for x in os.environ.get("AUREN_MCP_ALLOWED_HOSTS", "").split(",") if x.strip())
    if not hosts:
        raise RuntimeError("AUREN_MCP_ALLOWED_HOSTS must list the public MCP hostnames")
    return AurenMCPService(
        runtime, project_root=project_root, token_verifier=verifier,
        resource_url=resource_url, authorization_server_url=authorization_server,
        allowed_origins=origins, allowed_hosts=hosts, require_https=True,
        endpoint_path=os.environ.get("AUREN_MCP_ENDPOINT_PATH", DEFAULT_ENDPOINT_PATH),
        control_store_path=os.environ.get("AUREN_MCP_CONTROL_DB") or None,
        max_pending_tasks=int(os.environ.get("AUREN_MCP_MAX_PENDING_TASKS", "1000")),
        max_response_bytes=int(os.environ.get("AUREN_MCP_MAX_RESPONSE_BYTES", str(DEFAULT_MAX_RESPONSE_BYTES))),
    )


__all__ = [
    "AurenMCPASGI", "AurenMCPService", "MCPPrincipal", "OAuthIntrospectionVerifier",
    "StaticBearerTokenVerifier", "SUPPORTED_PROTOCOL_VERSIONS", "create_app",
    "service_from_environment",
]
