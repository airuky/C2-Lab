from __future__ import annotations

import concurrent.futures
import json
import unittest
from unittest.mock import patch

from c2lab.core import (
    DEFAULT_QUEUE_TTL_SECONDS,
    MAX_AUDIT_ENTRIES,
    MAX_EVENTS,
    MAX_OPERATOR_NOTE_LENGTH,
    MAX_OPERATOR_NOTES_RETAINED,
    MAX_QUEUE_TTL_SECONDS,
    MAX_NODES,
    MAX_QUEUED_PLAYBOOKS_PER_NODE,
    MAX_QUEUED_TASKS_PER_NODE,
    MAX_SYNC_CURSOR,
    MAX_SYNC_PAGE_SIZE,
    MAX_TASKS,
    NODE_STALE_SECONDS,
    MIN_QUEUE_TTL_SECONDS,
    STALE_SESSION_TTL_SECONDS,
    TASK_TIMEOUT_SECONDS,
    LabError,
    LabState,
)
from c2lab.protocol import capabilities_for_profile


class LabStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = LabState()
        enrollment = self.state.enroll_node(
            "unit-node",
            "0.2.0",
            "training",
            capabilities_for_profile("training"),
            1_000,
            now=10.0,
        )
        self.node = enrollment["node"]
        self.session_token = enrollment["session_token"]

    def test_enrollment_returns_private_session_separately(self) -> None:
        self.assertTrue(self.state.authenticate_node(self.node["id"], self.session_token))
        self.assertFalse(self.state.authenticate_node(self.node["id"], "wrong-token"))
        self.assertNotIn("session_token", self.node)
        self.assertNotIn("_session_token", self.state.nodes()[0])
        self.assertTrue(self.node["session_active"])
        self.assertEqual(self.node["transport"], "loopback-http-poll/v1")

    def test_allowlisted_task_moves_through_node_lifecycle(self) -> None:
        queued = self.state.queue_task(self.node["id"], "PING", {})

        polled = self.state.poll_node(self.node["id"], self.session_token, now=11.0)
        self.assertEqual(polled["task"]["id"], queued["id"])
        self.assertEqual(polled["task"]["status"], "dispatched")
        self.assertNotIn("created_by", polled["task"])

        finished = self.state.submit_result(
            self.node["id"],
            self.session_token,
            queued["id"],
            "completed",
            {"reply": "PONG"},
            now=11.1,
        )
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["result"], {"reply": "PONG"})
        self.assertTrue(finished["correlation_id"].startswith("corr-"))
        self.assertNotIn("created_by", finished)
        self.assertEqual(self.state.nodes()[0]["tasks_completed"], 1)

    def test_identical_result_retry_is_idempotent(self) -> None:
        task = self.state.queue_task(self.node["id"], "PING", {})
        self.state.poll_node(self.node["id"], self.session_token, now=11.0)
        first = self.state.submit_result(
            self.node["id"], self.session_token, task["id"], "completed", {"reply": "PONG"}, now=11.1
        )
        replay = self.state.submit_result(
            self.node["id"], self.session_token, task["id"], "completed", {"reply": "PONG"}, now=11.2
        )

        self.assertEqual(replay, first)
        self.assertEqual(self.state.nodes()[0]["tasks_completed"], 1)
        completed_events = [event for event in self.state.events() if event["kind"] == "task.completed"]
        self.assertEqual(len(completed_events), 1)
        with self.assertRaises(LabError) as context:
            self.state.submit_result(
                self.node["id"], self.session_token, task["id"], "failed", {"error": "different"}, now=11.3
            )
        self.assertEqual(context.exception.code, "result_conflict")

    def test_terminal_result_retry_compares_json_types_strictly(self) -> None:
        task = self.state.queue_task(
            self.node["id"],
            "GENERATE_EVENT",
            {"category": "training", "severity": "info", "message": "strict retry"},
        )
        self.state.poll_node(self.node["id"], self.session_token, now=11.0)
        self.state.submit_result(
            self.node["id"],
            self.session_token,
            task["id"],
            "completed",
            {
                "recorded": True,
                "category": "training",
                "severity": "info",
                "message": "strict retry",
            },
            now=11.1,
        )

        with self.assertRaises(LabError) as context:
            self.state.submit_result(
                self.node["id"],
                self.session_token,
                task["id"],
                "completed",
                {
                    "recorded": 1,
                    "category": "training",
                    "severity": "info",
                    "message": "strict retry",
                },
                now=11.2,
            )
        self.assertEqual(context.exception.code, "result_conflict")

    def test_pruned_sleep_result_retry_uses_bounded_ack_tombstone(self) -> None:
        with patch("c2lab.core.MAX_TASKS", 2):
            task = self.state.queue_task(
                self.node["id"],
                "SLEEP",
                {"interval_ms": 2_000, "jitter_percent": 0},
            )
            self.state.poll_node(self.node["id"], self.session_token, now=11.0)
            accepted = self.state.submit_result(
                self.node["id"],
                self.session_token,
                task["id"],
                "completed",
                {
                    "previous_interval_ms": 1_000,
                    "new_interval_ms": 2_000,
                    "jitter_percent": 0,
                },
                now=11.1,
            )
            self.state.queue_task(self.node["id"], "PING", {})
            self.state.queue_task(self.node["id"], "PING", {})

            self.assertNotIn(task["id"], {item["id"] for item in self.state.tasks()})
            replay = self.state.submit_result(
                self.node["id"],
                self.session_token,
                task["id"],
                "completed",
                {
                    "previous_interval_ms": 1_000,
                    "new_interval_ms": 2_000,
                    "jitter_percent": 0,
                },
                now=11.2,
            )
            self.assertEqual(replay, accepted)
            self.assertEqual(self.state.nodes()[0]["poll_interval_ms"], 2_000)
            self.assertEqual(self.state.nodes()[0]["tasks_completed"], 1)

            with self.assertRaises(LabError) as conflict:
                self.state.submit_result(
                    self.node["id"],
                    self.session_token,
                    task["id"],
                    "completed",
                    {
                        "previous_interval_ms": 1_000,
                        "new_interval_ms": 2_000,
                        "jitter_percent": False,
                    },
                    now=11.3,
                )
            self.assertEqual(conflict.exception.code, "result_conflict")

    def test_result_ack_tombstone_retention_is_bounded(self) -> None:
        with (
            patch("c2lab.core.MAX_TASKS", 1),
            patch("c2lab.core.MAX_TASK_RESULT_TOMBSTONES", 1),
        ):
            first = self.state.queue_task(self.node["id"], "PING", {})
            self.state.poll_node(self.node["id"], self.session_token, now=11.0)
            self.state.submit_result(
                self.node["id"],
                self.session_token,
                first["id"],
                "completed",
                {"reply": "PONG"},
                now=11.1,
            )
            second = self.state.queue_task(self.node["id"], "PING", {})
            self.state.poll_node(self.node["id"], self.session_token, now=12.0)
            accepted = self.state.submit_result(
                self.node["id"],
                self.session_token,
                second["id"],
                "completed",
                {"reply": "PONG"},
                now=12.1,
            )
            self.state.queue_task(self.node["id"], "PING", {})

            with self.assertRaises(LabError) as evicted:
                self.state.submit_result(
                    self.node["id"],
                    self.session_token,
                    first["id"],
                    "completed",
                    {"reply": "PONG"},
                    now=12.2,
                )
            self.assertEqual(evicted.exception.code, "not_found")
            self.assertEqual(
                self.state.submit_result(
                    self.node["id"],
                    self.session_token,
                    second["id"],
                    "completed",
                    {"reply": "PONG"},
                    now=12.3,
                ),
                accepted,
            )

    def test_task_creation_idempotency_prevents_duplicate_queue_entries(self) -> None:
        key = "operator-request-0001"
        first = self.state.queue_task(
            self.node["id"],
            "ECHO_TEXT",
            {"text": "idempotent"},
            idempotency_key=key,
            queue_ttl_seconds=60,
            now=20.0,
        )
        replay = self.state.queue_task(
            self.node["id"],
            "ECHO_TEXT",
            {"text": "idempotent"},
            idempotency_key=key,
            queue_ttl_seconds=60,
            now=21.0,
        )

        self.assertEqual(replay, first)
        self.assertEqual(len(self.state.tasks()), 1)
        self.assertNotIn("idempotency_key", first)
        with self.assertRaises(LabError) as context:
            self.state.queue_task(
                self.node["id"],
                "ECHO_TEXT",
                {"text": "different"},
                idempotency_key=key,
                queue_ttl_seconds=60,
                now=22.0,
            )
        self.assertEqual(context.exception.status, 409)
        self.assertEqual(context.exception.code, "idempotency_conflict")

    def test_dispatch_fifo_uses_creation_sequence_when_wall_clock_moves_back(self) -> None:
        with patch("c2lab.core.utc_now", return_value="2030-01-01T00:00:00.000Z"):
            first = self.state.queue_task(self.node["id"], "PING", {})
        with patch("c2lab.core.utc_now", return_value="2020-01-01T00:00:00.000Z"):
            second = self.state.queue_task(self.node["id"], "PING", {})

        polled = self.state.poll_node(self.node["id"], self.session_token, now=11.0)
        self.assertEqual(polled["task"]["id"], first["id"])
        self.assertNotEqual(polled["task"]["id"], second["id"])

    def test_retention_prunes_oldest_sequence_when_wall_clock_moves_back(self) -> None:
        with patch("c2lab.core.MAX_TASKS", 2):
            with patch("c2lab.core.utc_now", return_value="2030-01-01T00:00:00.000Z"):
                first = self.state.queue_task(self.node["id"], "PING", {})
            self.state.poll_node(self.node["id"], self.session_token, now=11.0)
            self.state.submit_result(
                self.node["id"],
                self.session_token,
                first["id"],
                "completed",
                {"reply": "PONG"},
                now=11.1,
            )
            with patch("c2lab.core.utc_now", return_value="2020-01-01T00:00:00.000Z"):
                second = self.state.queue_task(self.node["id"], "PING", {})
            self.state.poll_node(self.node["id"], self.session_token, now=12.0)
            self.state.submit_result(
                self.node["id"],
                self.session_token,
                second["id"],
                "completed",
                {"reply": "PONG"},
                now=12.1,
            )

            replacement = self.state.queue_task(self.node["id"], "PING", {})
            retained_ids = {task["id"] for task in self.state.tasks()}
            self.assertNotIn(first["id"], retained_ids)
            self.assertIn(second["id"], retained_ids)
            self.assertIn(replacement["id"], retained_ids)

    def test_task_records_creator_and_idempotency_is_actor_scoped(self) -> None:
        key = "operator-request-actor-0001"
        first = self.state.queue_task(
            self.node["id"],
            "PING",
            {},
            idempotency_key=key,
            actor="operator-alpha",
        )

        self.assertEqual(first["created_by"], "operator-alpha")
        self.assertEqual(self.state.tasks()[0]["created_by"], "operator-alpha")
        with self.assertRaises(LabError) as context:
            self.state.queue_task(
                self.node["id"],
                "PING",
                {},
                idempotency_key=key,
                actor="operator-beta",
            )
        self.assertEqual(context.exception.status, 409)
        self.assertEqual(context.exception.code, "idempotency_conflict")

    def test_queued_task_expires_but_dispatched_task_does_not(self) -> None:
        queued = self.state.queue_task(
            self.node["id"],
            "PING",
            {},
            queue_ttl_seconds=MIN_QUEUE_TTL_SECONDS,
            now=20.0,
        )
        self.state.expire(now=20.0 + MIN_QUEUE_TTL_SECONDS)
        expired = next(task for task in self.state.tasks() if task["id"] == queued["id"])
        self.assertEqual(expired["status"], "expired")
        self.assertEqual(expired["result"], {"reason": "queue_ttl_exceeded"})
        self.assertIn("task.expired", [event["kind"] for event in self.state.events()])
        self.assertEqual(self.state.overview()["counts"]["tasks_expired"], 1)

        dispatched = self.state.queue_task(
            self.node["id"],
            "PING",
            {},
            queue_ttl_seconds=MIN_QUEUE_TTL_SECONDS,
            now=30.0,
        )
        self.state.poll_node(self.node["id"], self.session_token, now=31.0)
        self.state.expire(now=30.0 + MIN_QUEUE_TTL_SECONDS)
        retained = next(task for task in self.state.tasks() if task["id"] == dispatched["id"])
        self.assertEqual(retained["status"], "dispatched")

    def test_queued_task_cancellation_is_idempotent_and_never_dispatches(self) -> None:
        queued = self.state.queue_task(self.node["id"], "PING", {}, now=20.0)
        cancelled = self.state.cancel_task(queued["id"])
        replayed = self.state.cancel_task(queued["id"])

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["result"], {"reason": "operator_cancelled"})
        self.assertEqual(replayed, cancelled)
        self.assertEqual(self.state.overview()["counts"]["tasks_cancelled"], 1)
        self.assertIsNone(self.state.poll_node(self.node["id"], self.session_token, now=21.0)["task"])
        self.assertEqual(
            len([event for event in self.state.events() if event["kind"] == "task.cancelled"]),
            1,
        )

        active = self.state.queue_task(self.node["id"], "PING", {}, now=22.0)
        self.state.poll_node(self.node["id"], self.session_token, now=22.1)
        with self.assertRaises(LabError) as context:
            self.state.cancel_task(active["id"])
        self.assertEqual(context.exception.code, "task_not_cancellable")

    def test_operator_actions_record_actor_identity(self) -> None:
        queued = self.state.queue_task(
            self.node["id"],
            "PING",
            {},
            actor="operator-primary",
        )
        self.state.cancel_task(queued["id"], actor="operator-admin")

        queued_event = next(
            event for event in self.state.events() if event["kind"] == "task.queued"
        )
        cancelled_event = next(
            event for event in self.state.events() if event["kind"] == "task.cancelled"
        )
        self.assertEqual(queued_event["actor"], "operator-primary")
        self.assertEqual(cancelled_event["actor"], "operator-admin")

        self.state.reset(actor="viewer-a")
        self.assertEqual(self.state.events()[0]["kind"], "lab.reset")
        self.assertEqual(self.state.events()[0]["actor"], "viewer-a")
        actors_by_action = {
            entry["action"]: entry["actor"]
            for entry in self.state.audit()
            if entry["action"] in {"task.queued", "task.cancelled", "lab.reset"}
        }
        self.assertEqual(
            actors_by_action,
            {
                "task.queued": "operator-primary",
                "task.cancelled": "operator-admin",
                "lab.reset": "viewer-a",
            },
        )

    def test_operator_actor_validation_is_strict_and_non_mutating(self) -> None:
        queued = self.state.queue_task(self.node["id"], "PING", {})
        baseline_nodes = self.state.nodes()
        baseline_tasks = self.state.tasks()
        baseline_events = self.state.events()
        baseline_audit = self.state.audit()

        invalid_actors = (
            "",
            "a" * 49,
            "operator primary",
            "operator.primary",
            "operator:primary",
            "operator/primary",
            "オペレーター",
            None,
            123,
        )
        for invalid_actor in invalid_actors:
            with self.subTest(method="queue", actor=invalid_actor), self.assertRaises(LabError):
                self.state.queue_task(
                    self.node["id"],
                    "PING",
                    {},
                    actor=invalid_actor,
                )
            with self.subTest(method="cancel", actor=invalid_actor), self.assertRaises(LabError):
                self.state.cancel_task(queued["id"], actor=invalid_actor)
            with self.subTest(method="reset", actor=invalid_actor), self.assertRaises(LabError):
                self.state.reset(actor=invalid_actor)

        self.assertEqual(self.state.nodes(), baseline_nodes)
        self.assertEqual(self.state.tasks(), baseline_tasks)
        self.assertEqual(self.state.events(), baseline_events)
        self.assertEqual(self.state.audit(), baseline_audit)

    def test_operator_notes_are_plain_text_attributed_and_audit_redacted(self) -> None:
        marker = "handoff-note-not-for-audit"
        note = self.state.post_operator_note(marker, actor="operator-alpha")

        self.assertEqual(note["kind"], "operator.note")
        self.assertEqual(note["actor"], "operator-alpha")
        self.assertEqual(note["data"], {"message": marker})
        note_audit = next(
            entry for entry in self.state.audit() if entry["action"] == "operator.note"
        )
        self.assertEqual(note_audit["actor"], "operator-alpha")
        self.assertNotIn(marker, json.dumps(note_audit))
        report = self.state.report()
        self.assertNotIn(marker, json.dumps(report))
        self.assertEqual(report["counts"]["operator_notes_retained"], 1)
        self.assertEqual(report["retention"]["operator_notes"], MAX_OPERATOR_NOTES_RETAINED)

    def test_operator_note_idempotency_and_conflict(self) -> None:
        key = "operator-note-request-0001"
        first = self.state.post_operator_note(
            "same note",
            actor="operator-alpha",
            idempotency_key=key,
        )
        replay = self.state.post_operator_note(
            "same note",
            actor="operator-alpha",
            idempotency_key=key,
        )

        self.assertEqual(replay, first)
        self.assertEqual(
            len([event for event in self.state.events() if event["kind"] == "operator.note"]),
            1,
        )
        self.assertEqual(
            len([entry for entry in self.state.audit() if entry["action"] == "operator.note"]),
            1,
        )
        for actor, message in (
            ("operator-alpha", "different note"),
            ("operator-beta", "same note"),
        ):
            with self.subTest(actor=actor, message=message), self.assertRaises(LabError) as context:
                self.state.post_operator_note(
                    message,
                    actor=actor,
                    idempotency_key=key,
                )
            self.assertEqual(context.exception.status, 409)
            self.assertEqual(context.exception.code, "idempotency_conflict")

    def test_operator_note_validation_is_non_mutating_and_bounded(self) -> None:
        baseline_events = self.state.events()
        baseline_audit = self.state.audit()
        invalid_messages = ("", "   ", "x" * (MAX_OPERATOR_NOTE_LENGTH + 1), None, "bad\x00note")
        for message in invalid_messages:
            with self.subTest(message=message), self.assertRaises(LabError):
                self.state.post_operator_note(message, actor="operator-alpha")
        with self.assertRaises(LabError):
            self.state.post_operator_note("valid", actor="operator alpha")
        self.assertEqual(self.state.events(), baseline_events)
        self.assertEqual(self.state.audit(), baseline_audit)

        for index in range(MAX_OPERATOR_NOTES_RETAINED):
            self.state.post_operator_note(
                f"note {index}",
                actor="operator-alpha",
            )
        with self.assertRaises(LabError) as context:
            self.state.post_operator_note("one too many", actor="operator-alpha")
        self.assertEqual(context.exception.status, 429)
        self.assertEqual(context.exception.code, "note_limit")

    def test_concurrent_operator_notes_have_unique_monotonic_sequences(self) -> None:
        def append(index: int) -> dict[str, object]:
            return self.state.post_operator_note(
                f"concurrent note {index}",
                actor=f"operator-{index % 4}",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            notes = list(pool.map(append, range(32)))

        sequences = [int(note["sequence"]) for note in notes]
        self.assertEqual(len(sequences), len(set(sequences)))
        retained_sequences = [
            event["sequence"]
            for event in reversed(self.state.events())
            if event["kind"] == "operator.note"
        ]
        self.assertEqual(retained_sequences, sorted(sequences))

    def test_sync_returns_atomic_snapshot_and_incremental_histories(self) -> None:
        initial = self.state.sync()
        self.assertRegex(initial["stream_id"], r"^stream-[0-9a-f]{24}$")
        self.assertEqual(initial["nodes"], self.state.nodes())
        self.assertEqual(initial["tasks"], [])
        self.assertEqual(
            [event["sequence"] for event in initial["events"]],
            sorted(event["sequence"] for event in initial["events"]),
        )
        self.assertEqual(
            [entry["sequence"] for entry in initial["audit"]],
            sorted(entry["sequence"] for entry in initial["audit"]),
        )
        self.assertEqual(initial["cursors"], initial["high_watermarks"])
        self.assertEqual(initial["cursor_reset"], {"events": False, "audit": False})

        unchanged = self.state.sync(
            events_after=initial["cursors"]["events"],
            audit_after=initial["cursors"]["audit"],
        )
        self.assertEqual(unchanged["stream_id"], initial["stream_id"])
        self.assertEqual(unchanged["events"], [])
        self.assertEqual(unchanged["audit"], [])

        queued = self.state.queue_task(
            self.node["id"],
            "PING",
            {},
            actor="operator-alpha",
        )
        delta = self.state.sync(
            events_after=initial["cursors"]["events"],
            audit_after=initial["cursors"]["audit"],
        )
        self.assertEqual(delta["tasks"][0]["id"], queued["id"])
        self.assertEqual(delta["tasks"][0]["created_by"], "operator-alpha")
        self.assertEqual([event["kind"] for event in delta["events"]], ["task.queued"])
        self.assertEqual([entry["action"] for entry in delta["audit"]], ["task.queued"])

        other_process = LabState().sync()
        self.assertNotEqual(other_process["stream_id"], initial["stream_id"])

    def test_sync_paginates_and_detects_retention_or_future_cursor_gaps(self) -> None:
        with self.state._lock:
            for index in range(MAX_EVENTS + 5):
                self.state._record_locked("test.event", data={"index": index})

        first = self.state.sync(events_after=0, audit_after=0, limit=10)
        self.assertTrue(first["cursor_reset"]["events"])
        self.assertTrue(first["has_more"]["events"])
        self.assertEqual(len(first["events"]), 10)
        self.assertGreater(first["oldest_available"]["events"], 1)
        self.assertEqual(first["cursors"]["events"], first["events"][-1]["sequence"])

        second = self.state.sync(
            events_after=first["cursors"]["events"],
            audit_after=first["cursors"]["audit"],
            limit=10,
        )
        self.assertFalse(second["cursor_reset"]["events"])
        self.assertEqual(
            second["events"][0]["sequence"],
            first["events"][-1]["sequence"] + 1,
        )

        future = self.state.sync(
            events_after=MAX_SYNC_CURSOR,
            audit_after=MAX_SYNC_CURSOR,
            limit=MAX_SYNC_PAGE_SIZE,
        )
        self.assertEqual(future["cursor_reset"], {"events": True, "audit": True})

    def test_sync_validates_cursor_and_limit_without_mutation(self) -> None:
        baseline = self.state.sync()
        baseline.pop("generated_at")
        invalid_calls = (
            {"events_after": -1},
            {"events_after": True},
            {"events_after": "1"},
            {"events_after": MAX_SYNC_CURSOR + 1},
            {"audit_after": -1},
            {"limit": 0},
            {"limit": MAX_SYNC_PAGE_SIZE + 1},
            {"limit": True},
        )
        for arguments in invalid_calls:
            with self.subTest(arguments=arguments), self.assertRaises(LabError):
                self.state.sync(**arguments)
        after = self.state.sync()
        after.pop("generated_at")
        self.assertEqual(after, baseline)

    def test_sync_requires_event_history_replacement_across_lab_reset(self) -> None:
        before_reset = self.state.sync()
        prior_event_cursor = before_reset["cursors"]["events"]
        prior_audit_cursor = before_reset["cursors"]["audit"]

        self.state.reset(actor="operator-admin")
        after_reset = self.state.sync(
            events_after=prior_event_cursor,
            audit_after=prior_audit_cursor,
        )

        self.assertTrue(after_reset["cursor_reset"]["events"])
        self.assertFalse(after_reset["cursor_reset"]["audit"])
        self.assertEqual([event["kind"] for event in after_reset["events"]], ["lab.reset"])
        self.assertEqual([entry["action"] for entry in after_reset["audit"]], ["lab.reset"])

    def test_queue_controls_validate_bounds_and_default(self) -> None:
        defaulted = self.state.queue_task(self.node["id"], "PING", {}, now=20.0)
        self.assertEqual(defaulted["queue_ttl_seconds"], DEFAULT_QUEUE_TTL_SECONDS)

        for invalid_ttl in (
            None,
            True,
            MIN_QUEUE_TTL_SECONDS - 1,
            MAX_QUEUE_TTL_SECONDS + 1,
            "60",
        ):
            with self.subTest(queue_ttl_seconds=invalid_ttl), self.assertRaises(LabError):
                self.state.queue_task(
                    self.node["id"],
                    "PING",
                    {},
                    queue_ttl_seconds=invalid_ttl,
                    now=20.0,
                )
        for invalid_key in ("short", "contains space", 123):
            with self.subTest(idempotency_key=invalid_key), self.assertRaises(LabError):
                self.state.queue_task(
                    self.node["id"],
                    "PING",
                    {},
                    idempotency_key=invalid_key,
                    now=20.0,
                )

    def test_only_one_task_is_dispatched_per_node(self) -> None:
        first = self.state.queue_task(self.node["id"], "PING", {})
        second = self.state.queue_task(self.node["id"], "RUNTIME_STATUS", {})

        first_poll = self.state.poll_node(self.node["id"], self.session_token, now=12.0)
        second_poll = self.state.poll_node(self.node["id"], self.session_token, now=12.1)

        self.assertEqual(first_poll["task"]["id"], first["id"])
        self.assertEqual(second_poll["task"]["id"], first["id"])
        self.assertEqual(second_poll["task"]["delivery_attempts"], 2)
        statuses = {task["id"]: task["status"] for task in self.state.tasks()}
        self.assertEqual(statuses[first["id"]], "dispatched")
        self.assertEqual(statuses[second["id"]], "queued")

    def test_second_task_dispatches_after_first_result(self) -> None:
        first = self.state.queue_task(self.node["id"], "PING", {})
        second = self.state.queue_task(self.node["id"], "ECHO_TEXT", {"text": "hello"})
        self.state.poll_node(self.node["id"], self.session_token, now=13.0)
        self.state.submit_result(
            self.node["id"], self.session_token, first["id"], "completed", {"reply": "PONG"}, now=13.1
        )

        polled = self.state.poll_node(self.node["id"], self.session_token, now=13.2)
        self.assertEqual(polled["task"]["id"], second["id"])

    def test_invalid_node_session_is_rejected(self) -> None:
        with self.assertRaises(LabError) as context:
            self.state.poll_node(self.node["id"], "not-the-session-token", now=14.0)
        self.assertEqual(context.exception.status, 401)
        self.assertEqual(context.exception.code, "invalid_node_session")

    def test_profile_capabilities_are_enforced(self) -> None:
        enrollment = self.state.enroll_node(
            "basic-node",
            "0.2.0",
            "basic",
            capabilities_for_profile("basic"),
            1_000,
            now=20.0,
        )
        with self.assertRaises(LabError) as context:
            self.state.queue_task(enrollment["node"]["id"], "WAIT", {"milliseconds": 5})
        self.assertEqual(context.exception.status, 409)
        self.assertEqual(context.exception.code, "capability_denied")

        with self.assertRaises(LabError) as playbook_context:
            self.state.queue_task(
                self.node["id"],
                "RUN_PLAYBOOK",
                {"playbook": "DISCOVERY_FIXTURES"},
            )
        self.assertEqual(playbook_context.exception.code, "capability_denied")

    def test_purple_lab_profile_accepts_only_fixed_playbooks(self) -> None:
        enrollment = self.state.enroll_node(
            "purple-node",
            "0.3.0",
            "purple_lab",
            capabilities_for_profile("purple_lab"),
            1_000,
            now=20.0,
        )
        task = self.state.queue_task(
            enrollment["node"]["id"],
            "RUN_PLAYBOOK",
            {"playbook": "DISCOVERY_FIXTURES"},
        )
        self.assertEqual(task["payload"], {"playbook": "DISCOVERY_FIXTURES"})

        for invalid_payload in (
            {"playbook": "CUSTOM"},
            {"playbook": "DISCOVERY_FIXTURES", "path": "/tmp"},
            {"playbook": ["DISCOVERY_FIXTURES"]},
        ):
            with self.subTest(payload=invalid_payload), self.assertRaises(LabError):
                self.state.queue_task(
                    enrollment["node"]["id"],
                    "RUN_PLAYBOOK",
                    invalid_payload,
                )

    def test_purple_lab_queue_has_a_smaller_bound(self) -> None:
        enrollment = self.state.enroll_node(
            "purple-queue-node",
            "0.3.0",
            "purple_lab",
            capabilities_for_profile("purple_lab"),
            1_000,
            now=20.0,
        )
        for _ in range(MAX_QUEUED_PLAYBOOKS_PER_NODE):
            self.state.queue_task(
                enrollment["node"]["id"],
                "RUN_PLAYBOOK",
                {"playbook": "DISCOVERY_FIXTURES"},
            )
        with self.assertRaises(LabError) as context:
            self.state.queue_task(
                enrollment["node"]["id"],
                "RUN_PLAYBOOK",
                {"playbook": "DISCOVERY_FIXTURES"},
            )
        self.assertEqual(context.exception.status, 429)
        self.assertEqual(context.exception.code, "playbook_queue_limit")

    def test_unknown_task_type_is_rejected(self) -> None:
        with self.assertRaises(LabError) as context:
            self.state.queue_task(self.node["id"], "RUN_COMMAND", {"command": "whoami"})
        self.assertEqual(context.exception.code, "unsupported_task_type")

    def test_payload_schema_and_bounds_are_strict(self) -> None:
        invalid_tasks = (
            ("PING", {"unexpected": True}),
            ("ECHO_TEXT", {"text": "ok", "extra": 1}),
            ("WAIT", {"milliseconds": 2_001}),
            ("GENERATE_EVENT", {"category": "training", "severity": "critical", "message": "x"}),
            ("GENERATE_EVENT", {"category": [], "severity": "info", "message": "x"}),
            ("GENERATE_EVENT", {"category": "training", "severity": {}, "message": "x"}),
            ("RUN_PLAYBOOK", {"playbook": "CUSTOM"}),
        )
        for task_type, payload in invalid_tasks:
            with self.subTest(task_type=task_type, payload=payload), self.assertRaises(LabError):
                self.state.queue_task(self.node["id"], task_type, payload)

    def test_result_status_type_is_rejected_without_internal_error(self) -> None:
        task = self.state.queue_task(self.node["id"], "PING", {})
        self.state.poll_node(self.node["id"], self.session_token, now=15.0)
        with self.assertRaises(LabError):
            self.state.submit_result(
                self.node["id"], self.session_token, task["id"], ["completed"], {"reply": "PONG"}, now=15.1
            )

    def test_generated_event_is_centrally_recorded(self) -> None:
        task = self.state.queue_task(
            self.node["id"],
            "GENERATE_EVENT",
            {"category": "training", "severity": "warning", "message": "demo alert"},
        )
        self.state.poll_node(self.node["id"], self.session_token, now=30.0)
        self.state.submit_result(
            self.node["id"],
            self.session_token,
            task["id"],
            "completed",
            {
                "recorded": True,
                "category": "training",
                "severity": "warning",
                "message": "demo alert",
            },
            now=30.1,
        )

        event = next(event for event in self.state.events() if event["kind"] == "node.generated_event")
        self.assertEqual(event["node_id"], self.node["id"])
        self.assertEqual(event["task_id"], task["id"])
        self.assertEqual(event["level"], "warning")

    def test_task_specific_result_schema_rejects_forged_node_output(self) -> None:
        task = self.state.queue_task(self.node["id"], "ECHO_TEXT", {"text": "expected"})
        self.state.poll_node(self.node["id"], self.session_token, now=30.0)

        for status, result in (
            ("completed", {"echo": "host-derived-value"}),
            ("completed", {"echo": "expected", "path": "/Users/example"}),
            ("failed", {"error_code": "CUSTOM_ERROR"}),
            ("failed", {"error": "arbitrary node text"}),
        ):
            with self.subTest(status=status, result=result), self.assertRaises(LabError):
                self.state.submit_result(
                    self.node["id"],
                    self.session_token,
                    task["id"],
                    status,
                    result,
                    now=30.1,
                )

        accepted = self.state.submit_result(
            self.node["id"],
            self.session_token,
            task["id"],
            "failed",
            {"error_code": "HANDLER_FAILED"},
            now=30.2,
        )
        self.assertEqual(accepted["result"], {"error_code": "HANDLER_FAILED"})
        rejected_events = [
            event for event in self.state.events() if event["kind"] == "task.result_rejected"
        ]
        self.assertEqual(len(rejected_events), 4)
        serialized = json.dumps({"events": rejected_events, "audit": self.state.audit()})
        self.assertNotIn("host-derived-value", serialized)
        self.assertNotIn("/Users/example", serialized)

    def test_event_sequences_are_monotonic_and_correlation_is_top_level(self) -> None:
        task = self.state.queue_task(self.node["id"], "PING", {})
        self.state.poll_node(self.node["id"], self.session_token, now=31.0)

        chronological = list(reversed(self.state.events()))
        sequences = [event["sequence"] for event in chronological]
        self.assertEqual(sequences, sorted(sequences))
        self.assertEqual(len(sequences), len(set(sequences)))
        queued_event = next(event for event in chronological if event["kind"] == "task.queued")
        self.assertEqual(queued_event["correlation_id"], task["correlation_id"])
        self.assertEqual(queued_event["data"]["correlation_id"], task["correlation_id"])

        last_sequence = sequences[-1]
        self.state.reset()
        self.assertGreater(self.state.events()[0]["sequence"], last_sequence)

    def test_audit_and_report_are_bounded_structured_and_redacted(self) -> None:
        marker = "do-not-copy-this-payload"
        task = self.state.queue_task(self.node["id"], "ECHO_TEXT", {"text": marker})
        self.state.poll_node(self.node["id"], self.session_token, now=32.0)
        self.state.submit_result(
            self.node["id"], self.session_token, task["id"], "completed", {"echo": marker}, now=32.1
        )

        expected_fields = {
            "id",
            "sequence",
            "time",
            "actor",
            "action",
            "node_id",
            "task_id",
            "correlation_id",
            "task_type",
            "from_state",
            "to_state",
            "outcome",
            "reason",
        }
        audit = self.state.audit()
        self.assertTrue({"node.enrolled", "task.queued", "task.dispatched", "task.completed"}.issubset(
            {entry["action"] for entry in audit}
        ))
        self.assertTrue(all(set(entry) == expected_fields for entry in audit))
        audit_json = json.dumps(audit)
        self.assertNotIn(marker, audit_json)
        self.assertNotIn(self.session_token, audit_json)

        report = self.state.report()
        report_json = json.dumps(report)
        self.assertNotIn(marker, report_json)
        self.assertNotIn(self.session_token, report_json)
        report_task = next(item for item in report["tasks"] if item["id"] == task["id"])
        self.assertNotIn("payload", report_task)
        self.assertNotIn("result", report_task)
        self.assertEqual(report_task["correlation_id"], task["correlation_id"])
        self.assertEqual(report_task["created_by"], "operator")

        self.state.reset()
        self.assertIn("task.completed", {entry["action"] for entry in self.state.audit()})
        for _ in range(MAX_AUDIT_ENTRIES + 5):
            self.state.reset()
        bounded = self.state.audit()
        self.assertEqual(len(bounded), MAX_AUDIT_ENTRIES)
        self.assertEqual(bounded[0]["sequence"], self.state.report()["sequences"]["audit"])

    def test_dispatched_task_times_out_and_rejects_late_result(self) -> None:
        task = self.state.queue_task(self.node["id"], "PING", {})
        self.state.poll_node(self.node["id"], self.session_token, now=40.0)

        self.state.expire(now=40.0 + TASK_TIMEOUT_SECONDS)
        timed_out = next(item for item in self.state.tasks() if item["id"] == task["id"])
        self.assertEqual(timed_out["status"], "timeout")
        self.assertEqual(self.state.nodes()[0]["tasks_failed"], 1)
        with self.assertRaises(LabError) as context:
            self.state.submit_result(
                self.node["id"], self.session_token, task["id"], "completed", {"reply": "late"}, now=50.0
            )
        self.assertEqual(context.exception.status, 409)

    def test_node_becomes_stale_and_poll_recovers_it(self) -> None:
        self.state.expire(now=10.0 + NODE_STALE_SECONDS)
        self.assertEqual(self.state.nodes()[0]["status"], "offline")

        recovered = self.state.poll_node(self.node["id"], self.session_token, now=19.0)
        self.assertEqual(recovered["node"]["status"], "online")
        self.assertIn("node.online", [event["kind"] for event in self.state.events()])
        lifecycle_audit = {
            entry["action"]: entry
            for entry in self.state.audit()
            if entry["action"] in {"node.stale", "node.online"}
        }
        self.assertEqual(lifecycle_audit["node.stale"]["from_state"], "online")
        self.assertEqual(lifecycle_audit["node.stale"]["to_state"], "offline")
        self.assertEqual(lifecycle_audit["node.online"]["from_state"], "offline")
        self.assertEqual(lifecycle_audit["node.online"]["to_state"], "online")

    def test_stale_session_expires_after_ttl_and_fails_queued_tasks(self) -> None:
        queued = [
            self.state.queue_task(self.node["id"], "PING", {}),
            self.state.queue_task(self.node["id"], "ECHO_TEXT", {"text": "expire-me"}),
        ]
        stale_at = 10.0 + NODE_STALE_SECONDS
        self.state.expire(now=stale_at)
        stale_node = self.state.nodes()[0]
        self.assertEqual(stale_node["status"], "offline")
        self.assertTrue(stale_node["session_active"])

        self.state.expire(now=stale_at + STALE_SESSION_TTL_SECONDS - 0.001)
        self.assertTrue(self.state.nodes()[0]["session_active"])
        self.assertTrue(all(task["status"] == "queued" for task in self.state.tasks()))

        self.state.expire(now=stale_at + STALE_SESSION_TTL_SECONDS)
        expired_node = self.state.nodes()[0]
        self.assertEqual(expired_node["status"], "offline")
        self.assertFalse(expired_node["session_active"])
        self.assertEqual(expired_node["tasks_failed"], len(queued))
        self.assertFalse(self.state.authenticate_node(self.node["id"], self.session_token))

        tasks = {task["id"]: task for task in self.state.tasks()}
        for task in queued:
            self.assertEqual(tasks[task["id"]]["status"], "failed")
            self.assertEqual(
                tasks[task["id"]]["result"],
                {"error": "node session expired before task completion"},
            )

        self.assertIn("node.session_expired", [event["kind"] for event in self.state.events()])
        session_audit = next(
            entry for entry in self.state.audit() if entry["action"] == "node.session_expired"
        )
        self.assertEqual(session_audit["from_state"], "offline")
        self.assertEqual(session_audit["to_state"], "offline")
        self.assertEqual(session_audit["reason"], "offline_session_ttl_exceeded")
        task_audit = [
            entry
            for entry in self.state.audit()
            if entry["action"] == "task.failed" and entry["reason"] == "node_session_expired"
        ]
        self.assertEqual(
            {entry["task_id"] for entry in task_audit},
            {task["id"] for task in queued},
        )

    def test_expired_stale_sessions_release_node_capacity(self) -> None:
        state = LabState()
        original_ids = set()
        for index in range(MAX_NODES):
            enrollment = state.enroll_node(
                f"stale-node-{index}",
                "0.2.0",
                "basic",
                capabilities_for_profile("basic"),
                1_000,
                now=0.0,
            )
            original_ids.add(enrollment["node"]["id"])

        state.expire(now=NODE_STALE_SECONDS + STALE_SESSION_TTL_SECONDS)
        self.assertTrue(all(not node["session_active"] for node in state.nodes()))

        replacement = state.enroll_node(
            "replacement-after-expiry",
            "0.2.0",
            "basic",
            capabilities_for_profile("basic"),
            1_000,
            now=NODE_STALE_SECONDS + STALE_SESSION_TTL_SECONDS + 1.0,
        )
        retained_ids = {node["id"] for node in state.nodes()}
        self.assertEqual(len(retained_ids), MAX_NODES)
        self.assertIn(replacement["node"]["id"], retained_ids)
        self.assertEqual(len(original_ids - retained_ids), 1)
        self.assertIn("node.pruned", [event["kind"] for event in state.events()])

    def test_disconnect_marks_node_offline(self) -> None:
        queued = self.state.queue_task(self.node["id"], "PING", {})
        disconnected = self.state.disconnect_node(self.node["id"], self.session_token)
        self.assertEqual(disconnected["status"], "offline")
        self.assertFalse(disconnected["session_active"])
        self.assertFalse(self.state.authenticate_node(self.node["id"], self.session_token))
        with self.assertRaises(LabError) as context:
            self.state.queue_task(self.node["id"], "PING", {})
        self.assertEqual(context.exception.code, "node_disconnected")
        task = next(item for item in self.state.tasks() if item["id"] == queued["id"])
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["result"]["error"], "node session closed before task completion")

    def test_reset_invalidates_node_sessions(self) -> None:
        self.state.queue_task(self.node["id"], "PING", {})
        self.state.reset()

        overview = self.state.overview()
        self.assertEqual(overview["nodes"], [])
        self.assertEqual(overview["tasks"], [])
        self.assertEqual([event["kind"] for event in overview["events"]], ["lab.reset"])
        self.assertFalse(self.state.authenticate_node(self.node["id"], self.session_token))

    def test_node_limit_is_enforced(self) -> None:
        state = LabState()
        for index in range(MAX_NODES):
            state.enroll_node(
                f"limit-node-{index}",
                "0.2.0",
                "basic",
                capabilities_for_profile("basic"),
                1_000,
            )
        with self.assertRaises(LabError) as context:
            state.enroll_node(
                "one-node-too-many",
                "0.2.0",
                "basic",
                capabilities_for_profile("basic"),
                1_000,
            )
        self.assertEqual(context.exception.code, "node_limit")

    def test_oldest_closed_node_record_is_pruned_at_limit(self) -> None:
        state = LabState()
        first_id = None
        for index in range(MAX_NODES):
            enrollment = state.enroll_node(
                f"closed-node-{index}",
                "0.2.0",
                "basic",
                capabilities_for_profile("basic"),
                1_000,
                now=float(index),
            )
            if first_id is None:
                first_id = enrollment["node"]["id"]
            state.disconnect_node(enrollment["node"]["id"], enrollment["session_token"])

        replacement = state.enroll_node(
            "replacement-node",
            "0.2.0",
            "basic",
            capabilities_for_profile("basic"),
            1_000,
            now=100.0,
        )
        node_ids = {node["id"] for node in state.nodes()}
        self.assertEqual(len(node_ids), MAX_NODES)
        self.assertNotIn(first_id, node_ids)
        self.assertIn(replacement["node"]["id"], node_ids)
        self.assertIn("node.pruned", [event["kind"] for event in state.events()])

    def test_per_node_and_total_task_limits_are_enforced(self) -> None:
        state = LabState()
        for index in range(MAX_TASKS // MAX_QUEUED_TASKS_PER_NODE):
            enrollment = state.enroll_node(
                f"queue-node-{index}",
                "0.2.0",
                "basic",
                capabilities_for_profile("basic"),
                1_000,
            )
            for _ in range(MAX_QUEUED_TASKS_PER_NODE):
                state.queue_task(enrollment["node"]["id"], "PING", {})

        self.assertEqual(len(state.tasks()), MAX_TASKS)
        overflow_node = state.enroll_node(
            "overflow-node",
            "0.2.0",
            "basic",
            capabilities_for_profile("basic"),
            1_000,
        )["node"]
        task_ids = {task["id"] for task in state.tasks()}
        with self.assertRaises(LabError) as context:
            state.queue_task(overflow_node["id"], "PING", {})
        self.assertEqual(context.exception.code, "task_limit")
        self.assertEqual({task["id"] for task in state.tasks()}, task_ids)
        self.assertNotIn("task.pruned", [event["kind"] for event in state.events()])

    def test_oldest_terminal_task_is_pruned_at_total_limit(self) -> None:
        state = LabState()
        enrollments = [
            state.enroll_node(
                f"retention-node-{index}",
                "0.2.0",
                "basic",
                capabilities_for_profile("basic"),
                1_000,
                now=0.0,
            )
            for index in range(MAX_TASKS // MAX_QUEUED_TASKS_PER_NODE)
        ]
        first_node = enrollments[0]
        oldest_terminal = state.queue_task(first_node["node"]["id"], "PING", {})
        state.poll_node(first_node["node"]["id"], first_node["session_token"], now=1.0)
        state.submit_result(
            first_node["node"]["id"],
            first_node["session_token"],
            oldest_terminal["id"],
            "completed",
            {"reply": "PONG"},
            now=1.1,
        )
        newer_terminal = state.queue_task(first_node["node"]["id"], "PING", {})
        state.poll_node(first_node["node"]["id"], first_node["session_token"], now=2.0)
        state.submit_result(
            first_node["node"]["id"],
            first_node["session_token"],
            newer_terminal["id"],
            "completed",
            {"reply": "PONG"},
            now=2.1,
        )

        queued_ids = set()
        for index, enrollment in enumerate(enrollments):
            queue_count = (
                MAX_QUEUED_TASKS_PER_NODE - 2
                if index == 0
                else MAX_QUEUED_TASKS_PER_NODE
            )
            for _ in range(queue_count):
                task = state.queue_task(enrollment["node"]["id"], "PING", {})
                queued_ids.add(task["id"])
        self.assertEqual(len(state.tasks()), MAX_TASKS)

        replacement = state.queue_task(first_node["node"]["id"], "PING", {})
        retained_ids = {task["id"] for task in state.tasks()}
        self.assertEqual(len(retained_ids), MAX_TASKS)
        self.assertNotIn(oldest_terminal["id"], retained_ids)
        self.assertIn(newer_terminal["id"], retained_ids)
        self.assertIn(replacement["id"], retained_ids)
        self.assertTrue(queued_ids.issubset(retained_ids))

        prune_event = next(event for event in state.events() if event["kind"] == "task.pruned")
        self.assertEqual(prune_event["task_id"], oldest_terminal["id"])
        self.assertEqual(prune_event["data"]["status"], "completed")
        self.assertEqual(prune_event["data"]["reason"], "terminal_task_retention_limit")
        prune_audit = next(entry for entry in state.audit() if entry["action"] == "task.pruned")
        self.assertEqual(prune_audit["task_id"], oldest_terminal["id"])
        self.assertEqual(prune_audit["from_state"], "completed")
        self.assertEqual(prune_audit["to_state"], "removed")
        self.assertEqual(prune_audit["reason"], "terminal_task_retention_limit")


class OperationBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = LabState()
        enrollment = self.state.enroll_node(
            "operation-node",
            "0.8.0",
            "purple_lab",
            capabilities_for_profile("purple_lab"),
            500,
            now=10.0,
        )
        self.node = enrollment["node"]
        self.session_token = enrollment["session_token"]

    @staticmethod
    def steps(*playbooks: str) -> list[dict[str, str]]:
        return [{"playbook": playbook} for playbook in playbooks]

    def test_operation_atomically_queues_ordered_public_playbook_tasks(self) -> None:
        steps = self.steps("DISCOVERY_FIXTURES", "COLLECT_AND_STAGE", "CREATE_CANARY")
        operation = self.state.queue_operation(
            self.node["id"],
            steps,
            queue_ttl_seconds=60,
            actor="operator-alpha",
            now=20.0,
        )

        self.assertEqual(
            set(operation),
            {"id", "node_id", "created_by", "steps", "tasks"},
        )
        self.assertTrue(operation["id"].startswith("operation-"))
        self.assertEqual(operation["node_id"], self.node["id"])
        self.assertEqual(operation["created_by"], "operator-alpha")
        self.assertEqual(operation["steps"], steps)
        self.assertEqual(len(operation["tasks"]), 3)
        for position, (task, step) in enumerate(
            zip(operation["tasks"], steps, strict=True),
            start=1,
        ):
            self.assertEqual(task["operation_id"], operation["id"])
            self.assertEqual(task["operation_step"], position)
            self.assertEqual(task["type"], "RUN_PLAYBOOK")
            self.assertEqual(task["payload"], step)
            self.assertEqual(task["queue_ttl_seconds"], 60)
            self.assertEqual(task["created_by"], "operator-alpha")

        persisted = sorted(
            self.state.tasks(),
            key=lambda task: task["operation_step"],
        )
        self.assertEqual(
            [task["id"] for task in persisted],
            [task["id"] for task in operation["tasks"]],
        )
        first_poll = self.state.poll_node(self.node["id"], self.session_token, now=20.1)
        self.assertEqual(first_poll["task"]["payload"], steps[0])
        self.assertNotIn("operation_id", first_poll["task"])
        self.assertNotIn("operation_step", first_poll["task"])

        operation_event = next(
            event for event in self.state.events() if event["kind"] == "operation.queued"
        )
        self.assertEqual(operation_event["correlation_id"], operation["id"])
        self.assertEqual(
            operation_event["data"],
            {
                "operation_id": operation["id"],
                "step_count": 3,
                "task_ids": [task["id"] for task in operation["tasks"]],
            },
        )
        operation_audit = next(
            entry for entry in self.state.audit() if entry["action"] == "operation.queued"
        )
        self.assertEqual(operation_audit["actor"], "operator-alpha")
        self.assertEqual(operation_audit["correlation_id"], operation["id"])
        self.assertEqual(operation_audit["reason"], "bounded_playbook_operation")
        self.assertNotIn("payload", operation_event)
        self.assertNotIn("payload", operation_audit)

    def test_operation_validation_is_exact_bounded_and_non_mutating(self) -> None:
        baseline_tasks = self.state.tasks()
        baseline_events = self.state.events()
        baseline_audit = self.state.audit()
        invalid_steps = (
            None,
            {},
            (),
            [],
            self.steps(
                "DISCOVERY_FIXTURES",
                "COLLECT_AND_STAGE",
                "CREATE_CANARY",
                "CLEANUP",
            ),
            [{}],
            [{"playbook": "DISCOVERY_FIXTURES", "command": "ignored"}],
            [{"playbook": "UNKNOWN"}],
            [{"playbook": 1}],
        )

        for invalid in invalid_steps:
            with self.subTest(steps=invalid), self.assertRaises(LabError):
                self.state.queue_operation(self.node["id"], invalid)
        for invalid_ttl in (None, False, 4, 86_401):
            with self.subTest(queue_ttl_seconds=invalid_ttl), self.assertRaises(LabError):
                self.state.queue_operation(
                    self.node["id"],
                    self.steps("DISCOVERY_FIXTURES"),
                    queue_ttl_seconds=invalid_ttl,
                )

        self.assertEqual(self.state.tasks(), baseline_tasks)
        self.assertEqual(self.state.events(), baseline_events)
        self.assertEqual(self.state.audit(), baseline_audit)

    def test_operation_requires_online_active_unpaused_purple_node(self) -> None:
        training = self.state.enroll_node(
            "training-node",
            "0.8.0",
            "training",
            capabilities_for_profile("training"),
            500,
        )
        with self.assertRaises(LabError) as profile_error:
            self.state.queue_operation(
                training["node"]["id"], self.steps("DISCOVERY_FIXTURES")
            )
        self.assertEqual(profile_error.exception.code, "capability_denied")

        self.state._nodes[self.node["id"]]["status"] = "offline"
        with self.assertRaises(LabError) as offline_error:
            self.state.queue_operation(
                self.node["id"], self.steps("DISCOVERY_FIXTURES")
            )
        self.assertEqual(offline_error.exception.code, "node_offline")

        self.state._nodes[self.node["id"]]["status"] = "online"
        self.state._nodes[self.node["id"]]["tasking_paused"] = True
        with self.assertRaises(LabError) as paused_error:
            self.state.queue_operation(
                self.node["id"], self.steps("DISCOVERY_FIXTURES")
            )
        self.assertEqual(paused_error.exception.code, "node_tasking_paused")

        self.state._nodes[self.node["id"]]["tasking_paused"] = False
        self.state.disconnect_node(self.node["id"], self.session_token)
        with self.assertRaises(LabError) as disconnected_error:
            self.state.queue_operation(
                self.node["id"], self.steps("DISCOVERY_FIXTURES")
            )
        self.assertEqual(disconnected_error.exception.code, "node_disconnected")
        self.assertEqual(self.state.tasks(), [])

    def test_operation_idempotency_binds_actor_node_order_steps_and_ttl(self) -> None:
        key = "operation-request-0001"
        steps = self.steps("DISCOVERY_FIXTURES", "COLLECT_AND_STAGE")
        first = self.state.queue_operation(
            self.node["id"],
            steps,
            queue_ttl_seconds=60,
            idempotency_key=key,
            actor="operator-alpha",
        )
        replay = self.state.queue_operation(
            self.node["id"],
            steps,
            queue_ttl_seconds=60,
            idempotency_key=key,
            actor="operator-alpha",
        )

        self.assertEqual(replay, first)
        self.assertEqual(len(self.state.tasks()), 2)
        self.assertEqual(
            len([event for event in self.state.events() if event["kind"] == "operation.queued"]),
            1,
        )
        conflicts = (
            {
                "steps": list(reversed(steps)),
                "queue_ttl_seconds": 60,
                "actor": "operator-alpha",
            },
            {
                "steps": steps,
                "queue_ttl_seconds": 61,
                "actor": "operator-alpha",
            },
            {
                "steps": steps,
                "queue_ttl_seconds": 60,
                "actor": "operator-beta",
            },
        )
        for request in conflicts:
            with self.subTest(request=request), self.assertRaises(LabError) as conflict:
                self.state.queue_operation(
                    self.node["id"],
                    request["steps"],
                    queue_ttl_seconds=request["queue_ttl_seconds"],
                    idempotency_key=key,
                    actor=request["actor"],
                )
            self.assertEqual(conflict.exception.code, "idempotency_conflict")
            self.assertEqual(conflict.exception.status, 409)
        self.assertEqual(len(self.state.tasks()), 2)

    def test_operation_idempotency_pressure_keeps_live_operation_replayable(self) -> None:
        steps = self.steps("DISCOVERY_FIXTURES")
        first_key = "operation-pressure-a"
        second_key = "operation-pressure-b"
        third_key = "operation-pressure-c"

        with patch("c2lab.core.MAX_OPERATION_IDEMPOTENCY_RECORDS", 2):
            first = self.state.queue_operation(
                self.node["id"],
                steps,
                idempotency_key=first_key,
                now=20.0,
            )
            dispatched = self.state.poll_node(
                self.node["id"],
                self.session_token,
                now=20.1,
            )["task"]
            self.assertEqual(dispatched["id"], first["tasks"][0]["id"])

            second = self.state.queue_operation(
                self.node["id"],
                steps,
                idempotency_key=second_key,
                now=20.2,
            )
            tasks_before_rejection = self.state.tasks()
            operation_events_before_rejection = sum(
                event["kind"] == "operation.queued" for event in self.state.events()
            )
            with self.assertRaises(LabError) as limit_error:
                self.state.queue_operation(
                    self.node["id"],
                    steps,
                    idempotency_key=third_key,
                    now=20.3,
                )
            self.assertEqual(limit_error.exception.code, "operation_idempotency_limit")
            self.assertEqual(limit_error.exception.status, 429)
            self.assertEqual(self.state.tasks(), tasks_before_rejection)
            self.assertEqual(
                sum(event["kind"] == "operation.queued" for event in self.state.events()),
                operation_events_before_rejection,
            )

            self.state.cancel_task(second["tasks"][0]["id"])
            third = self.state.queue_operation(
                self.node["id"],
                steps,
                idempotency_key=third_key,
                now=20.4,
            )
            replay = self.state.queue_operation(
                self.node["id"],
                steps,
                idempotency_key=first_key,
                now=20.5,
            )

        self.assertEqual(replay, first)
        self.assertIn(first_key, self.state._operation_idempotency)
        self.assertNotIn(second_key, self.state._operation_idempotency)
        self.assertIn(third_key, self.state._operation_idempotency)
        self.assertEqual(
            self.state._operation_idempotency[first_key]["task_ids"],
            (first["tasks"][0]["id"],),
        )
        self.assertEqual(
            [
                task["operation_id"]
                for task in self.state.tasks()
                if task["status"] in {"queued", "dispatched"}
            ],
            [third["id"], first["id"]],
        )

    def test_operation_queue_limits_reject_without_partial_tasks(self) -> None:
        steps = self.steps("DISCOVERY_FIXTURES", "COLLECT_AND_STAGE", "CREATE_CANARY")
        scenarios = (
            ("per-node task", 2, 10, 10, "queue_limit"),
            ("per-node playbook", 10, 2, 10, "playbook_queue_limit"),
            ("total task", 10, 10, 2, "task_limit"),
        )
        for label, task_limit, playbook_limit, total_limit, expected in scenarios:
            with self.subTest(limit=label):
                state = LabState()
                node = state.enroll_node(
                    f"limit-{label}",
                    "0.8.0",
                    "purple_lab",
                    capabilities_for_profile("purple_lab"),
                    500,
                )["node"]
                with (
                    patch("c2lab.core.MAX_QUEUED_TASKS_PER_NODE", task_limit),
                    patch("c2lab.core.MAX_QUEUED_PLAYBOOKS_PER_NODE", playbook_limit),
                    patch("c2lab.core.MAX_TASKS", total_limit),
                    self.assertRaises(LabError) as context,
                ):
                    state.queue_operation(node["id"], steps)
                self.assertEqual(context.exception.code, expected)
                self.assertEqual(state.tasks(), [])
                self.assertFalse(
                    any(event["kind"] == "operation.queued" for event in state.events())
                )

    def test_reset_clears_operation_idempotency(self) -> None:
        key = "operation-reset-0001"
        self.state.queue_operation(
            self.node["id"],
            self.steps("DISCOVERY_FIXTURES"),
            idempotency_key=key,
        )
        self.assertIn(key, self.state._operation_idempotency)

        self.state.reset(actor="admin-user")

        self.assertEqual(self.state._operation_idempotency, {})
        replacement = self.state.enroll_node(
            "replacement-operation-node",
            "0.8.0",
            "purple_lab",
            capabilities_for_profile("purple_lab"),
            500,
        )["node"]
        operation = self.state.queue_operation(
            replacement["id"],
            self.steps("CLEANUP"),
            idempotency_key=key,
        )
        self.assertEqual(operation["node_id"], replacement["id"])
        self.assertEqual(len(self.state.tasks()), 1)


if __name__ == "__main__":
    unittest.main()
