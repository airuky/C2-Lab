"""Foreground-only localhost node for the C2 learning framework.

The node has a fixed action registry. It cannot invoke a shell, touch user
files, inspect the host, load plugins, or connect anywhere except loopback.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import random
import threading
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from .lab_runtime import EphemeralLabWorkspace
from .protocol import (
    MAX_POLL_INTERVAL_MS,
    MIN_POLL_INTERVAL_MS,
    ProtocolError,
    capabilities_for_profile,
    validate_jitter_percent,
    validate_poll_interval,
    validate_profile,
    validate_result,
    validate_task_payload,
)


DEFAULT_CONTROLLER = "http://127.0.0.1:8765"
REQUEST_TIMEOUT_SECONDS = 3.0
MAX_RESPONSE_BYTES = 32 * 1024


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep every node request on the controller URL chosen at startup."""

    def redirect_request(
        self,
        request: Any,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


class NodeClientError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, code: str = "client_error"):
        super().__init__(message)
        self.status = status
        self.code = code


def _is_hex_identifier(value: Any, prefix: str, digits: int) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(prefix)
        and len(value) == len(prefix) + digits
        and all(character in "0123456789abcdef" for character in value[len(prefix) :])
    )


def validate_controller_url(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("controller URL must be a string")
    parsed = urlsplit(value)
    if parsed.scheme != "http":
        raise ValueError("controller URL must use http on loopback")
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("controller URL must use 127.0.0.1 or localhost")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("controller URL must not contain credentials, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("controller URL must not contain a path")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("controller URL contains an invalid port") from error
    if port is None:
        port = 80
    return f"http://127.0.0.1:{port}"


class NodeClient:
    """Small JSON client for the loopback-only node protocol."""

    def __init__(
        self,
        controller_url: str,
        enrollment_token: str,
        *,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.controller_url = validate_controller_url(controller_url)
        if not isinstance(enrollment_token, str) or len(enrollment_token) < 16:
            raise ValueError("enrollment token must contain at least 16 characters")
        self.enrollment_token = enrollment_token
        self.timeout = timeout
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )
        self.node_id: str | None = None
        self.session_token: str | None = None

    def enroll(
        self,
        *,
        name: str,
        version: str,
        profile: str,
        poll_interval_ms: int,
        jitter_percent: int = 0,
    ) -> dict[str, Any]:
        capabilities = capabilities_for_profile(profile)
        payload = self._request(
            "/node/v1/enroll",
            body={
                "name": name,
                "version": version,
                "profile": profile,
                "capabilities": capabilities,
                "poll_interval_ms": poll_interval_ms,
                "jitter_percent": jitter_percent,
            },
            authorization=f"Enroll {self.enrollment_token}",
        )
        node = payload.get("node")
        session_token = payload.get("session_token")
        if (
            not isinstance(node, dict)
            or not _is_hex_identifier(node.get("id"), "node-", 10)
            or not isinstance(session_token, str)
            or len(session_token) < 16
        ):
            raise NodeClientError("teamserver returned an invalid enrollment response")
        self.node_id = node["id"]
        self.session_token = session_token
        return node

    def poll(self) -> dict[str, Any]:
        response = self._node_request("/node/v1/poll", body={})
        task = response.get("task")
        if task is not None:
            if (
                not isinstance(task, dict)
                or not _is_hex_identifier(task.get("id"), "task-", 12)
                or not _is_hex_identifier(task.get("correlation_id"), "corr-", 12)
                or task.get("node_id") != self.node_id
                or task.get("status") != "dispatched"
            ):
                raise NodeClientError("teamserver returned an invalid task envelope")
            try:
                validate_task_payload(task.get("type"), task.get("payload"))
            except ProtocolError as error:
                raise NodeClientError("teamserver returned an invalid task payload") from error
        return response

    def submit_result(self, task_id: str, status: str, result: dict[str, Any]) -> dict[str, Any]:
        if not _is_hex_identifier(task_id, "task-", 12):
            raise NodeClientError("invalid task identifier")
        try:
            clean_status, clean_result = validate_result(status, result)
        except ProtocolError as error:
            raise NodeClientError("invalid task result") from error
        return self._node_request(
            f"/node/v1/tasks/{task_id}/result",
            body={"status": clean_status, "result": clean_result},
        )

    def disconnect(self) -> dict[str, Any]:
        return self._node_request("/node/v1/disconnect", body={})

    def clear_session(self) -> None:
        self.node_id = None
        self.session_token = None

    def _node_request(self, path: str, *, body: dict[str, Any]) -> dict[str, Any]:
        if not self.node_id or not self.session_token:
            raise NodeClientError("node is not enrolled", status=401, code="not_enrolled")
        return self._request(
            path,
            body=body,
            authorization=f"Node {self.session_token}",
            extra_headers={"X-C2Lab-Node": self.node_id},
        )

    def _request(
        self,
        path: str,
        *,
        body: dict[str, Any],
        authorization: str,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {
            "Authorization": authorization,
            "Content-Type": "application/json",
            "User-Agent": "C2Lab-Node/1",
        }
        headers.update(extra_headers or {})
        request = urllib.request.Request(
            f"{self.controller_url}{path}",
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            try:
                response = self._opener.open(request, timeout=self.timeout)
            except urllib.error.HTTPError as error:
                try:
                    raw_error = error.read(MAX_RESPONSE_BYTES + 1)
                finally:
                    error.close()
                if len(raw_error) > MAX_RESPONSE_BYTES:
                    raise NodeClientError(
                        "teamserver error response exceeded the size limit",
                        status=error.code,
                    ) from error
                try:
                    payload = json.loads(raw_error)
                except (UnicodeDecodeError, ValueError, RecursionError):
                    raise NodeClientError(
                        f"teamserver returned HTTP {error.code}",
                        status=error.code,
                        code="http_error",
                    ) from error
                if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
                    raise NodeClientError(
                        f"teamserver returned HTTP {error.code}",
                        status=error.code,
                        code="http_error",
                    ) from error
                api_error = payload["error"]
                raise NodeClientError(
                    api_error.get("message", f"teamserver returned HTTP {error.code}"),
                    status=error.code,
                    code=api_error.get("code", "http_error"),
                ) from error

            with response:
                raw_response = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw_response) > MAX_RESPONSE_BYTES:
                raise NodeClientError("teamserver response exceeded the size limit")
            try:
                payload = json.loads(raw_response)
            except (UnicodeDecodeError, ValueError, RecursionError) as error:
                raise NodeClientError("teamserver returned invalid JSON") from error
        except NodeClientError:
            raise
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            raise NodeClientError("teamserver is unavailable", code="connection_error") from error
        if not isinstance(payload, dict):
            raise NodeClientError("teamserver returned an invalid response")
        return payload


class NodeExecutor:
    """Executes only the fixed, side-effect-bounded training action registry."""

    def __init__(self, *, version: str, profile: str, poll_interval_ms: int, jitter_percent: int = 0) -> None:
        self.version = version
        self.profile = validate_profile(profile)
        self.poll_interval_ms = validate_poll_interval(poll_interval_ms)
        self.jitter_percent = validate_jitter_percent(jitter_percent)
        self.started = time.monotonic()
        self.tasks_completed = 0
        self.lab_workspace = EphemeralLabWorkspace() if self.profile == "purple_lab" else None

    def close(self) -> None:
        """Remove the node-owned ephemeral workspace, when this profile has one."""

        if self.lab_workspace is not None:
            self.lab_workspace.close()
            self.lab_workspace = None

    def reset_lab_workspace(self) -> None:
        """Rotate purple-lab artifacts when the controller invalidates the session."""

        self.close()
        if self.profile == "purple_lab":
            self.lab_workspace = EphemeralLabWorkspace()

    def execute(self, task: Any) -> tuple[str, dict[str, Any]]:
        try:
            if not isinstance(task, dict):
                raise ProtocolError("task envelope must be an object")
            task_type, payload = validate_task_payload(task.get("type"), task.get("payload"))
            if task_type not in capabilities_for_profile(self.profile):
                raise ProtocolError("task is outside the node profile")

            if task_type == "PING":
                result = {"reply": "PONG"}
            elif task_type == "RUNTIME_STATUS":
                result = {
                    "version": self.version,
                    "profile": self.profile,
                    "uptime_ms": int((time.monotonic() - self.started) * 1_000),
                    "tasks_completed": self.tasks_completed,
                    "poll_interval_ms": self.poll_interval_ms,
                    "jitter_percent": self.jitter_percent,
                }
            elif task_type == "ECHO_TEXT":
                result = {"echo": payload["text"]}
            elif task_type == "HASH_TEXT":
                result = {
                    "algorithm": "sha256",
                    "digest": hashlib.sha256(payload["text"].encode("utf-8")).hexdigest(),
                }
            elif task_type == "WAIT":
                time.sleep(payload["milliseconds"] / 1_000)
                result = {"waited_ms": payload["milliseconds"]}
            elif task_type == "SLEEP":
                result = {
                    "previous_interval_ms": self.poll_interval_ms,
                    "new_interval_ms": payload["interval_ms"],
                    "jitter_percent": payload["jitter_percent"],
                }
            elif task_type == "EXIT":
                result = {"acknowledged": True}
            elif task_type == "RUN_PLAYBOOK":
                if self.lab_workspace is None:
                    raise ProtocolError("playbook execution requires the purple_lab profile")
                result = self.lab_workspace.execute(payload["playbook"])
            else:
                result = {
                    "recorded": True,
                    "category": payload["category"],
                    "severity": payload["severity"],
                    "message": payload["message"],
                }
        except ProtocolError as error:
            return "failed", {"error_code": "INVALID_TASK"}
        except Exception:
            return "failed", {"error_code": "HANDLER_FAILED"}

        return "completed", result

    def acknowledge(self, task_type: Any, status: Any, result: Any) -> None:
        """Commit local runtime state only after Teamserver accepts a result."""

        if status != "completed":
            return
        if not isinstance(task_type, str) or task_type not in capabilities_for_profile(self.profile):
            raise ProtocolError("acknowledged task is outside the node profile")
        if not isinstance(result, dict):
            raise ProtocolError("acknowledged result must be an object")
        if task_type == "SLEEP":
            expected_keys = {"previous_interval_ms", "new_interval_ms", "jitter_percent"}
            if set(result) != expected_keys or result["previous_interval_ms"] != self.poll_interval_ms:
                raise ProtocolError("acknowledged sleep result does not match runtime state")
            self.poll_interval_ms = validate_poll_interval(result["new_interval_ms"])
            self.jitter_percent = validate_jitter_percent(result["jitter_percent"])
        self.tasks_completed += 1


def _jittered_wait(base_ms: int, jitter_percent: int, stop_event: threading.Event) -> None:
    """Sleep with bounded random jitter, clamped to the poll-interval range."""

    base = base_ms / 1_000
    if jitter_percent > 0:
        offset = base * jitter_percent / 100
        base += random.uniform(-offset, offset)
        base = max(MIN_POLL_INTERVAL_MS / 1_000, min(MAX_POLL_INTERVAL_MS / 1_000, base))
    stop_event.wait(base)


def run_node(
    *,
    controller_url: str,
    enrollment_token: str,
    name: str,
    version: str,
    profile: str,
    poll_interval_ms: int,
    jitter_percent: int = 0,
    stop_event: threading.Event | None = None,
) -> int:
    stop = stop_event or threading.Event()
    client = NodeClient(controller_url, enrollment_token)
    executor = NodeExecutor(
        version=version,
        profile=profile,
        poll_interval_ms=poll_interval_ms,
        jitter_percent=jitter_percent,
    )

    jitter_display = f", jitter={jitter_percent}%" if jitter_percent else ""
    print(f"C2 Lab Node '{name}' — profile={profile}{jitter_display}, controller={client.controller_url}")
    print("Foreground lab process only. Press Ctrl-C to disconnect.")
    pending_result: dict[str, Any] | None = None

    try:
        while not stop.is_set():
            if not client.node_id:
                try:
                    node = client.enroll(
                        name=name,
                        version=version,
                        profile=profile,
                        poll_interval_ms=executor.poll_interval_ms,
                        jitter_percent=executor.jitter_percent,
                    )
                    print(f"Enrolled as {node['id']}")
                except NodeClientError as error:
                    print(f"Enrollment failed: {error}")
                    stop.wait(1.0)
                    continue
            submitting_result = pending_result is not None
            try:
                if pending_result is not None:
                    client.submit_result(
                        pending_result["task_id"],
                        pending_result["status"],
                        pending_result["result"],
                    )
                    executor.acknowledge(
                        pending_result["task_type"],
                        pending_result["status"],
                        pending_result["result"],
                    )
                    print(
                        f"{pending_result['task_id']} {pending_result['task_type']} "
                        f"-> {pending_result['status']}"
                    )
                    if pending_result["task_type"] == "SLEEP" and pending_result["status"] == "completed":
                        print(
                            f"  interval={executor.poll_interval_ms}ms "
                            f"jitter={executor.jitter_percent}%"
                        )
                    if pending_result["task_type"] == "EXIT" and pending_result["status"] == "completed":
                        print("EXIT received — shutting down.")
                        stop.set()
                    pending_result = None
                else:
                    response = client.poll()
                    task = response.get("task")
                    if not task:
                        _jittered_wait(executor.poll_interval_ms, executor.jitter_percent, stop)
                        continue
                    status, result = executor.execute(task)
                    pending_result = {
                        "task_id": task["id"],
                        "task_type": task["type"],
                        "status": status,
                        "result": result,
                    }
                    continue
            except NodeClientError as error:
                if error.status == 401:
                    client.clear_session()
                    pending_result = None
                    executor.reset_lab_workspace()
                elif submitting_result and error.status is not None and 400 <= error.status < 500:
                    print(f"Result rejected permanently: {error}")
                    pending_result = None
                else:
                    operation = "Result submission" if submitting_result else "Check-in"
                    print(f"{operation} failed; will retry: {error}")
            _jittered_wait(executor.poll_interval_ms, executor.jitter_percent, stop)
    except KeyboardInterrupt:
        print("\nStopping node…")
    finally:
        if client.node_id:
            try:
                client.disconnect()
            except NodeClientError:
                pass
        executor.close()
    return 0
