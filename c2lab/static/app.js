"use strict";

const TOKEN_KEY = "c2lab.operator-token";
const REFRESH_INTERVAL_MS = 3000;
const MAX_EVENT_ROWS = 100;

const TASK_TEMPLATES = Object.freeze({
  PING: {
    payload: {},
    hint: "Node との疎通を確認します。payload は空オブジェクトです。",
  },
  RUNTIME_STATUS: {
    payload: {},
    hint: "version、profile、uptime、完了数、poll間隔だけを返します。ホスト情報は取得しません。",
  },
  ECHO_TEXT: {
    payload: { text: "hello from localhost lab" },
    hint: "1〜240文字のテキストを、そのまま安全な結果として返します。",
  },
  HASH_TEXT: {
    payload: { text: "learning sample" },
    hint: "1〜240文字のテキストから SHA-256 digest を計算します。ファイルは扱いません。",
  },
  WAIT: {
    payload: { milliseconds: 750 },
    hint: "別プロセスで 0〜2000ms 待機し、非同期 dispatch の流れを観察します。",
  },
  GENERATE_EVENT: {
    payload: {
      category: "training",
      severity: "info",
      message: "localhost training event",
    },
    hint: "category は training / telemetry / policy、severity は info / warning です。",
  },
  RUN_PLAYBOOK: {
    payload: { playbook: "DISCOVERY_FIXTURES" },
    hint: "purple_lab Node 専用です。Node自身の一時workspaceだけで固定playbookを実行し、実I/Oの証跡を返します。",
  },
});

const elementIds = [
  "apiState",
  "apiStateText",
  "refreshButton",
  "resetButton",
  "tokenManagement",
  "startupPanel",
  "tokenForm",
  "tokenInput",
  "tokenVisibilityButton",
  "connectButton",
  "clearTokenButton",
  "labModeValue",
  "protocolValue",
  "lastUpdated",
  "metricGrid",
  "metricNodesOnline",
  "metricNodesTotal",
  "metricTasksQueued",
  "metricTasksActive",
  "metricTasksCompleted",
  "metricTasksFailed",
  "metricTasksTimeout",
  "nodeList",
  "nodesEmpty",
  "nodeCountBadge",
  "taskForm",
  "taskNodeSelect",
  "taskTypeSelect",
  "playbookField",
  "playbookSelect",
  "taskPayloadField",
  "taskPayloadInput",
  "payloadHint",
  "payloadError",
  "restoreTemplateButton",
  "createTaskButton",
  "selectedNodeSummary",
  "selectedNodeName",
  "selectedNodeStatus",
  "selectedNodeProfile",
  "selectedNodeSession",
  "selectedNodeCapabilities",
  "taskStatusFilter",
  "taskSearchInput",
  "taskCountBadge",
  "taskTableBody",
  "tasksEmpty",
  "tasksEmptyTitle",
  "tasksEmptyDescription",
  "eventCountBadge",
  "activitySourceFilter",
  "eventSearchInput",
  "eventLevelFilter",
  "eventActorFilter",
  "eventList",
  "eventsEmpty",
  "eventsEmptyTitle",
  "eventsEmptyDescription",
  "toastRegion",
  "taskDetailDialog",
  "taskDetailBody",
  "closeTaskDetailButton",
];

const elements = Object.fromEntries(
  elementIds.map((id) => [id, document.getElementById(id)]),
);

let operatorToken = "";
let tokenGeneration = 0;
let latestOverview = null;
let refreshInFlight = false;
let renderedNodeKey = "";
let renderedTaskKey = "";
let renderedHistoryKey = "";
let renderedActorOptionsKey = "";
let pendingTaskSubmission = null;

const tableTimeFormatter = new Intl.DateTimeFormat("ja-JP", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const updateTimeFormatter = new Intl.DateTimeFormat("ja-JP", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function makeTextElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = text;
  return element;
}

function setApiState(state, message) {
  elements.apiState.dataset.state = state;
  elements.apiStateText.textContent = message;
}

function showToast(message, tone = "info") {
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.dataset.tone = tone;
  toast.textContent = message;
  elements.toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 4200);
}

function setConnectedLayout(connected) {
  const wasConnected = document.body.classList.contains("is-connected");
  document.body.classList.toggle("is-connected", connected);
  if (connected && !wasConnected) {
    elements.tokenManagement.open = false;
    elements.startupPanel.open = false;
  } else if (!connected) {
    elements.tokenManagement.open = true;
    elements.startupPanel.open = true;
  }
}

function scrollToSection(sectionId) {
  const section = document.getElementById(sectionId);
  if (!section) return;
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  section.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
}

function normalizedSearch(value) {
  return String(value || "").trim().toLocaleLowerCase("ja-JP");
}

function localTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return tableTimeFormatter.format(date);
}

function compactJson(value) {
  if (value === null || value === undefined) return "—";
  let encoded;
  try {
    encoded = JSON.stringify(value);
  } catch {
    return "[unavailable]";
  }
  return encoded.length > 220 ? `${encoded.slice(0, 217)}…` : encoded;
}

