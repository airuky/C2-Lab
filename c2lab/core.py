"""Authoritative in-memory state for the localhost C2 learning framework."""

from __future__ import annotations

import copy
import hmac
import secrets
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

from .protocol import (
    TASK_TYPES,
    ProtocolError,
    clean_text,
    validate_capabilities,
    validate_poll_interval,
    validate_result,
    validate_task_result,
    validate_task_payload,
)


MAX_NODES = 20
MAX_TASKS = 500
MAX_QUEUED_TASKS_PER_NODE = 50
MAX_QUEUED_PLAYBOOKS_PER_NODE = 3
MAX_EVENTS = 500
MAX_AUDIT_ENTRIES = 500
TASK_TIMEOUT_SECONDS = 8.0
NODE_STALE_SECONDS = 8.0
STALE_SESSION_TTL_SECONDS = 60.0
HEARTBEAT_EVENT_SECONDS = 5.0
DEFAULT_QUEUE_TTL_SECONDS = 300
MIN_QUEUE_TTL_SECONDS = 5
MAX_QUEUE_TTL_SECONDS = 86_400
MIN_IDEMPOTENCY_KEY_LENGTH = 8
MAX_IDEMPOTENCY_KEY_LENGTH = 128
TERMINAL_TASK_STATUSES = frozenset(
    {"completed", "failed", "timeout", "cancelled", "expired"}
)
_UNSET = object()


