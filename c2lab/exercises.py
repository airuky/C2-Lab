"""Fixed ATT&CK-aligned exercises for the bounded localhost lab.

The catalog is metadata only.  Every scenario is composed from existing
fixture-only playbooks and accepts no commands, paths, hosts, content, or
operator-defined detection logic.
"""

from __future__ import annotations

import copy
from typing import Any


MAX_EXERCISES = 50
MAX_EXERCISE_TIMELINE = 16
CONTAINMENT_ACTIONS = (
    "CANCEL_REMAINING",
    "PAUSE_NODE_TASKING",
)

_SCOPE = {
    "workspace": "ephemeral-node-private",
    "data": "synthetic-fixtures-only",
    "host_access": False,
    "network_access": False,
    "command_execution": False,
    "attack_mapping": "educational-only",
}

_TECHNIQUE_METADATA = {
    "T1083": {
        "id": "T1083",
        "name": "File and Directory Discovery",
        "url": "https://attack.mitre.org/techniques/T1083/",
    },
    "T1005": {
        "id": "T1005",
        "name": "Data from Local System",
        "url": "https://attack.mitre.org/techniques/T1005/",
    },
    "T1074.001": {
        "id": "T1074.001",
        "name": "Local Data Staging",
        "url": "https://attack.mitre.org/techniques/T1074/001/",
    },
    "T1070.004": {
        "id": "T1070.004",
        "name": "File Deletion",
        "url": "https://attack.mitre.org/techniques/T1070/004/",
    },
}


def _technique(technique_id: str) -> dict[str, str]:
    return copy.deepcopy(_TECHNIQUE_METADATA[technique_id])

_SCENARIOS: dict[str, dict[str, Any]] = {
    "DISCOVERY_COLLECTION": {
        "id": "DISCOVERY_COLLECTION",
        "title": "Synthetic discovery and collection",
        "description": (
            "Enumerate the fixed fixture registry, read only synthetic fixtures, "
            "and create a bounded digest manifest in the private workspace."
        ),
        "scope": _SCOPE,
        "playbooks": ["DISCOVERY_FIXTURES", "COLLECT_AND_STAGE"],
        "techniques": [
            _technique("T1083"),
            _technique("T1005"),
            _technique("T1074.001"),
        ],
        "detections": [
            {
                "id": "C2LAB-DET-001",
                "source_id": "DET0370",
                "name": "Synthetic fixture enumeration",
                "playbook": "DISCOVERY_FIXTURES",
                "technique_id": "T1083",
                "signal": "fixture-registry-enumerated",
                "severity": "medium",
            },
            {
                "id": "C2LAB-DET-002",
                "source_id": "DET0380",
                "name": "Synthetic fixture collection",
                "playbook": "COLLECT_AND_STAGE",
                "technique_id": "T1005",
                "signal": "synthetic-fixtures-read",
                "severity": "high",
            },
            {
                "id": "C2LAB-DET-003",
                "source_id": "DET0261",
                "name": "Synthetic manifest staging",
                "playbook": "COLLECT_AND_STAGE",
                "technique_id": "T1074.001",
                "signal": "staging-manifest-created",
                "severity": "high",
            },
        ],
        "containment_actions": list(CONTAINMENT_ACTIONS),
    },
    "CANARY_REMOVAL": {
        "id": "CANARY_REMOVAL",
        "title": "Synthetic canary removal",
        "description": (
            "Create and remove only the fixed canary and bounded staging artifact "
            "inside the private workspace."
        ),
        "scope": _SCOPE,
        "playbooks": ["CREATE_CANARY", "CLEANUP"],
        "techniques": [_technique("T1070.004")],
        "detections": [
            {
                "id": "C2LAB-DET-004",
                "source_id": "DET0140",
                "name": "Synthetic canary removal",
                "playbook": "CLEANUP",
                "technique_id": "T1070.004",
                "signal": "workspace-artifacts-cleaned",
                "severity": "high",
            }
        ],
        "containment_actions": list(CONTAINMENT_ACTIONS),
    },
}


SCENARIO_IDS = frozenset(_SCENARIOS)


def scenario_catalog() -> list[dict[str, Any]]:
    """Return a detached, fixed-order public scenario catalog."""

    return [copy.deepcopy(_SCENARIOS[scenario_id]) for scenario_id in _SCENARIOS]


def scenario_definition(scenario_id: str) -> dict[str, Any]:
    """Return one detached scenario definition or raise ``KeyError``."""

    return copy.deepcopy(_SCENARIOS[scenario_id])


def detections_for_playbook(
    scenario_id: str,
    playbook: str,
) -> list[dict[str, Any]]:
    """Return fixed detection metadata associated with one scenario step."""

    return [
        copy.deepcopy(rule)
        for rule in _SCENARIOS[scenario_id]["detections"]
        if rule["playbook"] == playbook
    ]


def technique_identity(technique_id: str) -> dict[str, str]:
    """Return only the fixed ID/name pair used by playbook results."""

    technique = _TECHNIQUE_METADATA[technique_id]
    return {"id": technique["id"], "name": technique["name"]}
