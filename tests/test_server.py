from __future__ import annotations

import http.client
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from c2lab.core import LabState
from c2lab.node import NodeClient, NodeClientError, NodeExecutor
from c2lab.server import LOOPBACK_HOST, create_server


OPERATOR_TOKEN = "operator-session-token-123456"
ENROLLMENT_TOKEN = "node-enrollment-token-123456"


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

        self.assertEqual(dashboard_status, 200)
        self.assertIn("C2 Lab", dashboard)
        self.assertNotIn(OPERATOR_TOKEN, dashboard)
        self.assertNotIn(ENROLLMENT_TOKEN, dashboard)
        self.assertEqual(health_status, 200)
        self.assertEqual(
            health,
            {"status": "ok", "mode": "localhost-lab", "protocol": "loopback-http-poll/v1"},
        )

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
        self.assertEqual(polled["task"]["id"], queued["id"])
        self.assertEqual(submitted["status"], "completed")
        self.assertEqual(replayed, submitted)
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


if __name__ == "__main__":
    unittest.main()
