from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest

from c2lab.lab_runtime import (
    CANARY_ARTIFACT,
    FIXTURE_DEFINITIONS,
    MANIFEST_ARTIFACT,
    MAX_RESULT_BYTES,
    PLAYBOOK_NAMES,
    EphemeralLabWorkspace,
    LabRuntimeError,
    UnknownPlaybookError,
    WorkspaceSafetyError,
    validate_playbook_result,
)


class EphemeralLabWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = EphemeralLabWorkspace()

    def tearDown(self) -> None:
        self.runtime.close()

    def test_fixed_playbook_registry_and_no_operator_path_surface(self) -> None:
        self.assertEqual(
            PLAYBOOK_NAMES,
            ("DISCOVERY_FIXTURES", "COLLECT_AND_STAGE", "CREATE_CANARY", "CLEANUP"),
        )
        for value in ("../DISCOVERY_FIXTURES", "/tmp/demo", "RUN_COMMAND", 1, None):
            with self.subTest(value=value), self.assertRaises(UnknownPlaybookError):
                self.runtime.execute(value)
        with self.assertRaises(TypeError):
            self.runtime.execute("DISCOVERY_FIXTURES", path="/tmp/demo")
        with self.assertRaises(TypeError):
            self.runtime.execute("DISCOVERY_FIXTURES", shell="whoami")

    def test_discovery_enumerates_only_private_synthetic_fixtures(self) -> None:
        result = self.runtime.execute("DISCOVERY_FIXTURES")
        self.assertEqual(result["playbook"], "DISCOVERY_FIXTURES")
        self.assertEqual(result["scope"]["workspace"], "ephemeral-node-private")
        self.assertEqual(result["scope"]["host_access"], False)
        self.assertEqual(result["scope"]["network_access"], False)
        self.assertEqual(
            [item["artifact"] for item in result["evidence"]],
            [definition[0] for definition in FIXTURE_DEFINITIONS],
        )
        self.assertNotIn(self.runtime._root, json.dumps(result))

    def test_collection_reads_hashes_and_writes_a_real_manifest(self) -> None:
        result = self.runtime.execute("COLLECT_AND_STAGE")
        for item, definition in zip(result["evidence"][:-1], FIXTURE_DEFINITIONS, strict=True):
            self.assertEqual(item["sha256"], hashlib.sha256(definition[3]).hexdigest())
        self.assertIn(MANIFEST_ARTIFACT, self.runtime._artifacts)
        manifest = json.loads(self.runtime._read_artifact(MANIFEST_ARTIFACT))
        self.assertTrue(manifest["synthetic"])
        self.assertEqual(len(manifest["entries"]), len(FIXTURE_DEFINITIONS))

    def test_canary_is_idempotent_and_cleanup_removes_only_bounded_artifacts(self) -> None:
        first = self.runtime.execute("CREATE_CANARY")
        second = self.runtime.execute("CREATE_CANARY")
        self.assertEqual(first["evidence"][0]["observation"], "canary-created")
        self.assertEqual(second["evidence"][0]["observation"], "canary-already-present")
        self.runtime.execute("COLLECT_AND_STAGE")

        cleaned = self.runtime.execute("CLEANUP")
        self.assertEqual(
            [item["observation"] for item in cleaned["evidence"]],
            ["artifact-removed", "artifact-removed"],
        )
        self.assertNotIn(MANIFEST_ARTIFACT, self.runtime._artifacts)
        self.assertNotIn(CANARY_ARTIFACT, self.runtime._artifacts)
        self.assertEqual(len(self.runtime._artifacts), len(FIXTURE_DEFINITIONS))

    def test_all_results_are_strict_bounded_json(self) -> None:
        for playbook in PLAYBOOK_NAMES:
            with self.subTest(playbook=playbook):
                result = self.runtime.execute(playbook)
                self.assertEqual(validate_playbook_result(playbook, result), result)
                encoded = json.dumps(result, separators=(",", ":")).encode()
                self.assertLessEqual(len(encoded), MAX_RESULT_BYTES)
                self.assertNotIn(self.runtime._root.encode(), encoded)

    def test_result_validator_rejects_extra_fields_and_absolute_paths(self) -> None:
        result = self.runtime.execute("DISCOVERY_FIXTURES")
        result["extra"] = True
        with self.assertRaises(LabRuntimeError):
            validate_playbook_result("DISCOVERY_FIXTURES", result)

        result = self.runtime.execute("DISCOVERY_FIXTURES")
        result["evidence"][0]["artifact"] = "/tmp/escape"
        with self.assertRaises(LabRuntimeError):
            validate_playbook_result("DISCOVERY_FIXTURES", result)

        result = self.runtime.execute("COLLECT_AND_STAGE")
        result["evidence"][0]["sha256"] = "0" * 64
        with self.assertRaises(LabRuntimeError):
            validate_playbook_result("COLLECT_AND_STAGE", result)

    def test_result_validator_does_not_coerce_json_scalar_types(self) -> None:
        for replacement in (0, 0.0):
            result = self.runtime.execute("DISCOVERY_FIXTURES")
            result["scope"]["host_access"] = replacement
            with self.subTest(field="host_access", replacement=replacement):
                with self.assertRaises(LabRuntimeError):
                    validate_playbook_result("DISCOVERY_FIXTURES", result)

        result = self.runtime.execute("DISCOVERY_FIXTURES")
        result["evidence"][0]["bytes"] = float(result["evidence"][0]["bytes"])
        with self.assertRaises(LabRuntimeError):
            validate_playbook_result("DISCOVERY_FIXTURES", result)

        result = self.runtime.execute("COLLECT_AND_STAGE")
        result["evidence"][-1]["entries"] = float(result["evidence"][-1]["entries"])
        with self.assertRaises(LabRuntimeError):
            validate_playbook_result("COLLECT_AND_STAGE", result)

    def test_context_and_close_remove_the_temporary_workspace(self) -> None:
        runtime = EphemeralLabWorkspace()
        root = runtime._root
        self.assertTrue(os.path.isdir(root))
        with runtime:
            runtime.execute("CREATE_CANARY")
        self.assertTrue(runtime.closed)
        self.assertFalse(os.path.exists(root))
        runtime.close()
        with self.assertRaises(LabRuntimeError):
            runtime.execute("DISCOVERY_FIXTURES")

    def test_path_traversal_and_unknown_members_are_rejected(self) -> None:
        for member in ("../escape", "nested/escape", "nested\\escape", ".", ""):
            with self.subTest(member=member), self.assertRaises(WorkspaceSafetyError):
                self.runtime._safe_member_path(member)

    def test_symlink_replacement_cannot_escape_workspace(self) -> None:
        self.runtime.execute("CREATE_CANARY")
        member = self.runtime._artifacts[CANARY_ARTIFACT]
        candidate = os.path.join(self.runtime._root, member)
        self.runtime._handles[member].close()
        os.remove(candidate)
        with tempfile.NamedTemporaryFile() as sentinel:
            sentinel.write(b"outside-sentinel")
            sentinel.flush()
            os.symlink(sentinel.name, candidate)
            with self.assertRaises(WorkspaceSafetyError):
                self.runtime.execute("CLEANUP")
            sentinel.seek(0)
            self.assertEqual(sentinel.read(), b"outside-sentinel")

    def test_unknown_workspace_entry_fails_closed_without_reading_it(self) -> None:
        with tempfile.NamedTemporaryFile() as sentinel:
            sentinel.write(b"outside-sentinel")
            sentinel.flush()
            injected = os.path.join(self.runtime._root, "unknown-link")
            os.symlink(sentinel.name, injected)
            with self.assertRaises(WorkspaceSafetyError):
                self.runtime.execute("DISCOVERY_FIXTURES")
            sentinel.seek(0)
            self.assertEqual(sentinel.read(), b"outside-sentinel")


if __name__ == "__main__":
    unittest.main()