function humanError(error) {
  if (error instanceof ApiError && error.status === 401) {
    return "Operator token が無効です。Teamserver が表示したURLを開き直してください。";
  }
  if (error instanceof TypeError) {
    return "Teamserver に接続できません。localhost で起動しているか確認してください。";
  }
  return error?.message || "予期しないエラーが発生しました。";
}

async function api(path, { method = "GET", body, idempotencyKey } = {}) {
  if (!operatorToken) {
    throw new ApiError("Operator token を入力してください。", 401);
  }

  const headers = {
    Accept: "application/json",
    Authorization: `Bearer ${operatorToken}`,
  };
  const options = {
    method,
    headers,
    cache: "no-store",
    credentials: "same-origin",
  };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;

  const response = await fetch(path, options);
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError("Teamserver が有効なJSONを返しませんでした。", response.status);
  }
  if (!response.ok) {
    throw new ApiError(
      payload?.error?.message || `Teamserver error (${response.status})`,
      response.status,
    );
  }
  return payload;
}

function setMetric(element, value) {
  element.textContent = Number.isFinite(value) ? String(value) : "—";
}

function renderMetrics(counts = {}) {
  setMetric(elements.metricNodesOnline, counts.nodes_online);
  setMetric(elements.metricNodesTotal, counts.nodes_total);
  setMetric(elements.metricTasksQueued, counts.tasks_queued);
  setMetric(elements.metricTasksActive, counts.tasks_active);
  setMetric(elements.metricTasksCompleted, counts.tasks_completed);
  setMetric(elements.metricTasksFailed, counts.tasks_failed);
  setMetric(elements.metricTasksTimeout, counts.tasks_timeout);
}

function createMetaItem(label, value, field = "") {
  const item = document.createElement("div");
  item.className = "node-meta__item";
  if (field) item.dataset.nodeField = field;
  item.append(
    makeTextElement("span", "", label),
    makeTextElement("strong", "", value ?? "—"),
  );
  return item;
}

function createNodeCard(node) {
  const card = document.createElement("article");
  card.className = "node-card";
  card.dataset.online = String(node.status === "online");
  card.dataset.nodeId = node.id || "";

  const header = document.createElement("div");
  header.className = "node-card__header";

  const statusLight = document.createElement("span");
  statusLight.className = "node-card__light";
  statusLight.setAttribute("aria-hidden", "true");

  const identity = document.createElement("div");
  identity.className = "node-card__identity";
  identity.append(
    makeTextElement("strong", "", node.name || "unnamed-node"),
    makeTextElement("code", "", node.id || "—"),
  );

  const status = makeTextElement(
    "span",
    "status-badge",
    String(node.status || "unknown").toUpperCase(),
  );
  status.dataset.status = node.status || "unknown";
  const badges = document.createElement("div");
  badges.className = "node-card__badges";
  badges.append(status);
  if (node.session_active === false) {
    badges.append(makeTextElement("span", "session-closed-badge", "SESSION CLOSED"));
  }
  header.append(statusLight, identity, badges);

  const meta = document.createElement("div");
  meta.className = "node-meta";
  meta.append(
    createMetaItem("VERSION", node.version),
    createMetaItem("CAPABILITY PROFILE", node.profile),
    createMetaItem("POLL", Number.isFinite(node.poll_interval_ms) ? `${node.poll_interval_ms} ms` : "—"),
    createMetaItem("LAST SEEN", localTime(node.last_seen), "last_seen"),
  );

  const transport = document.createElement("div");
  transport.className = "node-card__transport";
  transport.append(
    makeTextElement("span", "", "TRANSPORT"),
    makeTextElement("code", "", node.transport || "—"),
  );

  const footer = document.createElement("div");
  footer.className = "node-card__footer";
  const capabilities = document.createElement("div");
  capabilities.className = "capability-list";
  for (const capability of Array.isArray(node.capabilities) ? node.capabilities : []) {
    capabilities.append(makeTextElement("span", "capability-chip", capability));
  }

  const counters = document.createElement("div");
  counters.className = "node-counters";
  counters.append(
    makeTextElement("span", "node-counter node-counter--ok", `✓ ${node.tasks_completed ?? 0}`),
    makeTextElement("span", "node-counter node-counter--bad", `× ${node.tasks_failed ?? 0}`),
  );
  counters.setAttribute("aria-label", `完了 ${node.tasks_completed ?? 0}、失敗 ${node.tasks_failed ?? 0}`);

  const actions = document.createElement("div");
  actions.className = "node-card__actions";
  const selectButton = makeTextElement("button", "node-card__select", "選択してタスク作成");
  selectButton.type = "button";
  selectButton.disabled = node.session_active === false;
  selectButton.setAttribute("aria-label", `${node.name || "Node"} を選択して固定タスクを作成`);
  if (selectButton.disabled) selectButton.title = "このNodeセッションは終了しています";
  selectButton.addEventListener("click", () => selectNodeForTask(node.id));
  actions.append(counters, selectButton);
  footer.append(capabilities, actions);

  card.append(header, meta, transport, footer);
  return card;
}

