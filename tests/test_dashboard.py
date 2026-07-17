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
        self.label_depth = 0
        self.buttons_inside_labels = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "label":
            self.label_depth += 1
        elif tag == "button" and self.label_depth:
            self.buttons_inside_labels += 1
        if attributes.get("id"):
            self.ids.append(attributes["id"] or "")
        if tag == "script" and attributes.get("src"):
            self.assets.append(attributes["src"] or "")
        if tag == "link" and attributes.get("href"):
            self.assets.append(attributes["href"] or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "label":
            self.label_depth -= 1


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")
        cls.javascript = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.styles = (STATIC / "styles.css").read_text(encoding="utf-8")

    def javascript_function(self, name: str) -> str:
        start = self.javascript.index(f"function {name}(")
        end = self.javascript.index("\n}\n", start) + 2
        return self.javascript[start:end]

    def test_dashboard_ids_are_unique_and_assets_are_local(self) -> None:
        parser = IdCollector()
        parser.feed(self.html)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        self.assertEqual(parser.assets, ["/static/styles.css", "/static/app.js"])
        for source in (self.html, self.javascript, self.styles):
            without_svg_namespace = source.replace("http://www.w3.org/2000/svg", "")
            self.assertNotIn("https://", without_svg_namespace)
            self.assertNotIn("http://", without_svg_namespace)
        self.assertEqual(
            self.javascript.count('const SVG_NS = "http://www.w3.org/2000/svg";'),
            1,
        )

    def test_form_labels_do_not_nest_buttons(self) -> None:
        parser = IdCollector()
        parser.feed(self.html)
        self.assertEqual(parser.label_depth, 0)
        self.assertEqual(parser.buttons_inside_labels, 0)

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
        self.assertIn('let openTaskDetailId = "";', self.javascript)
        self.assertIn("refreshOpenTaskDetail(tasks, nodes);", self.javascript)
        self.assertIn('if (task.status === "queued")', self.javascript)

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
        note_input = re.search(r'<textarea\s+[^>]*id="noteInput"[^>]*>', self.html)
        self.assertIsNotNone(note_input)
        self.assertNotIn("maxlength", note_input.group(0))
        self.assertIn("function unicodeLength(value)", self.javascript)
        self.assertIn("function hasSupportedTextCharacters(value)", self.javascript)
        self.assertIn("unicodeLength(noteMessage)", self.javascript)
        self.assertIn("unicodeLength(message) > MAX_NOTE_LENGTH", self.javascript)
        self.assertIn("unicodeLength(payload.text.trim())", self.javascript)
        self.assertIn("unicodeLength(payload.message.trim())", self.javascript)
        self.assertIn("hasSupportedTextCharacters(payload.text)", self.javascript)
        self.assertIn("hasSupportedTextCharacters(payload.message)", self.javascript)
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
        for request in (
            'await api(`/lab/tasks/${task.id}/cancel`, { method: "POST", body: {} });',
            'const task = await api("/lab/tasks", {',
            'await api("/lab/reset", { method: "POST", body: {} });',
        ):
            request_offset = self.javascript.index(request)
            guard_offset = self.javascript.index(
                "if (requestIsStale(requestGeneration, requestToken)) return;",
                request_offset,
            )
            self.assertLess(guard_offset - request_offset, 500)

    def test_unchanged_task_rows_are_not_rebuilt_during_polling(self) -> None:
        self.assertIn('let renderedNodeKey = ""', self.javascript)
        self.assertIn("if (nodeKey === renderedNodeKey)", self.javascript)
        self.assertIn('let renderedTaskKey = ""', self.javascript)
        self.assertIn("if (renderKey === renderedTaskKey) return", self.javascript)
        self.assertIn('let renderedHistoryKey = ""', self.javascript)
        self.assertIn("if (historyKey === renderedHistoryKey) return", self.javascript)
        self.assertNotIn("const interacting = Boolean(", self.javascript)
        self.assertNotIn('activeElement?.matches("input, select, textarea, summary")', self.javascript)
        self.assertRegex(
            self.javascript,
            r'window\.setInterval\(\(\) => \{\s+if \(document\.visibilityState === "visible"\)',
        )

    def test_live_regions_and_narrow_layout_remain_usable(self) -> None:
        self.assertIn("function setText(element, value)", self.javascript)
        self.assertIn("setText(element, Number.isFinite(value)", self.javascript)
        self.assertIn("setText(elements.noteCharacterCount", self.javascript)
        self.assertNotIn("#resetButton {\n    display: none;", self.styles)
        self.assertIn("cell.dataset.label = label", self.javascript)
        self.assertIn('statusCell.dataset.label = "STATUS"', self.javascript)
        self.assertIn("content: attr(data-label);", self.styles)

    def test_attack_exercise_ui_uses_fixed_catalog_and_rbac_actions(self) -> None:
        for element_id in (
            "exercises",
            "exerciseForm",
            "exerciseNodeSelect",
            "exerciseScenarioSelect",
            "createExerciseButton",
            "exerciseList",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn('api("/lab/exercises"', self.javascript)
        self.assertIn("/lab/exercises/${encodeURIComponent(exerciseId)}/contain", self.javascript)
        self.assertIn('hasPermission("exercise_write")', self.javascript)
        self.assertIn('hasPermission("containment_write")', self.javascript)
        self.assertIn('"CANCEL_REMAINING"', self.javascript)
        self.assertIn('"PAUSE_NODE_TASKING"', self.javascript)
        self.assertNotIn("INVALIDATE_NODE_SESSION", self.javascript)
        self.assertIn("node.tasking_paused !== true", self.javascript)
        self.assertIn("canContainExercise(exercise)", self.javascript)
        self.assertIn("overview.scenario_catalog", self.javascript)
        self.assertIn("overview.exercises", self.javascript)

    def test_graph_view_is_a_bounded_read_only_sync_projection(self) -> None:
        for element_id in (
            "graph",
            "graphViewSelect",
            "graphFocusSelect",
            "graphZoomInput",
            "graphFitButton",
            "graphSvg",
            "graphInspector",
            "graphPathList",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn('href="#graph"', self.html)
        self.assertIn('<option value="topology">C2トポロジー</option>', self.html)
        self.assertIn('<option value="attack">Synthetic Attack Path</option>', self.html)
        self.assertIn("const MAX_GRAPH_NODES = 20;", self.javascript)
        self.assertIn("const MAX_GRAPH_TASKS = 40;", self.javascript)
        self.assertIn("function buildTopologyGraph(overview, focusValue)", self.javascript)
        self.assertIn("const emittedOperations = new Set();", self.javascript)
        self.assertIn("(left.operation_step || 0) - (right.operation_step || 0)", self.javascript)
        self.assertIn("function buildAttackGraph(overview, focusValue)", self.javascript)
        self.assertIn("renderGraph(latestOverview);", self.javascript)
        self.assertNotIn("/lab/graph", self.javascript)
        for library_name in ("cytoscape", "vis-network", "d3.min", "dagre"):
            self.assertNotIn(library_name, self.html + self.javascript)

    def test_graph_normalizes_fixed_attack_metadata_and_state_joins(self) -> None:
        for normalized_field in (
            "playbooks:",
            "detections:",
            "containment_actions:",
            "scope:",
        ):
            self.assertIn(normalized_field, self.javascript)
        for join in (
            "task.node_id",
            "exercise?.node_id",
            "exercise?.task_ids",
            "task?.result?.attack_techniques",
            "candidate.rule_id === rule.id",
            'exercise?.containment?.status === "applied"',
        ):
            self.assertIn(join, self.javascript)
        self.assertIn('source.host_access === false ? false : null', self.javascript)
        self.assertIn('source.network_access === false ? false : null', self.javascript)
        self.assertIn('edge.observed ? "OBSERVED" : "PLANNED"', self.javascript)
        self.assertIn('.graph-edge[data-observed="false"]', self.styles)

    def test_graph_keeps_topology_playbooks_and_retained_exercise_evidence_visible(self) -> None:
        self.assertIn('const playbookId = `topology-playbook:${task.id}:${playbook}`;', self.javascript)
        self.assertIn('`topology:${task.id}:${playbook}`', self.javascript)
        self.assertIn('const exerciseTimeline = Array.isArray(exercise?.timeline)', self.javascript)
        self.assertIn('"retained exercise timeline"', self.javascript)
        self.assertIn('"retained exercise alert"', self.javascript)
        self.assertIn('"task.expired"', self.javascript)
        self.assertIn("function retainedTechniqueIdsForTask(timeline, taskId)", self.javascript)
        self.assertIn('item.kind === "technique.observed"', self.javascript)
        self.assertIn("const retainedTechniqueIds = retainedTechniqueIdsForTask(", self.javascript)
        self.assertIn("...retainedTechniqueIds", self.javascript)
        self.assertIn('const observedTechniqueIds = new Set(', self.javascript)

    def test_graph_task_cap_never_spends_active_slots_on_terminal_siblings(self) -> None:
        selection = self.javascript_function("graphTaskSelection")
        self.assertIn("active.slice(0, MAX_GRAPH_TASKS)", selection)
        self.assertIn("if (selectedTasks.size >= MAX_GRAPH_TASKS) break;", selection)
        self.assertIn("selectedTasks.has(task)", selection)
        self.assertIn("return groupGraphTasksByOperation(selectedSeeds);", selection)
        self.assertLess(
            selection.index("active.slice(0, MAX_GRAPH_TASKS)"),
            selection.index("for (const task of terminal)"),
        )
        self.assertLess(
            selection.index("for (const task of terminal)"),
            selection.index("return groupGraphTasksByOperation(selectedSeeds);"),
        )

    def test_graph_connects_containment_to_detections_across_all_playbooks(self) -> None:
        self.assertIn("const containmentAnchors = [];", self.javascript)
        self.assertIn("containmentAnchors.push(...detectionAnchors);", self.javascript)
        self.assertIn(
            "const containmentSources = containmentAnchors.length ? containmentAnchors : anchors;",
            self.javascript,
        )

    def test_graph_has_native_svg_and_native_button_keyboard_alternative(self) -> None:
        graph_svg = re.search(r'<svg\s+[^>]*id="graphSvg"[^>]*>', self.html)
        self.assertIsNotNone(graph_svg)
        self.assertIn('role="img"', graph_svg.group(0))
        graph_viewport = re.search(
            r'<div\s+[^>]*class="graph-viewport"[^>]*id="graphViewport"[^>]*>',
            self.html,
        )
        self.assertIsNotNone(graph_viewport)
        self.assertNotIn("tabindex", graph_viewport.group(0))
        self.assertIn('button.className = "graph-path-button";', self.javascript)
        self.assertIn('button.type = "button";', self.javascript)
        self.assertIn('button.dataset.graphEdgeId = `entity:${entity.id}`;', self.javascript)
        self.assertIn('button.addEventListener("click", () => activateGraphEntity(entity.id));', self.javascript)
        self.assertIn('button.addEventListener("click", () => activateGraphEntity(to.id));', self.javascript)
        self.assertLess(
            self.javascript.index("const destinationIds = new Set(model.edges.map"),
            self.javascript.index("if (!model.edges.length)"),
        )
        self.assertNotIn('group.setAttribute("tabindex"', self.javascript)
        self.assertNotIn('group.setAttribute("role", "button")', self.javascript)

    def test_graph_selection_survives_polling_and_hands_off_to_existing_controls(self) -> None:
        self.assertIn('let graphSelectedEntityId = "";', self.javascript)
        self.assertIn('let renderedGraphKey = "";', self.javascript)
        self.assertIn('if (renderKey === renderedGraphKey)', self.javascript)
        self.assertIn("document.activeElement.dataset.graphEdgeId", self.javascript)
        self.assertIn("button.dataset.graphEdgeId === activePathEdge", self.javascript)
        self.assertIn("function primeGraphPlaybook(playbook, nodeId)", self.javascript)
        self.assertIn("selectNodeForTask(entity.action.nodeId);", self.javascript)
        self.assertIn("showTaskDetail(task, node?.name || task.node_id);", self.javascript)
        self.assertIn('elements.taskTypeSelect.value = "RUN_PLAYBOOK";', self.javascript)
        self.assertIn("elements.playbookSelect.value = playbook;", self.javascript)
        self.assertIn("function fitGraphToViewport()", self.javascript)
        self.assertIn("GRAPH_ZOOM_MIN", self.javascript)
        self.assertIn("GRAPH_ZOOM_MAX", self.javascript)

    def test_graph_fit_and_action_focus_handoff_cover_the_full_viewport(self) -> None:
        zoom = re.search(r'<input\s+[^>]*id="graphZoomInput"[^>]*>', self.html)
        self.assertIsNotNone(zoom)
        self.assertIn('min="10"', zoom.group(0))
        self.assertIn("const GRAPH_ZOOM_MIN = 10;", self.javascript)
        fit = self.javascript_function("fitGraphToViewport")
        self.assertIn("elements.graphViewport.clientWidth", fit)
        self.assertIn("elements.graphViewport.clientHeight", fit)
        self.assertIn("availableHeight / currentGraphDimensions.height", fit)

        activation = self.javascript_function("activateGraphEntity")
        rendering = self.javascript_function("renderGraph")
        self.assertIn("!graphEntityActionCanHandoff(entity)", activation)
        self.assertIn("restorePathFocus && elements.graphPathList.contains", rendering)
        graph_svg_rule = self.styles.split(".graph-viewport svg {", 1)[1].split("}", 1)[0]
        self.assertNotIn("min-width", graph_svg_rule)
        self.assertNotIn("min-height", graph_svg_rule)

    def test_topology_connection_state_requires_a_successful_overview(self) -> None:
        topology = self.javascript_function("buildTopologyGraph")
        status = self.javascript_function("teamserverGraphStatus")
        rendered_overview = self.javascript_function("renderOverview")
        cleared_overview = self.javascript_function("clearOverview")
        refresh = self.javascript_function("refresh")
        self.assertIn('["online", "offline", "unknown"].includes(status)', status)
        self.assertIn("teamserverGraphStatus(overview)", topology)
        self.assertNotIn('operatorToken ? "online" : "offline"', topology)
        self.assertIn('connection_status: "online"', rendered_overview)
        self.assertIn('connection_status: "offline"', cleared_overview)
        self.assertIn('latestOverview = { ...latestOverview, connection_status: "unknown" };', refresh)
        self.assertIn("renderGraph(latestOverview);", refresh)

    def test_operation_builder_composes_only_bounded_fixed_playbooks(self) -> None:
        for element_id in (
            "operationBuilder",
            "operationForm",
            "operationNodeSelect",
            "operationTtlSelect",
            "operationStepList",
            "operationPlaybookSelect",
            "operationAddStepButton",
            "operationLoadPathButton",
            "operationPreviewInput",
            "operationSubmitButton",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        preview = re.search(
            r'<textarea\s+[^>]*id="operationPreviewInput"[^>]*>',
            self.html,
        )
        self.assertIsNotNone(preview)
        self.assertIn("readonly", preview.group(0))
        self.assertIn('aria-label="Operation request JSON（読み取り専用）"', preview.group(0))
        self.assertIn("const MAX_OPERATION_STEPS = 3;", self.javascript)
        self.assertIn("const OPERATION_PLAYBOOKS = Object.freeze([", self.javascript)
        self.assertIn("operationSteps.length <= MAX_OPERATION_STEPS", self.javascript)
        self.assertIn("operationSteps.every((playbook) => GRAPH_PLAYBOOK_IDS.has(playbook))", self.javascript)
        self.assertIn("steps: operationSteps.map((playbook) => ({ playbook }))", self.javascript)
        self.assertIn('api("/lab/operations", {', self.javascript)
        self.assertIn("pendingOperationSubmission", self.javascript)
        self.assertNotIn("RUN_OPERATION", self.html + self.javascript)

    def test_operation_builder_supports_ordering_path_load_and_rbac(self) -> None:
        self.assertIn("function moveOperationStep(fromIndex, toIndex)", self.javascript)
        self.assertIn('up.dataset.operationMove = "up";', self.javascript)
        self.assertIn('down.dataset.operationMove = "down";', self.javascript)
        self.assertIn("operationSteps = operationSteps.filter", self.javascript)
        self.assertIn("function loadFocusedAttackPathIntoOperation()", self.javascript)
        self.assertIn(".slice(0, MAX_OPERATION_STEPS)", self.javascript)
        self.assertIn('hasPermission("task_write")', self.javascript)
        self.assertIn('elements.operationForm.dataset.permission = canWriteTasks ? "allowed" : "denied";', self.javascript)
        self.assertIn("operationNodeEligible(node)", self.javascript)
        self.assertIn("node.capabilities.includes(\"RUN_PLAYBOOK\")", self.javascript)
        self.assertIn("task.operation_id", self.javascript)
        self.assertIn("task.operation_step", self.javascript)
        self.assertIn(".operation-builder", self.styles)
        self.assertIn(".operation-step", self.styles)

    def test_operation_target_is_independent_from_task_and_exercise_selection(self) -> None:
        operation_nodes = self.javascript_function("renderOperationNodes")
        task_handoff = self.javascript_function("selectNodeForTask")
        self.assertNotIn("elements.taskNodeSelect.value", operation_nodes)
        self.assertNotIn("elements.exerciseNodeSelect.value", operation_nodes)
        self.assertNotIn("eligible[0]", operation_nodes)
        self.assertIn("preservedOperationNodeId(eligible, previousNodeId)", operation_nodes)
        self.assertNotIn("elements.operationNodeSelect", task_handoff)
        self.assertNotIn("elements.operationNodeSelect.value = nodeId;", self.javascript)

    def test_boundary_test_totals_match_the_enforced_module(self) -> None:
        boundary_tests = Path(__file__).with_name("test_safety_boundary.py").read_text(
            encoding="utf-8"
        )
        enforced_count = len(re.findall(r"^    def test_", boundary_tests, re.MULTILINE))
        counts = [
            int(value)
            for value in re.findall(
                r'<tr data-boundary-group="[^"]+">.*?<td class="center">(\d+)</td>',
                self.html,
                re.DOTALL,
            )
        ]
        self.assertEqual(len(counts), 13)
        self.assertEqual(sum(counts), enforced_count)
        self.assertIn(f"全{enforced_count}テスト", self.html)
        self.assertIn(f"<strong>{enforced_count}</strong> テスト", self.html)
        boundary_filters = re.findall(
            r'<button class="boundary-filter__btn[^"]*"[^>]*>', self.html
        )
        self.assertEqual(len(boundary_filters), 5)
        self.assertEqual(
            sum('aria-pressed="true"' in button for button in boundary_filters),
            1,
        )
        self.assertIn('candidate.setAttribute("aria-pressed", String(selected));', self.javascript)

    def test_task_composer_uses_typed_controls_and_read_only_json_preview(self) -> None:
        for element_id in (
            "taskTextInput",
            "waitRangeInput",
            "waitNumberInput",
            "eventCategorySelect",
            "eventSeveritySelect",
            "eventMessageInput",
            "sleepIntervalRangeInput",
            "sleepIntervalNumberInput",
            "sleepJitterRangeInput",
            "sleepJitterNumberInput",
            "exitConfirmInput",
            "playbookSelect",
            "taskPayloadInput",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        payload_preview = re.search(
            r'<textarea\s+[^>]*id="taskPayloadInput"[^>]*>',
            self.html,
        )
        self.assertIsNotNone(payload_preview)
        self.assertIn("readonly", payload_preview.group(0))
        self.assertNotIn("JSON.parse(elements.taskPayloadInput.value)", self.javascript)
        self.assertIn("function taskPayloadFromControls()", self.javascript)
        self.assertIn("text: elements.taskTextInput.value.trim()", self.javascript)
        self.assertIn("message: elements.eventMessageInput.value.trim()", self.javascript)
        self.assertIn("elements.waitNumberInput.valueAsNumber", self.javascript)
        self.assertIn("elements.sleepIntervalNumberInput.valueAsNumber", self.javascript)
        self.assertIn("elements.sleepJitterNumberInput.valueAsNumber", self.javascript)
        self.assertIn("node?.poll_interval_ms", self.javascript)
        self.assertIn("node?.jitter_percent", self.javascript)
        self.assertIn("変更はresult acknowledgement後に適用されます", self.javascript)
        self.assertIn("function setTaskValidationState(valid)", self.javascript)
        self.assertIn('control.setAttribute("aria-invalid", "true")', self.javascript)
        self.assertNotIn('elements.taskPayloadInput.setAttribute("aria-invalid"', self.javascript)
        for label in (
            "待機時間（ミリ秒）",
            "Poll間隔（ミリ秒）",
            "Jitter（パーセント）",
        ):
            self.assertIn(f'aria-label="{label}"', self.html)
        self.assertIn("!elements.exitConfirmInput.checked ||", self.javascript)
        self.assertIn('let exitConfirmedNodeId = "";', self.javascript)
        self.assertIn("exitConfirmedNodeId !== elements.taskNodeSelect.value", self.javascript)
        self.assertIn('if (["SLEEP", "EXIT"].includes(elements.taskTypeSelect.value))', self.javascript)
        self.assertIn("selectedNodeChanged &&", self.javascript)
        self.assertGreaterEqual(self.javascript.count('exitConfirmedNodeId = "";'), 3)
        for element_id in ("taskTextInput", "eventMessageInput"):
            text_input = re.search(rf'<textarea\s+[^>]*id="{element_id}"[^>]*>', self.html)
            self.assertIsNotNone(text_input)
            self.assertNotIn("maxlength", text_input.group(0))
        self.assertIn("unicodeLength(elements.taskTextInput.value.trim())", self.javascript)
        self.assertIn("unicodeLength(elements.eventMessageInput.value.trim())", self.javascript)

    def test_task_guidance_explains_action_adjustments_and_safety(self) -> None:
        for element_id in (
            "taskGuidanceTitle",
            "taskGuidanceAction",
            "taskGuidanceAdjustable",
            "taskGuidanceSafety",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn("const TASK_GUIDANCE = Object.freeze({", self.javascript)
        for task_type in TASK_TYPES:
            self.assertRegex(self.javascript, rf"\n  {re.escape(task_type)}: Object\.freeze\(\{{")
        self.assertIn("何をする", self.html)
        self.assertIn("変更できる項目", self.html)
        self.assertIn("安全範囲", self.html)
        self.assertIn("host accessとnetwork accessは常に無効", self.javascript)

    def test_playbook_results_have_structured_scope_attack_steps_and_evidence(self) -> None:
        self.assertIn("function addPlaybookResult(container, result)", self.javascript)
        for label in (
            "実行スコープ",
            "ATT&CK マッピング",
            "実行ステップ",
            "Evidence",
            "検証済みraw JSONを表示",
        ):
            self.assertIn(label, self.javascript)
        for result_key in ("result.scope", "result.attack_techniques", "result.steps", "result.evidence"):
            self.assertIn(result_key, self.javascript)
        self.assertIn(
            'if (task.type === "RUN_PLAYBOOK" && task.status === "completed")',
            self.javascript,
        )
        self.assertIn("addPlaybookResult(elements.taskDetailBody, task.result);", self.javascript)
        self.assertIn(".playbook-result", self.styles)
        self.assertIn(".playbook-evidence-list", self.styles)

    def test_non_playbook_results_are_explained_before_raw_json(self) -> None:
        self.assertIn("function addTaskResultSummary(container, task)", self.javascript)
        for label in (
            "Node応答",
            "実際の待機時間",
            "変更前のPoll",
            "変更後のPoll",
            "停止ACK",
            "Raw payload / resultを確認",
        ):
            self.assertIn(label, self.javascript)
        self.assertIn("addTaskResultSummary(elements.taskDetailBody, task);", self.javascript)
        self.assertIn(".task-result-summary", self.styles)
        self.assertIn(".task-result-raw", self.styles)

    def test_wait_and_sleep_interval_controls_allow_exact_integer_tuning(self) -> None:
        for element_id in (
            "waitRangeInput",
            "waitNumberInput",
            "sleepIntervalRangeInput",
            "sleepIntervalNumberInput",
        ):
            element = re.search(rf'<input\s+[^>]*id="{element_id}"[^>]*>', self.html)
            self.assertIsNotNone(element)
            self.assertIn('step="1"', element.group(0))

    def test_ui_preferences_are_bounded_and_saved_per_tab(self) -> None:
        for element_id in (
            "autoRefreshSelect",
            "historyLimitSelect",
            "densitySelect",
            "autoRefreshStatus",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        for refresh_value in (1000, 3000, 5000, 10000):
            self.assertIn(f'<option value="{refresh_value}"', self.html)
        for history_limit in (25, 50, 100):
            self.assertIn(f'<option value="{history_limit}"', self.html)
        self.assertIn('const UI_SETTINGS_KEY = "c2lab.ui-settings";', self.javascript)
        self.assertIn("window.sessionStorage.setItem(", self.javascript)
        self.assertIn("historyRowLimit", self.javascript)
        self.assertIn("matches.slice(0, historyRowLimit)", self.javascript)
        self.assertIn("scheduleAutoRefresh();", self.javascript)
        self.assertIn("updateAutoRefreshStatus();", self.javascript)
        self.assertIn("`${refreshIntervalMs / 1000}秒ごとに自動更新`", self.javascript)
        self.assertIn("document.body.dataset.density = interfaceDensity", self.javascript)
        self.assertIn('body[data-density="compact"]', self.styles)

    def test_queue_ttl_is_typed_validated_and_part_of_idempotent_request(self) -> None:
        for element_id in (
            "queueTtlPresetSelect",
            "queueTtlCustomField",
            "queueTtlNumberInput",
            "queueTtlError",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        for ttl_value in (30, 300, 1800):
            self.assertIn(f'<option value="{ttl_value}"', self.html)
        self.assertIn("dispatch後の実行期限やWAIT時間とは別", self.html)
        self.assertIn("elements.queueTtlNumberInput.valueAsNumber", self.javascript)
        self.assertIn("Number.isInteger(queueTtlSeconds)", self.javascript)
        self.assertNotIn("parseInt(", self.javascript)
        self.assertIn("queue_ttl_seconds: queueTtlSeconds", self.javascript)
        request_body_offset = self.javascript.index("const requestBody = {", self.javascript.index("elements.taskForm.addEventListener"))
        signature_offset = self.javascript.index("const signature = JSON.stringify(requestBody);", request_body_offset)
        ttl_offset = self.javascript.index("queue_ttl_seconds: queueTtlSeconds", request_body_offset)
        self.assertLess(ttl_offset, signature_offset)


if __name__ == "__main__":
    unittest.main()
