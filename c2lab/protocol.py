"""Typed, allow-listed protocol shared by the teamserver and local node."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .lab_runtime import PLAYBOOK_NAMES, validate_playbook_result


MAX_TEXT_LENGTH = 240
MAX_WAIT_MS = 2_000
MIN_POLL_INTERVAL_MS = 250
MAX_POLL_INTERVAL_MS = 3_000
MAX_JITTER_PERCENT = 50
MAX_RESULT_BYTES = 4_096
NODE_FAILURE_CODES = ("INVALID_TASK", "HANDLER_FAILED")

TRAINING_TASK_TYPES = (
    "PING",
    "RUNTIME_STATUS",
    "ECHO_TEXT",
    "HASH_TEXT",
    "WAIT",
    "GENERATE_EVENT",
    "SLEEP",
    "EXIT",
)
TASK_TYPES = TRAINING_TASK_TYPES + ("RUN_PLAYBOOK",)

NODE_PROFILES: dict[str, tuple[str, ...]] = {
    "basic": ("PING", "RUNTIME_STATUS", "ECHO_TEXT", "HASH_TEXT"),
    "training": TRAINING_TASK_TYPES,
    "purple_lab": TASK_TYPES,
}


class ProtocolError(ValueError):
    """A schema error that is safe to expose to an operator or node."""


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _same_json_value(actual: Any, expected: Any) -> bool:
    """Compare JSON-shaped values without Python's bool/number coercion."""

    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return set(actual) == set(expected) and all(
            _same_json_value(actual[key], expected[key]) for key in expected
        )
    if type(expected) is list:
        return len(actual) == len(expected) and all(
            _same_json_value(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected, strict=True)
        )
    return actual == expected


def clean_text(value: Any, field: str, *, minimum: int = 1, maximum: int = 120) -> str:
    if not isinstance(value, str):
        raise ProtocolError(f"{field} must be a string")
    cleaned = value.strip()
    if not minimum <= len(cleaned) <= maximum:
        raise ProtocolError(f"{field} must be {minimum} to {maximum} characters")
    if not all(character.isprintable() or character in "\n\t" for character in cleaned):
        raise ProtocolError(f"{field} contains unsupported control characters")
    return cleaned


def exact_keys(payload: dict[str, Any], expected: set[str]) -> None:
    actual = set(payload)
    if actual == expected:
        return
    details = []
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        details.append(f"missing: {', '.join(missing)}")
    if extra:
        details.append(f"unexpected: {', '.join(extra)}")
    raise ProtocolError("invalid payload fields (" + "; ".join(details) + ")")


def validate_profile(profile: Any) -> str:
    if not isinstance(profile, str) or profile not in NODE_PROFILES:
        raise ProtocolError(f"profile must be one of: {', '.join(NODE_PROFILES)}")
    return profile


def capabilities_for_profile(profile: Any) -> list[str]:
    normalized = validate_profile(profile)
    return list(NODE_PROFILES[normalized])


def validate_capabilities(profile: Any, capabilities: Any) -> list[str]:
    normalized_profile = validate_profile(profile)
    expected = list(NODE_PROFILES[normalized_profile])
    if not isinstance(capabilities, list) or capabilities != expected:
        raise ProtocolError(f"capabilities must exactly match the {normalized_profile} profile")
    return expected


def validate_poll_interval(value: Any) -> int:
    if not is_int(value) or not MIN_POLL_INTERVAL_MS <= value <= MAX_POLL_INTERVAL_MS:
        raise ProtocolError(
            f"poll_interval_ms must be an integer from {MIN_POLL_INTERVAL_MS} to {MAX_POLL_INTERVAL_MS}"
        )
    return value


def validate_jitter_percent(value: Any) -> int:
    if not is_int(value) or not 0 <= value <= MAX_JITTER_PERCENT:
        raise ProtocolError(
            f"jitter_percent must be an integer from 0 to {MAX_JITTER_PERCENT}"
        )
    return value


