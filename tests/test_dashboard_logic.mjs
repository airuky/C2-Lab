import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";


const javascript = readFileSync(
  new URL("../c2lab/static/app.js", import.meta.url),
  "utf8",
);


function functionSource(name) {
  const marker = `function ${name}(`;
  const start = javascript.indexOf(marker);
  assert.notEqual(start, -1, `${name} must exist in app.js`);
  const end = javascript.indexOf("\n}\n", start);
  assert.notEqual(end, -1, `${name} must have a top-level closing brace`);
  return javascript.slice(start, end + 2);
}


test("graph task cap retains every active task before terminal operation siblings", () => {
  const tasks = [];
  for (let operation = 1; operation <= 14; operation += 1) {
    for (const [step, status] of [[1, "completed"], [2, "dispatched"], [3, "queued"]]) {
      tasks.push({
        id: `operation-${operation}-step-${step}`,
        operation_id: `operation-${operation}`,
        operation_step: step,
        status,
      });
    }
  }
  const context = { tasks };
  vm.createContext(context);
  vm.runInContext(
    [
      "const MAX_GRAPH_TASKS = 40;",
      functionSource("groupGraphTasksByOperation"),
      functionSource("graphTaskSelection"),
      "globalThis.selected = graphTaskSelection(tasks);",
    ].join("\n"),
    context,
  );

  const selectedIds = new Set(context.selected.map((task) => task.id));
  const selected = Array.from(context.selected);
  const activeIds = tasks
    .filter((task) => ["queued", "dispatched"].includes(task.status))
    .map((task) => task.id);
  assert.equal(context.selected.length, 40);
  assert.ok(activeIds.every((taskId) => selectedIds.has(taskId)));
  for (let operation = 1; operation <= 14; operation += 1) {
    const operationId = `operation-${operation}`;
    const indexes = selected
      .map((task, index) => task.operation_id === operationId ? index : -1)
      .filter((index) => index >= 0);
    assert.equal(Math.max(...indexes) - Math.min(...indexes) + 1, indexes.length);
    const steps = indexes.map((index) => selected[index].operation_step);
    assert.deepEqual(
      steps,
      [...steps].sort((left, right) => left - right),
    );
  }
});


test("graph task cap contains only active tasks when active records exceed the cap", () => {
  const tasks = [
    ...Array.from({ length: 45 }, (_value, index) => ({
      id: `active-${index}`,
      status: index % 2 ? "queued" : "dispatched",
    })),
    ...Array.from({ length: 5 }, (_value, index) => ({
      id: `terminal-${index}`,
      status: "completed",
    })),
  ];
  const context = { tasks };
  vm.createContext(context);
  vm.runInContext(
    [
      "const MAX_GRAPH_TASKS = 40;",
      functionSource("groupGraphTasksByOperation"),
      functionSource("graphTaskSelection"),
      "globalThis.selected = graphTaskSelection(tasks);",
    ].join("\n"),
    context,
  );

  assert.equal(context.selected.length, 40);
  assert.ok(context.selected.every((task) => ["queued", "dispatched"].includes(task.status)));
});


test("graph task cap is record-bounded even when IDs are duplicate or empty", () => {
  const tasks = [
    ...Array.from({ length: 45 }, (_value, index) => ({
      id: index < 42 ? "duplicate" : "",
      status: "queued",
    })),
    { id: "duplicate", status: "completed" },
  ];
  const context = { tasks };
  vm.createContext(context);
  vm.runInContext(
    [
      "const MAX_GRAPH_TASKS = 40;",
      functionSource("groupGraphTasksByOperation"),
      functionSource("graphTaskSelection"),
      "globalThis.selected = graphTaskSelection(tasks);",
    ].join("\n"),
    context,
  );

  assert.equal(context.selected.length, 40);
  assert.ok(context.selected.every((task) => task.status === "queued"));
});