function renderNodes(nodes) {
  const nodeKey = JSON.stringify(
    nodes.map(({ last_seen: _lastSeen, ...stableNode }) => stableNode),
  );
  if (nodeKey === renderedNodeKey) {
    for (const node of nodes) {
      const card = Array.from(elements.nodeList.querySelectorAll(".node-card")).find(
        (candidate) => candidate.dataset.nodeId === node.id,
      );
      const lastSeen = card?.querySelector('[data-node-field="last_seen"] strong');
      if (lastSeen) lastSeen.textContent = localTime(node.last_seen);
    }
    updateSelectedNodeSummary(selectedNode());
    return;
  }
  renderedNodeKey = nodeKey;
  const previousNodeId = elements.taskNodeSelect.value;
  elements.nodeList.replaceChildren();
  elements.taskNodeSelect.replaceChildren(new Option("Node を選択", ""));
  elements.nodeCountBadge.textContent = `${nodes.length} NODES`;
  elements.nodesEmpty.classList.toggle("is-visible", nodes.length === 0);

  for (const node of nodes) {
    elements.nodeList.append(createNodeCard(node));
    const label = `${node.name} · ${String(node.status).toUpperCase()} · ${node.profile}`;
    const option = new Option(
      node.session_active === false ? `${label} · SESSION CLOSED` : label,
      node.id,
    );
    option.disabled = node.session_active === false;
    elements.taskNodeSelect.add(option);
  }

  if (nodes.some((node) => node.id === previousNodeId && node.session_active !== false)) {
    elements.taskNodeSelect.value = previousNodeId;
  } else if (nodes.some((node) => node.session_active !== false)) {
    const preferred =
      nodes.find((node) => node.status === "online" && node.session_active !== false) ||
      nodes.find((node) => node.session_active !== false);
    elements.taskNodeSelect.value = preferred.id;
  }
  updateTaskCapabilities();
}

function selectedNode() {
  const nodes = Array.isArray(latestOverview?.nodes) ? latestOverview.nodes : [];
  return nodes.find((node) => node.id === elements.taskNodeSelect.value) || null;
}

function updateSelectedNodeSummary(node) {
  elements.selectedNodeSummary.dataset.state = node ? "selected" : "empty";
  elements.selectedNodeName.textContent = node?.name || "Node 未選択";
  elements.selectedNodeStatus.textContent = node ? String(node.status || "unknown").toUpperCase() : "—";
  elements.selectedNodeStatus.dataset.status = node?.status || "unknown";
  elements.selectedNodeProfile.textContent = node?.profile || "—";
  elements.selectedNodeSession.textContent = node ? (node.session_active === false ? "CLOSED" : "ACTIVE") : "—";
  const capabilities = Array.isArray(node?.capabilities) ? node.capabilities : [];
  elements.selectedNodeCapabilities.textContent = node
    ? `固定タスク: ${capabilities.length ? capabilities.join(" · ") : "なし"}`
    : "登録済みNodeを選択すると、実行可能な固定タスクを確認できます。";

  for (const card of elements.nodeList.querySelectorAll(".node-card")) {
    card.classList.toggle("is-selected", Boolean(node) && card.dataset.nodeId === node.id);
  }
}

function selectNodeForTask(nodeId) {
  const option = Array.from(elements.taskNodeSelect.options).find(
    (candidate) => candidate.value === nodeId && !candidate.disabled,
  );
  if (!option) {
    showToast("このNodeは固定タスクの送信先に選択できません。", "error");
    return;
  }
  elements.taskNodeSelect.value = nodeId;
  updateTaskCapabilities();
  scrollToSection("dispatch");
  elements.taskNodeSelect.focus({ preventScroll: true });
}

function updateTaskCapabilities() {
  const node = selectedNode();
  const capabilities = new Set(Array.isArray(node?.capabilities) ? node.capabilities : []);
  const sessionActive = Boolean(node) && node.session_active !== false;
  let selectedWasDisabled = false;

  for (const option of elements.taskTypeSelect.options) {
    option.disabled = Boolean(node) && (!sessionActive || !capabilities.has(option.value));
    if (option.selected && option.disabled) selectedWasDisabled = true;
  }

  if (selectedWasDisabled) {
    const firstAllowed = Array.from(elements.taskTypeSelect.options).find((option) => !option.disabled);
    if (firstAllowed) {
      elements.taskTypeSelect.value = firstAllowed.value;
      applyTaskTemplate();
    }
  }
  updateSelectedNodeSummary(node);
  updateControls();
}

function createTableCell(className, text) {
  const cell = document.createElement("td");
  cell.append(makeTextElement("span", className, text));
  return cell;
}

