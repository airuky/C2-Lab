"""Loopback-only Teamserver HTTP APIs and operator dashboard."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import queue
import sys
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import parse_qsl, urlsplit

from .auth import AuthError, OperatorSessionRegistry
from .core import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MAX_SYNC_CURSOR,
    MAX_SYNC_PAGE_SIZE,
    MIN_IDEMPOTENCY_KEY_LENGTH,
    LabError,
    LabState,
)
from .observability import RequestMetrics, normalized_route


LOOPBACK_HOST = "127.0.0.1"
MAX_BODY_BYTES = 16 * 1024
MAX_HTTP_WORKERS = 16
SOCKET_TIMEOUT_SECONDS = 5.0
ACCESS_LOG_QUEUE_SIZE = 256
ACCESS_LOG_CLOSE_TIMEOUT_SECONDS = 1.0
MAX_SYNC_QUERY_LENGTH = 256
SYNC_QUERY_FIELDS = frozenset({"events_after", "audit_after", "limit"})
STATIC_DIRECTORY = Path(__file__).with_name("static")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/styles.css": ("styles.css", "text/css; charset=utf-8"),
}


class LabHTTPServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = True
    request_queue_size = MAX_HTTP_WORKERS

    def __init__(
        self,
        address: tuple[str, int],
        state: LabState,
        operator_sessions: OperatorSessionRegistry,
        enrollment_token: str,
        *,
        runtime: Any | None = None,
        access_log: bool = False,
        access_log_stream: TextIO | None = None,
    ) -> None:
        self._request_slots = threading.BoundedSemaphore(MAX_HTTP_WORKERS)
        self._access_log_queue: queue.Queue[str | None] | None = None
        self._access_log_thread: threading.Thread | None = None
        self._access_log_closed = threading.Event()
        super().__init__(address, LabRequestHandler)
        self.lab_state = state
        self.operator_sessions = operator_sessions
        self.enrollment_token_digest = hashlib.sha256(enrollment_token.encode("ascii")).digest()
        self.runtime = runtime
        self.metrics = RequestMetrics()
        self.access_log = access_log
        self.access_log_stream = access_log_stream or sys.stderr
        if self.access_log:
            self._access_log_queue = queue.Queue(maxsize=ACCESS_LOG_QUEUE_SIZE)
            self._access_log_thread = threading.Thread(
                target=self._write_access_log,
                name="c2lab-access-log",
                daemon=True,
            )
            try:
                self._access_log_thread.start()
            except Exception:
                self._access_log_queue = None
                self._access_log_thread = None
                super().server_close()
                raise

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._request_slots.acquire(blocking=False):
            self.metrics.record_worker_rejection()
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()

    def server_close(self) -> None:
        """Close request threads, then best-effort drain the bounded log queue."""

        try:
            super().server_close()
        finally:
            self._close_access_log()

    def _write_access_log(self) -> None:
        pending = self._access_log_queue
        if pending is None:
            return
        while True:
            line = pending.get()
            try:
                if line is None:
                    return
                self.access_log_stream.write(line + "\n")
                self.access_log_stream.flush()
            except Exception:
                self.metrics.record_access_log_drop()
            finally:
                pending.task_done()

    def flush_access_log(self, *, timeout: float = 1.0) -> bool:
        """Wait for queued lines without allowing a stalled sink to block forever."""

        pending = self._access_log_queue
        if pending is None:
            return True
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout < 0
        ):
            return False
        deadline = time.monotonic() + timeout
        with pending.all_tasks_done:
            while pending.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                pending.all_tasks_done.wait(remaining)
        return True

    def _close_access_log(self) -> None:
        pending = self._access_log_queue
        thread = self._access_log_thread
        if pending is None or thread is None or self._access_log_closed.is_set():
            return
        self._access_log_closed.set()
        try:
            pending.put_nowait(None)
        except queue.Full:
            try:
                pending.get_nowait()
                pending.task_done()
                self.metrics.record_access_log_drop()
            except queue.Empty:
                pass
            try:
                pending.put_nowait(None)
            except queue.Full:
                return
        thread.join(timeout=ACCESS_LOG_CLOSE_TIMEOUT_SECONDS)

    def readiness(self) -> tuple[HTTPStatus, dict[str, Any]]:
        """Return a fixed-schema readiness projection without exception details."""

        if self.runtime is None:
            health: dict[str, Any] = {
                "status": "unmanaged",
                "ready": False,
                "last_tick_age_seconds": None,
                "last_error": None,
            }
        else:
            try:
                candidate = self.runtime.health()
                health = candidate if isinstance(candidate, dict) else {}
            except Exception:
                health = {}
            status = health.get("status")
            if status not in {"running", "stopped"}:
                status = "error"
            age = health.get("last_tick_age_seconds")
            if not (
                age is None
                or (
                    isinstance(age, (int, float))
                    and not isinstance(age, bool)
                    and math.isfinite(age)
                    and age >= 0
                )
            ):
                age = None
            error_name = health.get("last_error")
            if error_name is not None and not (
                isinstance(error_name, str)
                and 1 <= len(error_name) <= 64
                and error_name.isascii()
                and all(character.isalnum() or character == "_" for character in error_name)
            ):
                error_name = "Exception"
            health = {
                "status": status,
                "ready": status == "running" and health.get("ready") is True,
                "last_tick_age_seconds": age,
                "last_error": error_name,
            }
        ready = health["ready"] is True
        return (
            HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
            {"status": "ready" if ready else "not_ready", "monitor": health},
        )

    def record_response(
        self,
        *,
        method: str,
        path: str,
        status: int,
        duration_ms: float,
        actor: str | None,
    ) -> None:
        """Record bounded aggregate metrics and an optional secret-free JSON line."""

        self.metrics.record(
            method=method,
            route=path,
            status=status,
            duration_ms=duration_ms,
        )
        if not self.access_log:
            return
        entry = {
            "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "event": "http.request",
            "method": method if method in {"GET", "POST"} else "OTHER",
            "route": normalized_route(path),
            "status": int(status),
            "duration_ms": round(max(0.0, duration_ms), 3),
            "principal_id": actor,
        }
        try:
            encoded = json.dumps(entry, ensure_ascii=True, separators=(",", ":"))
        except (TypeError, ValueError):
            self.metrics.record_access_log_drop()
            return
        pending = self._access_log_queue
        if pending is None or self._access_log_closed.is_set():
            self.metrics.record_access_log_drop()
            return
        try:
            pending.put_nowait(encoded)
        except queue.Full:
            self.metrics.record_access_log_drop()


class LabRequestHandler(BaseHTTPRequestHandler):
    server: LabHTTPServer
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self._request_started = time.monotonic()
        self._response_recorded = False
        self._request_actor: str | None = None
        self._request_route_path = "unmatched"
        self._request_query = ""
        self.connection.settimeout(SOCKET_TIMEOUT_SECONDS)

    def do_GET(self) -> None:  # noqa: N802
        if not self._request_is_local():
            return
        path = self._parse_request_target()
        if path is None:
            return
        if path in STATIC_FILES:
            filename, content_type = STATIC_FILES[path]
            self._serve_static(filename, content_type)
            return
        if path == "/healthz":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "mode": "localhost-lab",
                    "protocol": "loopback-http-poll/v1",
                },
            )
            return
        if path == "/readyz":
            status, payload = self.server.readiness()
            self._send_json(status, payload)
            return
        session = self._authorize_operator("operator_admin" if path == "/lab/operators" else "read")
        if session is None:
            return
        if path == "/lab/session":
            self._send_json(HTTPStatus.OK, session)
            return
        if path == "/lab/operators":
            self._send_json(HTTPStatus.OK, self.server.operator_sessions.list())
            return
        if path == "/lab/metrics":
            self._send_json(
                HTTPStatus.OK,
                {
                    "http": self.server.metrics.snapshot(),
                    "lab": self.server.lab_state.overview()["counts"],
                    "readiness": self.server.readiness()[1],
                },
            )
            return
        if path == "/lab/sync":
            try:
                query = self._read_sync_query()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.lab_state.sync(**query),
                )
            except LabError as error:
                self._send_error(error.status, error.code, error.message)
            return
        routes: dict[str, Any] = {
            "/lab/overview": self.server.lab_state.overview,
            "/lab/nodes": self.server.lab_state.nodes,
            "/lab/tasks": self.server.lab_state.tasks,
            "/lab/events": self.server.lab_state.events,
            "/lab/audit": self.server.lab_state.audit,
            "/lab/report": self.server.lab_state.report,
        }
        route = routes.get(path)
        if route is None:
            self._send_error(HTTPStatus.NOT_FOUND, "not_found", "route not found")
            return
        self._send_json(HTTPStatus.OK, route())

    def do_POST(self) -> None:  # noqa: N802
        if not self._request_is_local():
            return
        path = self._parse_request_target()
        if path is None:
            return
        try:
            if path == "/node/v1/enroll":
                if not self._authorize_enrollment():
                    return
                body = self._read_json_object()
                self._require_body_keys(
                    body,
                    required={
                        "name",
                        "version",
                        "profile",
                        "capabilities",
                        "poll_interval_ms",
                    },
                    optional={"jitter_percent"},
                )
                enrollment = self.server.lab_state.enroll_node(
                    body["name"],
                    body["version"],
                    body["profile"],
                    body["capabilities"],
                    body["poll_interval_ms"],
                    jitter_percent=body.get("jitter_percent", 0),
                )
                self._send_json(HTTPStatus.CREATED, enrollment)
                return

            if path.startswith("/node/v1/"):
                credentials = self._authorize_node()
                if credentials is None:
                    return
                node_id, session_token = credentials
                body = self._read_json_object()
                if path == "/node/v1/poll":
                    self._require_body_keys(body, required=set())
                    self._send_json(
                        HTTPStatus.OK,
                        self.server.lab_state.poll_node(node_id, session_token),
                    )
                    return
                if path == "/node/v1/disconnect":
                    self._require_body_keys(body, required=set())
                    self._send_json(
                        HTTPStatus.OK,
                        self.server.lab_state.disconnect_node(node_id, session_token),
                    )
                    return
                parts = path.strip("/").split("/")
                if len(parts) == 5 and parts[:3] == ["node", "v1", "tasks"] and parts[4] == "result":
                    self._require_body_keys(body, required={"status", "result"})
                    task = self.server.lab_state.submit_result(
                        node_id,
                        session_token,
                        parts[3],
                        body["status"],
                        body["result"],
                    )
                    self._send_json(HTTPStatus.OK, task)
                    return
                raise LabError("route not found", code="not_found", status=404)

            if not self._origin_is_local():
                return
            parts = path.strip("/").split("/")
            is_cancel = (
                len(parts) == 4
                and parts[:2] == ["lab", "tasks"]
                and parts[3] == "cancel"
            )
            is_revoke = (
                len(parts) == 4
                and parts[:2] == ["lab", "operators"]
                and parts[3] == "revoke"
            )
            if path == "/lab/tasks" or is_cancel:
                permission = "task_write"
            elif path == "/lab/notes":
                permission = "note_write"
            elif path == "/lab/reset":
                permission = "reset"
            elif is_revoke:
                permission = "operator_admin"
            else:
                permission = "read"
            session = self._authorize_operator(permission)
            if session is None:
                return
            if (
                path not in {"/lab/tasks", "/lab/notes", "/lab/reset"}
                and not is_cancel
                and not is_revoke
            ):
                self._send_error(HTTPStatus.NOT_FOUND, "not_found", "route not found")
                return
            body = self._read_json_object()
            if path == "/lab/tasks":
                self._require_body_keys(
                    body,
                    required={"node_id", "type", "payload"},
                    optional={"queue_ttl_seconds"},
                )
                queue_options = (
                    {"queue_ttl_seconds": body["queue_ttl_seconds"]}
                    if "queue_ttl_seconds" in body
                    else {}
                )
                task = self.server.lab_state.queue_task(
                    body["node_id"],
                    body["type"],
                    body["payload"],
                    idempotency_key=self._read_idempotency_key(),
                    actor=session["principal_id"],
                    **queue_options,
                )
                self._send_json(HTTPStatus.CREATED, task)
                return
            if path == "/lab/notes":
                self._require_body_keys(body, required={"message"})
                note = self.server.lab_state.post_operator_note(
                    body["message"],
                    actor=session["principal_id"],
                    idempotency_key=self._read_idempotency_key(),
                )
                self._send_json(HTTPStatus.CREATED, note)
                return
            if is_cancel:
                self._require_body_keys(body, required=set())
                task = self.server.lab_state.cancel_task(
                    parts[2],
                    actor=session["principal_id"],
                )
                self._send_json(HTTPStatus.OK, task)
                return
            if path == "/lab/reset":
                self._require_body_keys(body, required=set())
                self.server.lab_state.reset(actor=session["principal_id"])
                self._send_json(HTTPStatus.OK, {"reset": True, "sessions_invalidated": True})
                return
            if is_revoke:
                self._require_body_keys(body, required=set())
                revoked = self.server.operator_sessions.revoke(
                    parts[2],
                    actor_session_id=session["id"],
                )
                self._send_json(HTTPStatus.OK, revoked)
                return
        except (AuthError, LabError) as error:
            self._send_error(error.status, error.code, error.message)

    def _request_is_local(self) -> bool:
        try:
            peer_is_local = ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            peer_is_local = False
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        host_is_local = host in {
            "localhost",
            LOOPBACK_HOST,
            f"localhost:{port}",
            f"{LOOPBACK_HOST}:{port}",
        }
        if peer_is_local and host_is_local:
            return True
        self._send_error(HTTPStatus.FORBIDDEN, "local_only", "this lab accepts localhost requests only")
        return False

    def _parse_request_target(self) -> str | None:
        """Parse the request target once and fail closed without handler tracebacks."""

        try:
            parsed = urlsplit(self.path)
        except ValueError:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_request_target",
                "request target is invalid",
            )
            return None
        self._request_route_path = parsed.path
        self._request_query = parsed.query
        return parsed.path

    def _origin_is_local(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        port = self.server.server_address[1]
        if origin.lower() in {f"http://localhost:{port}", f"http://{LOOPBACK_HOST}:{port}"}:
            return True
        self._send_error(HTTPStatus.FORBIDDEN, "invalid_origin", "cross-origin requests are disabled")
        return False

    def _authorize_operator(self, permission: str = "read") -> dict[str, Any] | None:
        authorization = self.headers.get("Authorization", "")
        prefix = "Bearer "
        supplied = authorization[len(prefix) :] if authorization.startswith(prefix) else ""
        session = self.server.operator_sessions.authenticate(supplied)
        if session is None:
            self._send_error(
                HTTPStatus.UNAUTHORIZED,
                "unauthorized",
                "a valid operator session is required",
            )
            return None
        if permission not in session["permissions"]:
            self._request_actor = session["principal_id"]
            self._send_error(
                HTTPStatus.FORBIDDEN,
                "forbidden",
                f"{permission} permission is required",
            )
            return None
        self._request_actor = session["principal_id"]
        return session

    def _authorize_enrollment(self) -> bool:
        authorized = self._authorize_static_token(
            prefix="Enroll ",
            expected_digest=self.server.enrollment_token_digest,
            message="a valid enrollment token is required",
        )
        if authorized:
            self._request_actor = "enrollment"
        return authorized

    def _authorize_static_token(
        self,
        *,
        prefix: str,
        expected_digest: bytes,
        message: str,
    ) -> bool:
        authorization = self.headers.get("Authorization", "")
        supplied = authorization[len(prefix) :] if authorization.startswith(prefix) else ""
        supplied_digest = hashlib.sha256(supplied.encode("utf-8")).digest()
        if supplied and hmac.compare_digest(supplied_digest, expected_digest):
            return True
        self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized", message)
        return False

    def _authorize_node(self) -> tuple[str, str] | None:
        node_id = self.headers.get("X-C2Lab-Node", "")
        authorization = self.headers.get("Authorization", "")
        prefix = "Node "
        session_token = authorization[len(prefix) :] if authorization.startswith(prefix) else ""
        if self.server.lab_state.authenticate_node(node_id, session_token):
            self._request_actor = "node"
            return node_id, session_token
        self._send_error(HTTPStatus.UNAUTHORIZED, "invalid_node_session", "invalid node session")
        return None

    def _read_json_object(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise LabError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise LabError("invalid Content-Length") from error
        if length < 0 or length > MAX_BODY_BYTES:
            raise LabError(f"request body must be at most {MAX_BODY_BYTES} bytes", status=413)
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise LabError("Content-Type must be application/json", status=415)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError) as error:
            raise LabError("request body must be valid UTF-8 JSON") from error
        if not isinstance(body, dict):
            raise LabError("request body must be a JSON object")
        return body

    def _read_idempotency_key(self) -> str | None:
        value = self.headers.get("Idempotency-Key")
        if value is None:
            return None
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:"
        if not MIN_IDEMPOTENCY_KEY_LENGTH <= len(value) <= MAX_IDEMPOTENCY_KEY_LENGTH:
            raise LabError(
                "Idempotency-Key must be "
                f"{MIN_IDEMPOTENCY_KEY_LENGTH} to {MAX_IDEMPOTENCY_KEY_LENGTH} characters"
            )
        if any(character not in allowed for character in value):
            raise LabError("Idempotency-Key contains unsupported characters")
        return value

    def _read_sync_query(self) -> dict[str, int]:
        """Parse the bounded sync cursor query without accepting alternate forms."""

        raw_query = self._request_query
        values = {
            "events_after": 0,
            "audit_after": 0,
            "limit": MAX_SYNC_PAGE_SIZE,
        }
        if not raw_query:
            return values
        if len(raw_query) > MAX_SYNC_QUERY_LENGTH:
            raise LabError("sync query is too large")
        try:
            pairs = parse_qsl(
                raw_query,
                keep_blank_values=True,
                strict_parsing=True,
                encoding="utf-8",
                errors="strict",
                max_num_fields=len(SYNC_QUERY_FIELDS),
            )
        except (UnicodeDecodeError, ValueError) as error:
            raise LabError("sync query must contain valid key=value parameters") from error

        seen: set[str] = set()
        for field, raw_value in pairs:
            if field not in SYNC_QUERY_FIELDS:
                raise LabError("sync query contains unsupported parameters")
            if field in seen:
                raise LabError("sync query parameters must not be repeated")
            seen.add(field)
            if not raw_value or not raw_value.isascii() or any(
                character < "0" or character > "9" for character in raw_value
            ):
                raise LabError(f"{field} must be an unsigned decimal integer")

            maximum = MAX_SYNC_PAGE_SIZE if field == "limit" else MAX_SYNC_CURSOR
            significant_digits = raw_value.lstrip("0") or "0"
            if len(significant_digits) > len(str(maximum)):
                raise LabError(f"{field} is outside the supported range")
            numeric_value = int(significant_digits, 10)
            minimum = 1 if field == "limit" else 0
            if not minimum <= numeric_value <= maximum:
                raise LabError(f"{field} is outside the supported range")
            values[field] = numeric_value
        return values

    @staticmethod
    def _require_body_keys(
        body: dict[str, Any],
        *,
        required: set[str],
        optional: set[str] | None = None,
    ) -> None:
        optional = optional or set()
        actual = set(body)
        missing = required - actual
        unexpected = actual - required - optional
        if missing:
            raise LabError("missing fields: " + ", ".join(sorted(missing)))
        if unexpected:
            raise LabError("unexpected fields: " + ", ".join(sorted(unexpected)))

    def _serve_static(self, filename: str, content_type: str) -> None:
        path = STATIC_DIRECTORY / filename
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            self._send_error(HTTPStatus.NOT_FOUND, "not_found", "dashboard asset not found")
            return
        self._send_bytes(HTTPStatus.OK, content, content_type)

    def _send_json(self, status: int, payload: Any) -> None:
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, content, "application/json; charset=utf-8")

    def _send_error(self, status: int, code: str, message: str) -> None:
        self.close_connection = True
        self._send_json(status, {"error": {"code": code, "message": message}})

    def _send_bytes(self, status: int, content: bytes, content_type: str) -> None:
        if not self._response_recorded:
            self._response_recorded = True
            self.server.record_response(
                method=self.command,
                path=self._request_route_path,
                status=int(status),
                duration_ms=(time.monotonic() - self._request_started) * 1000,
                actor=self._request_actor,
            )
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Connection", "close")
        try:
            self.end_headers()
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def log_message(self, format: str, *args: Any) -> None:
        return


def create_server(
    state: LabState,
    operator_token: str,
    enrollment_token: str,
    port: int = 8765,
    *,
    operator_registry: OperatorSessionRegistry | None = None,
    runtime: Any | None = None,
    access_log: bool = False,
    access_log_stream: TextIO | None = None,
) -> LabHTTPServer:
    for label, token, minimum in (
        ("operator", operator_token, 24),
        ("enrollment", enrollment_token, 16),
    ):
        if not (
            isinstance(token, str)
            and minimum <= len(token) <= 512
            and token.isascii()
            and all(33 <= ord(character) <= 126 for character in token)
        ):
            raise ValueError(
                f"{label} token must contain {minimum} to 512 printable ASCII characters"
            )
    if hmac.compare_digest(operator_token, enrollment_token):
        raise ValueError("operator and enrollment tokens must be different")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65_535:
        raise ValueError("port must be an integer from 0 to 65535")
    if operator_registry is None:
        operator_registry = OperatorSessionRegistry()
        operator_registry.register("local-admin", "admin", operator_token)
    elif not isinstance(operator_registry, OperatorSessionRegistry):
        raise ValueError("operator_registry must be an OperatorSessionRegistry")
    elif operator_registry.authenticate(operator_token) is None:
        raise ValueError("operator token must identify an active registered session")
    if operator_registry.authenticate(enrollment_token) is not None:
        raise ValueError("operator and enrollment tokens must be different")
    if runtime is not None and not callable(getattr(runtime, "health", None)):
        raise ValueError("runtime must provide health()")
    if not isinstance(access_log, bool):
        raise ValueError("access_log must be a boolean")
    if access_log_stream is not None and not all(
        callable(getattr(access_log_stream, method, None)) for method in ("write", "flush")
    ):
        raise ValueError("access_log_stream must be writable and flushable")
    return LabHTTPServer(
        (LOOPBACK_HOST, port),
        state,
        operator_registry,
        enrollment_token,
        runtime=runtime,
        access_log=access_log,
        access_log_stream=access_log_stream,
    )