test("graph task cap deduplicates repeated references before selecting records", () => {
  const sharedTask = { id: "shared", status: "queued" };
  const context = { tasks: Array(50).fill(sharedTask) };
  vm.createContext(context);
  vm.runInContext(
    [
      "const MAX_GRAPH_TASKS = 40;",
      functionSource("groupGraphTasksByOperation"),
      functionSource("graphTaskSelection"),
      "globalThis.selected = graphTaskSelection(tasks);",
    ].join("\n"),
    context,
  );

  assert.equal(context.selected.length, 1);
  assert.equal(context.selected[0].id, "shared");
});


test("topology graph rejects empty and duplicate task IDs", () => {
  const overview = {
    connection_status: "online",
    nodes: [{ id: "node-a", name: "Node A", status: "online" }],
    tasks: [
      { id: "", node_id: "node-a", type: "PING", status: "queued" },
      { id: "same", node_id: "node-a", type: "PING", status: "queued" },
      { id: "same", node_id: "node-a", type: "WAIT", status: "completed" },
      { id: "other", node_id: "node-a", type: "WAIT", status: "completed" },
      { id: "wrong-node", node_id: "node-b", type: "PING", status: "queued" },
    ],
  };
  const context = { overview };
  vm.createContext(context);
  vm.runInContext(
    [
      "const MAX_GRAPH_NODES = 20;",
      "const MAX_GRAPH_TASKS = 40;",
      "const GRAPH_PLAYBOOK_IDS = new Set();",
      "const GRAPH_STATUSES = new Set(['online', 'queued', 'completed', 'unknown']);",
      functionSource("recordText"),
      functionSource("normalizedGraphStatus"),
      functionSource("graphEntity"),
      functionSource("graphRelation"),
      functionSource("teamserverGraphStatus"),
      functionSource("groupGraphTasksByOperation"),
      functionSource("graphTaskSelection"),
      functionSource("buildTopologyGraph"),
      "globalThis.model = buildTopologyGraph(overview, '');",
    ].join("\n"),
    context,
  );

  const taskIds = Array.from(context.model.entities)
    .filter((entity) => entity.kind === "task")
    .map((entity) => entity.id);
  assert.deepEqual(taskIds, ["task:same", "task:other"]);
  assert.equal(new Set(taskIds).size, taskIds.length);
});


test("fit zoom below fifty percent survives normal zoom reapplication", () => {
  const context = {};
  vm.createContext(context);
  vm.runInContext(
    [
      "const GRAPH_ZOOM_MIN = 10;",
      "const GRAPH_ZOOM_MAX = 160;",
      "const GRAPH_ZOOM_STEP = 10;",
      "let graphZoomPercent = 100;",
      "const currentGraphDimensions = { width: 4000, height: 1000 };",
      "const elements = {",
      "  graphZoomInput: { value: '100' },",
      "  graphZoomOutput: { value: '', textContent: '' },",
      "  graphSvg: { style: {} },",
      "  graphViewport: { clientWidth: 824, clientHeight: 1024, scrollTo() {} },",
      "};",
      functionSource("applyGraphZoom"),
      functionSource("fitGraphToViewport"),
      "fitGraphToViewport();",
      "applyGraphZoom();",
      "globalThis.result = {",
      "  percent: graphZoomPercent,",
      "  input: elements.graphZoomInput.value,",
      "  output: elements.graphZoomOutput.textContent,",
      "  width: elements.graphSvg.style.width,",
      "};",
    ].join("\n"),
    context,
  );

  assert.deepEqual(
    JSON.parse(JSON.stringify(context.result)),
    { percent: 20, input: "20", output: "20%", width: "800px" },
  );
});