function renderTasks(tasks, nodes) {
  const nodeNames = new Map(nodes.map((node) => [node.id, node.name]));
  const filter = elements.taskStatusFilter.value;
  const query = normalizedSearch(elements.taskSearchInput.value);
  const renderKey = JSON.stringify({
    filter,
    query,
    nodes: Array.from(nodeNames.entries()),
    tasks,
  });
  if (renderKey === renderedTaskKey) return;
  renderedTaskKey = renderKey;
  elements.taskTableBody.replaceChildren();
  for (const button of elements.metricGrid.querySelectorAll("[data-task-status]")) {
    const selected = filter !== "all" && button.dataset.taskStatus === filter;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-pressed", String(selected));
  }
  const visibleTasks = tasks.filter((task) => {
    if (filter !== "all" && task.status !== filter) return false;
    if (!query) return true;
    const searchable = [
      task.id,
      task.correlation_id,
      task.type,
      task.status,
      task.node_id,
      nodeNames.get(task.node_id),
    ]
      .map(normalizedSearch)
      .join(" ");
    return searchable.includes(query);
  });
  const filtersActive = filter !== "all" || Boolean(query);
  elements.taskCountBadge.textContent = filtersActive
    ? `${visibleTasks.length}/${tasks.length} TASKS`
    : `${tasks.length} TASKS`;
  elements.tasksEmpty.classList.toggle("is-visible", visibleTasks.length === 0);
  elements.tasksEmptyTitle.textContent = filtersActive
    ? "条件に一致するタスクがありません"
    : "表示できるタスクがありません";
  elements.tasksEmptyDescription.textContent = filtersActive
    ? "検索語または状態フィルタを変更してください。"
    : "登録済み Node を選び、許可タスクを送信してください。";

  for (const task of visibleTasks) {
    const row = document.createElement("tr");
    row.append(
      createTableCell("task-id", task.id || "—"),
      createTableCell("correlation-id", task.correlation_id || "—"),
      createTableCell("task-type", task.type || "—"),
      createTableCell("task-node", nodeNames.get(task.node_id) || task.node_id || "—"),
    );

    const statusCell = document.createElement("td");
    const statusName = String(task.status || "unknown");
    const status = makeTextElement("span", "status-badge", statusName.toUpperCase());
    status.dataset.status = statusName;
    statusCell.append(status);
    row.append(statusCell, createTableCell("task-time", localTime(task.created_at)));

    const detailCell = document.createElement("td");
    const detailButton = makeTextElement("button", "detail-button", "···");
    detailButton.type = "button";
    detailButton.setAttribute("aria-label", `${task.id || "task"} の詳細`);
    detailButton.addEventListener("click", () => showTaskDetail(task, nodeNames.get(task.node_id)));
    detailCell.append(detailButton);
    row.append(detailCell);
    elements.taskTableBody.append(row);
  }
}

function eventTone(event) {
  const kind = String(event.kind || "");
  if (event.level === "error") return "error";
  if (event.level === "warning" || kind.endsWith("timeout") || kind.endsWith("stale")) return "warning";
  if (kind.endsWith("completed") || kind.endsWith("online") || kind.endsWith("enrolled")) return "success";
  return "info";
}

function updateEventActorOptions(events) {
  const previousActor = elements.eventActorFilter.value;
  const actors = Array.from(
    new Set(events.map((event) => String(event.actor || "teamserver"))),
  ).sort((left, right) => left.localeCompare(right, "ja-JP"));
  const actorOptionsKey = JSON.stringify(actors);
  if (actorOptionsKey === renderedActorOptionsKey) return;
  renderedActorOptionsKey = actorOptionsKey;
  elements.eventActorFilter.replaceChildren(new Option("すべて", "all"));
  for (const actor of actors) elements.eventActorFilter.add(new Option(actor, actor));
  elements.eventActorFilter.value = actors.includes(previousActor) ? previousActor : "all";
}

function auditEntryAsEvent(entry) {
  const outcome = String(entry.outcome || "success");
  return {
    id: entry.id,
    sequence: entry.sequence,
    time: entry.time,
    kind: entry.action || "unknown.audit",
    level: ["failed", "timeout", "cancelled", "expired", "error"].includes(outcome)
      ? "warning"
      : "info",
    actor: entry.actor,
    node_id: entry.node_id,
    task_id: entry.task_id,
    correlation_id: entry.correlation_id,
    data: {
      task_type: entry.task_type,
      from_state: entry.from_state,
      to_state: entry.to_state,
      outcome: entry.outcome,
      reason: entry.reason,
    },
  };
}

