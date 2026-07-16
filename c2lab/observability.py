"""Bounded, secret-free observability helpers for the localhost server."""

from __future__ import annotations

import copy
import math
import threading
import time
from collections import Counter
from typing import Any, Callable


_EXACT_ROUTES = frozenset(
    {
        "/",
        "/index.html",
        "/static/app.js",
        "/static/styles.css",
        "/healthz",
        "/readyz",
        "/lab/session",
        "/lab/sync",
        "/lab/overview",
        "/lab/nodes",
        "/lab/tasks",
        "/lab/scenarios",
        "/lab/exercises",
        "/lab/notes",
        "/lab/events",
        "/lab/audit",
        "/lab/report",
        "/lab/metrics",
        "/lab/operators",
        "/lab/reset",
        "/node/v1/enroll",
        "/node/v1/poll",
        "/node/v1/disconnect",
    }
)


def normalized_route(path: Any) -> str:
    """Return a fixed-cardinality route label without retaining caller input."""

    if not isinstance(path, str):
        return "unmatched"
    if path in _EXACT_ROUTES:
        return path
    parts = path.strip("/").split("/")
    if len(parts) == 5 and parts[:3] == ["node", "v1", "tasks"] and parts[4] == "result":
        return "/node/v1/tasks/:task_id/result"
    if len(parts) == 4 and parts[:2] == ["lab", "tasks"] and parts[3] == "cancel":
        return "/lab/tasks/:task_id/cancel"
    if len(parts) == 4 and parts[:2] == ["lab", "operators"] and parts[3] == "revoke":
        return "/lab/operators/:session_id/revoke"
    if len(parts) == 4 and parts[:2] == ["lab", "exercises"] and parts[3] == "contain":
        return "/lab/exercises/:exercise_id/contain"
    return "unmatched"


class RequestMetrics:
    """Thread-safe aggregate counters with bounded labels and no request content."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started = clock()
        self._lock = threading.Lock()
        self._requests_total = 0
        self._worker_rejections = 0
        self._access_log_drops = 0
        self._methods: Counter[str] = Counter()
        self._routes: Counter[str] = Counter()
        self._status_classes: Counter[str] = Counter()
        self._duration_total_ms = 0.0
        self._duration_max_ms = 0.0

    def record(self, *, method: str, route: str, status: int, duration_ms: float) -> None:
        method_label = method if method in {"GET", "POST"} else "OTHER"
        route_label = normalized_route(route)
        status_label = f"{status // 100}xx" if 100 <= status <= 599 else "other"
        try:
            candidate_duration = float(duration_ms)
        except (TypeError, ValueError):
            candidate_duration = 0.0
        safe_duration = (
            candidate_duration
            if math.isfinite(candidate_duration) and candidate_duration >= 0
            else 0.0
        )
        with self._lock:
            self._requests_total += 1
            self._methods[method_label] += 1
            self._routes[route_label] += 1
            self._status_classes[status_label] += 1
            self._duration_total_ms += safe_duration
            self._duration_max_ms = max(self._duration_max_ms, safe_duration)

    def record_worker_rejection(self) -> None:
        with self._lock:
            self._worker_rejections += 1

    def record_access_log_drop(self) -> None:
        with self._lock:
            self._access_log_drops += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            request_count = self._requests_total
            average = self._duration_total_ms / request_count if request_count else 0.0
            snapshot = {
                "uptime_seconds": max(0.0, self._clock() - self._started),
                "requests_total": request_count,
                "worker_rejections": self._worker_rejections,
                "access_log_drops": self._access_log_drops,
                "methods": dict(sorted(self._methods.items())),
                "routes": dict(sorted(self._routes.items())),
                "status_classes": dict(sorted(self._status_classes.items())),
                "duration_ms": {
                    "average": round(average, 3),
                    "maximum": round(self._duration_max_ms, 3),
                },
            }
        return copy.deepcopy(snapshot)