class LabError(ValueError):
    """A validation, authorization, or state error safe for the local API."""

    def __init__(self, message: str, *, code: str = "invalid_request", status: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _protocol_call(function: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return function(*args, **kwargs)
    except ProtocolError as error:
        raise LabError(str(error)) from error


def _validate_queue_ttl(value: Any) -> int:
    if value is _UNSET:
        return DEFAULT_QUEUE_TTL_SECONDS
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not MIN_QUEUE_TTL_SECONDS <= value <= MAX_QUEUE_TTL_SECONDS
    ):
        raise LabError(
            "queue_ttl_seconds must be an integer from "
            f"{MIN_QUEUE_TTL_SECONDS} to {MAX_QUEUE_TTL_SECONDS}"
        )
    return value


def _validate_idempotency_key(value: Any) -> str | None:
    if value is None:
        return None
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:"
    if not isinstance(value, str) or not MIN_IDEMPOTENCY_KEY_LENGTH <= len(value) <= MAX_IDEMPOTENCY_KEY_LENGTH:
        raise LabError(
            "idempotency_key must be "
            f"{MIN_IDEMPOTENCY_KEY_LENGTH} to {MAX_IDEMPOTENCY_KEY_LENGTH} characters"
        )
    if any(character not in allowed for character in value):
        raise LabError("idempotency_key contains unsupported characters")
    return value


class LabState:
    """Thread-safe Teamserver state shared by operator and node API handlers."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._nodes: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, dict[str, Any]] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self._audit_entries: deque[dict[str, Any]] = deque(maxlen=MAX_AUDIT_ENTRIES)
        self._event_sequence = 0
        self._audit_sequence = 0

    def _record_locked(
        self,
        kind: str,
        *,
        level: str = "info",
        node_id: str | None = None,
        task_id: str | None = None,
        correlation_id: str | None = None,
        actor: str = "teamserver",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        public_data = copy.deepcopy(data or {})
        if correlation_id is None:
            candidate = public_data.get("correlation_id")
            if isinstance(candidate, str):
                correlation_id = candidate
        self._event_sequence += 1
        event = {
            "id": f"event-{uuid.uuid4().hex[:12]}",
            "sequence": self._event_sequence,
            "time": utc_now(),
            "kind": kind,
            "level": level,
            "node_id": node_id,
            "task_id": task_id,
            "correlation_id": correlation_id,
            "actor": actor,
            "data": public_data,
        }
        self._events.append(event)
        return event

    def _audit_locked(
        self,
        action: str,
        *,
        actor: str,
        node_id: str | None = None,
        task_id: str | None = None,
        correlation_id: str | None = None,
        task_type: str | None = None,
        from_state: str | None = None,
        to_state: str | None = None,
        outcome: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Record fixed-schema control-plane metadata without payloads or secrets."""

        self._audit_sequence += 1
        entry = {
            "id": f"audit-{self._audit_sequence:012d}",
            "sequence": self._audit_sequence,
            "time": utc_now(),
            "actor": actor,
            "action": action,
            "node_id": node_id,
            "task_id": task_id,
            "correlation_id": correlation_id,
            "task_type": task_type,
            "from_state": from_state,
            "to_state": to_state,
            "outcome": outcome,
            "reason": reason,
        }
        self._audit_entries.append(entry)
        return entry

    def _reject_result_locked(self, task: dict[str, Any], *, reason: str) -> None:
        """Record a schema rejection without copying untrusted result content."""

        self._record_locked(
            "task.result_rejected",
            level="warning",
            node_id=task["node_id"],
            task_id=task["id"],
            correlation_id=task["correlation_id"],
            actor="node",
            data={"type": task["type"], "reason": reason},
        )
        self._audit_locked(
            "task.result_rejected",
            actor="node",
            node_id=task["node_id"],
            task_id=task["id"],
            correlation_id=task["correlation_id"],
            task_type=task["type"],
            from_state=task["status"],
            to_state=task["status"],
            outcome="denied",
            reason=reason,
        )

    def enroll_node(
        self,
        name: Any,
        version: Any,
        profile: Any,
        capabilities: Any,
        poll_interval_ms: Any,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        clean_name = _protocol_call(clean_text, name, "name", maximum=48)
        clean_version = _protocol_call(clean_text, version, "version", maximum=32)
        clean_capabilities = _protocol_call(validate_capabilities, profile, capabilities)
        clean_interval = _protocol_call(validate_poll_interval, poll_interval_ms)
        instant = time.monotonic() if now is None else now

        with self._lock:
            if len(self._nodes) >= MAX_NODES:
                closed_nodes = [node for node in self._nodes.values() if not node["session_active"]]
                if not closed_nodes:
                    raise LabError("active node limit reached", code="node_limit", status=429)
                oldest = min(closed_nodes, key=lambda node: node["created_at"])
                del self._nodes[oldest["id"]]
                self._record_locked(
                    "node.pruned",
                    level="warning",
                    node_id=oldest["id"],
                    data={"reason": "closed_node_record_limit"},
                )
                self._audit_locked(
                    "node.pruned",
                    actor="teamserver",
                    node_id=oldest["id"],
                    from_state=oldest["status"],
                    to_state="removed",
                    outcome="success",
                    reason="closed_node_record_limit",
                )
            node_id = f"node-{uuid.uuid4().hex[:10]}"
            session_token = secrets.token_urlsafe(32)
            timestamp = utc_now()
            node = {
                "id": node_id,
                "name": clean_name,
                "status": "online",
                "session_active": True,
                "version": clean_version,
                "profile": profile,
                "transport": "loopback-http-poll/v1",
                "capabilities": clean_capabilities,
                "poll_interval_ms": clean_interval,
                "created_at": timestamp,
                "last_seen": timestamp,
                "tasks_completed": 0,
                "tasks_failed": 0,
                "_session_token": session_token,
                "_last_seen_monotonic": instant,
                "_last_heartbeat_event": instant,
            }
            self._nodes[node_id] = node
            self._record_locked(
                "node.enrolled",
                node_id=node_id,
                actor="node",
                data={
                    "name": clean_name,
                    "version": clean_version,
                    "profile": profile,
                    "transport": node["transport"],
                },
            )
            self._audit_locked(
                "node.enrolled",
                actor="node",
                node_id=node_id,
                to_state="online",
                outcome="success",
            )
            return {"node": self._public_node(node), "session_token": session_token}

    def authenticate_node(self, node_id: Any, session_token: Any) -> bool:
        if not isinstance(node_id, str) or not isinstance(session_token, str):
            return False
        with self._lock:
            node = self._nodes.get(node_id)
            if node is None:
                return False
            return hmac.compare_digest(
                node["_session_token"].encode("utf-8"),
                session_token.encode("utf-8"),
            )

    def _require_node_session_locked(self, node_id: Any, session_token: Any) -> dict[str, Any]:
        if not self.authenticate_node(node_id, session_token):
            raise LabError("invalid node session", code="invalid_node_session", status=401)
        return self._nodes[node_id]

    def poll_node(
        self,
        node_id: Any,
        session_token: Any,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        instant = time.monotonic() if now is None else now
        with self._lock:
            self._expire_locked(instant)
            node = self._require_node_session_locked(node_id, session_token)
            was_offline = node["status"] == "offline"
            node["status"] = "online"
            node["last_seen"] = utc_now()
            node["_last_seen_monotonic"] = instant
            if was_offline:
                self._record_locked("node.online", node_id=node_id, actor="node")
                self._audit_locked(
                    "node.online",
                    actor="node",
                    node_id=node_id,
                    from_state="offline",
                    to_state="online",
                    outcome="success",
                    reason="poll_recovered",
                )
            if instant - node["_last_heartbeat_event"] >= HEARTBEAT_EVENT_SECONDS:
                node["_last_heartbeat_event"] = instant
                self._record_locked("node.heartbeat", node_id=node_id, actor="node")

            active = next(
                (
                    task
                    for task in self._tasks.values()
                    if task["node_id"] == node_id and task["status"] == "dispatched"
                ),
                None,
            )
            if active is not None:
                active["delivery_attempts"] += 1
                self._record_locked(
                    "task.redelivered",
                    level="warning",
                    node_id=node_id,
                    task_id=active["id"],
                    actor="node",
                    data={
                        "type": active["type"],
                        "correlation_id": active["correlation_id"],
                        "delivery_attempt": active["delivery_attempts"],
                    },
                )
                return {
                    "node": self._public_node(node),
                    "task": self._public_task(active),
                    "server_time": utc_now(),
                }

            candidates = [
                task
                for task in self._tasks.values()
                if task["node_id"] == node_id and task["status"] == "queued"
            ]
            task = min(candidates, key=lambda item: item["created_at"]) if candidates else None
            if task is not None:
                task["status"] = "dispatched"
                task["dispatched_at"] = utc_now()
                task["_deadline"] = instant + TASK_TIMEOUT_SECONDS
                task["_queue_deadline"] = None
                task["delivery_attempts"] = 1
                self._record_locked(
                    "task.dispatched",
                    node_id=node_id,
                    task_id=task["id"],
                    actor="node",
                    data={"type": task["type"], "correlation_id": task["correlation_id"]},
                )
                self._audit_locked(
                    "task.dispatched",
                    actor="node",
                    node_id=node_id,
                    task_id=task["id"],
                    correlation_id=task["correlation_id"],
                    task_type=task["type"],
                    from_state="queued",
                    to_state="dispatched",
                    outcome="success",
                )
            return {
                "node": self._public_node(node),
                "task": self._public_task(task) if task else None,
                "server_time": utc_now(),
            }

    def submit_result(
        self,
        node_id: Any,
        session_token: Any,
        task_id: Any,
        status: Any,
        result: Any,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        instant = time.monotonic() if now is None else now
        with self._lock:
            self._expire_locked(instant)
            node = self._require_node_session_locked(node_id, session_token)
            task = self._tasks.get(task_id) if isinstance(task_id, str) else None
            if task is None or task["node_id"] != node_id:
                raise LabError("task not found for node", code="not_found", status=404)
            try:
                clean_status, clean_result = validate_result(status, result)
            except ProtocolError as error:
                self._reject_result_locked(task, reason="invalid_result_envelope")
                raise LabError(str(error), code="invalid_result") from error
            if task["status"] in {"completed", "failed"}:
                if task["status"] == clean_status and task["result"] == clean_result:
                    return self._public_task(task)
                raise LabError(
                    "task already has a different result",
                    code="result_conflict",
                    status=409,
                )
            if task["status"] != "dispatched":
                raise LabError("task is not accepting a result", code="invalid_task_state", status=409)

            try:
                clean_status, clean_result = validate_task_result(
                    task["type"],
                    task["payload"],
                    clean_status,
                    clean_result,
                    expected_runtime={
                        "version": node["version"],
                        "profile": node["profile"],
                        "poll_interval_ms": node["poll_interval_ms"],
                    },
                )
            except ProtocolError as error:
                self._reject_result_locked(task, reason="task_contract_mismatch")
                raise LabError(str(error), code="invalid_result") from error

            task["status"] = clean_status
            task["result"] = copy.deepcopy(clean_result)
            task["completed_at"] = utc_now()
            node["last_seen"] = utc_now()
            node["_last_seen_monotonic"] = instant
            task["_deadline"] = None
            if clean_status == "completed":
                node["tasks_completed"] += 1
            else:
                node["tasks_failed"] += 1
            if task["type"] == "GENERATE_EVENT" and clean_status == "completed":
                self._record_locked(
                    "node.generated_event",
                    level=task["payload"]["severity"],
                    node_id=node_id,
                    task_id=task_id,
                    correlation_id=task["correlation_id"],
                    actor="node",
                    data={
                        "category": task["payload"]["category"],
                        "message": task["payload"]["message"],
                    },
                )
            self._record_locked(
                f"task.{clean_status}",
                level="info" if clean_status == "completed" else "warning",
                node_id=node_id,
                task_id=task_id,
                actor="node",
                data={"type": task["type"], "correlation_id": task["correlation_id"]},
            )
            self._audit_locked(
                f"task.{clean_status}",
                actor="node",
                node_id=node_id,
                task_id=task_id,
                correlation_id=task["correlation_id"],
                task_type=task["type"],
                from_state="dispatched",
                to_state=clean_status,
                outcome=clean_status,
            )
            return self._public_task(task)

    def _fail_unfinished_tasks_locked(
        self,
        node: dict[str, Any],
        *,
        reason: str,
        result_message: str,
    ) -> None:
        """Fail only non-terminal work owned by a session that is being closed."""

        for task in self._tasks.values():
            if task["node_id"] != node["id"] or task["status"] not in {"queued", "dispatched"}:
                continue
            previous_task_state = task["status"]
            task["status"] = "failed"
            task["result"] = {"error": result_message}
            task["completed_at"] = utc_now()
            task["_deadline"] = None
            task["_queue_deadline"] = None
            node["tasks_failed"] += 1
            self._record_locked(
                "task.failed",
                level="warning",
                node_id=node["id"],
                task_id=task["id"],
                correlation_id=task["correlation_id"],
                data={
                    "type": task["type"],
                    "correlation_id": task["correlation_id"],
                    "reason": reason,
                },
            )
            self._audit_locked(
                "task.failed",
                actor="teamserver",
                node_id=node["id"],
                task_id=task["id"],
                correlation_id=task["correlation_id"],
                task_type=task["type"],
                from_state=previous_task_state,
                to_state="failed",
                outcome="failed",
                reason=reason,
            )

    def disconnect_node(self, node_id: Any, session_token: Any) -> dict[str, Any]:
        with self._lock:
            node = self._require_node_session_locked(node_id, session_token)
            previous_node_state = node["status"]
            self._fail_unfinished_tasks_locked(
                node,
                reason="node_disconnected",
                result_message="node session closed before task completion",
            )
            node["status"] = "offline"
            self._record_locked(
                "node.disconnected",
                level="warning",
                node_id=node_id,
                actor="node",
                data={"session_invalidated": True},
            )
            node["session_active"] = False
            node["_session_token"] = secrets.token_urlsafe(32)
            self._audit_locked(
                "node.disconnected",
                actor="node",
                node_id=node_id,
                from_state=previous_node_state,
                to_state="offline",
                outcome="success",
                reason="session_invalidated",
            )
            return self._public_node(node)

    def _prune_oldest_terminal_task_locked(self) -> None:
        """Make one task slot without ever removing queued or dispatched work."""

        # ISO timestamps are sortable; dictionary insertion order is the stable
        # tie-breaker when multiple tasks share the same millisecond.
        oldest = min(
            (task for task in self._tasks.values() if task["status"] in TERMINAL_TASK_STATUSES),
            key=lambda task: task["created_at"],
            default=None,
        )
        if oldest is None:
            raise LabError("task limit reached; reset the lab", code="task_limit", status=429)

        del self._tasks[oldest["id"]]
        reason = "terminal_task_retention_limit"
        self._record_locked(
            "task.pruned",
            level="warning",
            node_id=oldest["node_id"],
            task_id=oldest["id"],
            correlation_id=oldest["correlation_id"],
            data={
                "type": oldest["type"],
                "status": oldest["status"],
                "correlation_id": oldest["correlation_id"],
                "reason": reason,
            },
        )
        self._audit_locked(
            "task.pruned",
            actor="teamserver",
            node_id=oldest["node_id"],
            task_id=oldest["id"],
            correlation_id=oldest["correlation_id"],
            task_type=oldest["type"],
            from_state=oldest["status"],
            to_state="removed",
            outcome="success",
            reason=reason,
        )

    def queue_task(
        self,
        node_id: Any,
        task_type: Any,
        payload: Any,
        *,
        queue_ttl_seconds: Any = _UNSET,
        idempotency_key: Any = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        if not isinstance(node_id, str):
            raise LabError("node_id must be a string")
        try:
            clean_type, clean_payload = validate_task_payload(task_type, payload)
        except ProtocolError as error:
            code = "unsupported_task_type" if task_type not in TASK_TYPES else "invalid_request"
            raise LabError(str(error), code=code) from error
        clean_ttl = _validate_queue_ttl(queue_ttl_seconds)
        clean_idempotency_key = _validate_idempotency_key(idempotency_key)
        instant = time.monotonic() if now is None else now

        with self._lock:
            if clean_idempotency_key is not None:
                existing = next(
                    (
                        task
                        for task in self._tasks.values()
                        if task.get("_idempotency_key") == clean_idempotency_key
                    ),
                    None,
                )
                if existing is not None:
                    same_request = (
                        existing["node_id"] == node_id
                        and existing["type"] == clean_type
                        and existing["payload"] == clean_payload
                        and existing["queue_ttl_seconds"] == clean_ttl
                    )
                    if same_request:
                        return self._public_task(existing)
                    raise LabError(
                        "Idempotency-Key was already used for a different task request",
                        code="idempotency_conflict",
                        status=409,
                    )

            node = self._nodes.get(node_id)
            if node is None:
                raise LabError("node not found", code="not_found", status=404)
            if not node["session_active"]:
                raise LabError(
                    "node session is closed; start a new node process",
                    code="node_disconnected",
                    status=409,
                )
            if clean_type not in node["capabilities"]:
                raise LabError(
                    "task is not allowed by the node profile",
                    code="capability_denied",
                    status=409,
                )
            queued_for_node = sum(
                task["status"] == "queued" and task["node_id"] == node_id
                for task in self._tasks.values()
            )
            if queued_for_node >= MAX_QUEUED_TASKS_PER_NODE:
                raise LabError("node queue limit reached", code="queue_limit", status=429)
            if clean_type == "RUN_PLAYBOOK":
                queued_playbooks = sum(
                    task["status"] == "queued"
                    and task["node_id"] == node_id
                    and task["type"] == "RUN_PLAYBOOK"
                    for task in self._tasks.values()
                )
                if queued_playbooks >= MAX_QUEUED_PLAYBOOKS_PER_NODE:
                    raise LabError(
                        "purple-lab playbook queue limit reached",
                        code="playbook_queue_limit",
                        status=429,
                    )
            if len(self._tasks) >= MAX_TASKS:
                self._prune_oldest_terminal_task_locked()

            task_id = f"task-{uuid.uuid4().hex[:12]}"
            task = {
                "id": task_id,
                "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
                "node_id": node_id,
                "type": clean_type,
                "payload": clean_payload,
                "status": "queued",
                "result": None,
                "created_at": utc_now(),
                "queue_ttl_seconds": clean_ttl,
                "dispatched_at": None,
                "completed_at": None,
                "delivery_attempts": 0,
                "_deadline": None,
                "_queue_deadline": instant + clean_ttl,
                "_idempotency_key": clean_idempotency_key,
            }
            self._tasks[task_id] = task
            self._record_locked(
                "task.queued",
                node_id=node_id,
                task_id=task_id,
                actor="operator",
                data={"type": clean_type, "correlation_id": task["correlation_id"]},
            )
            self._audit_locked(
                "task.queued",
                actor="operator",
                node_id=node_id,
                task_id=task_id,
                correlation_id=task["correlation_id"],
                task_type=clean_type,
                to_state="queued",
                outcome="accepted",
            )
            return self._public_task(task)

    def cancel_task(self, task_id: Any) -> dict[str, Any]:
        if not isinstance(task_id, str):
            raise LabError("task_id must be a string")
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise LabError("task not found", code="not_found", status=404)
            if task["status"] == "cancelled":
                return self._public_task(task)
            if task["status"] != "queued":
                raise LabError(
                    "only queued tasks can be cancelled",
                    code="task_not_cancellable",
                    status=409,
                )

            task["status"] = "cancelled"
            task["result"] = {"reason": "operator_cancelled"}
            task["completed_at"] = utc_now()
            task["_queue_deadline"] = None
            self._record_locked(
                "task.cancelled",
                level="warning",
                node_id=task["node_id"],
                task_id=task["id"],
                correlation_id=task["correlation_id"],
                actor="operator",
                data={
                    "type": task["type"],
                    "correlation_id": task["correlation_id"],
                    "reason": "operator_cancelled",
                },
            )
            self._audit_locked(
                "task.cancelled",
                actor="operator",
                node_id=task["node_id"],
                task_id=task["id"],
                correlation_id=task["correlation_id"],
                task_type=task["type"],
                from_state="queued",
                to_state="cancelled",
                outcome="cancelled",
                reason="operator_cancelled",
            )
            return self._public_task(task)

    def expire(self, *, now: float | None = None) -> None:
        instant = time.monotonic() if now is None else now
        with self._lock:
            self._expire_locked(instant)

    def _expire_locked(self, now: float) -> None:
        for task in self._tasks.values():
            queue_deadline = task.get("_queue_deadline")
            if task["status"] != "queued" or queue_deadline is None or now < queue_deadline:
                continue
            task["status"] = "expired"
            task["completed_at"] = utc_now()
            task["result"] = {"reason": "queue_ttl_exceeded"}
            task["_queue_deadline"] = None
            self._record_locked(
                "task.expired",
                level="warning",
                node_id=task["node_id"],
                task_id=task["id"],
                correlation_id=task["correlation_id"],
                data={
                    "type": task["type"],
                    "correlation_id": task["correlation_id"],
                    "reason": "queue_ttl_exceeded",
                },
            )
            self._audit_locked(
                "task.expired",
                actor="teamserver",
                node_id=task["node_id"],
                task_id=task["id"],
                correlation_id=task["correlation_id"],
                task_type=task["type"],
                from_state="queued",
                to_state="expired",
                outcome="expired",
                reason="queue_ttl_exceeded",
            )
        for task in self._tasks.values():
            if task["status"] != "dispatched" or now < task["_deadline"]:
                continue
            task["status"] = "timeout"
            task["completed_at"] = utc_now()
            task["result"] = {"error": "node did not return before the task deadline"}
            task["_deadline"] = None
            node = self._nodes.get(task["node_id"])
            if node is not None:
                node["tasks_failed"] += 1
            self._record_locked(
                "task.timeout",
                level="warning",
                node_id=task["node_id"],
                task_id=task["id"],
                data={"type": task["type"], "correlation_id": task["correlation_id"]},
            )
            self._audit_locked(
                "task.timeout",
                actor="teamserver",
                node_id=task["node_id"],
                task_id=task["id"],
                correlation_id=task["correlation_id"],
                task_type=task["type"],
                from_state="dispatched",
                to_state="timeout",
                outcome="timeout",
                reason="deadline_exceeded",
            )
        for node in self._nodes.values():
            stale_after = max(NODE_STALE_SECONDS, (node["poll_interval_ms"] / 1_000) * 3)
            if node["status"] == "online" and now - node["_last_seen_monotonic"] >= stale_after:
                node["status"] = "offline"
                self._record_locked(
                    "node.stale",
                    level="warning",
                    node_id=node["id"],
                    data={"stale_after_seconds": stale_after},
                )
                self._audit_locked(
                    "node.stale",
                    actor="teamserver",
                    node_id=node["id"],
                    from_state="online",
                    to_state="offline",
                    outcome="success",
                    reason="heartbeat_timeout",
                )
            offline_for = now - node["_last_seen_monotonic"] - stale_after
            if (
                node["status"] == "offline"
                and node["session_active"]
                and offline_for >= STALE_SESSION_TTL_SECONDS
            ):
                self._fail_unfinished_tasks_locked(
                    node,
                    reason="node_session_expired",
                    result_message="node session expired before task completion",
                )
                node["session_active"] = False
                node["_session_token"] = secrets.token_urlsafe(32)
                self._record_locked(
                    "node.session_expired",
                    level="warning",
                    node_id=node["id"],
                    data={
                        "session_invalidated": True,
                        "offline_ttl_seconds": STALE_SESSION_TTL_SECONDS,
                    },
                )
                self._audit_locked(
                    "node.session_expired",
                    actor="teamserver",
                    node_id=node["id"],
                    from_state="offline",
                    to_state="offline",
                    outcome="success",
                    reason="offline_session_ttl_exceeded",
                )

    def reset(self) -> None:
        with self._lock:
            self._nodes.clear()
            self._tasks.clear()
            self._events.clear()
            self._record_locked("lab.reset", actor="operator", data={"sessions_invalidated": True})
            self._audit_locked(
                "lab.reset",
                actor="operator",
                outcome="success",
                reason="sessions_invalidated",
            )

    def nodes(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(
                (self._public_node(node) for node in self._nodes.values()),
                key=lambda node: node["created_at"],
            )

    def tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(
                (self._public_task(task) for task in self._tasks.values()),
                key=lambda task: task["created_at"],
                reverse=True,
            )

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(copy.deepcopy(list(self._events))))

    def audit(self) -> list[dict[str, Any]]:
        """Return newest-first, fixed-schema control-plane audit metadata."""

        with self._lock:
            return list(reversed(copy.deepcopy(list(self._audit_entries))))

    @staticmethod
    def _counts(nodes: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "nodes_online": sum(node["status"] == "online" for node in nodes),
            "nodes_total": len(nodes),
            "tasks_queued": sum(task["status"] == "queued" for task in tasks),
            "tasks_active": sum(task["status"] == "dispatched" for task in tasks),
            "tasks_completed": sum(task["status"] == "completed" for task in tasks),
            "tasks_failed": sum(task["status"] == "failed" for task in tasks),
            "tasks_timeout": sum(task["status"] == "timeout" for task in tasks),
            "tasks_cancelled": sum(task["status"] == "cancelled" for task in tasks),
            "tasks_expired": sum(task["status"] == "expired" for task in tasks),
        }

    def overview(self) -> dict[str, Any]:
        with self._lock:
            nodes = self.nodes()
            tasks = self.tasks()
            return {
                "lab_mode": True,
                "protocol": "loopback-http-poll/v1",
                "counts": self._counts(nodes, tasks),
                "nodes": nodes,
                "tasks": tasks,
                "events": self.events(),
            }

    def report(self) -> dict[str, Any]:
        """Return a bounded snapshot without task payloads, results, or credentials."""

        with self._lock:
            nodes = self.nodes()
            tasks = self.tasks()
            audit_entries = self.audit()
            safe_nodes = [
                {
                    key: node[key]
                    for key in (
                        "id",
                        "name",
                        "status",
                        "session_active",
                        "profile",
                        "transport",
                        "created_at",
                        "last_seen",
                        "tasks_completed",
                        "tasks_failed",
                    )
                }
                for node in nodes
            ]
            safe_tasks = [
                {
                    key: task[key]
                    for key in (
                        "id",
                        "correlation_id",
                        "node_id",
                        "type",
                        "status",
                        "created_at",
                        "queue_ttl_seconds",
                        "dispatched_at",
                        "completed_at",
                        "delivery_attempts",
                    )
                }
                for task in tasks
            ]
            counts = self._counts(nodes, tasks)
            counts.update(
                {
                    "events_retained": len(self._events),
                    "audit_retained": len(audit_entries),
                }
            )
            return {
                "generated_at": utc_now(),
                "lab_mode": True,
                "protocol": "loopback-http-poll/v1",
                "counts": counts,
                "retention": {
                    "nodes": MAX_NODES,
                    "tasks": MAX_TASKS,
                    "events": MAX_EVENTS,
                    "audit": MAX_AUDIT_ENTRIES,
                    "default_queue_ttl_seconds": DEFAULT_QUEUE_TTL_SECONDS,
                    "stale_session_ttl_seconds": STALE_SESSION_TTL_SECONDS,
                },
                "sequences": {
                    "events": self._event_sequence,
                    "audit": self._audit_sequence,
                },
                "nodes": safe_nodes,
                "tasks": safe_tasks,
                "audit": audit_entries,
            }

    @staticmethod
    def _public_node(node: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy({key: value for key, value in node.items() if not key.startswith("_")})

    @staticmethod
    def _public_task(task: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy({key: value for key, value in task.items() if not key.startswith("_")})


class LabRuntime:
    """Background expiry monitor; task execution occurs in separate node processes."""

    def __init__(self, state: LabState, *, tick_seconds: float = 0.25) -> None:
        self.state = state
        self.tick_seconds = tick_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="c2lab-teamserver-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.wait(self.tick_seconds):
            self.state.expire()