test("graph action hands focus off without restoring the old path button", () => {
  const handoffState = { renderOptions: null, focusOwner: "graph" };
  const context = {
    currentGraphModel: {
      entities: [{ id: "task:one", action: { kind: "task", taskId: "task-one" } }],
    },
    graphSelectedEntityId: "",
    renderedGraphKey: "rendered",
    latestOverview: { tasks: [{ id: "task-one" }] },
    handoffState,
    renderGraph(_overview, options) {
      handoffState.renderOptions = options;
    },
    performGraphEntityAction() {
      handoffState.focusOwner = "composer";
    },
  };
  vm.createContext(context);
  vm.runInContext(
    [
      functionSource("graphEntityActionCanHandoff"),
      functionSource("activateGraphEntity"),
      "activateGraphEntity('task:one');",
      "globalThis.result = handoffState;",
    ].join("\n"),
    context,
  );

  assert.equal(context.result.renderOptions.restorePathFocus, false);
  assert.equal(context.result.focusOwner, "composer");
});


test("graph action failure restores the relation button focus", () => {
  const handoffState = { renderOptions: null, focusOwner: "graph" };
  const context = {
    currentGraphModel: {
      entities: [{
        id: "playbook:planned",
        action: { kind: "playbook", playbook: "DISCOVERY_FIXTURES", nodeId: "" },
      }],
    },
    graphSelectedEntityId: "",
    renderedGraphKey: "rendered",
    latestOverview: { tasks: [], nodes: [] },
    handoffState,
    renderGraph(_overview, options) {
      handoffState.renderOptions = options;
    },
    performGraphEntityAction() {},
  };
  vm.createContext(context);
  vm.runInContext(
    [
      "const GRAPH_PLAYBOOK_IDS = new Set(['DISCOVERY_FIXTURES']);",
      functionSource("graphEntityActionCanHandoff"),
      functionSource("activateGraphEntity"),
      "activateGraphEntity('playbook:planned');",
      "globalThis.result = handoffState;",
    ].join("\n"),
    context,
  );

  assert.equal(context.result.renderOptions.restorePathFocus, true);
  assert.equal(context.result.focusOwner, "graph");
});


test("teamserver graph status requires a successful authenticated overview", () => {
  const context = {};
  vm.createContext(context);
  vm.runInContext(
    [
      functionSource("teamserverGraphStatus"),
      "globalThis.result = [",
      "  teamserverGraphStatus({ connection_status: 'online' }),",
      "  teamserverGraphStatus({ connection_status: 'offline' }),",
      "  teamserverGraphStatus({ connection_status: 'unknown' }),",
      "  teamserverGraphStatus({}),",
      "  teamserverGraphStatus(null),",
      "];",
    ].join("\n"),
    context,
  );

  assert.deepEqual(
    Array.from(context.result),
    ["online", "offline", "unknown", "offline", "offline"],
  );
});


test("retained technique evidence is allowlisted and scoped to one task", () => {
  const context = {
    timeline: [
      { kind: "technique.observed", task_id: "task-one", technique_id: "T1005" },
      { kind: "technique.observed", task_id: "task-one", technique_id: "UNKNOWN" },
      { kind: "technique.observed", task_id: "task-two", technique_id: "T1083" },
      { kind: "detection.matched", task_id: "task-one", technique_id: "T1074.001" },
    ],
  };
  vm.createContext(context);
  vm.runInContext(
    [
      "const GRAPH_TECHNIQUE_IDS = new Set(['T1083', 'T1005', 'T1074.001', 'T1070.004']);",
      functionSource("retainedTechniqueIdsForTask"),
      "globalThis.result = Array.from(retainedTechniqueIdsForTask(timeline, 'task-one'));",
    ].join("\n"),
    context,
  );

  assert.deepEqual(Array.from(context.result), ["T1005"]);
});


test("operation target never falls back to a different eligible node", () => {
  const context = {
    eligible: [{ id: "node-a" }, { id: "node-b" }],
  };
  vm.createContext(context);
  vm.runInContext(
    [
      functionSource("preservedOperationNodeId"),
      "globalThis.result = [",
      "  preservedOperationNodeId(eligible, 'node-b'),",
      "  preservedOperationNodeId(eligible, 'node-missing'),",
      "  preservedOperationNodeId(eligible, ''),",
      "];",
    ].join("\n"),
    context,
  );

  assert.deepEqual(Array.from(context.result), ["node-b", "", ""]);
});
