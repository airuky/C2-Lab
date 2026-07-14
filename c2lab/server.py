"""Loopback-only Teamserver HTTP APIs and operator dashboard."""

from __future__ import annotations

import hmac
import ipaddress
import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .core import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MIN_IDEMPOTENCY_KEY_LENGTH,
    LabError,
    LabState,
)


LOOPBACK_HOST = "127.0.0.1"
MAX_BODY_BYTES = 16 * 1024
MAX_HTTP_WORKERS = 16
SOCKET_TIMEOUT_SECONDS = 5.0
STATIC_DIRECTORY = Path(__file__).with_name("static")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/styles.css": ("styles.css", "text/css; charset=utf-8"),
}


class LabHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = MAX_HTTP_WORKERS

    def __init__(
        self,
        address: tuple[str, int],
        state: LabState,
        operator_token: str,
        enrollment_token: str,
    ) -> None:
        self._request_slots = threading.BoundedSemaphore(MAX_HTTP_WORKERS)
        super().__init__(address, LabRequestHandler)
        self.lab_state = state
        self.operator_token = operator_token
        self.enrollment_token = enrollment_token

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._request_slots.acquire(blocking=False):
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


class LabRequestHandler(BaseHTTPRequestHandler):
    server: LabHTTPServer
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(SOCKET_TIMEOUT_SECONDS)

    def do_GET(self) -> None:  # noqa: N802
        if not self._request_is_local():
            return
        path = urlsplit(self.path).path
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
        if not self._authorize_operator():
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
        path = urlsplit(self.path).path
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
                )
                enrollment = self.server.lab_state.enroll_node(
                    body["name"],
                    body["version"],
                    body["profile"],
                    body["capabilities"],
                    body["poll_interval_ms"],
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

            if not self._origin_is_local() or not self._authorize_operator():
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
                    **queue_options,
                )
                self._send_json(HTTPStatus.CREATED, task)
                return
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["lab", "tasks"] and parts[3] == "cancel":
                self._require_body_keys(body, required=set())
                task = self.server.lab_state.cancel_task(parts[2])
                self._send_json(HTTPStatus.OK, task)
                return
            if path == "/lab/reset":
                self._require_body_keys(body, required=set())
                self.server.lab_state.reset()
                self._send_json(HTTPStatus.OK, {"reset": True, "sessions_invalidated": True})
                return
            self._send_error(HTTPStatus.NOT_FOUND, "not_found", "route not found")
        except LabError as error:
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

    def _origin_is_local(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        port = self.server.server_address[1]
        if origin.lower() in {f"http://localhost:{port}", f"http://{LOOPBACK_HOST}:{port}"}:
            return True
        self._send_error(HTTPStatus.FORBIDDEN, "invalid_origin", "cross-origin requests are disabled")
        return False

    def _authorize_operator(self) -> bool:
        return self._authorize_static_token(
            prefix="Bearer ",
            expected=self.server.operator_token,
            message="a valid operator token is required",
        )

    def _authorize_enrollment(self) -> bool:
        return self._authorize_static_token(
            prefix="Enroll ",
            expected=self.server.enrollment_token,
            message="a valid enrollment token is required",
        )

    def _authorize_static_token(self, *, prefix: str, expected: str, message: str) -> bool:
        authorization = self.headers.get("Authorization", "")
        supplied = authorization[len(prefix) :] if authorization.startswith(prefix) else ""
        if supplied and hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
            return True
        self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized", message)
        return False

    def _authorize_node(self) -> tuple[str, str] | None:
        node_id = self.headers.get("X-C2Lab-Node", "")
        authorization = self.headers.get("Authorization", "")
        prefix = "Node "
        session_token = authorization[len(prefix) :] if authorization.startswith(prefix) else ""
        if self.server.lab_state.authenticate_node(node_id, session_token):
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
) -> LabHTTPServer:
    for label, token in (("operator", operator_token), ("enrollment", enrollment_token)):
        if not isinstance(token, str) or len(token) < 16:
            raise ValueError(f"{label} token must contain at least 16 characters")
    if hmac.compare_digest(operator_token, enrollment_token):
        raise ValueError("operator and enrollment tokens must be different")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65_535:
        raise ValueError("port must be an integer from 0 to 65535")
    return LabHTTPServer((LOOPBACK_HOST, port), state, operator_token, enrollment_token)
