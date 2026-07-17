from __future__ import annotations

import unittest

from c2lab.observability import RequestMetrics, normalized_route


class ObservabilityTests(unittest.TestCase):
    def test_route_labels_are_bounded_and_do_not_retain_identifiers(self) -> None:
        self.assertEqual(
            normalized_route("/node/v1/tasks/task-123/result"),
            "/node/v1/tasks/:task_id/result",
        )
        self.assertEqual(
            normalized_route("/lab/tasks/task-123/cancel"),
            "/lab/tasks/:task_id/cancel",
        )
        self.assertEqual(
            normalized_route("/lab/operators/session-123/revoke"),
            "/lab/operators/:session_id/revoke",
        )
        self.assertEqual(
            normalized_route("/lab/exercises/exercise-123/contain"),
            "/lab/exercises/:exercise_id/contain",
        )
        self.assertEqual(normalized_route("/lab/sync"), "/lab/sync")
        self.assertEqual(normalized_route("/lab/operations"), "/lab/operations")
        self.assertEqual(normalized_route("/lab/scenarios"), "/lab/scenarios")
        self.assertEqual(normalized_route("/lab/exercises"), "/lab/exercises")
        self.assertEqual(normalized_route("/lab/notes"), "/lab/notes")
        self.assertEqual(
            normalized_route("/lab/sync?events_after=query-secret"),
            "unmatched",
        )
        self.assertEqual(normalized_route("/attacker-controlled/value"), "unmatched")

    def test_metrics_are_aggregate_and_snapshot_is_detached(self) -> None:
        ticks = iter((10.0, 12.5, 13.0))
        metrics = RequestMetrics(clock=lambda: next(ticks))
        metrics.record(method="GET", route="/lab/overview", status=200, duration_ms=4.0)
        metrics.record(
            method="POST",
            route="/node/v1/tasks/task-secret/result",
            status=409,
            duration_ms=6.0,
        )
        metrics.record(
            method="DELETE",
            route="/private/unbounded-value",
            status=777,
            duration_ms=float("inf"),
        )
        metrics.record_worker_rejection()
        metrics.record_access_log_drop()

        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["uptime_seconds"], 2.5)
        self.assertEqual(snapshot["requests_total"], 3)
        self.assertEqual(snapshot["worker_rejections"], 1)
        self.assertEqual(snapshot["access_log_drops"], 1)
        self.assertEqual(snapshot["methods"], {"GET": 1, "OTHER": 1, "POST": 1})
        self.assertEqual(snapshot["status_classes"], {"2xx": 1, "4xx": 1, "other": 1})
        self.assertEqual(
            snapshot["routes"],
            {
                "/lab/overview": 1,
                "/node/v1/tasks/:task_id/result": 1,
                "unmatched": 1,
            },
        )
        self.assertNotIn("task-secret", str(snapshot))
        self.assertNotIn("unbounded-value", str(snapshot))
        self.assertEqual(snapshot["duration_ms"], {"average": 3.333, "maximum": 6.0})

        snapshot["methods"]["GET"] = 999
        self.assertEqual(metrics.snapshot()["methods"]["GET"], 1)


if __name__ == "__main__":
    unittest.main()
