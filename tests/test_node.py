from __future__ import annotations

import contextlib
import hashlib
import http.client
import io
import os
import threading
import unittest
from unittest import mock

from c2lab.node import NodeClient, NodeClientError, NodeExecutor, run_node, validate_controller_url


class ControllerURLTests(unittest.TestCase):
    def test_only_canonical_loopback_http_urls_are_accepted(self) -> None:
        self.assertEqual(validate_controller_url("http://127.0.0.1:8765"), "http://127.0.0.1:8765")
        self.assertEqual(validate_controller_url("http://localhost"), "http://127.0.0.1:80")

    def test_external_or_ambiguous_controller_urls_are_rejected(self) -> None:
        invalid_urls = (
            "https://127.0.0.1:8765",
            "http://192.0.2.10:8765",
            "http://example.invalid:8765",
            "http://[::1]:8765",
            "http://user:pass@localhost:8765",
            "http://localhost:8765/path",
            "http://localhost:8765/?query=yes",
            "http://localhost:8765/#fragment",
        )
        for value in invalid_urls:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_controller_url(value)


class NodeExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = NodeExecutor(version="0.2.0", profile="training", poll_interval_ms=1_000)

    def test_fixed_benign_action_registry(self) -> None:
        scenarios = (
            ({"type": "PING", "payload": {}}, {"reply": "PONG"}),
            ({"type": "ECHO_TEXT", "payload": {"text": "hello"}}, {"echo": "hello"}),
            (
                {"type": "HASH_TEXT", "payload": {"text": "hello"}},
                {"algorithm": "sha256", "digest": hashlib.sha256(b"hello").hexdigest()},
            ),
            ({"type": "WAIT", "payload": {"milliseconds": 0}}, {"waited_ms": 0}),
            (
                {
                    "type": "GENERATE_EVENT",
                    "payload": {"category": "training", "severity": "info", "message": "lab event"},
                },
                {"recorded": True, "category": "training", "severity": "info", "message": "lab event"},
            ),
        )
        for task, expected_subset in scenarios:
            with self.subTest(task_type=task["type"]):
                status, result = self.executor.execute(task)
                self.assertEqual(status, "completed")
                for key, value in expected_subset.items():
                    self.assertEqual(result[key], value)

    def test_runtime_status_contains_only_process_level_lab_fields(self) -> None:
        status, result = self.executor.execute({"type": "RUNTIME_STATUS", "payload": {}})
        self.assertEqual(status, "completed")
        self.assertEqual(
            set(result),
            {"version", "profile", "uptime_ms", "tasks_completed", "poll_interval_ms", "jitter_percent"},
        )
        for forbidden in ("hostname", "username", "cwd", "pid", "environment", "interfaces"):
            self.assertNotIn(forbidden, result)

    def test_command_shaped_or_malformed_tasks_fail_closed(self) -> None:
        invalid = (
            {"type": "RUN_COMMAND", "payload": {"command": "whoami"}},
            {"type": "PING", "payload": {"command": "whoami"}},
            {"type": "WAIT", "payload": {"milliseconds": 2_001}},
            "not-an-envelope",
        )
        for task in invalid:
            with self.subTest(task=task):
                status, result = self.executor.execute(task)
                self.assertEqual(status, "failed")
                self.assertEqual(result, {"error_code": "INVALID_TASK"})

    def test_sleep_updates_executor_poll_state(self) -> None:
        executor = NodeExecutor(version="0.2.0", profile="training", poll_interval_ms=1_000)
        self.assertEqual(executor.poll_interval_ms, 1_000)
        self.assertEqual(executor.jitter_percent, 0)
        status, result = executor.execute(
            {"type": "SLEEP", "payload": {"interval_ms": 2000, "jitter_percent": 30}}
        )
        self.assertEqual(status, "completed")
        self.assertEqual(result["previous_interval_ms"], 1_000)
        self.assertEqual(result["new_interval_ms"], 2000)
        self.assertEqual(result["jitter_percent"], 30)
        self.assertEqual(executor.poll_interval_ms, 2000)
        self.assertEqual(executor.jitter_percent, 30)

    def test_exit_returns_acknowledged(self) -> None:
        status, result = self.executor.execute({"type": "EXIT", "payload": {}})
        self.assertEqual(status, "completed")
        self.assertEqual(result, {"acknowledged": True})

    def test_basic_profile_rejects_sleep_and_exit(self) -> None:
        executor = NodeExecutor(version="0.2.0", profile="basic", poll_interval_ms=1_000)
        for task_type, payload in (
            ("SLEEP", {"interval_ms": 500, "jitter_percent": 0}),
            ("EXIT", {}),
        ):
            with self.subTest(task_type=task_type):
                status, result = executor.execute({"type": task_type, "payload": payload})
                self.assertEqual(status, "failed")
                self.assertEqual(result, {"error_code": "INVALID_TASK"})

    def test_basic_profile_rejects_training_only_action(self) -> None:
        executor = NodeExecutor(version="0.2.0", profile="basic", poll_interval_ms=1_000)
        status, result = executor.execute({"type": "WAIT", "payload": {"milliseconds": 0}})
        self.assertEqual(status, "failed")
        self.assertEqual(result, {"error_code": "INVALID_TASK"})

    def test_purple_lab_profile_executes_fixed_workspace_playbook(self) -> None:
        executor = NodeExecutor(version="0.3.0", profile="purple_lab", poll_interval_ms=1_000)
        self.addCleanup(executor.close)

        status, result = executor.execute(
            {"type": "RUN_PLAYBOOK", "payload": {"playbook": "DISCOVERY_FIXTURES"}}
        )

        self.assertEqual(status, "completed")
        self.assertEqual(result["playbook"], "DISCOVERY_FIXTURES")
        self.assertEqual(result["scope"]["workspace"], "ephemeral-node-private")
        self.assertFalse(result["scope"]["host_access"])
        serialized = str(result)
        self.assertNotIn("/tmp/", serialized)
        self.assertNotIn("/Users/", serialized)

    def test_training_profile_cannot_execute_lab_playbooks(self) -> None:
        status, result = self.executor.execute(
            {"type": "RUN_PLAYBOOK", "payload": {"playbook": "DISCOVERY_FIXTURES"}}
        )
        self.assertEqual(status, "failed")
        self.assertEqual(result, {"error_code": "INVALID_TASK"})

    def test_purple_lab_session_reset_rotates_the_workspace(self) -> None:
        executor = NodeExecutor(version="0.3.0", profile="purple_lab", poll_interval_ms=1_000)
        self.addCleanup(executor.close)
        assert executor.lab_workspace is not None
        original_workspace = executor.lab_workspace
        original_root = original_workspace._root
        status, _ = executor.execute(
            {"type": "RUN_PLAYBOOK", "payload": {"playbook": "CREATE_CANARY"}}
        )
        self.assertEqual(status, "completed")

        executor.reset_lab_workspace()

        self.assertTrue(original_workspace.closed)
        self.assertFalse(os.path.exists(original_root))
        self.assertIsNotNone(executor.lab_workspace)
        self.assertIsNot(executor.lab_workspace, original_workspace)

    def test_wait_uses_only_validated_bounded_duration(self) -> None:
        with mock.patch("c2lab.node.time.sleep") as sleep:
            status, result = self.executor.execute({"type": "WAIT", "payload": {"milliseconds": 250}})
        self.assertEqual(status, "completed")
        self.assertEqual(result, {"waited_ms": 250})
        sleep.assert_called_once_with(0.25)


