"""Ephemeral, fixture-only playbooks for the foreground lab node.

The runtime performs real file operations, but only against files it creates in
its own ``TemporaryDirectory``.  It accepts a fixed playbook identifier rather
than paths, commands, content, hosts, or network destinations.
"""

from __future__ import annotations

import copy
import hashlib
import json
import ntpath
import os
import tempfile
import threading
from typing import Any, BinaryIO

from .exercises import technique_identity


PLAYBOOK_NAMES = (
    "DISCOVERY_FIXTURES",
    "COLLECT_AND_STAGE",
    "CREATE_CANARY",
    "CLEANUP",
)

MAX_WORKSPACE_FILES = 5
MAX_ARTIFACT_BYTES = 2_048
MAX_WORKSPACE_BYTES = 4_096
MAX_RESULT_BYTES = 4_096

FIXTURE_DEFINITIONS = (
    (
        "fixtures/accounts.json",
        "fixture-accounts-",
        ".json",
        b'{"synthetic":true,"records":[{"alias":"analyst-a","role":"training"}]}'
    ),
    (
        "fixtures/planning.txt",
        "fixture-planning-",
        ".txt",
        b"Synthetic exercise plan: validate fixture-only collection controls.\n",
    ),
    (
        "fixtures/telemetry.csv",
        "fixture-telemetry-",
        ".csv",
        b"event_id,severity,synthetic\nLAB-001,info,true\n",
    ),
)

MANIFEST_ARTIFACT = "staging/manifest.json"
CANARY_ARTIFACT = "markers/canary.json"
_CANARY_BYTES = b'{"marker":"C2LAB-CANARY","synthetic":true}\n'

_SCOPE = {
    "workspace": "ephemeral-node-private",
    "data": "synthetic-fixtures-only",
    "host_access": False,
    "network_access": False,
}

_TECHNIQUES: dict[str, list[dict[str, str]]] = {
    "DISCOVERY_FIXTURES": [
        {
            **technique_identity("T1083"),
            "emulation": "fixture-only",
        }
    ],
    "COLLECT_AND_STAGE": [
        {**technique_identity("T1005"), "emulation": "synthetic-only"},
        {
            **technique_identity("T1074.001"),
            "emulation": "ephemeral-only",
        },
    ],
    "CREATE_CANARY": [],
    "CLEANUP": [
        {**technique_identity("T1070.004"), "emulation": "lab-artifacts-only"}
    ],
}

_STEPS: dict[str, list[dict[str, str]]] = {
    "DISCOVERY_FIXTURES": [
        {
            "name": "enumerate-fixtures",
            "status": "completed",
            "observation": "fixture-registry-enumerated",
        }
    ],
    "COLLECT_AND_STAGE": [
        {
            "name": "read-synthetic-fixtures",
            "status": "completed",
            "observation": "synthetic-fixtures-read",
        },
        {
            "name": "hash-fixture-content",
            "status": "completed",
            "observation": "fixture-digests-computed",
        },
        {
            "name": "write-staging-manifest",
            "status": "completed",
            "observation": "staging-manifest-created",
        },
    ],
    "CREATE_CANARY": [
        {
            "name": "create-canary-marker",
            "status": "completed",
            "observation": "canary-marker-ready",
        }
    ],
    "CLEANUP": [
        {
            "name": "remove-bounded-artifacts",
            "status": "completed",
            "observation": "workspace-artifacts-cleaned",
        }
    ],
}


