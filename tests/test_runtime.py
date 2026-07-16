from __future__ import annotations

import threading
import time
import unittest
from typing import Any

from c2lab.core import LabRuntime


class _RecordingState:
    def __init__(self) -> None:
        self.ticked = threading.Event()

    def expire(self) -> None:
        self.ticked.set()


class _FailingState:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.failed = threading.Event()
        self.should_fail = True

    def expire(self) -> None:
        if self.should_fail:
            self.failed.set()
            raise RuntimeError(self.secret)


class LabRuntimeHealthTests(unittest.TestCase):
    def assertEventually(self, predicate: Any, *, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.001)
        self.fail("condition did not become true before timeout")

    def test_health_tracks_start_tick_and_stop_lifecycle(self) -> None:
        now = [10.0]
        state = _RecordingState()
        runtime = LabRuntime(
            state,  # type: ignore[arg-type]
            tick_seconds=0.001,
            clock=lambda: now[0],
        )
        self.addCleanup(runtime.stop)

        self.assertEqual(
            runtime.health(),
            {
                "status": "stopped",
                "ready": False,
                "last_tick_age_seconds": None,
                "last_error": None,
            },
        )

        runtime.start()
        self.assertTrue(state.ticked.wait(timeout=1.0))
        self.assertEventually(lambda: runtime.health()["ready"])
        self.assertEqual(
            runtime.health(),
            {
                "status": "running",
                "ready": True,
                "last_tick_age_seconds": 0.0,
                "last_error": None,
            },
        )

        runtime.stop()
        now[0] = 12.5
        self.assertEqual(
            runtime.health(),
            {
                "status": "stopped",
                "ready": False,
                "last_tick_age_seconds": 2.5,
                "last_error": None,
            },
        )

    def test_start_is_not_ready_before_the_first_tick(self) -> None:
        runtime = LabRuntime(
            _RecordingState(),  # type: ignore[arg-type]
            tick_seconds=60.0,
            clock=lambda: 20.0,
        )
        self.addCleanup(runtime.stop)

        runtime.start()

        self.assertEqual(
            runtime.health(),
            {
                "status": "running",
                "ready": False,
                "last_tick_age_seconds": None,
                "last_error": None,
            },
        )

    def test_stale_monitor_tick_is_not_ready(self) -> None:
        now = [10.0]
        runtime = LabRuntime(
            _RecordingState(),  # type: ignore[arg-type]
            tick_seconds=0.25,
            clock=lambda: now[0],
        )
        with runtime._runtime_lock:
            runtime._status = "running"
            runtime._ready = True
            runtime._last_tick_at = 10.0

        now[0] = 11.001
        health = runtime.health()

        self.assertFalse(health["ready"])
        self.assertEqual(health["status"], "running")
        self.assertEqual(health["last_tick_age_seconds"], 1.001)

    def test_unexpected_error_is_redacted_and_restart_clears_it(self) -> None:
        secret = "do-not-leak-runtime-secret"
        state = _FailingState(secret)
        runtime = LabRuntime(
            state,  # type: ignore[arg-type]
            tick_seconds=0.001,
            clock=lambda: 30.0,
        )
        self.addCleanup(runtime.stop)

        runtime.start()
        self.assertTrue(state.failed.wait(timeout=1.0))
        self.assertEventually(lambda: runtime.health()["last_error"] is not None)
        failed_health = runtime.health()
        self.assertEqual(
            failed_health,
            {
                "status": "stopped",
                "ready": False,
                "last_tick_age_seconds": None,
                "last_error": "RuntimeError",
            },
        )
        self.assertNotIn(secret, repr(failed_health))

        runtime.stop()
        state.should_fail = False
        runtime.tick_seconds = 60.0
        runtime.start()
        self.assertEqual(
            runtime.health(),
            {
                "status": "running",
                "ready": False,
                "last_tick_age_seconds": None,
                "last_error": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