class NodeClientStateTests(unittest.TestCase):
    def test_node_api_requires_enrollment_before_polling(self) -> None:
        client = NodeClient("http://127.0.0.1:8765", "enrollment-token-123456")
        with self.assertRaises(NodeClientError) as context:
            client.poll()
        self.assertEqual(context.exception.code, "not_enrolled")

    def test_result_submission_rejects_non_protocol_task_id_locally(self) -> None:
        client = NodeClient("http://127.0.0.1:8765", "enrollment-token-123456")
        client.node_id = "node-" + "b" * 10
        client.session_token = "node-session-token-123456"
        with mock.patch.object(client, "_node_request") as request:
            with self.assertRaises(NodeClientError):
                client.submit_result("../../unexpected", "completed", {"reply": "PONG"})
        request.assert_not_called()

    def test_result_submission_normalizes_invalid_status_and_result(self) -> None:
        client = NodeClient("http://127.0.0.1:8765", "enrollment-token-123456")
        client.node_id = "node-" + "b" * 10
        client.session_token = "node-session-token-123456"
        invalid_results = (
            ("running", {"reply": "PONG"}),
            ("completed", {"not_json": object()}),
        )

        with mock.patch.object(client, "_node_request") as request:
            for status, result in invalid_results:
                with self.subTest(status=status), self.assertRaises(NodeClientError) as context:
                    client.submit_result("task-" + "a" * 12, status, result)
                self.assertEqual(str(context.exception), "invalid task result")
                self.assertEqual(context.exception.code, "client_error")
        request.assert_not_called()

    def test_raw_timeout_and_mid_response_disconnect_are_normalized(self) -> None:
        client = NodeClient("http://127.0.0.1:8765", "enrollment-token-123456")
        client._opener = mock.Mock()
        client._opener.open.side_effect = TimeoutError("timed out")
        with self.assertRaises(NodeClientError) as timeout_context:
            client.enroll(name="node", version="0.2.0", profile="basic", poll_interval_ms=1_000)
        self.assertEqual(timeout_context.exception.code, "connection_error")

        response = mock.MagicMock()
        response.read.side_effect = http.client.RemoteDisconnected("response ended early")
        client._opener.open.side_effect = None
        client._opener.open.return_value = response
        with self.assertRaises(NodeClientError) as disconnect_context:
            client.enroll(name="node", version="0.2.0", profile="basic", poll_interval_ms=1_000)
        self.assertEqual(disconnect_context.exception.code, "connection_error")

    def test_poll_rejects_incomplete_or_mismatched_task_envelopes(self) -> None:
        client = NodeClient("http://127.0.0.1:8765", "enrollment-token-123456")
        client.node_id = "node-" + "b" * 10
        client.session_token = "node-session-token-123456"
        valid = {
            "id": "task-" + "a" * 12,
            "correlation_id": "corr-" + "c" * 12,
            "node_id": client.node_id,
            "status": "dispatched",
            "type": "PING",
            "payload": {},
        }
        invalid_tasks = (
            {key: value for key, value in valid.items() if key != "type"},
            {**valid, "correlation_id": "bad"},
            {**valid, "node_id": "node-" + "d" * 10},
            {**valid, "status": "queued"},
            {**valid, "payload": {"unexpected": True}},
        )
        for task in invalid_tasks:
            with self.subTest(task=task), mock.patch.object(
                client, "_node_request", return_value={"task": task}
            ), self.assertRaises(NodeClientError):
                client.poll()

    def test_result_outbox_retries_same_result_after_connection_loss(self) -> None:
        stop = threading.Event()

        class FakeClient:
            controller_url = "http://127.0.0.1:8765"

            def __init__(self) -> None:
                self.node_id = None
                self.session_token = None
                self.poll_calls = 0
                self.submissions: list[tuple[str, str, dict[str, object]]] = []

            def enroll(self, **kwargs: object) -> dict[str, object]:
                self.node_id = "node-" + "1" * 10
                self.session_token = "session-token-123456"
                return {"id": self.node_id}

            def poll(self) -> dict[str, object]:
                self.poll_calls += 1
                return {
                    "task": {
                        "id": "task-" + "2" * 12,
                        "type": "ECHO_TEXT",
                        "payload": {"text": "retry-me"},
                    }
                }

            def submit_result(self, task_id: str, status: str, result: dict[str, object]) -> dict[str, object]:
                self.submissions.append((task_id, status, dict(result)))
                if len(self.submissions) == 1:
                    raise NodeClientError("connection lost", code="connection_error")
                stop.set()
                return {"status": status}

            def clear_session(self) -> None:
                self.node_id = None
                self.session_token = None

            def disconnect(self) -> dict[str, object]:
                return {"status": "offline"}

        fake = FakeClient()
        with mock.patch("c2lab.node.NodeClient", return_value=fake), contextlib.redirect_stdout(io.StringIO()):
            result = run_node(
                controller_url="http://127.0.0.1:8765",
                enrollment_token="enrollment-token-123456",
                name="outbox-node",
                version="0.2.0",
                profile="training",
                poll_interval_ms=250,
                stop_event=stop,
            )

        self.assertEqual(result, 0)
        self.assertEqual(fake.poll_calls, 1)
        self.assertEqual(len(fake.submissions), 2)
        self.assertEqual(fake.submissions[0], fake.submissions[1])

    def test_unauthorized_session_rotates_purple_workspace_before_reenrollment(self) -> None:
        stop = threading.Event()

        class FakeClient:
            controller_url = "http://127.0.0.1:8765"

            def __init__(self) -> None:
                self.node_id = None
                self.session_token = None
                self.enroll_calls = 0
                self.poll_calls = 0

            def enroll(self, **kwargs: object) -> dict[str, object]:
                self.enroll_calls += 1
                self.node_id = "node-" + str(self.enroll_calls) * 10
                self.session_token = "session-token-123456"
                return {"id": self.node_id}

            def poll(self) -> dict[str, object]:
                self.poll_calls += 1
                if self.poll_calls == 1:
                    raise NodeClientError("session expired", status=401)
                stop.set()
                return {"task": None}

            def clear_session(self) -> None:
                self.node_id = None
                self.session_token = None

            def disconnect(self) -> dict[str, object]:
                return {"status": "offline"}

        class FakeExecutor:
            def __init__(self) -> None:
                self.reset_calls = 0
                self.close_calls = 0
                self.poll_interval_ms = 250
                self.jitter_percent = 0

            def reset_lab_workspace(self) -> None:
                self.reset_calls += 1

            def close(self) -> None:
                self.close_calls += 1

        client = FakeClient()
        executor = FakeExecutor()
        with (
            mock.patch("c2lab.node.NodeClient", return_value=client),
            mock.patch("c2lab.node.NodeExecutor", return_value=executor),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = run_node(
                controller_url="http://127.0.0.1:8765",
                enrollment_token="enrollment-token-123456",
                name="purple-reset-node",
                version="0.3.0",
                profile="purple_lab",
                poll_interval_ms=250,
                stop_event=stop,
            )

        self.assertEqual(result, 0)
        self.assertEqual(client.enroll_calls, 2)
        self.assertEqual(executor.reset_calls, 1)
        self.assertEqual(executor.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
