from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from c2lab.core import LabError, LabState
from c2lab.exercises import MAX_EXERCISE_TIMELINE
from c2lab.lab_runtime import EphemeralLabWorkspace
from c2lab.protocol import capabilities_for_profile


class AttackExerciseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = LabState()
        enrollment = self.state.enroll_node(
            "purple-node",
            "0.7.0",
            "purple_lab",
            capabilities_for_profile("purple_lab"),
            500,
        )
        self.node_id = enrollment["node"]["id"]
        self.session_token = enrollment["session_token"]

    def complete_next(self, workspace: EphemeralLabWorkspace) -> dict[str, object]:
        envelope = self.state.poll_node(self.node_id, self.session_token)
        task = envelope["task"]
        self.assertIsNotNone(task)
        result = workspace.execute(task["payload"]["playbook"])
        return self.state.submit_result(
            self.node_id,
            self.session_token,
            task["id"],
            "completed",
            result,
        )

    def test_discovery_collection_derives_fixed_alerts_from_validated_results(self) -> None:
        exercise = self.state.start_exercise(
            self.node_id,
            "DISCOVERY_COLLECTION",
            actor="purple-operator",
        )
        self.assertEqual(exercise["status"], "running")
        self.assertEqual(len(exercise["task_ids"]), 2)
        self.assertEqual(
            {task["created_by"] for task in self.state.tasks()},
            {"purple-operator"},
        )

        with EphemeralLabWorkspace() as workspace:
            self.complete_next(workspace)
            detected = self.state.exercises()[0]
            self.assertEqual(detected["detection_status"], "detected")
            self.assertEqual(
                {alert["source_id"] for alert in detected["alerts"]},
                {"DET0370"},
            )
            self.complete_next(workspace)

        completed = self.state.exercises()[0]
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(
            {alert["source_id"] for alert in completed["alerts"]},
            {"DET0370", "DET0380", "DET0261"},
        )
        self.assertEqual(
            {alert["technique_id"] for alert in completed["alerts"]},
            {"T1083", "T1005", "T1074.001"},
        )
        self.assertLessEqual(len(completed["timeline"]), MAX_EXERCISE_TIMELINE)
        self.assertNotIn("result", json.dumps(completed))

    def test_detected_exercise_can_cancel_remaining_scenario_work(self) -> None:
        exercise = self.state.start_exercise(
            self.node_id,
            "DISCOVERY_COLLECTION",
            actor="purple-operator",
        )
        with EphemeralLabWorkspace() as workspace:
            self.complete_next(workspace)

        contained = self.state.contain_exercise(
            exercise["id"],
            "CANCEL_REMAINING",
            actor="admin-user",
        )
        self.assertEqual(contained["status"], "contained")
        self.assertEqual(contained["containment"]["action"], "CANCEL_REMAINING")
        self.assertEqual(
            {task["status"] for task in self.state.tasks()},
            {"cancelled", "completed"},
        )
        self.assertTrue(all(alert["status"] == "contained" for alert in contained["alerts"]))
        self.assertEqual(
            self.state.contain_exercise(
                exercise["id"],
                "CANCEL_REMAINING",
                actor="admin-user",
            ),
            contained,
        )

    def test_pause_node_tasking_blocks_queue_and_dispatch_but_keeps_polling(self) -> None:
        exercise = self.state.start_exercise(
            self.node_id,
            "DISCOVERY_COLLECTION",
            actor="purple-operator",
        )
        with EphemeralLabWorkspace() as workspace:
            self.complete_next(workspace)
        contained = self.state.contain_exercise(
            exercise["id"],
            "PAUSE_NODE_TASKING",
            actor="admin-user",
        )

        node = next(item for item in self.state.nodes() if item["id"] == self.node_id)
        self.assertTrue(node["tasking_paused"])
        self.assertEqual(node["tasking_paused_by"], "admin-user")
        self.assertIsNone(self.state.poll_node(self.node_id, self.session_token)["task"])
        with self.assertRaises(LabError) as context:
            self.state.queue_task(self.node_id, "PING", {}, actor="purple-operator")
        self.assertEqual(context.exception.code, "node_tasking_paused")
        self.assertEqual(contained["containment"]["action"], "PAUSE_NODE_TASKING")

    def test_canary_scenario_maps_only_the_fixed_cleanup_signal(self) -> None:
        self.state.start_exercise(self.node_id, "CANARY_REMOVAL")
        with EphemeralLabWorkspace() as workspace:
            self.complete_next(workspace)
            self.assertEqual(self.state.exercises()[0]["detection_status"], "pending")
            self.complete_next(workspace)
        exercise = self.state.exercises()[0]
        self.assertEqual(exercise["status"], "completed")
        self.assertEqual(len(exercise["alerts"]), 1)
        self.assertEqual(exercise["alerts"][0]["source_id"], "DET0140")
        self.assertEqual(exercise["alerts"][0]["technique_id"], "T1070.004")

    def test_running_exercise_tasks_are_not_pruned_before_completion(self) -> None:
        with patch("c2lab.core.MAX_TASKS", 3):
            exercise = self.state.start_exercise(self.node_id, "DISCOVERY_COLLECTION")
            with EphemeralLabWorkspace() as workspace:
                first = self.complete_next(workspace)
                filler = self.state.queue_task(self.node_id, "PING", {})
                with self.assertRaises(LabError) as context:
                    self.state.queue_task(self.node_id, "PING", {})
                self.assertEqual(context.exception.code, "task_limit")
                self.assertIn(first["id"], {task["id"] for task in self.state.tasks()})
                self.assertEqual(self.state.exercises()[0]["status"], "running")

                self.complete_next(workspace)

            self.assertEqual(self.state.exercises()[0]["status"], "completed")
            replacement = self.state.queue_task(self.node_id, "PING", {})
            retained_ids = {task["id"] for task in self.state.tasks()}
            self.assertNotIn(first["id"], retained_ids)
            self.assertIn(filler["id"], retained_ids)
            self.assertIn(replacement["id"], retained_ids)
            self.assertEqual(self.state.exercises()[0]["id"], exercise["id"])
            self.assertEqual(self.state.exercises()[0]["status"], "completed")

    def test_start_exercise_prunes_enough_eligible_terminal_tasks_atomically(self) -> None:
        with patch("c2lab.core.MAX_TASKS", 2):
            old_task_ids = set()
            for offset in range(2):
                task = self.state.queue_task(self.node_id, "PING", {})
                old_task_ids.add(task["id"])
                self.state.poll_node(self.node_id, self.session_token, now=10.0 + offset)
                self.state.submit_result(
                    self.node_id,
                    self.session_token,
                    task["id"],
                    "completed",
                    {"reply": "PONG"},
                    now=10.1 + offset,
                )

            exercise = self.state.start_exercise(self.node_id, "DISCOVERY_COLLECTION")
            retained_ids = {task["id"] for task in self.state.tasks()}
            self.assertEqual(retained_ids, set(exercise["task_ids"]))
            self.assertTrue(old_task_ids.isdisjoint(retained_ids))
            self.assertEqual(
                len([event for event in self.state.events() if event["kind"] == "task.pruned"]),
                2,
            )

    def test_start_validation_idempotency_and_reset_are_non_leaking(self) -> None:
        first = self.state.start_exercise(
            self.node_id,
            "DISCOVERY_COLLECTION",
            actor="purple-operator",
            idempotency_key="exercise:key:001",
        )
        retry = self.state.start_exercise(
            self.node_id,
            "DISCOVERY_COLLECTION",
            actor="purple-operator",
            idempotency_key="exercise:key:001",
        )
        self.assertEqual(first, retry)
        self.assertEqual(len(self.state.exercises()), 1)
        self.assertEqual(len(self.state.tasks()), 2)

        with self.assertRaises(LabError) as conflict:
            self.state.start_exercise(
                self.node_id,
                "CANARY_REMOVAL",
                actor="purple-operator",
                idempotency_key="exercise:key:001",
            )
        self.assertEqual(conflict.exception.code, "idempotency_conflict")
        with self.assertRaises(LabError) as unknown:
            self.state.start_exercise(self.node_id, "UNKNOWN_SCENARIO")
        self.assertEqual(unknown.exception.code, "unsupported_scenario")
        with self.assertRaises(LabError) as premature:
            self.state.contain_exercise(first["id"], "CANCEL_REMAINING")
        self.assertEqual(premature.exception.code, "detection_required")

        self.state.reset(actor="admin-user")
        self.assertEqual(self.state.exercises(), [])
        self.assertEqual(self.state.sync()["exercises"], [])

    def test_catalog_is_detached_and_report_contains_only_bounded_metadata(self) -> None:
        catalog = self.state.scenarios()
        catalog[0]["title"] = "changed"
        self.assertNotEqual(self.state.scenarios()[0]["title"], "changed")
        self.state.start_exercise(self.node_id, "DISCOVERY_COLLECTION")
        report = self.state.report()
        self.assertEqual(report["retention"]["exercise_timeline"], MAX_EXERCISE_TIMELINE)
        self.assertEqual(report["counts"]["exercises_total"], 1)
        self.assertEqual(len(report["scenario_catalog"]), 2)
        serialized = json.dumps(report)
        self.assertNotIn(self.session_token, serialized)
        self.assertNotIn("payload", serialized)
        self.assertNotIn("result", serialized)


if __name__ == "__main__":
    unittest.main()