function renderEvents(events, { auditView = false } = {}) {
  updateEventActorOptions(events);
  const query = normalizedSearch(elements.eventSearchInput.value);
  const level = elements.eventLevelFilter.value;
  const actor = elements.eventActorFilter.value;
  const historyKey = JSON.stringify({ auditView, query, level, actor, events });
  if (historyKey === renderedHistoryKey) return;
  renderedHistoryKey = historyKey;
  elements.eventList.replaceChildren();
  const matches = events.filter((event) => {
    const eventLevel = String(event.level || "info").toLocaleLowerCase("ja-JP");
    const eventActor = String(event.actor || "teamserver");
    if (level !== "all" && eventLevel !== level) return false;
    if (actor !== "all" && eventActor !== actor) return false;
    if (!query) return true;
    const searchable = [
      event.kind,
      eventActor,
      event.node_id,
      event.task_id,
      event.correlation_id,
      event.sequence,
      eventLevel,
      compactJson(event.data),
    ]
      .map(normalizedSearch)
      .join(" ");
    return searchable.includes(query);
  });
  const recent = matches.slice(0, MAX_EVENT_ROWS);
  const filtersActive = Boolean(query) || level !== "all" || actor !== "all";
  const unit = auditView ? "AUDIT" : "EVENTS";
  elements.eventCountBadge.textContent = filtersActive || recent.length !== events.length
    ? `${recent.length}/${events.length} ${unit}`
    : `${events.length} ${unit}`;
  elements.eventsEmpty.classList.toggle("is-visible", recent.length === 0);
  elements.eventsEmptyTitle.textContent = filtersActive
    ? "条件に一致する履歴がありません"
    : auditView ? "監査記録を待機中" : "イベントを待機中";
  elements.eventsEmptyDescription.textContent = filtersActive
    ? "検索語、level、actorフィルタを変更してください。"
    : auditView
      ? "固定schemaでredactされた重要な状態遷移がここに表示されます。"
      : "enroll、poll、dispatch、result の運用イベントがここに表示されます。";

  for (const event of recent) {
    const item = document.createElement("article");
    item.className = "event-item";
    item.dataset.tone = eventTone(event);

    const time = makeTextElement("time", "event-item__time", localTime(event.time));
    if (event.time) time.dateTime = event.time;

    const marker = document.createElement("span");
    marker.className = "event-item__node";
    marker.setAttribute("aria-hidden", "true");

    const content = document.createElement("div");
    content.className = "event-item__content";
    content.append(
      makeTextElement("strong", "", event.kind || "unknown.event"),
      makeTextElement("p", "", compactJson(event.data)),
    );

    const sourceParts = [];
    if (Number.isInteger(event.sequence)) sourceParts.push(`#${event.sequence}`);
    sourceParts.push(event.actor || "teamserver");
    if (event.node_id) sourceParts.push(event.node_id);
    if (event.task_id) sourceParts.push(event.task_id);
    if (event.correlation_id) sourceParts.push(event.correlation_id);
    const source = makeTextElement("span", "event-item__source", sourceParts.join(" · "));
    source.title = sourceParts.join(" · ");

    item.append(time, marker, content, source);
    elements.eventList.append(item);
  }
}

function renderHistory() {
  const auditView = elements.activitySourceFilter.value === "audit";
  const source = auditView ? latestOverview?.audit : latestOverview?.events;
  const records = Array.isArray(source) ? source : [];
  renderEvents(auditView ? records.map(auditEntryAsEvent) : records, { auditView });
}

function renderOverview(overview, audit) {
  if (overview.lab_mode !== true) {
    throw new ApiError("localhost lab_mode を確認できない応答を拒否しました。");
  }

  const nodes = Array.isArray(overview.nodes) ? overview.nodes : [];
  const tasks = Array.isArray(overview.tasks) ? overview.tasks : [];
  const events = Array.isArray(overview.events) ? overview.events : [];
  const auditEntries = Array.isArray(audit) ? audit : [];
  latestOverview = { ...overview, nodes, tasks, events, audit: auditEntries };

  elements.labModeValue.textContent = "LOCALHOST LAB";
  elements.protocolValue.textContent = overview.protocol || "unknown";
  renderMetrics(overview.counts || {});
  renderNodes(nodes);
  renderTasks(tasks, nodes);
  renderHistory();
  elements.lastUpdated.textContent = `最終更新 ${updateTimeFormatter.format(new Date())}`;
  setConnectedLayout(true);
}

function clearOverview() {
  setConnectedLayout(false);
  latestOverview = null;
  elements.labModeValue.textContent = "LOCALHOST LAB";
  elements.protocolValue.textContent = "loopback-http-poll/v1";
  renderMetrics();
  renderNodes([]);
  renderTasks([], []);
  renderEvents([], { auditView: elements.activitySourceFilter.value === "audit" });
  elements.lastUpdated.textContent = "未取得";
}

function updateControls() {
  const hasToken = Boolean(operatorToken);
  const node = selectedNode();
  const selectedType = elements.taskTypeSelect.value;
  const typeAllowed =
    Boolean(node) &&
    node.session_active !== false &&
    Array.isArray(node.capabilities) &&
    node.capabilities.includes(selectedType);
  elements.refreshButton.disabled =
    !hasToken || refreshInFlight || elements.refreshButton.dataset.busy === "true";
  elements.connectButton.disabled = elements.connectButton.dataset.busy === "true";
  elements.resetButton.disabled =
    !hasToken || elements.resetButton.dataset.busy === "true";
  elements.createTaskButton.disabled =
    !hasToken ||
    !node ||
    !typeAllowed ||
    elements.createTaskButton.dataset.busy === "true";
}

async function refresh({ silent = false } = {}) {
  if (!operatorToken || refreshInFlight) return;
  const requestToken = operatorToken;
  const requestGeneration = tokenGeneration;
  refreshInFlight = true;
  elements.metricGrid.setAttribute("aria-busy", "true");
  elements.nodeList.setAttribute("aria-busy", "true");
  elements.eventList.setAttribute("aria-busy", "true");
  if (!silent || !latestOverview) setApiState("loading", "更新中");
  updateControls();

  try {
    const [overview, audit] = await Promise.all([api("/lab/overview"), api("/lab/audit")]);
    if (requestGeneration !== tokenGeneration || requestToken !== operatorToken) return;
    renderOverview(overview, audit);
    setApiState("online", "localhost 接続中");
  } catch (error) {
    if (requestGeneration !== tokenGeneration || requestToken !== operatorToken) return;
    if (error?.status === 401) clearOverview();
    setApiState("error", error?.status === 401 ? "認証エラー" : "接続エラー");
    if (!silent) showToast(humanError(error), "error");
  } finally {
    refreshInFlight = false;
    elements.metricGrid.setAttribute("aria-busy", "false");
    elements.nodeList.setAttribute("aria-busy", "false");
    elements.eventList.setAttribute("aria-busy", "false");
    updateControls();
  }
}

