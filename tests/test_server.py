from __future__ import annotations

import http.client
import io
import json
import socket
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest import mock

from c2lab.auth import OperatorSessionRegistry
from c2lab.core import LabRuntime, LabState
from c2lab.node import NodeClient, NodeClientError, NodeExecutor
from c2lab.server import LOOPBACK_HOST, create_server


OPERATOR_TOKEN = "operator-session-token-123456"
ENROLLMENT_TOKEN = "node-enrollment-token-123456"
ADMIN_TOKEN = "generated-admin-session-token-" + "a" * 32
TASK_OPERATOR_TOKEN = "generated-task-operator-token-" + "o" * 32
VIEWER_TOKEN = "generated-viewer-session-token-" + "v" * 32
EXPIRED_TOKEN = "generated-expired-session-token-" + "e" * 32


class RedirectHandler(BaseHTTPRequestHandler):
    target_hits = 0

    def do_POST(self) -> None:  # noqa: N802
        self.send_response(302)
        self.send_header("Location", "/redirect-target")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        type(self).target_hits += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "2")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format: str, *args: Any) -> None:
        return


class BlockingAccessLogStream:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def write(self, value: str) -> int:
        self.started.set()
        self.release.wait(timeout=5)
        return len(value)

    def flush(self) -> None:
        return


class LabServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = LabState()
        self.server = create_server(self.state, OPERATOR_TOKEN, ENROLLMENT_TOKEN, 0)
        self.port = self.server.server_address[1]
        self.base_url = f"http://{LOOPBACK_HOST}:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        authorization: str | None = f"Bearer {OPERATOR_TOKEN}",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        request_headers = dict(headers or {})
        if authorization is not None:
            request_headers["Authorization"] = authorization
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            response = urllib.request.urlopen(request, timeout=2)
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read())
            finally:
                error.close()
        with response:
            content_type = response.headers.get_content_type()
            payload = response.read()
            if content_type == "application/json":
                return response.status, json.loads(payload)
            return response.status, payload.decode("utf-8")

    def make_node(self, *, name: str = "api-node", profile: str = "training") -> NodeClient:
        client = NodeClient(self.base_url, ENROLLMENT_TOKEN)
        client.enroll(name=name, version="0.2.0", profile=profile, poll_interval_ms=500)
        return client

    def test_server_is_bound_to_ipv4_loopback(self) -> None:
        self.assertEqual(self.server.server_address[0], LOOPBACK_HOST)

    def test_server_rejects_shared_operator_and_enrollment_secret(self) -> None:
        with self.assertRaises(ValueError):
            create_server(LabState(), OPERATOR_TOKEN, OPERATOR_TOKEN, 0)

    def test_blocked_access_log_sink_cannot_block_requests_or_shutdown_indefinitely(self) -> None:
        sink = BlockingAccessLogStream()
        server = create_server(
            LabState(),
            OPERATOR_TOKEN,
            ENROLLMENT_TOKEN,
            0,
            access_log=True,
            access_log_stream=sink,
        )
        try:
            started = time.monotonic()
            server.record_response(
                method="GET",
                path="/healthz",
                status=200,
                duration_ms=1.0,
                actor=None,
            )
            self.assertLess(time.monotonic() - started, 0.2)
            self.assertTrue(sink.started.wait(timeout=1))
            self.assertFalse(server.flush_access_log(timeout=0.01))

            closing = time.monotonic()
            server.server_close()
            self.assertLess(time.monotonic() - closing, 1.5)
        finally:
            sink.release.set()
            server.server_close()

    def test_node_client_does_not_follow_http_redirects(self) -> None:
        RedirectHandler.target_hits = 0
        redirect_server = ThreadingHTTPServer((LOOPBACK_HOST, 0), RedirectHandler)
        redirect_port = redirect_server.server_address[1]
        redirect_thread = threading.Thread(target=redirect_server.serve_forever, daemon=True)
        redirect_thread.start()
        try:
            client = NodeClient(f"http://{LOOPBACK_HOST}:{redirect_port}", ENROLLMENT_TOKEN)
            with self.assertRaises(NodeClientError) as context:
                client.enroll(name="redirect-test", version="0.2.0", profile="basic", poll_interval_ms=500)
            self.assertEqual(context.exception.status, 302)
            self.assertEqual(RedirectHandler.target_hits, 0)
        finally:
            redirect_server.shutdown()
            redirect_server.server_close()
            redirect_thread.join(timeout=2)

    def test_dashboard_and_health_are_public_but_do_not_expose_tokens(self) -> None:
        dashboard_status, dashboard = self.request("/", authorization=None)
        health_status, health = self.request("/healthz", authorization=None)
        ready_status, ready = self.request("/readyz", authorization=None)

        self.assertEqual(dashboard_status, 200)
        self.assertIn("C2 Lab", dashboard)
        self.assertNotIn(OPERATOR_TOKEN, dashboard)
        self.assertNotIn(ENROLLMENT_TOKEN, dashboard)
        self.assertEqual(health_status, 200)
        self.assertEqual(
            health,
            {"status": "ok", "mode": "localhost-lab", "protocol": "loopback-http-poll/v1"},
        )
        self.assertEqual(ready_status, 503)
        self.assertEqual(ready["status"], "not_ready")
        self.assertEqual(ready["monitor"]["status"], "unmanaged")
        self.assertFalse(ready["monitor"]["ready"])

    def test_operator_api_requires_operator_bearer_token(self) -> None:
        status, payload = self.request("/lab/overview", authorization=None)
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "unauthorized")

    def test_audit_and_report_routes_are_operator_only_and_redacted(self) -> None:
        client = self.make_node()
        marker = "report-must-not-contain-this"
        queue_status, queued = self.request(
            "/lab/tasks",
            method="POST",
            body={"node_id": client.node_id, "type": "ECHO_TEXT", "payload": {"text": marker}},
        )
        self.assertEqual(queue_status, 201)

        audit_status, audit = self.request("/lab/audit")
        report_status, report = self.request("/lab/report")
        denied_audit, _ = self.request("/lab/audit", authorization=None)
        denied_report, _ = self.request("/lab/report", authorization=None)

        self.assertEqual(audit_status, 200)
        self.assertEqual(report_status, 200)
        self.assertEqual(denied_audit, 401)
        self.assertEqual(denied_report, 401)
        self.assertIn("task.queued", {entry["action"] for entry in audit})
        report_task = next(task for task in report["tasks"] if task["id"] == queued["id"])
        self.assertEqual(report_task["correlation_id"], queued["correlation_id"])
        self.assertNotIn("payload", report_task)
        self.assertNotIn("result", report_task)
        serialized = json.dumps({"audit": audit, "report": report})
        self.assertNotIn(marker, serialized)
        self.assertNotIn(client.session_token or "missing-session", serialized)

    def test_enrollment_requires_separate_token_and_strict_schema(self) -> None:
        body = {
            "name": "strict-node",
            "version": "0.2.0",
            "profile": "basic",
            "capabilities": ["PING", "RUNTIME_STATUS", "ECHO_TEXT", "HASH_TEXT"],
            "poll_interval_ms": 1_000,
        }
        denied_status, denied = self.request(
            "/node/v1/enroll",
            method="POST",
            body=body,
            authorization=f"Bearer {OPERATOR_TOKEN}",
        )
        strict_status, strict = self.request(
            "/node/v1/enroll",
            method="POST",
            body={**body, "unexpected": True},
            authorization=f"Enroll {ENROLLMENT_TOKEN}",
        )
        success_status, enrollment = self.request(
            "/node/v1/enroll",
            method="POST",
            body=body,
            authorization=f"Enroll {ENROLLMENT_TOKEN}",
        )

        self.assertEqual(denied_status, 401)
        self.assertEqual(denied["error"]["code"], "unauthorized")
        self.assertEqual(strict_status, 400)
        self.assertIn("unexpected fields", strict["error"]["message"])
        self.assertEqual(success_status, 201)
        self.assertTrue(enrollment["session_token"])
        self.assertNotIn("session_token", enrollment["node"])

    def test_real_node_client_task_result_end_to_end(self) -> None:
        client = self.make_node()
        executor = NodeExecutor(version="0.2.0", profile="training", poll_interval_ms=500)

        queue_status, queued = self.request(
            "/lab/tasks",
            method="POST",
            body={"node_id": client.node_id, "type": "HASH_TEXT", "payload": {"text": "C2 Lab"}},
        )
        polled = client.poll()
        result_status, result = executor.execute(polled["task"])
        submitted = client.submit_result(polled["task"]["id"], result_status, result)
        replayed = client.submit_result(polled["task"]["id"], result_status, result)
        overview_status, overview = self.request("/lab/overview")

        self.assertEqual(queue_status, 201)
        self.assertIn("created_by", queued)
        self.assertEqual(polled["task"]["id"], queued["id"])
        self.assertNotIn("created_by", polled["task"])
        self.assertEqual(submitted["status"], "completed")
        self.assertEqual(replayed, submitted)
        self.assertNotIn("created_by", submitted)
        self.assertNotIn("created_by", replayed)
        self.assertEqual(submitted["correlation_id"], queued["correlation_id"])
        self.assertEqual(overview_status, 200)
        self.assertEqual(overview["counts"]["nodes_online"], 1)
        self.assertEqual(overview["counts"]["tasks_completed"], 1)
        self.assertEqual(overview["protocol"], "loopback-http-poll/v1")

    def test_operator_task_creation_is_idempotent_when_key_is_supplied(self) -> None:
        client = self.make_node()
        body = {"node_id": client.node_id, "type": "PING", "payload": {}}
        headers = {"Idempotency-Key": "operator-request-0001"}

        first_status, first = self.request(
            "/lab/tasks", method="POST", body=body, headers=headers
        )
        replay_status, replay = self.request(
            "/lab/tasks", method="POST", body=body, headers=headers
        )
        conflict_status, conflict = self.request(
            "/lab/tasks",
            method="POST",
            body={"node_id": client.node_id, "type": "RUNTIME_STATUS", "payload": {}},
            headers=headers,
        )
        overview_status, overview = self.request("/lab/overview")

        self.assertEqual(first_status, 201)
        self.assertEqual(replay_status, 201)
        self.assertEqual(replay["id"], first["id"])
        self.assertEqual(conflict_status, 409)
        self.assertEqual(conflict["error"]["code"], "idempotency_conflict")
        self.assertEqual(overview_status, 200)
        self.assertEqual(len(overview["tasks"]), 1)

    def test_queued_task_can_be_cancelled_but_not_dispatched(self) -> None:
        client = self.make_node()
        queued_status, queued = self.request(
            "/lab/tasks",
            method="POST",
            body={
                "node_id": client.node_id,
                "type": "PING",
                "payload": {},
                "queue_ttl_seconds": 60,
            },
        )
        cancel_status, cancelled = self.request(
            f"/lab/tasks/{queued['id']}/cancel", method="POST", body={}
        )
        replay_status, replayed = self.request(
            f"/lab/tasks/{queued['id']}/cancel", method="POST", body={}
        )
        polled = client.poll()

        self.assertEqual(queued_status, 201)
        self.assertEqual(cancel_status, 200)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(replay_status, 200)
        self.assertEqual(replayed, cancelled)
        self.assertIsNone(polled["task"])

        dispatched_status, dispatched = self.request(
            "/lab/tasks",
            method="POST",
            body={"node_id": client.node_id, "type": "PING", "payload": {}},
        )
        self.assertEqual(dispatched_status, 201)
        self.assertEqual(client.poll()["task"]["id"], dispatched["id"])
        denied_status, denied = self.request(
            f"/lab/tasks/{dispatched['id']}/cancel", method="POST", body={}
        )
        self.assertEqual(denied_status, 409)
        self.assertEqual(denied["error"]["code"], "task_not_cancellable")

    def test_invalid_idempotency_key_and_queue_ttl_are_rejected(self) -> None:
        client = self.make_node()
        body = {"node_id": client.node_id, "type": "PING", "payload": {}}
        key_status, key_error = self.request(
            "/lab/tasks",
            method="POST",
            body=body,
            headers={"Idempotency-Key": "bad key"},
        )
        ttl_errors = [
            self.request(
                "/lab/tasks",
                method="POST",
                body={**body, "queue_ttl_seconds": invalid_ttl},
            )
            for invalid_ttl in (None, 0)
        ]

        self.assertEqual(key_status, 400)
        self.assertIn("Idempotency-Key", key_error["error"]["message"])
        for ttl_status, ttl_error in ttl_errors:
            self.assertEqual(ttl_status, 400)
            self.assertIn("queue_ttl_seconds", ttl_error["error"]["message"])

    def test_purple_lab_playbook_end_to_end_stays_in_ephemeral_scope(self) -> None:
        client = self.make_node(profile="purple_lab")
        executor = NodeExecutor(version="0.3.0", profile="purple_lab", poll_interval_ms=500)
        self.addCleanup(executor.close)

        queue_status, queued = self.request(
            "/lab/tasks",
            method="POST",
            body={
                "node_id": client.node_id,
                "type": "RUN_PLAYBOOK",
                "payload": {"playbook": "COLLECT_AND_STAGE"},
            },
        )
        polled = client.poll()
        result_status, result = executor.execute(polled["task"])
        submitted = client.submit_result(polled["task"]["id"], result_status, result)

        self.assertEqual(queue_status, 201)
        self.assertEqual(queued["type"], "RUN_PLAYBOOK")
        self.assertEqual(submitted["status"], "completed")
        self.assertEqual(submitted["result"]["scope"]["workspace"], "ephemeral-node-private")
        self.assertFalse(submitted["result"]["scope"]["host_access"])
        self.assertNotIn("/tmp/", json.dumps(submitted))

    def test_forged_playbook_result_is_rejected_without_audit_leakage(self) -> None:
        client = self.make_node(profile="purple_lab")
        executor = NodeExecutor(version="0.3.0", profile="purple_lab", poll_interval_ms=500)
        self.addCleanup(executor.close)
        marker = "result-secret-marker"
        self.request(
            "/lab/tasks",
            method="POST",
            body={
                "node_id": client.node_id,
                "type": "RUN_PLAYBOOK",
                "payload": {"playbook": "DISCOVERY_FIXTURES"},
            },
        )
        polled = client.poll()
        status, result = executor.execute(polled["task"])
        result["extra"] = marker

        with self.assertRaises(NodeClientError) as context:
            client.submit_result(polled["task"]["id"], status, result)

        self.assertEqual(context.exception.status, 400)
        self.assertEqual(context.exception.code, "invalid_result")
        audit_status, audit = self.request("/lab/audit")
        overview_status, overview = self.request("/lab/overview")
        self.assertEqual(audit_status, 200)
        self.assertEqual(overview_status, 200)
        self.assertIn("task.result_rejected", {entry["action"] for entry in audit})
        task = next(item for item in overview["tasks"] if item["id"] == polled["task"]["id"])
        self.assertEqual(task["status"], "dispatched")
        self.assertNotIn(marker, json.dumps({"audit": audit, "overview": overview}))

    def test_operator_and_node_credentials_cannot_be_swapped(self) -> None:
        client = self.make_node()
        operator_status, _ = self.request(
            "/lab/overview",
            authorization=f"Node {client.session_token}",
            headers={"X-C2Lab-Node": client.node_id or ""},
        )
        node_status, node_payload = self.request(
            "/node/v1/poll",
            method="POST",
            body={},
            authorization=f"Bearer {OPERATOR_TOKEN}",
        )

        self.assertEqual(operator_status, 401)
        self.assertEqual(node_status, 401)
        self.assertEqual(node_payload["error"]["code"], "invalid_node_session")

    def test_reset_invalidates_existing_node_session(self) -> None:
        client = self.make_node()
        reset_status, reset = self.request("/lab/reset", method="POST", body={})
        self.assertEqual(reset_status, 200)
        self.assertTrue(reset["sessions_invalidated"])
        with self.assertRaises(NodeClientError) as context:
            client.poll()
        self.assertEqual(context.exception.status, 401)

    def test_command_shaped_task_is_rejected(self) -> None:
        client = self.make_node()
        status, payload = self.request(
            "/lab/tasks",
            method="POST",
            body={
                "node_id": client.node_id,
                "type": "RUN_COMMAND",
                "payload": {"command": "whoami"},
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "unsupported_task_type")

    def test_cross_origin_operator_write_is_rejected(self) -> None:
        status, payload = self.request(
            "/lab/reset",
            method="POST",
            body={},
            headers={"Origin": "https://example.invalid"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "invalid_origin")

    def test_non_local_host_header_is_rejected(self) -> None:
        for host in ("example.invalid", "localhost:evil", f"localhost:{self.port}.invalid"):
            with self.subTest(host=host):
                connection = http.client.HTTPConnection(LOOPBACK_HOST, self.port, timeout=2)
                connection.request("GET", "/healthz", headers={"Host": host})
                response = connection.getresponse()
                payload = json.loads(response.read())
                connection.close()

                self.assertEqual(response.status, 403)
        self.assertEqual(payload["error"]["code"], "local_only")

    def test_malformed_request_target_is_fixed_400_and_observable(self) -> None:
        before = self.server.metrics.snapshot()
        with socket.create_connection((LOOPBACK_HOST, self.port), timeout=2) as connection:
            connection.sendall(
                (
                    "GET http://[::1/lab/sync HTTP/1.1\r\n"
                    f"Host: localhost:{self.port}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
            )
            response = bytearray()
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                response.extend(chunk)

        self.assertTrue(response.startswith(b"HTTP/1.1 400"), response[:80])
        self.assertIn(b'"code":"invalid_request_target"', response)
        after = self.server.metrics.snapshot()
        self.assertEqual(after["requests_total"], before["requests_total"] + 1)
        self.assertEqual(
            after["routes"].get("unmatched", 0),
            before["routes"].get("unmatched", 0) + 1,
        )

    def test_early_post_error_closes_http11_connection(self) -> None:
        connection = http.client.HTTPConnection(LOOPBACK_HOST, self.port, timeout=2)
        connection.request(
            "POST",
            "/lab/reset",
            body=b"{}",
            headers={
                "Authorization": f"Bearer {OPERATOR_TOKEN}",
                "Content-Type": "text/plain",
            },
        )
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 415)
        self.assertEqual(response.getheader("Connection"), "close")
        connection.close()

    def test_pathological_json_is_rejected_as_400(self) -> None:
        bodies = (
            b'{"unexpected":' + (b"9" * 5_000) + b"}",
            (b"[" * 1_200) + b"0" + (b"]" * 1_200),
        )
        for body in bodies:
            with self.subTest(length=len(body)):
                connection = http.client.HTTPConnection(LOOPBACK_HOST, self.port, timeout=2)
                connection.request(
                    "POST",
                    "/lab/reset",
                    body=body,
                    headers={
                        "Authorization": f"Bearer {OPERATOR_TOKEN}",
                        "Content-Type": "application/json",
                    },
                )
                response = connection.getresponse()
                payload = json.loads(response.read())
                connection.close()
                self.assertEqual(response.status, 400)
                self.assertEqual(payload["error"]["code"], "invalid_request")

    def test_static_content_types_are_fixed(self) -> None:
        for path, expected in (
            ("/", "text/html"),
            ("/static/styles.css", "text/css"),
            ("/static/app.js", "text/javascript"),
        ):
            connection = http.client.HTTPConnection(LOOPBACK_HOST, self.port, timeout=2)
            connection.request("GET", path)
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 200, path)
            self.assertEqual(response.headers.get_content_type(), expected, path)
            self.assertEqual(response.getheader("Connection"), "close", path)
            connection.close()


class OperatorRBACServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = LabState()
        self.registry = OperatorSessionRegistry()
        now = time.monotonic()
        self.sessions = {
            "admin": self.registry.register(
                "admin-user", "admin", ADMIN_TOKEN, ttl_seconds=600, now=now
            ),
            "operator": self.registry.register(
                "task-operator", "operator", TASK_OPERATOR_TOKEN, ttl_seconds=600, now=now
            ),
            "viewer": self.registry.register(
                "read-viewer", "viewer", VIEWER_TOKEN, ttl_seconds=600, now=now
            ),
            "expired": self.registry.register(
                "expired-viewer", "viewer", EXPIRED_TOKEN, ttl_seconds=1, now=now - 10
            ),
        }
        self.runtime = LabRuntime(self.state, tick_seconds=0.01)
        self.runtime.start()
        deadline = time.monotonic() + 1
        while not self.runtime.health()["ready"] and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(self.runtime.health()["ready"])

        self.access_log = io.StringIO()
        self.server = create_server(
            self.state,
            ADMIN_TOKEN,
            ENROLLMENT_TOKEN,
            0,
            operator_registry=self.registry,
            runtime=self.runtime,
            access_log=True,
            access_log_stream=self.access_log,
        )
        self.port = self.server.server_address[1]
        self.base_url = f"http://{LOOPBACK_HOST}:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.runtime.stop()

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        authorization: str | None = f"Bearer {ADMIN_TOKEN}",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        request_headers = dict(headers or {})
        if authorization is not None:
            request_headers["Authorization"] = authorization
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            response = urllib.request.urlopen(request, timeout=2)
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read())
            finally:
                error.close()
        with response:
            return response.status, json.loads(response.read())

    def make_node(self) -> NodeClient:
        client = NodeClient(self.base_url, ENROLLMENT_TOKEN)
        client.enroll(name="rbac-node", version="0.2.0", profile="training", poll_interval_ms=500)
        return client

    @staticmethod
    def bearer(secret: str) -> str:
        return f"Bearer {secret}"

    def test_session_identifies_principal_and_expired_session_is_unauthorized(self) -> None:
        expected = {
            ADMIN_TOKEN: (
                "admin-user",
                "admin",
                {
                    "read",
                    "task_write",
                    "exercise_write",
                    "containment_write",
                    "note_write",
                    "reset",
                    "operator_admin",
                },
            ),
            TASK_OPERATOR_TOKEN: (
                "task-operator",
                "operator",
                {"read", "task_write", "exercise_write", "note_write"},
            ),
            VIEWER_TOKEN: ("read-viewer", "viewer", {"read"}),
        }
        for secret, (principal_id, role, permissions) in expected.items():
            with self.subTest(role=role):
                status, session = self.request(
                    "/lab/session", authorization=self.bearer(secret)
                )
                self.assertEqual(status, 200)
                self.assertEqual(session["principal_id"], principal_id)
                self.assertEqual(session["role"], role)
                self.assertEqual(set(session["permissions"]), permissions)
                self.assertTrue(session["active"])
                self.assertNotIn("token", session)
                self.assertNotIn(secret, json.dumps(session))

        missing_status, missing = self.request("/lab/session", authorization=None)
        expired_status, expired = self.request(
            "/lab/session", authorization=self.bearer(EXPIRED_TOKEN)
        )
        self.assertEqual(missing_status, 401)
        self.assertEqual(missing["error"]["code"], "unauthorized")
        self.assertEqual(expired_status, 401)
        self.assertEqual(expired["error"]["code"], "unauthorized")

    def test_operator_admin_routes_are_admin_only_and_revoke_immediately(self) -> None:
        for secret in (VIEWER_TOKEN, TASK_OPERATOR_TOKEN):
            with self.subTest(secret=secret):
                status, payload = self.request(
                    "/lab/operators", authorization=self.bearer(secret)
                )
                self.assertEqual(status, 403)
                self.assertEqual(payload["error"]["code"], "forbidden")

        list_status, operators = self.request("/lab/operators")
        self.assertEqual(list_status, 200)
        self.assertEqual(
            {operator["id"] for operator in operators},
            {session["id"] for session in self.sessions.values()},
        )
        serialized_operators = json.dumps(operators)
        for secret in (ADMIN_TOKEN, TASK_OPERATOR_TOKEN, VIEWER_TOKEN, EXPIRED_TOKEN):
            self.assertNotIn(secret, serialized_operators)
        self.assertTrue(
            all("token" not in key for operator in operators for key in operator)
        )

        target_id = self.sessions["viewer"]["id"]
        denied_status, denied = self.request(
            f"/lab/operators/{target_id}/revoke",
            method="POST",
            body={},
            authorization=self.bearer(TASK_OPERATOR_TOKEN),
        )
        self.assertEqual(denied_status, 403)
        self.assertEqual(denied["error"]["code"], "forbidden")

        revoke_status, revoked = self.request(
            f"/lab/operators/{target_id}/revoke", method="POST", body={}
        )
        self.assertEqual(revoke_status, 200)
        self.assertEqual(revoked["id"], target_id)
        self.assertEqual(revoked["status"], "revoked")
        rejected_status, _ = self.request(
            "/lab/session", authorization=self.bearer(VIEWER_TOKEN)
        )
        self.assertEqual(rejected_status, 401)

        admin_id = self.sessions["admin"]["id"]
        self_revoke_status, self_revoke = self.request(
            f"/lab/operators/{admin_id}/revoke", method="POST", body={}
        )
        self.assertEqual(self_revoke_status, 409)
        self.assertEqual(self_revoke["error"]["code"], "last_admin_session")

    def test_role_permissions_enforce_read_task_write_and_reset(self) -> None:
        for secret in (VIEWER_TOKEN, TASK_OPERATOR_TOKEN, ADMIN_TOKEN):
            with self.subTest(permission="read", secret=secret):
                status, _ = self.request(
                    "/lab/overview", authorization=self.bearer(secret)
                )
                self.assertEqual(status, 200)

        client = self.make_node()
        task_body = {"node_id": client.node_id, "type": "PING", "payload": {}}
        viewer_queue_status, viewer_queue = self.request(
            "/lab/tasks",
            method="POST",
            body=task_body,
            authorization=self.bearer(VIEWER_TOKEN),
        )
        self.assertEqual(viewer_queue_status, 403)
        self.assertEqual(viewer_queue["error"]["code"], "forbidden")

        operator_queue_status, operator_task = self.request(
            "/lab/tasks",
            method="POST",
            body=task_body,
            authorization=self.bearer(TASK_OPERATOR_TOKEN),
        )
        self.assertEqual(operator_queue_status, 201)
        viewer_cancel_status, viewer_cancel = self.request(
            f"/lab/tasks/{operator_task['id']}/cancel",
            method="POST",
            body={},
            authorization=self.bearer(VIEWER_TOKEN),
        )
        self.assertEqual(viewer_cancel_status, 403)
        self.assertEqual(viewer_cancel["error"]["code"], "forbidden")
        operator_cancel_status, _ = self.request(
            f"/lab/tasks/{operator_task['id']}/cancel",
            method="POST",
            body={},
            authorization=self.bearer(TASK_OPERATOR_TOKEN),
        )
        self.assertEqual(operator_cancel_status, 200)

        admin_queue_status, admin_task = self.request(
            "/lab/tasks", method="POST", body=task_body
        )
        self.assertEqual(admin_queue_status, 201)
        admin_cancel_status, _ = self.request(
            f"/lab/tasks/{admin_task['id']}/cancel", method="POST", body={}
        )
        self.assertEqual(admin_cancel_status, 200)

        for secret in (VIEWER_TOKEN, TASK_OPERATOR_TOKEN):
            with self.subTest(permission="reset", secret=secret):
                status, payload = self.request(
                    "/lab/reset",
                    method="POST",
                    body={},
                    authorization=self.bearer(secret),
                )
                self.assertEqual(status, 403)
                self.assertEqual(payload["error"]["code"], "forbidden")
        admin_reset_status, _ = self.request("/lab/reset", method="POST", body={})
        self.assertEqual(admin_reset_status, 200)

    def test_exercise_api_enforces_catalog_schema_rbac_and_containment(self) -> None:
        catalog_status, catalog = self.request(
            "/lab/scenarios",
            authorization=self.bearer(VIEWER_TOKEN),
        )
        self.assertEqual(catalog_status, 200)
        self.assertEqual(
            {scenario["id"] for scenario in catalog},
            {"DISCOVERY_COLLECTION", "CANARY_REMOVAL"},
        )

        client = NodeClient(self.base_url, ENROLLMENT_TOKEN)
        client.enroll(
            name="exercise-node",
            version="0.7.0",
            profile="purple_lab",
            poll_interval_ms=500,
        )
        request_body = {
            "node_id": client.node_id,
            "scenario_id": "DISCOVERY_COLLECTION",
        }
        viewer_status, viewer_error = self.request(
            "/lab/exercises",
            method="POST",
            body=request_body,
            authorization=self.bearer(VIEWER_TOKEN),
        )
        self.assertEqual(viewer_status, 403)
        self.assertEqual(viewer_error["error"]["code"], "forbidden")

        invalid_status, invalid = self.request(
            "/lab/exercises",
            method="POST",
            body={**request_body, "steps": []},
            authorization=self.bearer(TASK_OPERATOR_TOKEN),
        )
        self.assertEqual(invalid_status, 400)
        self.assertEqual(invalid["error"]["code"], "invalid_request")

        create_status, exercise = self.request(
            "/lab/exercises",
            method="POST",
            body=request_body,
            authorization=self.bearer(TASK_OPERATOR_TOKEN),
            headers={"Idempotency-Key": "exercise:http:001"},
        )
        retry_status, retry = self.request(
            "/lab/exercises",
            method="POST",
            body=request_body,
            authorization=self.bearer(TASK_OPERATOR_TOKEN),
            headers={"Idempotency-Key": "exercise:http:001"},
        )
        self.assertEqual(create_status, 201)
        self.assertEqual(retry_status, 201)
        self.assertEqual(retry["id"], exercise["id"])

        executor = NodeExecutor(
            version="0.7.0",
            profile="purple_lab",
            poll_interval_ms=500,
        )
        try:
            task = client.poll()["task"]
            status, result = executor.execute(task)
            client.submit_result(task["id"], status, result)
        finally:
            executor.close()

        operator_contain_status, operator_contain = self.request(
            f"/lab/exercises/{exercise['id']}/contain",
            method="POST",
            body={"action": "PAUSE_NODE_TASKING"},
            authorization=self.bearer(TASK_OPERATOR_TOKEN),
        )
        self.assertEqual(operator_contain_status, 403)
        self.assertEqual(operator_contain["error"]["code"], "forbidden")

        contain_status, contained = self.request(
            f"/lab/exercises/{exercise['id']}/contain",
            method="POST",
            body={"action": "PAUSE_NODE_TASKING"},
        )
        self.assertEqual(contain_status, 200)
        self.assertEqual(contained["status"], "contained")
        self.assertEqual(contained["containment"]["action"], "PAUSE_NODE_TASKING")

        exercises_status, exercises = self.request(
            "/lab/exercises",
            authorization=self.bearer(VIEWER_TOKEN),
        )
        sync_status, sync = self.request(
            "/lab/sync",
            authorization=self.bearer(VIEWER_TOKEN),
        )
        self.assertEqual(exercises_status, 200)
        self.assertEqual(sync_status, 200)
        self.assertEqual(exercises[0]["id"], exercise["id"])
        self.assertEqual(sync["exercises"][0]["status"], "contained")
        self.assertEqual(sync["counts"]["alerts_open"], 0)
        self.assertTrue(sync["nodes"][0]["tasking_paused"])

    def test_sync_requires_read_and_strictly_parses_bounded_decimal_query(self) -> None:
        with mock.patch.object(self.state, "sync", wraps=self.state.sync) as sync:
            default_status, default_payload = self.request(
                "/lab/sync",
                authorization=self.bearer(VIEWER_TOKEN),
            )
        self.assertEqual(default_status, 200)
        self.assertTrue(default_payload["lab_mode"])
        sync.assert_called_once_with(events_after=0, audit_after=0, limit=100)

        with mock.patch.object(self.state, "sync", wraps=self.state.sync) as sync:
            explicit_status, _ = self.request(
                "/lab/sync?events_after=1&audit_after=2&limit=3",
                authorization=self.bearer(VIEWER_TOKEN),
            )
        self.assertEqual(explicit_status, 200)
        sync.assert_called_once_with(events_after=1, audit_after=2, limit=3)

        denied_status, denied = self.request("/lab/sync", authorization=None)
        self.assertEqual(denied_status, 401)
        self.assertEqual(denied["error"]["code"], "unauthorized")

        invalid_queries = (
            "events_after=0&events_after=1",
            "unknown=0",
            "events_after=-1",
            "audit_after=0x10",
            "limit=1.0",
            "limit=0",
            "limit=101",
            "events_after=9223372036854775808",
            "events_after=",
            "events_after",
            "events_after=%EF%BC%91",
            "events_after=" + "9" * 300,
        )
        with mock.patch.object(self.state, "sync", wraps=self.state.sync) as sync:
            for query in invalid_queries:
                with self.subTest(query=query):
                    status, payload = self.request(
                        f"/lab/sync?{query}",
                        authorization=self.bearer(VIEWER_TOKEN),
                    )
                    self.assertEqual(status, 400)
                    self.assertEqual(payload["error"]["code"], "invalid_request")
        sync.assert_not_called()

    def test_notes_require_local_origin_note_write_and_exact_idempotent_body(self) -> None:
        body = {"message": "handoff note for the localhost lab"}
        headers = {"Idempotency-Key": "shared-note-request-0001"}

        viewer_status, viewer_error = self.request(
            "/lab/notes",
            method="POST",
            body=body,
            authorization=self.bearer(VIEWER_TOKEN),
            headers=headers,
        )
        self.assertEqual(viewer_status, 403)
        self.assertEqual(viewer_error["error"]["code"], "forbidden")

        origin_status, origin_error = self.request(
            "/lab/notes",
            method="POST",
            body=body,
            authorization=self.bearer(TASK_OPERATOR_TOKEN),
            headers={**headers, "Origin": "https://not-local.example"},
        )
        self.assertEqual(origin_status, 403)
        self.assertEqual(origin_error["error"]["code"], "invalid_origin")

        first_status, first = self.request(
            "/lab/notes",
            method="POST",
            body=body,
            authorization=self.bearer(TASK_OPERATOR_TOKEN),
            headers=headers,
        )
        replay_status, replay = self.request(
            "/lab/notes",
            method="POST",
            body=body,
            authorization=self.bearer(TASK_OPERATOR_TOKEN),
            headers=headers,
        )
        conflict_status, conflict = self.request(
            "/lab/notes",
            method="POST",
            body={"message": "different handoff note"},
            authorization=self.bearer(TASK_OPERATOR_TOKEN),
            headers=headers,
        )
        self.assertEqual(first_status, 201)
        self.assertEqual(first["kind"], "operator.note")
        self.assertEqual(first["actor"], "task-operator")
        self.assertEqual(replay_status, 201)
        self.assertEqual(replay, first)
        self.assertEqual(conflict_status, 409)
        self.assertEqual(conflict["error"]["code"], "idempotency_conflict")

        for invalid_body in ({}, {"message": "valid", "unexpected": True}):
            with self.subTest(body=invalid_body):
                status, payload = self.request(
                    "/lab/notes",
                    method="POST",
                    body=invalid_body,
                    authorization=self.bearer(TASK_OPERATOR_TOKEN),
                )
                self.assertEqual(status, 400)
                self.assertEqual(payload["error"]["code"], "invalid_request")

        admin_status, admin_note = self.request(
            "/lab/notes",
            method="POST",
            body={"message": "admin coordination note"},
        )
        self.assertEqual(admin_status, 201)
        self.assertEqual(admin_note["actor"], "admin-user")

        sync_status, synced = self.request(
            "/lab/sync?events_after=0&audit_after=0&limit=100",
            authorization=self.bearer(VIEWER_TOKEN),
        )
        self.assertEqual(sync_status, 200)
        operator_event = next(
            event
            for event in synced["events"]
            if event["id"] == first["id"]
        )
        self.assertEqual(operator_event["actor"], "task-operator")
        self.assertIn(
            ("operator.note", "task-operator"),
            {(entry["action"], entry["actor"]) for entry in synced["audit"]},
        )

    def test_note_and_sync_access_logs_omit_body_key_token_and_query_values(self) -> None:
        note_message = "note-body-secret-marker"
        idempotency_key = "note-key-secret-0001"
        query_value = "9223372036854775806"
        note_status, _ = self.request(
            "/lab/notes",
            method="POST",
            body={"message": note_message},
            authorization=self.bearer(TASK_OPERATOR_TOKEN),
            headers={"Idempotency-Key": idempotency_key},
        )
        sync_status, _ = self.request(
            f"/lab/sync?events_after={query_value}&audit_after=0&limit=1",
            authorization=self.bearer(VIEWER_TOKEN),
        )
        self.assertEqual(note_status, 201)
        self.assertEqual(sync_status, 200)
        self.assertTrue(self.server.flush_access_log(timeout=1.0))

        entries = [
            json.loads(line)
            for line in self.access_log.getvalue().splitlines()
            if line.strip()
        ]
        note_entry = next(entry for entry in entries if entry["route"] == "/lab/notes")
        sync_entry = next(entry for entry in entries if entry["route"] == "/lab/sync")
        self.assertEqual(note_entry["principal_id"], "task-operator")
        self.assertEqual(sync_entry["principal_id"], "read-viewer")
        serialized = json.dumps(entries)
        for secret in (
            note_message,
            idempotency_key,
            query_value,
            TASK_OPERATOR_TOKEN,
            VIEWER_TOKEN,
        ):
            self.assertNotIn(secret, serialized)

    def test_mutating_audit_entries_use_authenticated_principal(self) -> None:
        client = self.make_node()
        queued_status, queued = self.request(
            "/lab/tasks",
            method="POST",
            body={"node_id": client.node_id, "type": "PING", "payload": {}},
            authorization=self.bearer(TASK_OPERATOR_TOKEN),
        )
        self.assertEqual(queued_status, 201)
        cancelled_status, _ = self.request(
            f"/lab/tasks/{queued['id']}/cancel",
            method="POST",
            body={},
            authorization=self.bearer(TASK_OPERATOR_TOKEN),
        )
        self.assertEqual(cancelled_status, 200)
        reset_status, _ = self.request("/lab/reset", method="POST", body={})
        self.assertEqual(reset_status, 200)

        audit_status, audit = self.request("/lab/audit")
        self.assertEqual(audit_status, 200)
        queued_entry = next(
            entry
            for entry in audit
            if entry["action"] == "task.queued" and entry["task_id"] == queued["id"]
        )
        cancelled_entry = next(
            entry
            for entry in audit
            if entry["action"] == "task.cancelled" and entry["task_id"] == queued["id"]
        )
        reset_entry = next(entry for entry in audit if entry["action"] == "lab.reset")
        self.assertEqual(queued_entry["actor"], "task-operator")
        self.assertEqual(cancelled_entry["actor"], "task-operator")
        self.assertEqual(reset_entry["actor"], "admin-user")

    def test_ready_and_metrics_are_aggregate_and_secret_free(self) -> None:
        client = self.make_node()
        queued_status, queued = self.request(
            "/lab/tasks",
            method="POST",
            body={"node_id": client.node_id, "type": "PING", "payload": {}},
            authorization=self.bearer(TASK_OPERATOR_TOKEN),
        )
        self.assertEqual(queued_status, 201)
        cancel_status, _ = self.request(
            f"/lab/tasks/{queued['id']}/cancel",
            method="POST",
            body={},
            authorization=self.bearer(TASK_OPERATOR_TOKEN),
        )
        self.assertEqual(cancel_status, 200)
        raw_dynamic_path = "/attacker-secret-segment/private/value"
        raw_status, _ = self.request(
            raw_dynamic_path, authorization=self.bearer(VIEWER_TOKEN)
        )
        self.assertEqual(raw_status, 404)

        ready_status, ready = self.request("/readyz", authorization=None)
        metrics_status, metrics = self.request(
            "/lab/metrics", authorization=self.bearer(VIEWER_TOKEN)
        )
        denied_metrics_status, _ = self.request("/lab/metrics", authorization=None)
        self.assertEqual(ready_status, 200)
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(
            set(ready["monitor"]),
            {"status", "ready", "last_tick_age_seconds", "last_error"},
        )
        self.assertEqual(ready["monitor"]["status"], "running")
        self.assertTrue(ready["monitor"]["ready"])
        self.assertEqual(metrics_status, 200)
        self.assertEqual(set(metrics), {"http", "lab", "readiness"})
        self.assertEqual(metrics["readiness"]["status"], "ready")
        self.assertGreaterEqual(
            metrics["http"]["routes"]["/lab/tasks/:task_id/cancel"], 1
        )
        self.assertGreaterEqual(metrics["http"]["routes"]["unmatched"], 1)
        self.assertEqual(denied_metrics_status, 401)

        self.runtime.stop()
        stopped_status, stopped = self.request("/readyz", authorization=None)
        self.assertEqual(stopped_status, 503)
        self.assertEqual(stopped["status"], "not_ready")
        self.assertEqual(stopped["monitor"]["status"], "stopped")
        self.assertFalse(stopped["monitor"]["ready"])

        serialized = json.dumps({"ready": ready, "metrics": metrics, "stopped": stopped})
        forbidden_values = (
            ADMIN_TOKEN,
            TASK_OPERATOR_TOKEN,
            VIEWER_TOKEN,
            EXPIRED_TOKEN,
            ENROLLMENT_TOKEN,
            client.session_token or "missing-node-session",
            client.node_id or "missing-node-id",
            queued["id"],
            raw_dynamic_path,
            "attacker-secret-segment",
        )
        for forbidden in forbidden_values:
            self.assertNotIn(forbidden, serialized)

    def test_structured_access_log_uses_normalized_routes_and_principals(self) -> None:
        client = self.make_node()
        client.poll()
        queued_status, queued = self.request(
            "/lab/tasks",
            method="POST",
            body={"node_id": client.node_id, "type": "PING", "payload": {}},
            authorization=self.bearer(TASK_OPERATOR_TOKEN),
        )
        self.assertEqual(queued_status, 201)
        cancel_status, _ = self.request(
            f"/lab/tasks/{queued['id']}/cancel",
            method="POST",
            body={},
            authorization=self.bearer(TASK_OPERATOR_TOKEN),
        )
        self.assertEqual(cancel_status, 200)
        ready_status, _ = self.request("/readyz", authorization=None)
        self.assertEqual(ready_status, 200)
        raw_dynamic_path = "/raw-secret-segment/nested"
        raw_status, _ = self.request(
            raw_dynamic_path, authorization=self.bearer(VIEWER_TOKEN)
        )
        self.assertEqual(raw_status, 404)
        self.assertTrue(self.server.flush_access_log(timeout=1.0))

        entries = [
            json.loads(line)
            for line in self.access_log.getvalue().splitlines()
            if line.strip()
        ]
        self.assertTrue(entries)
        expected_keys = {
            "time",
            "event",
            "method",
            "route",
            "status",
            "duration_ms",
            "principal_id",
        }
        self.assertTrue(all(set(entry) == expected_keys for entry in entries))
        cancel_entry = next(
            entry
            for entry in entries
            if entry["route"] == "/lab/tasks/:task_id/cancel"
        )
        self.assertEqual(cancel_entry["method"], "POST")
        self.assertEqual(cancel_entry["status"], 200)
        self.assertEqual(cancel_entry["principal_id"], "task-operator")
        self.assertEqual(
            next(entry for entry in entries if entry["route"] == "/node/v1/enroll")[
                "principal_id"
            ],
            "enrollment",
        )
        self.assertEqual(
            next(entry for entry in entries if entry["route"] == "/node/v1/poll")[
                "principal_id"
            ],
            "node",
        )
        self.assertIsNone(
            next(entry for entry in entries if entry["route"] == "/readyz")[
                "principal_id"
            ]
        )
        self.assertEqual(
            next(entry for entry in entries if entry["route"] == "unmatched")[
                "principal_id"
            ],
            "read-viewer",
        )

        serialized = json.dumps(entries)
        forbidden_values = [
            ADMIN_TOKEN,
            TASK_OPERATOR_TOKEN,
            VIEWER_TOKEN,
            EXPIRED_TOKEN,
            ENROLLMENT_TOKEN,
            client.session_token or "missing-node-session",
            client.node_id or "missing-node-id",
            queued["id"],
            raw_dynamic_path,
            "raw-secret-segment",
            *(session["id"] for session in self.sessions.values()),
        ]
        for forbidden in forbidden_values:
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
