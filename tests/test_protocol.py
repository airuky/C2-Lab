from __future__ import annotations

import hashlib
import unittest

from c2lab.protocol import ProtocolError, validate_task_result


class TaskResultContractTests(unittest.TestCase):
    def test_fixed_task_results_match_the_dispatched_payload(self) -> None:
        scenarios = (
            ("PING", {}, {"reply": "PONG"}, {"reply": "PONG", "extra": True}),
            ("ECHO_TEXT", {"text": "expected"}, {"echo": "expected"}, {"echo": "different"}),
            (
                "HASH_TEXT",
                {"text": "expected"},
                {
                    "algorithm": "sha256",
                    "digest": hashlib.sha256(b"expected").hexdigest(),
                },
                {"algorithm": "sha256", "digest": "0" * 64},
            ),
            ("WAIT", {"milliseconds": 25}, {"waited_ms": 25}, {"waited_ms": 24}),
            (
                "GENERATE_EVENT",
                {"category": "training", "severity": "info", "message": "event"},
                {
                    "recorded": True,
                    "category": "training",
                    "severity": "info",
                    "message": "event",
                },
                {
                    "recorded": True,
                    "category": "training",
                    "severity": "warning",
                    "message": "event",
                },
            ),
        )
        for task_type, payload, valid, invalid in scenarios:
            with self.subTest(task_type=task_type):
                self.assertEqual(
                    validate_task_result(task_type, payload, "completed", valid),
                    ("completed", valid),
                )
                with self.assertRaises(ProtocolError):
                    validate_task_result(task_type, payload, "completed", invalid)

    def test_runtime_status_identity_must_match_enrollment(self) -> None:
        expected_runtime = {
            "version": "0.3.0",
            "profile": "purple_lab",
            "poll_interval_ms": 500,
            "jitter_percent": 0,
        }
        result = {
            **expected_runtime,
            "uptime_ms": 100,
            "tasks_completed": 2,
        }
        self.assertEqual(
            validate_task_result(
                "RUNTIME_STATUS",
                {},
                "completed",
                result,
                expected_runtime=expected_runtime,
            ),
            ("completed", result),
        )
        with self.assertRaises(ProtocolError):
            validate_task_result(
                "RUNTIME_STATUS",
                {},
                "completed",
                {**result, "version": "forged"},
                expected_runtime=expected_runtime,
            )

    def test_sleep_result_must_match_requested_interval_and_jitter(self) -> None:
        payload = {"interval_ms": 2000, "jitter_percent": 20}
        valid = {"previous_interval_ms": 1000, "new_interval_ms": 2000, "jitter_percent": 20}
        self.assertEqual(
            validate_task_result("SLEEP", payload, "completed", valid),
            ("completed", valid),
        )
        with self.assertRaises(ProtocolError):
            validate_task_result(
                "SLEEP", payload, "completed",
                {"previous_interval_ms": 1000, "new_interval_ms": 999, "jitter_percent": 20},
            )
        with self.assertRaises(ProtocolError):
            validate_task_result(
                "SLEEP", payload, "completed",
                {"previous_interval_ms": 1000, "new_interval_ms": 2000, "jitter_percent": 10},
            )

    def test_exit_result_must_be_acknowledged(self) -> None:
        self.assertEqual(
            validate_task_result("EXIT", {}, "completed", {"acknowledged": True}),
            ("completed", {"acknowledged": True}),
        )
        with self.assertRaises(ProtocolError):
            validate_task_result("EXIT", {}, "completed", {"acknowledged": False})
        with self.assertRaises(ProtocolError):
            validate_task_result("EXIT", {}, "completed", {})

    def test_failed_results_use_only_fixed_error_codes(self) -> None:
        for accepted in ("INVALID_TASK", "HANDLER_FAILED"):
            self.assertEqual(
                validate_task_result("PING", {}, "failed", {"error_code": accepted}),
                ("failed", {"error_code": accepted}),
            )
        for rejected in (
            {"error": "arbitrary text"},
            {"error_code": "CUSTOM"},
            {"error_code": "HANDLER_FAILED", "details": "/Users/example"},
        ):
            with self.subTest(result=rejected), self.assertRaises(ProtocolError):
                validate_task_result("PING", {}, "failed", rejected)


if __name__ == "__main__":
    unittest.main()