function addDetailValue(container, label, value) {
  const block = document.createElement("div");
  block.className = "detail-value";
  const display = value === null || value === undefined || value === "" ? "—" : String(value);
  block.append(makeTextElement("span", "", label), makeTextElement("strong", "", display));
  container.append(block);
}

function addJsonBlock(container, label, value) {
  const block = document.createElement("div");
  block.className = "json-block";
  const pre = document.createElement("pre");
  pre.textContent = value === null || value === undefined ? "—" : JSON.stringify(value, null, 2);
  block.append(makeTextElement("span", "", label), pre);
  container.append(block);
}

function createTaskLifecycle(task) {
  const status = String(task.status || "queued").toLowerCase();
  const terminalStatuses = new Set(["completed", "failed", "timeout", "cancelled", "expired"]);
  const isTerminal = terminalStatuses.has(status);
  const wasDispatched = Boolean(task.dispatched_at) || status === "dispatched";
  const lifecycle = document.createElement("section");
  lifecycle.className = "task-lifecycle";
  lifecycle.dataset.status = status;
  lifecycle.setAttribute("aria-label", "タスクのライフサイクル");
  lifecycle.append(makeTextElement("span", "task-lifecycle__label", "LIFECYCLE"));

  const steps = document.createElement("ol");
  steps.className = "task-lifecycle__steps";
  const definitions = [
    {
      label: "QUEUED",
      time: task.created_at,
      state: status === "queued" ? "current" : "complete",
    },
    {
      label: isTerminal && !wasDispatched ? "NOT DISPATCHED" : "DISPATCHED",
      time: task.dispatched_at,
      state: status === "dispatched" ? "current" : wasDispatched ? "complete" : isTerminal ? "skipped" : "pending",
    },
    {
      label: isTerminal ? status.toUpperCase() : "RESULT",
      time: task.completed_at,
      state: isTerminal ? "current" : "pending",
    },
  ];
  for (const definition of definitions) {
    const step = document.createElement("li");
    step.className = "task-lifecycle__step";
    step.dataset.state = definition.state;
    const marker = makeTextElement("span", "task-lifecycle__marker", "");
    marker.setAttribute("aria-hidden", "true");
    const copy = document.createElement("span");
    copy.append(
      makeTextElement("strong", "", definition.label),
      makeTextElement("small", "", definition.time ? localTime(definition.time) : "未到達"),
    );
    step.append(marker, copy);
    steps.append(step);
  }
  lifecycle.append(steps);
  return lifecycle;
}

function showTaskDetail(task, nodeName) {
  elements.taskDetailBody.replaceChildren();
  elements.taskDetailBody.append(createTaskLifecycle(task));
  const grid = document.createElement("div");
  grid.className = "detail-grid";
  addDetailValue(grid, "Task ID", task.id);
  addDetailValue(grid, "Correlation ID", task.correlation_id);
  addDetailValue(grid, "Type", task.type);
  addDetailValue(grid, "Status", String(task.status || "unknown").toUpperCase());
  addDetailValue(grid, "Delivery attempts", task.delivery_attempts);
  addDetailValue(grid, "Node", nodeName || task.node_id);
  addDetailValue(grid, "Created", localTime(task.created_at));
  addDetailValue(grid, "Dispatched", localTime(task.dispatched_at));
  addDetailValue(grid, "Completed", localTime(task.completed_at));
  addDetailValue(
    grid,
    "Queue TTL",
    Number.isFinite(task.queue_ttl_seconds) ? `${task.queue_ttl_seconds} s` : "—",
  );
  elements.taskDetailBody.append(grid);
  addJsonBlock(elements.taskDetailBody, "Payload", task.payload);
  addJsonBlock(elements.taskDetailBody, "Result", task.result);
  if (task.status === "queued") {
    const actions = document.createElement("div");
    actions.className = "detail-actions";
    const cancelButton = makeTextElement(
      "button",
      "button button--danger-ghost",
      "待機タスクを取り消す",
    );
    cancelButton.type = "button";
    cancelButton.addEventListener("click", async () => {
      cancelButton.disabled = true;
      try {
        await api(`/lab/tasks/${task.id}/cancel`, { method: "POST", body: {} });
        elements.taskDetailDialog.close();
        showToast(`タスク ${task.id} を取り消しました。`, "success");
        await refresh({ silent: true });
      } catch (error) {
        showToast(humanError(error), "error");
      } finally {
        cancelButton.disabled = false;
      }
    });
    actions.append(cancelButton);
    elements.taskDetailBody.append(actions);
  }
  if (elements.taskDetailDialog.open) elements.taskDetailDialog.close();
  elements.taskDetailDialog.showModal();
}

