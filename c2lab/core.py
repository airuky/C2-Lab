"""Authoritative in-memory state for the localhost C2 learning framework."""

from __future__ import annotations

import copy
import hmac
import secrets
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .exercises import (
    CONTAINMENT_ACTIONS,
    MAX_EXERCISES,
    MAX_EXERCISE_TIMELINE,
    SCENARIO_IDS,
    detections_for_playbook,
    scenario_catalog,
    scenario_definition,
)
from .protocol import (
    MAX_JITTER_PERCENT,
    TASK_TYPES,
    ProtocolError,
    clean_text,
    validate_capabilities,
    validate_jitter_percent,
    validate_poll_interval,
    validate_result,
    validate_task_result,
    validate_task_payload,
)


MAX_NODES = 20
MAX_TASKS = 500
MAX_QUEUED_TASKS_PER_NODE = 50
MAX_QUEUED_PLAYBOOKS_PER_NODE = 3
MAX_OPERATION_STEPS = 3
MAX_OPERATION_IDEMPOTENCY_RECORDS = MAX_TASKS
MAX_EVENTS = 500
MAX_AUDIT_ENTRIES = 500
MAX_OPERATOR_NOTES_RETAINED = 100
MAX_OPERATOR_NOTE_LENGTH = 240
MAX_SYNC_CURSOR = 9_223_372_036_854_775_807
MAX_SYNC_PAGE_SIZE = 100
TASK_TIMEOUT_SECONDS = 8.0
NODE_STALE_SECONDS = 8.0
STALE_SESSION_TTL_SECONDS = 60.0
HEARTBEAT_EVENT_SECONDS = 5.0
DEFAULT_QUEUE_TTL_SECONDS = 300
MIN_QUEUE_TTL_SECONDS = 5
MAX_QUEUE_TTL_SECONDS = 86_400
MIN_IDEMPOTENCY_KEY_LENGTH = 8
MAX_IDEMPOTENCY_KEY_LENGTH = 128
DEFAULT_OPERATOR_ACTOR = "operator"
MAX_OPERATOR_ACTOR_LENGTH = 48
MAX_TASK_RESULT_TOMBSTONES = MAX_TASKS
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


def _validate_operator_actor(value: Any) -> str:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_OPERATOR_ACTOR_LENGTH:
        raise LabError(
            f"actor must be 1 to {MAX_OPERATOR_ACTOR_LENGTH} characters"
        )
    if any(character not in allowed for character in value):
        raise LabError("actor contains unsupported characters")
    return value


def _validate_operation_steps(value: Any) -> list[dict[str, str]]:
    if type(value) is not list or not 1 <= len(value) <= MAX_OPERATION_STEPS:
        raise LabError(f"steps must be a list of 1 to {MAX_OPERATION_STEPS} playbooks")
    clean_steps: list[dict[str, str]] = []
    for index, step in enumerate(value):
        if type(step) is not dict or set(step) != {"playbook"}:
            raise LabError(
                f"steps[{index}] must be an object containing only playbook"
            )
        try:
            _task_type, clean_payload = validate_task_payload("RUN_PLAYBOOK", step)
        except ProtocolError as error:
            raise LabError(str(error)) from error
        clean_steps.append(clean_payload)
    return clean_steps


def _validate_sync_cursor(value: Any, field: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= MAX_SYNC_CURSOR
    ):
        raise LabError(f"{field} must be an integer from 0 to {MAX_SYNC_CURSOR}")
    return value


def _validate_sync_limit(value: Any) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= MAX_SYNC_PAGE_SIZE
    ):
        raise LabError(f"limit must be an integer from 1 to {MAX_SYNC_PAGE_SIZE}")
    return value


def _type_strict_equal(left: Any, right: Any) -> bool:
    """Compare validated JSON values without Python's bool/int coercion."""

    if type(left) is not type(right):
        return False
    if type(left) is dict:
        if len(left) != len(right) or any(key not in right for key in left):
            return False
        return all(_type_strict_equal(value, right[key]) for key, value in left.items())
    if type(left) is list:
        return len(left) == len(right) and all(
            _type_strict_equal(left_value, right_value)
            for left_value, right_value in zip(left, right, strict=True)
        )
    return left == right