class LabRuntimeError(ValueError):
    """A fixed-code runtime failure that never includes a filesystem path."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class UnknownPlaybookError(LabRuntimeError):
    def __init__(self) -> None:
        super().__init__("unsupported lab playbook", code="unknown_playbook")


class WorkspaceSafetyError(LabRuntimeError):
    def __init__(self) -> None:
        super().__init__("ephemeral workspace boundary check failed", code="workspace_violation")


def _is_absolute_path_string(value: str) -> bool:
    return os.path.isabs(value) or ntpath.isabs(value)


def _contains_absolute_path(value: Any) -> bool:
    if isinstance(value, str):
        return _is_absolute_path_string(value)
    if isinstance(value, list):
        return any(_contains_absolute_path(item) for item in value)
    if isinstance(value, dict):
        return any(
            _contains_absolute_path(key) or _contains_absolute_path(item)
            for key, item in value.items()
        )
    return False


def _require_exact_keys(value: Any, expected: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != expected:
        raise LabRuntimeError("invalid playbook result schema", code="result_schema")
    return value


def validate_playbook_result(playbook: Any, result: Any) -> dict[str, Any]:
    """Validate and copy a type-specific, path-free playbook result."""

    if type(playbook) is not str or playbook not in PLAYBOOK_NAMES:
        raise UnknownPlaybookError()
    clean = _require_exact_keys(
        result,
        {"playbook", "scope", "attack_techniques", "steps", "evidence"},
    )
    if clean["playbook"] != playbook:
        raise LabRuntimeError("invalid playbook result schema", code="result_schema")
    if clean["scope"] != _SCOPE:
        raise LabRuntimeError("invalid playbook result schema", code="result_schema")
    if clean["attack_techniques"] != _TECHNIQUES[playbook]:
        raise LabRuntimeError("invalid playbook result schema", code="result_schema")
    if clean["steps"] != _STEPS[playbook]:
        raise LabRuntimeError("invalid playbook result schema", code="result_schema")
    if type(clean["evidence"]) is not list:
        raise LabRuntimeError("invalid playbook result schema", code="result_schema")

    evidence = clean["evidence"]
    fixture_names = [definition[0] for definition in FIXTURE_DEFINITIONS]
    if playbook == "DISCOVERY_FIXTURES":
        if len(evidence) != len(fixture_names):
            raise LabRuntimeError("invalid playbook result schema", code="result_schema")
        for item, definition in zip(evidence, FIXTURE_DEFINITIONS, strict=True):
            artifact = definition[0]
            entry = _require_exact_keys(item, {"artifact", "observation", "bytes"})
            if (
                entry["artifact"] != artifact
                or entry["observation"] != "fixture-enumerated"
                or entry["bytes"] != len(definition[3])
            ):
                raise LabRuntimeError("invalid playbook result schema", code="result_schema")
    elif playbook == "COLLECT_AND_STAGE":
        if len(evidence) != len(fixture_names) + 1:
            raise LabRuntimeError("invalid playbook result schema", code="result_schema")
        for item, definition in zip(evidence[:-1], FIXTURE_DEFINITIONS, strict=True):
            artifact = definition[0]
            entry = _require_exact_keys(
                item,
                {"artifact", "observation", "bytes", "sha256"},
            )
            if (
                entry["artifact"] != artifact
                or entry["observation"] != "content-hashed"
                or entry["bytes"] != len(definition[3])
                or entry["sha256"] != hashlib.sha256(definition[3]).hexdigest()
            ):
                raise LabRuntimeError("invalid playbook result schema", code="result_schema")
        manifest = _require_exact_keys(
            evidence[-1],
            {"artifact", "observation", "entries"},
        )
        if manifest != {
            "artifact": MANIFEST_ARTIFACT,
            "observation": "manifest-written",
            "entries": len(fixture_names),
        }:
            raise LabRuntimeError("invalid playbook result schema", code="result_schema")
    elif playbook == "CREATE_CANARY":
        if len(evidence) != 1:
            raise LabRuntimeError("invalid playbook result schema", code="result_schema")
        marker = _require_exact_keys(evidence[0], {"artifact", "observation", "bytes"})
        if (
            marker["artifact"] != CANARY_ARTIFACT
            or marker["observation"] not in {"canary-created", "canary-already-present"}
            or marker["bytes"] != len(_CANARY_BYTES)
        ):
            raise LabRuntimeError("invalid playbook result schema", code="result_schema")
    else:
        if len(evidence) != 2:
            raise LabRuntimeError("invalid playbook result schema", code="result_schema")
        for item, artifact in zip(evidence, (MANIFEST_ARTIFACT, CANARY_ARTIFACT), strict=True):
            entry = _require_exact_keys(item, {"artifact", "observation"})
            if (
                entry["artifact"] != artifact
                or entry["observation"] not in {"artifact-removed", "artifact-absent"}
            ):
                raise LabRuntimeError("invalid playbook result schema", code="result_schema")

    if _contains_absolute_path(clean):
        raise LabRuntimeError("invalid playbook result schema", code="result_schema")
    try:
        encoded = json.dumps(
            clean,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise LabRuntimeError("invalid playbook result schema", code="result_schema") from error
    if len(encoded) > MAX_RESULT_BYTES:
        raise LabRuntimeError("playbook result exceeded its size limit", code="result_schema")
    return copy.deepcopy(clean)


class EphemeralLabWorkspace:
    """Own one private temporary workspace and execute fixed lab playbooks."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="c2lab-node-")
        self._root = os.path.abspath(self._temporary_directory.name)
        self._root_real = os.path.realpath(self._root)
        self._closed = False
        self._artifacts: dict[str, str] = {}
        self._handles: dict[str, BinaryIO] = {}
        try:
            for logical_name, prefix, suffix, contents in FIXTURE_DEFINITIONS:
                self._create_member(logical_name, prefix, suffix, contents)
            self._assert_workspace()
        except Exception:
            self.close()
            raise

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> EphemeralLabWorkspace:
        self._require_open()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for handle in self._handles.values():
                try:
                    handle.close()
                except OSError:
                    pass
            self._handles.clear()
            self._artifacts.clear()
            self._temporary_directory.cleanup()

    def execute(self, playbook_name: Any) -> dict[str, Any]:
        """Execute one of ``PLAYBOOK_NAMES``; no other input is accepted."""

        if type(playbook_name) is not str or playbook_name not in PLAYBOOK_NAMES:
            raise UnknownPlaybookError()
        with self._lock:
            self._require_open()
            self._assert_workspace()
            if playbook_name == "DISCOVERY_FIXTURES":
                evidence = self._discover_fixtures()
            elif playbook_name == "COLLECT_AND_STAGE":
                evidence = self._collect_and_stage()
            elif playbook_name == "CREATE_CANARY":
                evidence = self._create_canary()
            else:
                evidence = self._cleanup_artifacts()
            self._assert_workspace()
            result = {
                "playbook": playbook_name,
                "scope": copy.deepcopy(_SCOPE),
                "attack_techniques": copy.deepcopy(_TECHNIQUES[playbook_name]),
                "steps": copy.deepcopy(_STEPS[playbook_name]),
                "evidence": evidence,
            }
            return validate_playbook_result(playbook_name, result)

    def _require_open(self) -> None:
        if self._closed:
            raise LabRuntimeError("ephemeral workspace is closed", code="workspace_closed")

    def _create_member(
        self,
        logical_name: str,
        prefix: str,
        suffix: str,
        contents: bytes,
    ) -> None:
        self._require_open()
        self._assert_root()
        if (
            logical_name in self._artifacts
            or len(self._artifacts) >= MAX_WORKSPACE_FILES
            or not 0 <= len(contents) <= MAX_ARTIFACT_BYTES
        ):
            raise WorkspaceSafetyError()
        current_bytes = sum(os.fstat(handle.fileno()).st_size for handle in self._handles.values())
        if current_bytes + len(contents) > MAX_WORKSPACE_BYTES:
            raise WorkspaceSafetyError()

        handle: BinaryIO | None = None
        candidate = ""
        try:
            handle = tempfile.NamedTemporaryFile(
                mode="w+b",
                prefix=prefix,
                suffix=suffix,
                dir=self._root,
                delete=False,
            )
            candidate = os.path.abspath(handle.name)
            member = os.path.basename(candidate)
            if (
                os.path.dirname(candidate) != self._root
                or not member.isascii()
                or not member
                or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in member)
            ):
                raise WorkspaceSafetyError()
            handle.write(contents)
            handle.flush()
            self._artifacts[logical_name] = member
            self._handles[member] = handle
            self._safe_member_path(member)
        except Exception:
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
            if candidate and os.path.lexists(candidate):
                try:
                    os.remove(candidate)
                except OSError:
                    pass
            raise

    def _assert_root(self) -> None:
        if (
            self._closed
            or os.path.islink(self._root)
            or not os.path.isdir(self._root)
            or os.path.realpath(self._root) != self._root_real
        ):
            raise WorkspaceSafetyError()

    def _safe_member_path(self, member: Any) -> str:
        self._assert_root()
        if (
            type(member) is not str
            or member not in self._handles
            or os.path.basename(member) != member
            or member in {".", ".."}
            or "/" in member
            or "\\" in member
        ):
            raise WorkspaceSafetyError()
        candidate = os.path.join(self._root, member)
        if (
            not os.path.lexists(candidate)
            or os.path.islink(candidate)
            or os.path.commonpath((self._root_real, os.path.realpath(candidate))) != self._root_real
        ):
            raise WorkspaceSafetyError()
        return candidate

    def _assert_workspace(self) -> None:
        self._assert_root()
        entries = list(os.scandir(self._root))
        if len(entries) > MAX_WORKSPACE_FILES or {entry.name for entry in entries} != set(self._handles):
            raise WorkspaceSafetyError()
        total_bytes = 0
        for entry in entries:
            self._safe_member_path(entry.name)
            handle = self._handles[entry.name]
            if handle.closed or entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                raise WorkspaceSafetyError()
            entry_status = entry.stat(follow_symlinks=False)
            handle_status = os.fstat(handle.fileno())
            if (
                entry_status.st_dev != handle_status.st_dev
                or entry_status.st_ino != handle_status.st_ino
                or entry_status.st_nlink != 1
                or handle_status.st_nlink != 1
                or handle_status.st_size > MAX_ARTIFACT_BYTES
            ):
                raise WorkspaceSafetyError()
            total_bytes += handle_status.st_size
        if total_bytes > MAX_WORKSPACE_BYTES:
            raise WorkspaceSafetyError()

    def _read_artifact(self, logical_name: str) -> bytes:
        member = self._artifacts.get(logical_name)
        if member is None:
            raise WorkspaceSafetyError()
        self._safe_member_path(member)
        handle = self._handles[member]
        handle.flush()
        handle.seek(0)
        contents = handle.read(MAX_ARTIFACT_BYTES + 1)
        handle.seek(0)
        if len(contents) > MAX_ARTIFACT_BYTES:
            raise WorkspaceSafetyError()
        return contents

    def _remove_artifact(self, logical_name: str) -> bool:
        member = self._artifacts.get(logical_name)
        if member is None:
            return False
        candidate = self._safe_member_path(member)
        handle = self._handles[member]
        handle.close()
        try:
            os.remove(candidate)
        except OSError as error:
            raise LabRuntimeError("lab artifact cleanup failed", code="artifact_failure") from error
        del self._handles[member]
        del self._artifacts[logical_name]
        return True

    def _discover_fixtures(self) -> list[dict[str, Any]]:
        evidence = []
        for logical_name, _prefix, _suffix, _contents in FIXTURE_DEFINITIONS:
            contents = self._read_artifact(logical_name)
            evidence.append(
                {
                    "artifact": logical_name,
                    "observation": "fixture-enumerated",
                    "bytes": len(contents),
                }
            )
        return evidence

    def _collect_and_stage(self) -> list[dict[str, Any]]:
        self._remove_artifact(MANIFEST_ARTIFACT)
        evidence = []
        manifest_entries = []
        for logical_name, _prefix, _suffix, _contents in FIXTURE_DEFINITIONS:
            contents = self._read_artifact(logical_name)
            digest = hashlib.sha256(contents).hexdigest()
            evidence.append(
                {
                    "artifact": logical_name,
                    "observation": "content-hashed",
                    "bytes": len(contents),
                    "sha256": digest,
                }
            )
            manifest_entries.append(
                {"artifact": logical_name, "bytes": len(contents), "sha256": digest}
            )
        manifest = json.dumps(
            {"synthetic": True, "entries": manifest_entries},
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        self._create_member(MANIFEST_ARTIFACT, "stage-manifest-", ".json", manifest)
        evidence.append(
            {
                "artifact": MANIFEST_ARTIFACT,
                "observation": "manifest-written",
                "entries": len(manifest_entries),
            }
        )
        return evidence

    def _create_canary(self) -> list[dict[str, Any]]:
        observation = "canary-already-present"
        if CANARY_ARTIFACT not in self._artifacts:
            self._create_member(CANARY_ARTIFACT, "marker-canary-", ".json", _CANARY_BYTES)
            observation = "canary-created"
        else:
            self._read_artifact(CANARY_ARTIFACT)
        return [
            {
                "artifact": CANARY_ARTIFACT,
                "observation": observation,
                "bytes": len(_CANARY_BYTES),
            }
        ]

    def _cleanup_artifacts(self) -> list[dict[str, str]]:
        evidence = []
        for logical_name in (MANIFEST_ARTIFACT, CANARY_ARTIFACT):
            removed = self._remove_artifact(logical_name)
            evidence.append(
                {
                    "artifact": logical_name,
                    "observation": "artifact-removed" if removed else "artifact-absent",
                }
            )
        return evidence
