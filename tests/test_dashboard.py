from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

from c2lab.core import TASK_TYPES


STATIC = Path(__file__).parents[1] / "c2lab" / "static"


class IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.assets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(attributes["id"] or "")
        if tag == "script" and attributes.get("src"):
            self.assets.append(attributes["src"] or "")
        if tag == "link" and attributes.get("href"):
            self.assets.append(attributes["href"] or "")


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")
        cls.javascript = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.styles = (STATIC / "styles.css").read_text(encoding="utf-8")

    def test_dashboard_ids_are_unique_and_assets_are_local(self) -> None:
        parser = IdCollector()
        parser.feed(self.html)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        self.assertEqual(parser.assets, ["/static/styles.css", "/static/app.js"])
        for source in (self.html, self.javascript, self.styles):
            self.assertNotIn("https://", source)
            self.assertNotIn("http://", source)

    def test_user_data_is_rendered_without_html_sinks(self) -> None:
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "new Function"):
            self.assertNotIn(sink, self.javascript)

    def test_ui_exposes_only_core_task_allowlist(self) -> None:
        for task_type in TASK_TYPES:
            self.assertIn(f'value="{task_type}"', self.html)
            self.assertIn(task_type, self.javascript)
        self.assertIn("LOCALHOST LAB", self.html)
        self.assertIn("REAL PROCESS NODE", self.html)
        self.assertIn("LAB-BOUND PLAYBOOKS", self.html)
        for playbook in (
            "DISCOVERY_FIXTURES",
            "COLLECT_AND_STAGE",
            "CREATE_CANARY",
            "CLEANUP",
        ):
            self.assertIn(playbook, self.javascript)
            self.assertIn(f'value="{playbook}"', self.html)
        self.assertIn('id="playbookSelect"', self.html)
        self.assertIn('elements.playbookField.hidden = !isPlaybook', self.javascript)
        self.assertIn('payload = { playbook: elements.playbookSelect.value }', self.javascript)
        self.assertNotIn("RUN_COMMAND", self.html + self.javascript)
        self.assertNotIn("/lab/agents", self.javascript)

    def test_javascript_element_registry_matches_document(self) -> None:
        parser = IdCollector()
        parser.feed(self.html)
        registry = self.javascript.split("const elementIds = [", 1)[1].split("];", 1)[0]
        referenced_ids = set(re.findall(r'^\s+"([A-Za-z][A-Za-z0-9]+)",$', registry, re.MULTILINE))
        self.assertTrue(referenced_ids)
        self.assertEqual(referenced_ids - set(parser.ids), set())

    def test_closed_node_sessions_are_not_taskable_in_ui(self) -> None:
        self.assertIn("session_active", self.javascript)
        self.assertIn("SESSION CLOSED", self.javascript)
        self.assertIn("option.disabled", self.javascript)
        self.assertIn("delivery_attempts", self.javascript)

    def test_operator_console_navigation_and_metric_drill_down_are_present(self) -> None:
        for target in ("overview", "nodes", "dispatch", "tasks", "events"):
            self.assertIn(f'href="#{target}"', self.html)
            self.assertRegex(self.html, rf'id="{target}"')
        for status in ("queued", "dispatched", "completed", "failed", "timeout"):
            self.assertIn(f'data-task-status="{status}"', self.html)
        self.assertIn('data-section-target="nodes"', self.html)
        self.assertIn(".section-nav", self.styles)
        self.assertIn("position: sticky", self.styles)

    def test_dashboard_has_safe_search_filters_and_node_task_handoff(self) -> None:
        for element_id in (
            "selectedNodeSummary",
            "taskSearchInput",
            "eventSearchInput",
            "eventLevelFilter",
            "eventActorFilter",
            "activitySourceFilter",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn("selectNodeForTask", self.javascript)
        self.assertIn("node-card__select", self.javascript)
        self.assertIn("task.correlation_id", self.javascript)
        self.assertIn("event.actor", self.javascript)
        self.assertIn("/lab/sync?events_after=", self.javascript)
        self.assertIn("auditEntryAsEvent", self.javascript)
        self.assertNotIn("hostname", self.html + self.javascript)

    def test_task_lifecycle_and_progressive_disclosure_are_present(self) -> None:
        self.assertIn("createTaskLifecycle", self.javascript)
        self.assertIn("task-lifecycle__steps", self.javascript)
        self.assertIn("setConnectedLayout", self.javascript)
        self.assertIn('state: status === "dispatched" ? "current" : wasDispatched ? "complete" : isTerminal ? "skipped" : "pending"', self.javascript)
        self.assertRegex(self.html, r'<details class="auth-card" id="tokenManagement" open>')
        self.assertRegex(self.html, r'<details class="panel startup-panel" id="startupPanel" open>')
        self.assertIn("NO SHELL · EPHEMERAL LAB FILES ONLY · NO REMOTE TRANSPORT", self.html)

    def test_task_retry_and_queue_controls_are_present(self) -> None:
        self.assertIn('headers["Idempotency-Key"]', self.javascript)
        self.assertIn("pendingTaskSubmission", self.javascript)
        self.assertIn("newIdempotencyKey", self.javascript)
        self.assertIn("/cancel`, { method: \"POST\", body: {} }", self.javascript)
        for status in ("cancelled", "expired"):
            self.assertIn(f'<option value="{status}">', self.html)
            self.assertIn(f'data-status="{status}"', self.styles)
            self.assertIn(status, self.javascript)

    def test_operator_session_and_rbac_controls_are_present(self) -> None:
        for element_id in ("operatorPrincipalId", "operatorRole"):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn('api("/lab/session")', self.javascript)
        self.assertIn(
            "const needsSession = sessionGeneration !== requestGeneration;",
            self.javascript,
        )
        self.assertIn(
            'const sessionRequest = needsSession ? api("/lab/session") : Promise.resolve(null);',
            self.javascript,
        )
        self.assertIn("let sessionPermissions = [];", self.javascript)
        self.assertIn('hasPermission("task_write")', self.javascript)
        self.assertIn('hasPermission("note_write")', self.javascript)
        self.assertIn('hasPermission("reset")', self.javascript)
        self.assertIn("error.status === 403", self.javascript)
        self.assertIn('data-task-cancel', self.javascript)
        self.assertNotIn("session.token", self.javascript)
        self.assertIn('.session-fact[data-state="active"]', self.styles)

    def test_dashboard_uses_bounded_cursor_delta_sync(self) -> None:
        for declaration in (
            "const SYNC_PAGE_SIZE = 100;",
            "const MAX_SYNC_PAGES = 10;",
            "const MAX_HISTORY_RECORDS = 500;",
            'let syncStreamId = "";',
            "let syncCursors = { events: 0, audit: 0 };",
            "let retainedHistory = { events: [], audit: [] };",
        ):
            self.assertIn(declaration, self.javascript)
        self.assertIn(
            "for (let pageIndex = 0; pageIndex < MAX_SYNC_PAGES; pageIndex += 1)",
            self.javascript,
        )
        self.assertIn("page.cursor_reset.events", self.javascript)
        self.assertIn("page.cursor_reset.audit", self.javascript)
        self.assertIn("page.stream_id !== nextStreamId", self.javascript)
        self.assertIn("nextCursors = { events: 0, audit: 0 };", self.javascript)
        self.assertIn("nextHistory = { events: [], audit: [] };", self.javascript)
        self.assertIn("page.has_more.events", self.javascript)
        self.assertIn("page.has_more.audit", self.javascript)
        self.assertIn(".slice(-MAX_HISTORY_RECORDS)", self.javascript)
        self.assertIn("requestIsStale(requestGeneration, requestToken)", self.javascript)
        self.assertNotIn('api("/lab/overview")', self.javascript)
        self.assertNotIn('api("/lab/audit")', self.javascript)

    def test_operator_notes_and_task_attribution_are_present(self) -> None:
        for element_id in (
            "noteForm",
            "noteInput",
            "notePermissionHint",
            "noteCharacterCount",
            "noteSubmitButton",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertRegex(self.html, r'id="noteInput"[\s\S]+?maxlength="240"')
        self.assertIn('api("/lab/notes", {', self.javascript)
        self.assertIn('body: { message },', self.javascript)
        self.assertIn("pendingNoteSubmission", self.javascript)
        self.assertIn('event.kind === "operator.note"', self.javascript)
        self.assertIn("CREATED BY", self.html)
        self.assertIn("task.created_by", self.javascript)
        self.assertIn('addDetailValue(grid, "Created by", task.created_by);', self.javascript)
        self.assertIn(".operator-note", self.styles)
        self.assertIn(".task-table th:nth-child(8)", self.styles)

    def test_token_refresh_races_and_busy_connect_button_are_guarded(self) -> None:
        self.assertIn("tokenGeneration", self.javascript)
        self.assertIn("requestGeneration !== tokenGeneration", self.javascript)
        self.assertIn("elements.connectButton.disabled", self.javascript)
        self.assertIn("setTokenVisibility(false)", self.javascript)

    def test_unchanged_task_rows_are_not_rebuilt_during_polling(self) -> None:
        self.assertIn('let renderedNodeKey = ""', self.javascript)
        self.assertIn("if (nodeKey === renderedNodeKey)", self.javascript)
        self.assertIn('let renderedTaskKey = ""', self.javascript)
        self.assertIn("if (renderKey === renderedTaskKey) return", self.javascript)
        self.assertIn('let renderedHistoryKey = ""', self.javascript)
        self.assertIn("if (historyKey === renderedHistoryKey) return", self.javascript)
        self.assertIn("elements.taskDetailDialog.open ||", self.javascript)
        self.assertIn('activeElement?.matches("input, select, textarea, summary")', self.javascript)


if __name__ == "__main__":
    unittest.main()