function applyTaskTemplate() {
  const template = TASK_TEMPLATES[elements.taskTypeSelect.value];
  if (!template) return;
  const isPlaybook = elements.taskTypeSelect.value === "RUN_PLAYBOOK";
  elements.playbookField.hidden = !isPlaybook;
  elements.taskPayloadField.hidden = isPlaybook;
  if (isPlaybook) elements.playbookSelect.value = template.payload.playbook;
  elements.taskPayloadInput.value = JSON.stringify(template.payload, null, 2);
  elements.payloadHint.textContent = template.hint;
  elements.payloadError.textContent = "";
  elements.taskPayloadInput.removeAttribute("aria-invalid");
}

function exactPayloadKeys(payload, expected) {
  const actual = Object.keys(payload).sort();
  const wanted = [...expected].sort();
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) {
    throw new Error(`payload のキーは ${wanted.length ? wanted.join(", ") : "不要"} です。`);
  }
}

function readPayload() {
  let payload;
  const type = elements.taskTypeSelect.value;
  if (type === "RUN_PLAYBOOK") {
    payload = { playbook: elements.playbookSelect.value };
  } else {
    try {
      payload = JSON.parse(elements.taskPayloadInput.value);
    } catch {
      throw new Error("payload は有効なJSONにしてください。");
    }
  }
  if (!payload || Array.isArray(payload) || typeof payload !== "object") {
    throw new Error("payload はJSONオブジェクトにしてください。");
  }

  if (type === "PING" || type === "RUNTIME_STATUS") {
    exactPayloadKeys(payload, []);
  } else if (type === "ECHO_TEXT" || type === "HASH_TEXT") {
    exactPayloadKeys(payload, ["text"]);
    if (typeof payload.text !== "string" || payload.text.trim().length < 1 || payload.text.trim().length > 240) {
      throw new Error("text は1〜240文字にしてください。");
    }
  } else if (type === "WAIT") {
    exactPayloadKeys(payload, ["milliseconds"]);
    if (!Number.isInteger(payload.milliseconds) || payload.milliseconds < 0 || payload.milliseconds > 2000) {
      throw new Error("milliseconds は0〜2000の整数にしてください。");
    }
  } else if (type === "GENERATE_EVENT") {
    exactPayloadKeys(payload, ["category", "severity", "message"]);
    if (!["training", "telemetry", "policy"].includes(payload.category)) {
      throw new Error("category は training / telemetry / policy から選んでください。");
    }
    if (!["info", "warning"].includes(payload.severity)) {
      throw new Error("severity は info / warning から選んでください。");
    }
    if (typeof payload.message !== "string" || payload.message.trim().length < 1 || payload.message.trim().length > 240) {
      throw new Error("message は1〜240文字にしてください。");
    }
  } else if (type === "RUN_PLAYBOOK") {
    exactPayloadKeys(payload, ["playbook"]);
    if (!["DISCOVERY_FIXTURES", "COLLECT_AND_STAGE", "CREATE_CANARY", "CLEANUP"].includes(payload.playbook)) {
      throw new Error("playbook は固定LAB playbookから選んでください。");
    }
  }
  return payload;
}

function consumeHashToken() {
  const rawHash = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : "";
  const params = new URLSearchParams(rawHash);
  if (!params.has("token")) return null;
  const token = (params.get("token") || "").trim();
  window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  return token;
}

function storedToken() {
  try {
    return window.sessionStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

function saveToken(token) {
  const nextToken = token.trim();
  if (nextToken !== operatorToken) {
    tokenGeneration += 1;
    pendingTaskSubmission = null;
  }
  operatorToken = nextToken;
  try {
    if (operatorToken) window.sessionStorage.setItem(TOKEN_KEY, operatorToken);
    else window.sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // The tab still works when storage is unavailable.
  }
  elements.tokenInput.value = operatorToken;
  if (!operatorToken) setTokenVisibility(false);
  updateControls();
}

function newIdempotencyKey() {
  if (typeof window.crypto?.randomUUID === "function") {
    return `ui:${window.crypto.randomUUID()}`;
  }
  const bytes = new Uint8Array(16);
  window.crypto.getRandomValues(bytes);
  return `ui:${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")}`;
}

function setTokenVisibility(visible) {
  elements.tokenInput.type = visible ? "text" : "password";
  elements.tokenVisibilityButton.textContent = visible ? "隠す" : "表示";
  elements.tokenVisibilityButton.setAttribute("aria-pressed", String(visible));
  elements.tokenVisibilityButton.setAttribute("aria-label", visible ? "トークンを隠す" : "トークンを表示");
}

async function runWithBusyButton(button, label, action) {
  const originalNodes = Array.from(button.childNodes);
  button.dataset.busy = "true";
  button.replaceChildren(document.createTextNode(label));
  button.disabled = true;
  try {
    return await action();
  } finally {
    button.replaceChildren(...originalNodes);
    delete button.dataset.busy;
    updateControls();
  }
}

elements.tokenForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  saveToken(elements.tokenInput.value);
  if (!operatorToken) {
    setApiState("error", "token が必要です");
    showToast("Operator token を入力してください。", "error");
    return;
  }
  await runWithBusyButton(elements.connectButton, "接続中…", () => refresh());
});

elements.clearTokenButton.addEventListener("click", () => {
  saveToken("");
  clearOverview();
  setApiState("idle", "接続待ち");
  showToast("このタブの Operator token を消去しました。");
});