def validate_task_payload(task_type: Any, payload: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(task_type, str) or task_type not in TASK_TYPES:
        raise ProtocolError(f"task type must be one of: {', '.join(TASK_TYPES)}")
    if not isinstance(payload, dict):
        raise ProtocolError("payload must be a JSON object")

    if task_type in {"PING", "RUNTIME_STATUS"}:
        exact_keys(payload, set())
        return task_type, {}

    if task_type in {"ECHO_TEXT", "HASH_TEXT"}:
        exact_keys(payload, {"text"})
        return task_type, {"text": clean_text(payload["text"], "text", maximum=MAX_TEXT_LENGTH)}

    if task_type == "WAIT":
        exact_keys(payload, {"milliseconds"})
        milliseconds = payload["milliseconds"]
        if not is_int(milliseconds) or not 0 <= milliseconds <= MAX_WAIT_MS:
            raise ProtocolError(f"milliseconds must be an integer from 0 to {MAX_WAIT_MS}")
        return task_type, {"milliseconds": milliseconds}

    if task_type == "SLEEP":
        exact_keys(payload, {"interval_ms", "jitter_percent"})
        interval = payload["interval_ms"]
        if not is_int(interval) or not MIN_POLL_INTERVAL_MS <= interval <= MAX_POLL_INTERVAL_MS:
            raise ProtocolError(
                f"interval_ms must be an integer from {MIN_POLL_INTERVAL_MS} to {MAX_POLL_INTERVAL_MS}"
            )
        jitter = payload["jitter_percent"]
        validate_jitter_percent(jitter)
        return task_type, {"interval_ms": interval, "jitter_percent": jitter}

    if task_type == "EXIT":
        exact_keys(payload, set())
        return task_type, {}

    if task_type == "RUN_PLAYBOOK":
        exact_keys(payload, {"playbook"})
        playbook = payload["playbook"]
        if not isinstance(playbook, str) or playbook not in PLAYBOOK_NAMES:
            raise ProtocolError(f"playbook must be one of: {', '.join(PLAYBOOK_NAMES)}")
        return task_type, {"playbook": playbook}

    exact_keys(payload, {"category", "severity", "message"})
    category = payload["category"]
    severity = payload["severity"]
    if not isinstance(category, str) or category not in {"training", "telemetry", "policy"}:
        raise ProtocolError("category must be training, telemetry, or policy")
    if not isinstance(severity, str) or severity not in {"info", "warning"}:
        raise ProtocolError("severity must be info or warning")
    return task_type, {
        "category": category,
        "severity": severity,
        "message": clean_text(payload["message"], "message", maximum=MAX_TEXT_LENGTH),
    }


def validate_result(status: Any, result: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(status, str) or status not in {"completed", "failed"}:
        raise ProtocolError("result status must be completed or failed")
    if not isinstance(result, dict):
        raise ProtocolError("result must be a JSON object")
    try:
        encoded = json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ProtocolError("result must contain JSON-compatible values") from error
    if len(encoded) > MAX_RESULT_BYTES:
        raise ProtocolError(f"result must be at most {MAX_RESULT_BYTES} bytes")
    return status, result


def validate_task_result(
    task_type: str,
    task_payload: dict[str, Any],
    status: Any,
    result: Any,
    *,
    expected_runtime: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Validate a node result against the dispatched task, not just a size limit."""

    clean_status, clean_result = validate_result(status, result)
    if clean_status == "failed":
        exact_keys(clean_result, {"error_code"})
        if clean_result["error_code"] not in NODE_FAILURE_CODES:
            raise ProtocolError(f"error_code must be one of: {', '.join(NODE_FAILURE_CODES)}")
        return clean_status, {"error_code": clean_result["error_code"]}

    if task_type == "PING":
        expected = {"reply": "PONG"}
        if not _same_json_value(clean_result, expected):
            raise ProtocolError("PING result must be the fixed PONG response")
        return clean_status, expected

    if task_type == "RUNTIME_STATUS":
        exact_keys(
            clean_result,
            {"version", "profile", "uptime_ms", "tasks_completed", "poll_interval_ms", "jitter_percent"},
        )
        version = clean_text(clean_result["version"], "version", maximum=32)
        profile = validate_profile(clean_result["profile"])
        uptime_ms = clean_result["uptime_ms"]
        tasks_completed = clean_result["tasks_completed"]
        poll_interval_ms = validate_poll_interval(clean_result["poll_interval_ms"])
        jitter_percent = validate_jitter_percent(clean_result["jitter_percent"])
        if not is_int(uptime_ms) or not 0 <= uptime_ms <= 2**53 - 1:
            raise ProtocolError("uptime_ms must be a non-negative safe integer")
        if not is_int(tasks_completed) or not 0 <= tasks_completed <= 1_000_000:
            raise ProtocolError("tasks_completed must be an integer from 0 to 1000000")
        if expected_runtime is not None and (
            not _same_json_value(version, expected_runtime.get("version"))
            or not _same_json_value(profile, expected_runtime.get("profile"))
            or not _same_json_value(
                poll_interval_ms,
                expected_runtime.get("poll_interval_ms"),
            )
            or not _same_json_value(
                jitter_percent,
                expected_runtime.get("jitter_percent", 0),
            )
        ):
            raise ProtocolError("runtime identity fields do not match the enrolled node")
        return clean_status, {
            "version": version,
            "profile": profile,
            "uptime_ms": uptime_ms,
            "tasks_completed": tasks_completed,
            "poll_interval_ms": poll_interval_ms,
            "jitter_percent": jitter_percent,
        }

    if task_type == "ECHO_TEXT":
        expected = {"echo": task_payload["text"]}
        if not _same_json_value(clean_result, expected):
            raise ProtocolError("ECHO_TEXT result must match the queued text")
        return clean_status, expected

    if task_type == "HASH_TEXT":
        expected = {
            "algorithm": "sha256",
            "digest": hashlib.sha256(task_payload["text"].encode("utf-8")).hexdigest(),
        }
        if not _same_json_value(clean_result, expected):
            raise ProtocolError("HASH_TEXT result must match the queued text digest")
        return clean_status, expected

    if task_type == "WAIT":
        expected = {"waited_ms": task_payload["milliseconds"]}
        if not _same_json_value(clean_result, expected):
            raise ProtocolError("WAIT result must match the bounded wait request")
        return clean_status, expected

    if task_type == "SLEEP":
        exact_keys(
            clean_result,
            {"previous_interval_ms", "new_interval_ms", "jitter_percent"},
        )
        prev = clean_result["previous_interval_ms"]
        new = clean_result["new_interval_ms"]
        jitter = clean_result["jitter_percent"]
        if not is_int(prev) or not MIN_POLL_INTERVAL_MS <= prev <= MAX_POLL_INTERVAL_MS:
            raise ProtocolError("previous_interval_ms is outside the supported range")
        if expected_runtime is not None and not _same_json_value(
            prev,
            expected_runtime.get("poll_interval_ms"),
        ):
            raise ProtocolError("previous_interval_ms does not match the enrolled node")
        if not is_int(new) or not MIN_POLL_INTERVAL_MS <= new <= MAX_POLL_INTERVAL_MS:
            raise ProtocolError("new_interval_ms is outside the supported range")
        if not _same_json_value(new, task_payload["interval_ms"]):
            raise ProtocolError("new_interval_ms must match the requested interval")
        if not is_int(jitter) or not 0 <= jitter <= MAX_JITTER_PERCENT:
            raise ProtocolError("jitter_percent is outside the supported range")
        if not _same_json_value(jitter, task_payload["jitter_percent"]):
            raise ProtocolError("jitter_percent must match the requested jitter")
        return clean_status, {
            "previous_interval_ms": prev,
            "new_interval_ms": new,
            "jitter_percent": jitter,
        }

    if task_type == "EXIT":
        expected = {"acknowledged": True}
        if not _same_json_value(clean_result, expected):
            raise ProtocolError("EXIT result must be the fixed acknowledgement")
        return clean_status, expected

    if task_type == "GENERATE_EVENT":
        expected = {
            "recorded": True,
            "category": task_payload["category"],
            "severity": task_payload["severity"],
            "message": task_payload["message"],
        }
        if not _same_json_value(clean_result, expected):
            raise ProtocolError("GENERATE_EVENT result must match the queued event")
        return clean_status, expected

    if task_type == "RUN_PLAYBOOK":
        try:
            return clean_status, validate_playbook_result(task_payload["playbook"], clean_result)
        except ValueError as error:
            raise ProtocolError(str(error)) from error

    raise ProtocolError("result task type is unsupported")