class LabState:
    """Thread-safe Teamserver state shared by operator and node API handlers."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._nodes: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, dict[str, Any]] = {}
        self._task_result_tombstones: dict[str, dict[str, Any]] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self._audit_entries: deque[dict[str, Any]] = deque(maxlen=MAX_AUDIT_ENTRIES)
        self._note_idempotency: dict[str, tuple[str, str, str]] = {}
        self._exercises: dict[str, dict[str, Any]] = {}
        self._exercise_idempotency: dict[str, tuple[str, str, str, str]] = {}
        self._operation_idempotency: dict[str, dict[str, Any]] = {}
        self._event_sequence = 0
        self._audit_sequence = 0
        self._task_sequence = 0
        self._stream_id = f"stream-{secrets.token_hex(12)}"

    def _next_task_sequence_locked(self) -> int:
        self._task_sequence += 1
        return self._task_sequence

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
        jitter_percent: Any = 0,
        now: float | None = None,
    ) -> dict[str, Any]:
        clean_name = _protocol_call(clean_text, name, "name", maximum=48)
        clean_version = _protocol_call(clean_text, version, "version", maximum=32)
        clean_capabilities = _protocol_call(validate_capabilities, profile, capabilities)
        clean_interval = _protocol_call(validate_poll_interval, poll_interval_ms)
        clean_jitter = _protocol_call(validate_jitter_percent, jitter_percent)
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
                "jitter_percent": clean_jitter,
                "created_at": timestamp,
                "last_seen": timestamp,
                "tasks_completed": 0,
                "tasks_failed": 0,
                "tasking_paused": False,
                "tasking_paused_at": None,
                "tasking_paused_by": None,
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

            if node.get("tasking_paused"):
                return {
                    "node": self._public_node(node),
                    "task": None,
                    "server_time": utc_now(),
                }

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
                    "task": self._node_task(active),
                    "server_time": utc_now(),
                }

            candidates = [
                task
                for task in self._tasks.values()
                if task["node_id"] == node_id and task["status"] == "queued"
            ]
            task = min(candidates, key=lambda item: item["_sequence"]) if candidates else None
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
                "task": self._node_task(task) if task else None,
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
            tombstone = (
                self._task_result_tombstones.get(task_id)
                if task is None and isinstance(task_id, str)
                else None
            )
            result_record = task if task is not None else tombstone
            if result_record is None or result_record["node_id"] != node_id:
                raise LabError("task not found for node", code="not_found", status=404)
            try:
                clean_status, clean_result = validate_result(status, result)
            except ProtocolError as error:
                self._reject_result_locked(result_record, reason="invalid_result_envelope")
                raise LabError(str(error), code="invalid_result") from error
            if task is None:
                if result_record["status"] == clean_status and _type_strict_equal(
                    result_record["result"], clean_result
                ):
                    return self._node_task(result_record)
                raise LabError(
                    "task already has a different result",
                    code="result_conflict",
                    status=409,
                )
            if task["status"] in {"completed", "failed"}:
                if task["status"] == clean_status and _type_strict_equal(
                    task["result"], clean_result
                ):
                    return self._node_task(task)
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
                        "jitter_percent": node.get("jitter_percent", 0),
                    },
                )
            except ProtocolError as error:
                self._reject_result_locked(task, reason="task_contract_mismatch")
                raise LabError(str(error), code="invalid_result") from error

            task["status"] = clean_status
            task["result"] = copy.deepcopy(clean_result)
            task["_result_accepted"] = True
            task["completed_at"] = utc_now()
            node["last_seen"] = utc_now()
            node["_last_seen_monotonic"] = instant
            task["_deadline"] = None
            if clean_status == "completed":
                node["tasks_completed"] += 1
            else:
                node["tasks_failed"] += 1
            if task["type"] == "SLEEP" and clean_status == "completed":
                node["poll_interval_ms"] = clean_result["new_interval_ms"]
                node["jitter_percent"] = clean_result["jitter_percent"]
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
            self._refresh_exercises_locked()
            return self._node_task(task)

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
        self._refresh_exercises_locked()

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

    def _task_is_required_by_running_exercise_locked(self, task: dict[str, Any]) -> bool:
        exercise_id = task.get("_exercise_id")
        if not isinstance(exercise_id, str):
            return False
        exercise = self._exercises.get(exercise_id)
        return exercise is not None and exercise["status"] == "running"

    def _remember_task_result_tombstone_locked(self, task: dict[str, Any]) -> None:
        """Retain a bounded ACK record so a Node can safely retry after pruning."""

        if not task.get("_result_accepted"):
            return
        while len(self._task_result_tombstones) >= MAX_TASK_RESULT_TOMBSTONES:
            oldest_task_id = next(iter(self._task_result_tombstones))
            del self._task_result_tombstones[oldest_task_id]
        self._task_result_tombstones[task["id"]] = self._node_task(task)

    def _prune_terminal_task_locked(self, task: dict[str, Any]) -> None:
        self._remember_task_result_tombstone_locked(task)
        del self._tasks[task["id"]]
        reason = "terminal_task_retention_limit"
        self._record_locked(
            "task.pruned",
            level="warning",
            node_id=task["node_id"],
            task_id=task["id"],
            correlation_id=task["correlation_id"],
            data={
                "type": task["type"],
                "status": task["status"],
                "correlation_id": task["correlation_id"],
                "reason": reason,
            },
        )
        self._audit_locked(
            "task.pruned",
            actor="teamserver",
            node_id=task["node_id"],
            task_id=task["id"],
            correlation_id=task["correlation_id"],
            task_type=task["type"],
            from_state=task["status"],
            to_state="removed",
            outcome="success",
            reason=reason,
        )

    def _make_task_slots_locked(self, required_slots: int) -> None:
        """Atomically free task slots without dropping live scenario evidence."""

        slots_to_free = len(self._tasks) + required_slots - MAX_TASKS
        if slots_to_free <= 0:
            return
        candidates = sorted(
            (
                task
                for task in self._tasks.values()
                if task["status"] in TERMINAL_TASK_STATUSES
                and not self._task_is_required_by_running_exercise_locked(task)
            ),
            key=lambda task: task["_sequence"],
        )
        if len(candidates) < slots_to_free:
            raise LabError("task limit reached; reset the lab", code="task_limit", status=429)
        for task in candidates[:slots_to_free]:
            self._prune_terminal_task_locked(task)

    def _operation_idempotency_record_is_evictable_locked(
        self,
        record: dict[str, Any],
    ) -> bool:
        """Keep replay records while any task in the operation is still live."""

        return all(
            (task := self._tasks.get(task_id)) is None
            or task["status"] in TERMINAL_TASK_STATUSES
            for task_id in record["task_ids"]
        )

    def queue_task(
        self,
        node_id: Any,
        task_type: Any,
        payload: Any,
        *,
        queue_ttl_seconds: Any = _UNSET,
        idempotency_key: Any = None,
        actor: Any = DEFAULT_OPERATOR_ACTOR,
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
        clean_actor = _validate_operator_actor(actor)
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
                        and existing["created_by"] == clean_actor
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
            if node.get("tasking_paused"):
                raise LabError(
                    "node tasking is paused by containment",
                    code="node_tasking_paused",
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
            self._make_task_slots_locked(1)

            task_id = f"task-{uuid.uuid4().hex[:12]}"
            task = {
                "id": task_id,
                "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
                "node_id": node_id,
                "type": clean_type,
                "created_by": clean_actor,
                "payload": clean_payload,
                "status": "queued",
                "result": None,
                "created_at": utc_now(),
                "queue_ttl_seconds": clean_ttl,
                "dispatched_at": None,
                "completed_at": None,
                "delivery_attempts": 0,
                "_sequence": self._next_task_sequence_locked(),
                "_deadline": None,
                "_queue_deadline": instant + clean_ttl,
                "_idempotency_key": clean_idempotency_key,
                "_result_accepted": False,
            }
            self._tasks[task_id] = task
            self._record_locked(
                "task.queued",
                node_id=node_id,
                task_id=task_id,
                actor=clean_actor,
                data={"type": clean_type, "correlation_id": task["correlation_id"]},
            )
            self._audit_locked(
                "task.queued",
                actor=clean_actor,
                node_id=node_id,
                task_id=task_id,
                correlation_id=task["correlation_id"],
                task_type=clean_type,
                to_state="queued",
                outcome="accepted",
            )
            return self._public_task(task)

    def queue_operation(
        self,
        node_id: Any,
        steps: Any,
        *,
        queue_ttl_seconds: Any = _UNSET,
        idempotency_key: Any = None,
        actor: Any = DEFAULT_OPERATOR_ACTOR,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Atomically queue an ordered, bounded set of fixed purple-lab playbooks."""

        if not isinstance(node_id, str):
            raise LabError("node_id must be a string")
        clean_steps = _validate_operation_steps(steps)
        clean_ttl = _validate_queue_ttl(queue_ttl_seconds)
        clean_idempotency_key = _validate_idempotency_key(idempotency_key)
        clean_actor = _validate_operator_actor(actor)
        instant = time.monotonic() if now is None else now
        playbooks = tuple(step["playbook"] for step in clean_steps)

        with self._lock:
            idempotency_eviction_keys: list[str] = []
            if clean_idempotency_key is not None:
                previous = self._operation_idempotency.get(clean_idempotency_key)
                if previous is not None:
                    same_request = (
                        previous["actor"] == clean_actor
                        and previous["node_id"] == node_id
                        and previous["playbooks"] == playbooks
                        and previous["queue_ttl_seconds"] == clean_ttl
                    )
                    if same_request:
                        return copy.deepcopy(previous["summary"])
                    raise LabError(
                        "Idempotency-Key was already used for a different operation request",
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
            if node["status"] != "online":
                raise LabError(
                    "operation requires an online node",
                    code="node_offline",
                    status=409,
                )
            if node.get("tasking_paused"):
                raise LabError(
                    "node tasking is paused by containment",
                    code="node_tasking_paused",
                    status=409,
                )
            if node["profile"] != "purple_lab" or "RUN_PLAYBOOK" not in node["capabilities"]:
                raise LabError(
                    "operations require a purple_lab node",
                    code="capability_denied",
                    status=409,
                )

            queued_for_node = sum(
                task["status"] == "queued" and task["node_id"] == node_id
                for task in self._tasks.values()
            )
            if queued_for_node + len(clean_steps) > MAX_QUEUED_TASKS_PER_NODE:
                raise LabError("node queue limit reached", code="queue_limit", status=429)
            queued_playbooks = sum(
                task["status"] == "queued"
                and task["node_id"] == node_id
                and task["type"] == "RUN_PLAYBOOK"
                for task in self._tasks.values()
            )
            if queued_playbooks + len(clean_steps) > MAX_QUEUED_PLAYBOOKS_PER_NODE:
                raise LabError(
                    "purple-lab playbook queue limit reached",
                    code="playbook_queue_limit",
                    status=429,
                )

            if clean_idempotency_key is not None:
                records_to_free = (
                    len(self._operation_idempotency)
                    + 1
                    - MAX_OPERATION_IDEMPOTENCY_RECORDS
                )
                if records_to_free > 0:
                    idempotency_eviction_keys = [
                        key
                        for key, record in self._operation_idempotency.items()
                        if self._operation_idempotency_record_is_evictable_locked(record)
                    ][:records_to_free]
                    if len(idempotency_eviction_keys) < records_to_free:
                        raise LabError(
                            "operation idempotency retention limit reached",
                            code="operation_idempotency_limit",
                            status=429,
                        )

            self._make_task_slots_locked(len(clean_steps))
            operation_id = f"operation-{uuid.uuid4().hex[:12]}"
            created_tasks: list[dict[str, Any]] = []
            for operation_step, payload in enumerate(clean_steps, start=1):
                task_id = f"task-{uuid.uuid4().hex[:12]}"
                task = {
                    "id": task_id,
                    "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
                    "node_id": node_id,
                    "type": "RUN_PLAYBOOK",
                    "created_by": clean_actor,
                    "payload": copy.deepcopy(payload),
                    "status": "queued",
                    "result": None,
                    "created_at": utc_now(),
                    "queue_ttl_seconds": clean_ttl,
                    "dispatched_at": None,
                    "completed_at": None,
                    "delivery_attempts": 0,
                    "operation_id": operation_id,
                    "operation_step": operation_step,
                    "_sequence": self._next_task_sequence_locked(),
                    "_deadline": None,
                    "_queue_deadline": instant + clean_ttl,
                    "_idempotency_key": None,
                    "_result_accepted": False,
                }
                self._tasks[task_id] = task
                created_tasks.append(task)
                self._record_locked(
                    "task.queued",
                    node_id=node_id,
                    task_id=task_id,
                    actor=clean_actor,
                    data={"type": "RUN_PLAYBOOK", "correlation_id": task["correlation_id"]},
                )
                self._audit_locked(
                    "task.queued",
                    actor=clean_actor,
                    node_id=node_id,
                    task_id=task_id,
                    correlation_id=task["correlation_id"],
                    task_type="RUN_PLAYBOOK",
                    to_state="queued",
                    outcome="accepted",
                    reason="operation_request",
                )

            task_ids = [task["id"] for task in created_tasks]
            self._record_locked(
                "operation.queued",
                node_id=node_id,
                correlation_id=operation_id,
                actor=clean_actor,
                data={
                    "operation_id": operation_id,
                    "step_count": len(clean_steps),
                    "task_ids": task_ids,
                },
            )
            self._audit_locked(
                "operation.queued",
                actor=clean_actor,
                node_id=node_id,
                correlation_id=operation_id,
                to_state="queued",
                outcome="accepted",
                reason="bounded_playbook_operation",
            )
            summary = {
                "id": operation_id,
                "node_id": node_id,
                "created_by": clean_actor,
                "steps": copy.deepcopy(clean_steps),
                "tasks": [self._public_task(task) for task in created_tasks],
            }
            if clean_idempotency_key is not None:
                for key in idempotency_eviction_keys:
                    del self._operation_idempotency[key]
                self._operation_idempotency[clean_idempotency_key] = {
                    "actor": clean_actor,
                    "node_id": node_id,
                    "playbooks": playbooks,
                    "queue_ttl_seconds": clean_ttl,
                    "task_ids": tuple(task_ids),
                    "summary": copy.deepcopy(summary),
                }
            return summary

    def cancel_task(
        self,
        task_id: Any,
        *,
        actor: Any = DEFAULT_OPERATOR_ACTOR,
    ) -> dict[str, Any]:
        if not isinstance(task_id, str):
            raise LabError("task_id must be a string")
        clean_actor = _validate_operator_actor(actor)
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
                actor=clean_actor,
                data={
                    "type": task["type"],
                    "correlation_id": task["correlation_id"],
                    "reason": "operator_cancelled",
                },
            )
            self._audit_locked(
                "task.cancelled",
                actor=clean_actor,
                node_id=task["node_id"],
                task_id=task["id"],
                correlation_id=task["correlation_id"],
                task_type=task["type"],
                from_state="queued",
                to_state="cancelled",
                outcome="cancelled",
                reason="operator_cancelled",
            )
            self._refresh_exercises_locked()
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
        self._refresh_exercises_locked()

    @staticmethod
    def _public_exercise(exercise: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(
            {key: value for key, value in exercise.items() if not key.startswith("_")}
        )

    def _append_exercise_timeline_locked(
        self,
        exercise: dict[str, Any],
        *,
        phase: str,
        kind: str,
        summary: str,
        task_id: str | None = None,
        rule_id: str | None = None,
        technique_id: str | None = None,
        action: str | None = None,
    ) -> None:
        """Append one fixed-schema timeline item within the catalog bound."""

        if len(exercise["timeline"]) >= MAX_EXERCISE_TIMELINE:
            return
        exercise["_timeline_sequence"] += 1
        exercise["timeline"].append(
            {
                "sequence": exercise["_timeline_sequence"],
                "time": utc_now(),
                "phase": phase,
                "kind": kind,
                "summary": summary,
                "task_id": task_id,
                "rule_id": rule_id,
                "technique_id": technique_id,
                "action": action,
            }
        )

    def _append_exercise_task_evidence_locked(
        self,
        exercise: dict[str, Any],
        task: dict[str, Any],
    ) -> None:
        """Retain terminal state and fixed ATT&CK IDs without retaining task results."""

        playbook = task["payload"]["playbook"]
        self._append_exercise_timeline_locked(
            exercise,
            phase="simulate",
            kind=f"task.{task['status']}",
            summary=f"{playbook} {task['status']}",
            task_id=task["id"],
        )
        if task["status"] != "completed":
            return
        allowed_technique_ids = {
            technique["id"]
            for technique in scenario_definition(exercise["scenario_id"])["techniques"]
        }
        for technique in task["result"]["attack_techniques"]:
            technique_id = technique["id"]
            if technique_id not in allowed_technique_ids:
                continue
            self._append_exercise_timeline_locked(
                exercise,
                phase="simulate",
                kind="technique.observed",
                summary=f"{technique_id} observed",
                task_id=task["id"],
                technique_id=technique_id,
            )

    def _refresh_exercises_locked(self) -> None:
        """Derive fixed alerts and terminal state from validated task records."""

        for exercise in self._exercises.values():
            if exercise["status"] == "contained":
                # A task may already be dispatched when containment is applied.
                # Preserve its eventual terminal state for the read model, but do
                # not create new detections or replace the contained state.
                for task_id in exercise["task_ids"]:
                    if task_id in exercise["_observed_task_ids"]:
                        continue
                    task = self._tasks.get(task_id)
                    if task is None or task["status"] not in TERMINAL_TASK_STATUSES:
                        continue
                    exercise["_observed_task_ids"].append(task_id)
                    self._append_exercise_task_evidence_locked(exercise, task)
                continue
            for task_id in exercise["task_ids"]:
                if task_id in exercise["_observed_task_ids"]:
                    continue
                task = self._tasks.get(task_id)
                if task is None or task["status"] not in TERMINAL_TASK_STATUSES:
                    continue
                exercise["_observed_task_ids"].append(task_id)
                playbook = task["payload"]["playbook"]
                self._append_exercise_task_evidence_locked(exercise, task)
                if task["status"] != "completed":
                    continue
                for rule in detections_for_playbook(exercise["scenario_id"], playbook):
                    if any(alert["rule_id"] == rule["id"] for alert in exercise["alerts"]):
                        continue
                    detected_at = utc_now()
                    alert = {
                        "id": f"alert-{uuid.uuid4().hex[:12]}",
                        "rule_id": rule["id"],
                        "source_id": rule["source_id"],
                        "name": rule["name"],
                        "technique_id": rule["technique_id"],
                        "severity": rule["severity"],
                        "signal": rule["signal"],
                        "status": "open",
                        "detected_at": detected_at,
                        "contained_at": None,
                        "task_id": task_id,
                    }
                    exercise["alerts"].append(alert)
                    exercise["detection_status"] = "detected"
                    self._append_exercise_timeline_locked(
                        exercise,
                        phase="detect",
                        kind="detection.matched",
                        summary=rule["name"],
                        task_id=task_id,
                        rule_id=rule["id"],
                        technique_id=rule["technique_id"],
                    )
                    self._record_locked(
                        "exercise.detected",
                        level="warning",
                        node_id=exercise["node_id"],
                        task_id=task_id,
                        correlation_id=exercise["id"],
                        data={
                            "exercise_id": exercise["id"],
                            "scenario_id": exercise["scenario_id"],
                            "rule_id": rule["id"],
                            "source_id": rule["source_id"],
                            "technique_id": rule["technique_id"],
                            "severity": rule["severity"],
                            "signal": rule["signal"],
                        },
                    )
                    self._audit_locked(
                        "exercise.detected",
                        actor="teamserver",
                        node_id=exercise["node_id"],
                        task_id=task_id,
                        correlation_id=exercise["id"],
                        task_type="RUN_PLAYBOOK",
                        outcome="detected",
                        reason=rule["id"],
                    )

            tasks = [self._tasks.get(task_id) for task_id in exercise["task_ids"]]
            if not tasks or any(
                task is None or task["status"] not in TERMINAL_TASK_STATUSES
                for task in tasks
            ):
                continue
            if exercise["_completion_recorded"]:
                continue
            exercise["_completion_recorded"] = True
            exercise["status"] = (
                "completed" if all(task["status"] == "completed" for task in tasks) else "failed"
            )
            exercise["completed_at"] = utc_now()
            self._append_exercise_timeline_locked(
                exercise,
                phase="complete",
                kind=f"exercise.{exercise['status']}",
                summary=f"Exercise {exercise['status']}",
            )
            self._record_locked(
                f"exercise.{exercise['status']}",
                level="info" if exercise["status"] == "completed" else "warning",
                node_id=exercise["node_id"],
                correlation_id=exercise["id"],
                data={
                    "exercise_id": exercise["id"],
                    "scenario_id": exercise["scenario_id"],
                    "alerts": len(exercise["alerts"]),
                },
            )
            self._audit_locked(
                f"exercise.{exercise['status']}",
                actor="teamserver",
                node_id=exercise["node_id"],
                correlation_id=exercise["id"],
                outcome=exercise["status"],
                reason="scenario_tasks_terminal",
            )

    def start_exercise(
        self,
        node_id: Any,
        scenario_id: Any,
        *,
        actor: Any = DEFAULT_OPERATOR_ACTOR,
        idempotency_key: Any = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Queue one fixed scenario against a purple-lab foreground node."""

        if type(node_id) is not str:
            raise LabError("node_id must be a string")
        if type(scenario_id) is not str or scenario_id not in SCENARIO_IDS:
            raise LabError(
                "unsupported exercise scenario",
                code="unsupported_scenario",
            )
        clean_actor = _validate_operator_actor(actor)
        clean_idempotency_key = _validate_idempotency_key(idempotency_key)
        definition = scenario_definition(scenario_id)
        instant = time.monotonic() if now is None else now

        with self._lock:
            if clean_idempotency_key is not None:
                previous = self._exercise_idempotency.get(clean_idempotency_key)
                if previous is not None:
                    previous_actor, previous_node, previous_scenario, exercise_id = previous
                    if (
                        previous_actor == clean_actor
                        and previous_node == node_id
                        and previous_scenario == scenario_id
                    ):
                        retained = self._exercises.get(exercise_id)
                        if retained is not None:
                            return self._public_exercise(retained)
                    raise LabError(
                        "Idempotency-Key was already used for a different exercise request",
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
            if node.get("tasking_paused"):
                raise LabError(
                    "node tasking is paused by containment",
                    code="node_tasking_paused",
                    status=409,
                )
            if node["profile"] != "purple_lab" or "RUN_PLAYBOOK" not in node["capabilities"]:
                raise LabError(
                    "exercise scenarios require a purple_lab node",
                    code="capability_denied",
                    status=409,
                )
            if len(self._exercises) >= MAX_EXERCISES:
                raise LabError(
                    "exercise retention limit reached; reset the lab",
                    code="exercise_limit",
                    status=429,
                )

            playbooks = definition["playbooks"]
            queued_for_node = sum(
                task["status"] == "queued" and task["node_id"] == node_id
                for task in self._tasks.values()
            )
            queued_playbooks = sum(
                task["status"] == "queued"
                and task["node_id"] == node_id
                and task["type"] == "RUN_PLAYBOOK"
                for task in self._tasks.values()
            )
            if queued_for_node + len(playbooks) > MAX_QUEUED_TASKS_PER_NODE:
                raise LabError("node queue limit reached", code="queue_limit", status=429)
            if queued_playbooks + len(playbooks) > MAX_QUEUED_PLAYBOOKS_PER_NODE:
                raise LabError(
                    "purple-lab playbook queue limit reached",
                    code="playbook_queue_limit",
                    status=429,
                )
            self._make_task_slots_locked(len(playbooks))

            exercise_id = f"exercise-{uuid.uuid4().hex[:12]}"
            timestamp = utc_now()
            exercise = {
                "id": exercise_id,
                "scenario_id": scenario_id,
                "title": definition["title"],
                "description": definition["description"],
                "scope": definition["scope"],
                "status": "running",
                "detection_status": "pending",
                "node_id": node_id,
                "created_by": clean_actor,
                "created_at": timestamp,
                "completed_at": None,
                "task_ids": [],
                "techniques": definition["techniques"],
                "alerts": [],
                "timeline": [],
                "containment": {
                    "status": "not_started",
                    "action": None,
                    "actor": None,
                    "time": None,
                },
                "_timeline_sequence": 0,
                "_observed_task_ids": [],
                "_completion_recorded": False,
            }
            self._append_exercise_timeline_locked(
                exercise,
                phase="prepare",
                kind="exercise.started",
                summary=definition["title"],
            )

            for playbook in playbooks:
                clean_type, clean_payload = _protocol_call(
                    validate_task_payload,
                    "RUN_PLAYBOOK",
                    {"playbook": playbook},
                )
                task_id = f"task-{uuid.uuid4().hex[:12]}"
                task = {
                    "id": task_id,
                    "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
                    "node_id": node_id,
                    "type": clean_type,
                    "created_by": clean_actor,
                    "payload": clean_payload,
                    "status": "queued",
                    "result": None,
                    "created_at": utc_now(),
                    "queue_ttl_seconds": DEFAULT_QUEUE_TTL_SECONDS,
                    "dispatched_at": None,
                    "completed_at": None,
                    "delivery_attempts": 0,
                    "_sequence": self._next_task_sequence_locked(),
                    "_deadline": None,
                    "_queue_deadline": instant + DEFAULT_QUEUE_TTL_SECONDS,
                    "_idempotency_key": None,
                    "_exercise_id": exercise_id,
                    "_result_accepted": False,
                }
                self._tasks[task_id] = task
                exercise["task_ids"].append(task_id)
                self._append_exercise_timeline_locked(
                    exercise,
                    phase="prepare",
                    kind="task.queued",
                    summary=f"{playbook} queued",
                    task_id=task_id,
                )
                self._record_locked(
                    "task.queued",
                    node_id=node_id,
                    task_id=task_id,
                    actor=clean_actor,
                    data={"type": clean_type, "correlation_id": task["correlation_id"]},
                )
                self._audit_locked(
                    "task.queued",
                    actor=clean_actor,
                    node_id=node_id,
                    task_id=task_id,
                    correlation_id=task["correlation_id"],
                    task_type=clean_type,
                    to_state="queued",
                    outcome="accepted",
                    reason="exercise_scenario",
                )

            self._exercises[exercise_id] = exercise
            if clean_idempotency_key is not None:
                self._exercise_idempotency[clean_idempotency_key] = (
                    clean_actor,
                    node_id,
                    scenario_id,
                    exercise_id,
                )
            self._record_locked(
                "exercise.started",
                node_id=node_id,
                correlation_id=exercise_id,
                actor=clean_actor,
                data={
                    "exercise_id": exercise_id,
                    "scenario_id": scenario_id,
                    "task_count": len(exercise["task_ids"]),
                },
            )
            self._audit_locked(
                "exercise.started",
                actor=clean_actor,
                node_id=node_id,
                correlation_id=exercise_id,
                outcome="accepted",
                reason=scenario_id,
            )
            return self._public_exercise(exercise)

    def contain_exercise(
        self,
        exercise_id: Any,
        action: Any,
        *,
        actor: Any = DEFAULT_OPERATOR_ACTOR,
    ) -> dict[str, Any]:
        """Apply one control-plane-only response to a detected exercise."""

        if type(exercise_id) is not str:
            raise LabError("exercise_id must be a string")
        if type(action) is not str or action not in CONTAINMENT_ACTIONS:
            raise LabError("unsupported containment action", code="unsupported_action")
        clean_actor = _validate_operator_actor(actor)
        with self._lock:
            exercise = self._exercises.get(exercise_id)
            if exercise is None:
                raise LabError("exercise not found", code="not_found", status=404)
            if exercise["status"] == "contained":
                if exercise["containment"]["action"] == action:
                    return self._public_exercise(exercise)
                raise LabError(
                    "exercise already has a different containment action",
                    code="containment_conflict",
                    status=409,
                )
            if exercise["detection_status"] != "detected":
                raise LabError(
                    "containment requires a detected exercise",
                    code="detection_required",
                    status=409,
                )

            for task_id in exercise["task_ids"]:
                task = self._tasks.get(task_id)
                if task is None or task["status"] != "queued":
                    continue
                task["status"] = "cancelled"
                task["result"] = {"reason": "exercise_contained"}
                task["completed_at"] = utc_now()
                task["_queue_deadline"] = None
                playbook = task["payload"]["playbook"]
                self._append_exercise_timeline_locked(
                    exercise,
                    phase="simulate",
                    kind="task.cancelled",
                    summary=f"{playbook} cancelled",
                    task_id=task_id,
                )
                exercise["_observed_task_ids"].append(task_id)
                self._record_locked(
                    "task.cancelled",
                    level="warning",
                    node_id=task["node_id"],
                    task_id=task_id,
                    correlation_id=task["correlation_id"],
                    actor=clean_actor,
                    data={
                        "type": task["type"],
                        "correlation_id": task["correlation_id"],
                        "reason": "exercise_contained",
                    },
                )
                self._audit_locked(
                    "task.cancelled",
                    actor=clean_actor,
                    node_id=task["node_id"],
                    task_id=task_id,
                    correlation_id=task["correlation_id"],
                    task_type=task["type"],
                    from_state="queued",
                    to_state="cancelled",
                    outcome="cancelled",
                    reason="exercise_contained",
                )

            node = self._nodes.get(exercise["node_id"])
            if action == "PAUSE_NODE_TASKING" and node is not None:
                node["tasking_paused"] = True
                node["tasking_paused_at"] = utc_now()
                node["tasking_paused_by"] = clean_actor
                self._record_locked(
                    "node.tasking_paused",
                    level="warning",
                    node_id=node["id"],
                    correlation_id=exercise_id,
                    actor=clean_actor,
                    data={"exercise_id": exercise_id},
                )
                self._audit_locked(
                    "node.tasking_paused",
                    actor=clean_actor,
                    node_id=node["id"],
                    correlation_id=exercise_id,
                    from_state="active",
                    to_state="paused",
                    outcome="success",
                    reason="exercise_containment",
                )

            contained_at = utc_now()
            exercise["status"] = "contained"
            if exercise["completed_at"] is None:
                exercise["completed_at"] = contained_at
            exercise["containment"] = {
                "status": "applied",
                "action": action,
                "actor": clean_actor,
                "time": contained_at,
            }
            for alert in exercise["alerts"]:
                if alert["status"] == "open":
                    alert["status"] = "contained"
                    alert["contained_at"] = contained_at
            self._append_exercise_timeline_locked(
                exercise,
                phase="contain",
                kind="containment.applied",
                summary="Control-plane containment applied",
                action=action,
            )
            self._record_locked(
                "exercise.contained",
                level="warning",
                node_id=exercise["node_id"],
                correlation_id=exercise_id,
                actor=clean_actor,
                data={
                    "exercise_id": exercise_id,
                    "scenario_id": exercise["scenario_id"],
                    "action": action,
                    "alerts": len(exercise["alerts"]),
                },
            )
            self._audit_locked(
                "exercise.contained",
                actor=clean_actor,
                node_id=exercise["node_id"],
                correlation_id=exercise_id,
                from_state="detected",
                to_state="contained",
                outcome="success",
                reason=action,
            )
            return self._public_exercise(exercise)

    def exercises(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(
                (self._public_exercise(exercise) for exercise in self._exercises.values()),
                key=lambda exercise: exercise["created_at"],
                reverse=True,
            )

    @staticmethod
    def scenarios() -> list[dict[str, Any]]:
        return scenario_catalog()

    def reset(self, *, actor: Any = DEFAULT_OPERATOR_ACTOR) -> None:
        clean_actor = _validate_operator_actor(actor)
        with self._lock:
            self._nodes.clear()
            self._tasks.clear()
            self._task_result_tombstones.clear()
            self._exercises.clear()
            self._events.clear()
            self._note_idempotency.clear()
            self._exercise_idempotency.clear()
            self._operation_idempotency.clear()
            self._record_locked(
                "lab.reset",
                actor=clean_actor,
                data={"sessions_invalidated": True},
            )
            self._audit_locked(
                "lab.reset",
                actor=clean_actor,
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
            return [
                self._public_task(task)
                for task in sorted(
                    self._tasks.values(),
                    key=lambda task: task["_sequence"],
                    reverse=True,
                )
            ]

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(copy.deepcopy(list(self._events))))

    def audit(self) -> list[dict[str, Any]]:
        """Return newest-first, fixed-schema control-plane audit metadata."""

        with self._lock:
            return list(reversed(copy.deepcopy(list(self._audit_entries))))

    def post_operator_note(
        self,
        message: Any,
        *,
        actor: Any = DEFAULT_OPERATOR_ACTOR,
        idempotency_key: Any = None,
    ) -> dict[str, Any]:
        """Append a bounded plain-text collaboration note to the shared event feed."""

        clean_actor = _validate_operator_actor(actor)
        clean_message = _protocol_call(
            clean_text,
            message,
            "message",
            maximum=MAX_OPERATOR_NOTE_LENGTH,
        )
        clean_idempotency_key = _validate_idempotency_key(idempotency_key)
        with self._lock:
            if clean_idempotency_key is not None:
                existing = self._note_idempotency.get(clean_idempotency_key)
                if existing is not None:
                    existing_actor, existing_message, event_id = existing
                    if existing_actor != clean_actor or existing_message != clean_message:
                        raise LabError(
                            "Idempotency-Key was already used for a different note request",
                            code="idempotency_conflict",
                            status=409,
                        )
                    retained = next(
                        (event for event in self._events if event["id"] == event_id),
                        None,
                    )
                    if retained is not None:
                        return copy.deepcopy(retained)
                    del self._note_idempotency[clean_idempotency_key]

            retained_notes = sum(
                event["kind"] == "operator.note" for event in self._events
            )
            if retained_notes >= MAX_OPERATOR_NOTES_RETAINED:
                raise LabError(
                    "operator note retention limit reached",
                    code="note_limit",
                    status=429,
                )
            event = self._record_locked(
                "operator.note",
                actor=clean_actor,
                data={"message": clean_message},
            )
            self._audit_locked(
                "operator.note",
                actor=clean_actor,
                outcome="accepted",
                reason="shared_event_feed",
            )
            if clean_idempotency_key is not None:
                self._note_idempotency[clean_idempotency_key] = (
                    clean_actor,
                    clean_message,
                    event["id"],
                )
                while len(self._note_idempotency) > MAX_OPERATOR_NOTES_RETAINED:
                    self._note_idempotency.pop(next(iter(self._note_idempotency)))
            return copy.deepcopy(event)

    def sync(
        self,
        *,
        events_after: Any = 0,
        audit_after: Any = 0,
        limit: Any = MAX_SYNC_PAGE_SIZE,
    ) -> dict[str, Any]:
        """Return current state plus cursor-based, retention-aware history deltas."""

        clean_events_after = _validate_sync_cursor(events_after, "events_after")
        clean_audit_after = _validate_sync_cursor(audit_after, "audit_after")
        clean_limit = _validate_sync_limit(limit)
        with self._lock:
            nodes = self.nodes()
            tasks = self.tasks()
            exercises = self.exercises()
            retained_events = list(self._events)
            retained_audit = list(self._audit_entries)

            def history_delta(
                retained: list[dict[str, Any]],
                after: int,
                latest: int,
                *,
                reset_floor: int | None = None,
            ) -> tuple[list[dict[str, Any]], int, int, bool, bool]:
                earliest = retained[0]["sequence"] if retained else latest + 1
                reset_required = (
                    after > latest
                    or after < earliest - 1
                    or (reset_floor is not None and after < reset_floor)
                )
                candidates = (
                    retained
                    if reset_required
                    else [entry for entry in retained if entry["sequence"] > after]
                )
                selected = candidates[:clean_limit]
                next_cursor = selected[-1]["sequence"] if selected else latest
                return (
                    copy.deepcopy(selected),
                    next_cursor,
                    earliest,
                    reset_required,
                    len(candidates) > len(selected),
                )

            reset_floor = next(
                (
                    event["sequence"]
                    for event in retained_events
                    if event["kind"] == "lab.reset"
                ),
                None,
            )
            events, next_events, oldest_event, reset_events, more_events = history_delta(
                retained_events,
                clean_events_after,
                self._event_sequence,
                reset_floor=reset_floor,
            )
            audit_entries, next_audit, oldest_audit, reset_audit, more_audit = history_delta(
                retained_audit,
                clean_audit_after,
                self._audit_sequence,
            )
            return {
                "generated_at": utc_now(),
                "stream_id": self._stream_id,
                "lab_mode": True,
                "protocol": "loopback-http-poll/v1",
                "counts": self._counts(nodes, tasks, exercises),
                "nodes": nodes,
                "tasks": tasks,
                "scenario_catalog": self.scenarios(),
                "exercises": exercises,
                "events": events,
                "audit": audit_entries,
                "cursors": {
                    "events": next_events,
                    "audit": next_audit,
                },
                "high_watermarks": {
                    "events": self._event_sequence,
                    "audit": self._audit_sequence,
                },
                "oldest_available": {
                    "events": oldest_event,
                    "audit": oldest_audit,
                },
                "cursor_reset": {
                    "events": reset_events,
                    "audit": reset_audit,
                },
                "has_more": {
                    "events": more_events,
                    "audit": more_audit,
                },
            }

    @staticmethod
    def _counts(
        nodes: list[dict[str, Any]],
        tasks: list[dict[str, Any]],
        exercises: list[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        exercise_records = exercises or []
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
            "exercises_total": len(exercise_records),
            "exercises_running": sum(
                exercise["status"] == "running" for exercise in exercise_records
            ),
            "exercises_detected": sum(
                exercise["detection_status"] == "detected"
                for exercise in exercise_records
            ),
            "exercises_contained": sum(
                exercise["status"] == "contained" for exercise in exercise_records
            ),
            "alerts_open": sum(
                alert["status"] == "open"
                for exercise in exercise_records
                for alert in exercise["alerts"]
            ),
        }

    def overview(self) -> dict[str, Any]:
        with self._lock:
            nodes = self.nodes()
            tasks = self.tasks()
            exercises = self.exercises()
            return {
                "lab_mode": True,
                "protocol": "loopback-http-poll/v1",
                "counts": self._counts(nodes, tasks, exercises),
                "nodes": nodes,
                "tasks": tasks,
                "scenario_catalog": self.scenarios(),
                "exercises": exercises,
                "events": self.events(),
            }

    def report(self) -> dict[str, Any]:
        """Return a bounded snapshot without task payloads, results, or credentials."""

        with self._lock:
            nodes = self.nodes()
            tasks = self.tasks()
            exercises = self.exercises()
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
                        "tasking_paused",
                        "tasking_paused_at",
                        "tasking_paused_by",
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
                        "created_by",
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
            counts = self._counts(nodes, tasks, exercises)
            counts.update(
                {
                    "events_retained": len(self._events),
                    "audit_retained": len(audit_entries),
                    "operator_notes_retained": sum(
                        event["kind"] == "operator.note" for event in self._events
                    ),
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
                    "operator_notes": MAX_OPERATOR_NOTES_RETAINED,
                    "operator_note_length": MAX_OPERATOR_NOTE_LENGTH,
                    "exercises": MAX_EXERCISES,
                    "exercise_timeline": MAX_EXERCISE_TIMELINE,
                    "sync_page_size": MAX_SYNC_PAGE_SIZE,
                    "default_queue_ttl_seconds": DEFAULT_QUEUE_TTL_SECONDS,
                    "stale_session_ttl_seconds": STALE_SESSION_TTL_SECONDS,
                },
                "sequences": {
                    "events": self._event_sequence,
                    "audit": self._audit_sequence,
                },
                "nodes": safe_nodes,
                "tasks": safe_tasks,
                "scenario_catalog": self.scenarios(),
                "exercises": exercises,
                "audit": audit_entries,
            }

    @staticmethod
    def _public_node(node: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy({key: value for key, value in node.items() if not key.startswith("_")})

    @staticmethod
    def _public_task(task: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy({key: value for key, value in task.items() if not key.startswith("_")})

    @staticmethod
    def _node_task(task: dict[str, Any]) -> dict[str, Any]:
        """Project a task to the Node protocol without Operator-only attribution."""

        return copy.deepcopy(
            {
                key: value
                for key, value in task.items()
                if not key.startswith("_")
                and key not in {"created_by", "operation_id", "operation_step"}
            }
        )


class LabRuntime:
    """Background expiry monitor; task execution occurs in separate node processes."""

    def __init__(
        self,
        state: LabState,
        *,
        tick_seconds: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.state = state
        self.tick_seconds = tick_seconds
        self._clock = clock
        self._stop = threading.Event()
        self._runtime_lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._status = "stopped"
        self._ready = False
        self._last_tick_at: float | None = None
        self._last_error: str | None = None

    @staticmethod
    def _safe_error_type(error: BaseException) -> str:
        name = type(error).__name__
        if (
            1 <= len(name) <= 64
            and name.isascii()
            and all(character.isalnum() or character == "_" for character in name)
        ):
            return name
        return "Exception"

    def start(self) -> None:
        with self._runtime_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._status = "running"
            self._ready = False
            self._last_tick_at = None
            self._last_error = None
            thread = threading.Thread(
                target=self._run,
                name="c2lab-teamserver-monitor",
                daemon=True,
            )
            self._thread = thread
            try:
                thread.start()
            except Exception as error:
                self._thread = None
                self._status = "stopped"
                self._last_error = self._safe_error_type(error)
                self._stop.set()
                raise

    def stop(self) -> None:
        with self._runtime_lock:
            self._stop.set()
            self._status = "stopped"
            self._ready = False
            thread = self._thread
        if (
            thread
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(timeout=2)

    def health(self) -> dict[str, Any]:
        """Return a thread-safe, secret-free runtime readiness snapshot."""

        with self._runtime_lock:
            last_tick_age = (
                None
                if self._last_tick_at is None
                else round(max(0.0, self._clock() - self._last_tick_at), 3)
            )
            tick_is_fresh = (
                last_tick_age is not None
                and last_tick_age <= max(1.0, self.tick_seconds * 4)
            )
            return {
                "status": self._status,
                "ready": self._status == "running" and self._ready and tick_is_fresh,
                "last_tick_age_seconds": last_tick_age,
                "last_error": self._last_error,
            }

    def _run(self) -> None:
        try:
            while not self._stop.wait(self.tick_seconds):
                self.state.expire()
                tick_at = self._clock()
                with self._runtime_lock:
                    self._last_tick_at = tick_at
                    if not self._stop.is_set() and self._status == "running":
                        self._ready = True
        except Exception as error:
            with self._runtime_lock:
                self._last_error = self._safe_error_type(error)
                self._status = "stopped"
                self._ready = False
            self._stop.set()
        finally:
            with self._runtime_lock:
                self._status = "stopped"
                self._ready = False