elements.tokenVisibilityButton.addEventListener("click", () => {
  setTokenVisibility(elements.tokenInput.type !== "text");
});

elements.refreshButton.addEventListener("click", () => refresh());
elements.taskNodeSelect.addEventListener("change", updateTaskCapabilities);
elements.taskTypeSelect.addEventListener("change", () => {
  applyTaskTemplate();
  updateControls();
});
elements.restoreTemplateButton.addEventListener("click", applyTaskTemplate);

elements.taskPayloadInput.addEventListener("input", () => {
  try {
    readPayload();
    elements.payloadError.textContent = "";
    elements.taskPayloadInput.removeAttribute("aria-invalid");
  } catch (error) {
    elements.payloadError.textContent = error.message;
    elements.taskPayloadInput.setAttribute("aria-invalid", "true");
  }
});

elements.taskForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const node = selectedNode();
  if (!node) {
    showToast("送信先 Node を選択してください。", "error");
    return;
  }

  let payload;
  try {
    payload = readPayload();
    elements.payloadError.textContent = "";
    elements.taskPayloadInput.removeAttribute("aria-invalid");
  } catch (error) {
    elements.payloadError.textContent = error.message;
    elements.taskPayloadInput.setAttribute("aria-invalid", "true");
    elements.taskPayloadInput.focus();
    return;
  }

  await runWithBusyButton(elements.createTaskButton, "キューへ送信中…", async () => {
    const requestBody = {
      node_id: node.id,
      type: elements.taskTypeSelect.value,
      payload,
    };
    const signature = JSON.stringify(requestBody);
    if (!pendingTaskSubmission || pendingTaskSubmission.signature !== signature) {
      pendingTaskSubmission = { signature, key: newIdempotencyKey() };
    }
    try {
      const task = await api("/lab/tasks", {
        method: "POST",
        body: requestBody,
        idempotencyKey: pendingTaskSubmission.key,
      });
      pendingTaskSubmission = null;
      showToast(`タスクを追加しました。相関ID: ${task.correlation_id}`, "success");
      await refresh({ silent: true });
    } catch (error) {
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        pendingTaskSubmission = null;
      }
      showToast(humanError(error), "error");
    }
  });
});

elements.taskStatusFilter.addEventListener("change", () => {
  if (latestOverview) renderTasks(latestOverview.tasks, latestOverview.nodes);
});
elements.taskSearchInput.addEventListener("input", () => {
  if (latestOverview) renderTasks(latestOverview.tasks, latestOverview.nodes);
});
elements.eventSearchInput.addEventListener("input", () => {
  if (latestOverview) renderHistory();
});
elements.eventLevelFilter.addEventListener("change", () => {
  if (latestOverview) renderHistory();
});
elements.eventActorFilter.addEventListener("change", () => {
  if (latestOverview) renderHistory();
});
elements.activitySourceFilter.addEventListener("change", () => {
  elements.eventSearchInput.value = "";
  elements.eventLevelFilter.value = "all";
  elements.eventActorFilter.value = "all";
  if (latestOverview) renderHistory();
});

elements.metricGrid.addEventListener("click", (event) => {
  const metric = event.target.closest(".metric-button");
  if (!metric) return;
  if (metric.dataset.taskStatus) {
    elements.taskSearchInput.value = "";
    elements.taskStatusFilter.value = metric.dataset.taskStatus;
    if (latestOverview) renderTasks(latestOverview.tasks, latestOverview.nodes);
    for (const button of elements.metricGrid.querySelectorAll("[data-task-status]")) {
      button.classList.toggle("is-active", button === metric);
      button.setAttribute("aria-pressed", String(button === metric));
    }
    scrollToSection("tasks");
    return;
  }
  if (metric.dataset.sectionTarget) scrollToSection(metric.dataset.sectionTarget);
});

elements.resetButton.addEventListener("click", async () => {
  const confirmed = window.confirm(
    "登録Node、タスク、イベントを消去し、現在のNodeセッションを無効化しますか？",
  );
  if (!confirmed) return;

  await runWithBusyButton(elements.resetButton, "リセット中…", async () => {
    try {
      await api("/lab/reset", { method: "POST", body: {} });
      showToast("ラボをリセットしました。foreground Node は自動で再登録します。", "success");
      await refresh({ silent: true });
    } catch (error) {
      showToast(humanError(error), "error");
    }
  });
});

elements.closeTaskDetailButton.addEventListener("click", () => elements.taskDetailDialog.close());
elements.taskDetailDialog.addEventListener("click", (event) => {
  if (event.target === elements.taskDetailDialog) elements.taskDetailDialog.close();
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") refresh({ silent: true });
});

const tokenFromHash = consumeHashToken();
saveToken(tokenFromHash === null ? storedToken() : tokenFromHash);
applyTaskTemplate();
clearOverview();
if (operatorToken) refresh();
window.setInterval(() => {
  const activeElement = document.activeElement;
  const interacting = Boolean(
    elements.taskDetailDialog.open ||
    activeElement?.matches("input, select, textarea, summary"),
  );
  if (document.visibilityState === "visible" && !interacting) {
    refresh({ silent: true });
  }
}, REFRESH_INTERVAL_MS);
