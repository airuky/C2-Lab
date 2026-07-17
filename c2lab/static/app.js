"use strict";

const TOKEN_KEY = "c2lab.operator-token";
const UI_SETTINGS_KEY = "c2lab.ui-settings";
const REFRESH_INTERVAL_MS = 3000;
const MAX_EVENT_ROWS = 100;
const AUTO_REFRESH_OPTIONS = new Set([1000, 3000, 5000, 10000]);
const HISTORY_LIMIT_OPTIONS = new Set([25, 50, 100]);
const DENSITY_OPTIONS = new Set(["comfortable", "compact"]);
const SYNC_PAGE_SIZE = 100;
const MAX_SYNC_PAGES = 10;
const MAX_HISTORY_RECORDS = 500;
const MAX_NOTE_LENGTH = 240;
const SVG_NS = "http://www.w3.org/2000/svg";
const MAX_GRAPH_NODES = 20;
const MAX_GRAPH_TASKS = 40;
const MAX_GRAPH_EXERCISES = 20;
const GRAPH_ZOOM_MIN = 10;
const GRAPH_ZOOM_MAX = 160;
const GRAPH_ZOOM_STEP = 10;
const MAX_OPERATION_STEPS = 3;
const OPERATION_PLAYBOOKS = Object.freeze([
  "DISCOVERY_FIXTURES",
  "COLLECT_AND_STAGE",
  "CREATE_CANARY",
  "CLEANUP",
]);
const GRAPH_PLAYBOOK_IDS = new Set(OPERATION_PLAYBOOKS);
const GRAPH_TECHNIQUE_IDS = new Set([
  "T1083",
  "T1005",
  "T1074.001",
  "T1070.004",
]);
const GRAPH_STATUSES = new Set([
  "online",
  "offline",
  "active",
  "paused",
  "queued",
  "dispatched",
  "completed",
  "failed",
  "timeout",
  "cancelled",
  "expired",
  "running",
  "pending",
  "detected",
  "contained",
  "open",
  "planned",
  "observed",
  "unknown",
]);
const FIXED_EXERCISE_SCENARIOS = Object.freeze([
  Object.freeze({
    id: "DISCOVERY_COLLECTION",
    title: "DISCOVERY_COLLECTION",
    description: "同期カタログ取得後にシナリオ説明を表示します。",
    techniques: [],
  }),
  Object.freeze({
    id: "CANARY_REMOVAL",
    title: "CANARY_REMOVAL",
    description: "同期カタログ取得後にシナリオ説明を表示します。",
    techniques: [],
  }),
]);
const EXERCISE_SCENARIO_IDS = new Set(
  FIXED_EXERCISE_SCENARIOS.map((scenario) => scenario.id),
);
const CONTAINMENT_ACTIONS = new Set([
  "CANCEL_REMAINING",
  "PAUSE_NODE_TASKING",
]);

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
  SLEEP: {
    payload: { interval_ms: 2000, jitter_percent: 20 },
    hint: "Node の poll 間隔とジッターを変更します。CS の sleep コマンドに相当します。",
  },
  EXIT: {
    payload: {},
    hint: "Node に正常停止を指示します。CS の exit コマンドに相当します。",
  },
  RUN_PLAYBOOK: {
    payload: { playbook: "DISCOVERY_FIXTURES" },
    hint: "purple_lab Node 専用です。Node自身の一時workspaceだけで固定playbookを実行し、実I/Oの証跡を返します。",
  },
});

const TASK_GUIDANCE = Object.freeze({
  PING: Object.freeze({
    action: "Nodeとの疎通とtask dispatchの往復を確認します。",
    adjustable: "追加設定はありません。",
    safety: "固定応答のみ。ホスト情報、ファイル、ネットワーク探索には触れません。",
  }),
  RUNTIME_STATUS: Object.freeze({
    action: "Nodeランタイムの稼働時間、profile、poll設定、完了数を取得します。",
    adjustable: "追加設定はありません。",
    safety: "Node自身の固定ランタイム値だけを返し、OSやホストの識別情報は取得しません。",
  }),
  ECHO_TEXT: Object.freeze({
    action: "入力したplain textを別プロセスNodeで受け取り、そのまま結果に返します。",
    adjustable: "1〜240文字のテキスト。",
    safety: "文字列だけを扱います。コマンドとして解釈せず、ファイルにも書き込みません。",
  }),
  HASH_TEXT: Object.freeze({
    action: "入力したplain textのSHA-256 digestをNodeで計算します。",
    adjustable: "1〜240文字のテキスト。",
    safety: "入力文字列だけを計算対象とし、パスやホストファイルを受け付けません。",
  }),
  WAIT: Object.freeze({
    action: "Nodeで指定時間待機し、非同期dispatchからresultまでの時間差を観察します。",
    adjustable: "0〜2000msの待機時間。スライダーまたは数値で調整できます。",
    safety: "Nodeプロセス内の短い待機だけで、外部I/Oは発生しません。",
  }),
  GENERATE_EVENT: Object.freeze({
    action: "学習用イベントを生成し、イベント履歴と監査の見え方を確認します。",
    adjustable: "category、severity、1〜240文字のメッセージ。",
    safety: "固定categoryとseverityのローカル記録だけで、外部通知は行いません。",
  }),
  SLEEP: Object.freeze({
    action: "Nodeの次回以降のpoll間隔とjitterを変更し、check-in周期を観察します。",
    adjustable: "poll間隔250〜3000ms、jitter 0〜50%。",
    safety: "localhostのpoll周期だけを変更します。通信先やtransportは変更できません。",
  }),
  EXIT: Object.freeze({
    action: "選択したforeground Nodeへ正常停止を指示します。",
    adjustable: "停止理由は学習用Nodeの正常停止に固定されています。送信前の確認が必要です。",
    safety: "プロセスを正常終了するだけです。永続化や再起動処理は行いません。",
  }),
  RUN_PLAYBOOK: Object.freeze({
    action: "purple_lab Nodeの一時workspaceで固定playbookを実行し、手順と証跡を返します。",
    adjustable: "4種類の固定playbookから選択できます。path、command、対象データは指定できません。",
    safety: "Node-privateなsynthetic fixtureだけを扱い、host accessとnetwork accessは常に無効です。",
  }),
});

const elementIds = [
  "apiState",
  "apiStateText",
  "refreshButton",
  "resetButton",
  "consoleSettings",
  "autoRefreshSelect",
  "historyLimitSelect",
  "densitySelect",
  "autoRefreshStatus",
  "tokenManagement",
  "startupPanel",
  "tokenForm",
  "tokenInput",
  "tokenVisibilityButton",
  "connectButton",
  "clearTokenButton",
  "labModeValue",
  "protocolValue",
  "operatorPrincipalId",
  "operatorRole",
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
  "taskGuidance",
  "taskGuidanceTitle",
  "taskGuidanceAction",
  "taskGuidanceAdjustable",
  "taskGuidanceSafety",
  "queueTtlPresetSelect",
  "queueTtlCustomField",
  "queueTtlNumberInput",
  "queueTtlError",
  "taskInputFields",
  "taskNoInput",
  "taskTextField",
  "taskTextInput",
  "taskTextCharacterCount",
  "waitField",
  "waitRangeInput",
  "waitNumberInput",
  "eventFields",
  "eventCategorySelect",
  "eventSeveritySelect",
  "eventMessageInput",
  "eventMessageCharacterCount",
  "sleepFields",
  "sleepIntervalRangeInput",
  "sleepIntervalNumberInput",
  "sleepJitterRangeInput",
  "sleepJitterNumberInput",
  "exitField",
  "exitConfirmInput",
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
  "graphEntityCountBadge",
  "graphViewSelect",
  "graphFocusSelect",
  "graphZoomOutButton",
  "graphZoomInput",
  "graphZoomOutput",
  "graphZoomInButton",
  "graphFitButton",
  "graphStatus",
  "graphViewport",
  "graphSvg",
  "graphEmpty",
  "graphInspector",
  "graphPathList",
  "operationBuilder",
  "operationForm",
  "operationNodeSelect",
  "operationTtlSelect",
  "operationStepCount",
  "operationStepList",
  "operationPlaybookSelect",
  "operationAddStepButton",
  "operationLoadPathButton",
  "operationPreviewInput",
  "operationHint",
  "operationError",
  "operationSubmitButton",
  "taskStatusFilter",
  "taskSearchInput",
  "taskCountBadge",
  "taskTableBody",
  "tasksEmpty",
  "tasksEmptyTitle",
  "tasksEmptyDescription",
  "exerciseForm",
  "exerciseNodeSelect",
  "exerciseScenarioSelect",
  "exerciseScenarioHint",
  "exerciseTechniqueList",
  "exercisePermissionHint",
  "createExerciseButton",
  "exerciseCountBadge",
  "exerciseList",
  "exercisesEmpty",
  "eventCountBadge",
  "activitySourceFilter",
  "eventSearchInput",
  "eventLevelFilter",
  "eventActorFilter",
  "noteForm",
  "noteInput",
  "notePermissionHint",
  "noteCharacterCount",
  "noteSubmitButton",
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
let currentPrincipalId = "";
let currentRole = "";
let sessionPermissions = [];
let sessionGeneration = -1;
let syncStreamId = "";
let syncCursors = { events: 0, audit: 0 };
let retainedHistory = { events: [], audit: [] };
let latestOverview = null;
let refreshInFlight = false;
let renderedNodeKey = "";
let renderedTaskKey = "";
let renderedExerciseCatalogKey = "";
let renderedExerciseNodeKey = "";
let renderedExerciseKey = "";
let renderedGraphFocusKey = "";
let renderedGraphKey = "";
let renderedHistoryKey = "";
let renderedActorOptionsKey = "";
let pendingTaskSubmission = null;
let pendingOperationSubmission = null;
let pendingNoteSubmission = null;
let pendingExerciseSubmission = null;
let exitConfirmedNodeId = "";
let openTaskDetailId = "";
let renderedTaskDetailKey = "";
let currentScenarioCatalog = FIXED_EXERCISE_SCENARIOS.map((scenario) => ({ ...scenario }));
let graphSelectedEntityId = "";
let graphZoomPercent = 100;
let currentGraphDimensions = { width: 760, height: 360 };
let currentGraphModel = null;
let operationSteps = ["DISCOVERY_FIXTURES", "COLLECT_AND_STAGE"];
let renderedOperationNodeKey = "";
let refreshIntervalMs = REFRESH_INTERVAL_MS;
let historyRowLimit = MAX_EVENT_ROWS;
let interfaceDensity = "comfortable";
let refreshTimerId = null;

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

function setText(element, value) {
  const nextValue = String(value);
  if (element.textContent !== nextValue) element.textContent = nextValue;
}

function unicodeLength(value) {
  return Array.from(String(value)).length;
}

function hasSupportedTextCharacters(value) {
  const unsupported = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}\p{Zs}]/u;
  return Array.from(String(value)).every(
    (character) => character === "\n" || character === "\t" || character === " " || !unsupported.test(character),
  );
}

function storedUiSettings() {
  try {
    const parsed = JSON.parse(window.sessionStorage.getItem(UI_SETTINGS_KEY) || "{}");
    return parsed && !Array.isArray(parsed) && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function saveUiSettings() {
  try {
    window.sessionStorage.setItem(
      UI_SETTINGS_KEY,
      JSON.stringify({
        refresh_interval_ms: refreshIntervalMs,
        history_limit: historyRowLimit,
        density: interfaceDensity,
      }),
    );
  } catch {
    // The console remains usable when sessionStorage is unavailable.
  }
}

function initializeUiSettings() {
  const stored = storedUiSettings();
  const storedInterval = Number(stored.refresh_interval_ms);
  const storedHistoryLimit = Number(stored.history_limit);
  refreshIntervalMs = AUTO_REFRESH_OPTIONS.has(storedInterval)
    ? storedInterval
    : REFRESH_INTERVAL_MS;
  historyRowLimit = HISTORY_LIMIT_OPTIONS.has(storedHistoryLimit)
    ? storedHistoryLimit
    : MAX_EVENT_ROWS;
  interfaceDensity = DENSITY_OPTIONS.has(stored.density)
    ? stored.density
    : "comfortable";
  elements.autoRefreshSelect.value = String(refreshIntervalMs);
  elements.historyLimitSelect.value = String(historyRowLimit);
  elements.densitySelect.value = interfaceDensity;
  document.body.dataset.density = interfaceDensity;
  updateAutoRefreshStatus();
}

function updateAutoRefreshStatus() {
  setText(elements.autoRefreshStatus, `${refreshIntervalMs / 1000}秒ごとに自動更新`);
}

function scheduleAutoRefresh() {
  if (refreshTimerId !== null) window.clearInterval(refreshTimerId);
  refreshTimerId = window.setInterval(() => {
    if (document.visibilityState === "visible") {
      refresh({ silent: true });
    }
  }, refreshIntervalMs);
}

function setApiState(state, message) {
  if (elements.apiState.dataset.state !== state) elements.apiState.dataset.state = state;
  setText(elements.apiStateText, message);
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
    return "Operator session が無効または期限切れです。期限切れの場合はTeamserverを再起動し、新しいURLを開いてください。";
  }
  if (error instanceof ApiError && error.status === 403) {
    return "このOperatorには操作権限がありません。現在のroleとpermissionsを確認してください。";
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

function hasPermission(permission) {
  return sessionPermissions.includes(permission);
}

function renderOperatorSession(session) {
  if (
    !session ||
    typeof session.principal_id !== "string" ||
    !session.principal_id ||
    typeof session.role !== "string" ||
    !session.role ||
    !Array.isArray(session.permissions) ||
    session.permissions.some((permission) => typeof permission !== "string")
  ) {
    throw new ApiError("Operator session 情報が不正なため接続を拒否しました。");
  }

  currentPrincipalId = session.principal_id;
  currentRole = session.role;
  sessionPermissions = [...new Set(session.permissions)];
  setText(elements.operatorPrincipalId, currentPrincipalId);
  setText(elements.operatorRole, currentRole.toUpperCase());
  elements.operatorPrincipalId.parentElement.dataset.state = "active";
  elements.operatorRole.parentElement.dataset.state = "active";
  elements.operatorRole.parentElement.dataset.role = currentRole;
}

function clearOperatorSession() {
  currentPrincipalId = "";
  currentRole = "";
  sessionPermissions = [];
  setText(elements.operatorPrincipalId, "—");
  setText(elements.operatorRole, "—");
  elements.operatorPrincipalId.parentElement.dataset.state = "unknown";
  elements.operatorRole.parentElement.dataset.state = "unknown";
  delete elements.operatorRole.parentElement.dataset.role;
}

function resetSyncState() {
  sessionGeneration = -1;
  syncStreamId = "";
  syncCursors = { events: 0, audit: 0 };
  retainedHistory = { events: [], audit: [] };
}

function requestIsStale(requestGeneration, requestToken) {
  return requestGeneration !== tokenGeneration || requestToken !== operatorToken;
}

function syncCounter(group, key, label) {
  const value = group?.[key];
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new ApiError(`${label}.${key} が不正な同期応答を拒否しました。`);
  }
  return value;
}

function validateSyncPage(page) {
  if (
    !page ||
    page.lab_mode !== true ||
    typeof page.stream_id !== "string" ||
    !/^stream-[0-9a-f]{24}$/.test(page.stream_id) ||
    !Array.isArray(page.nodes) ||
    !Array.isArray(page.tasks) ||
    !Array.isArray(page.exercises) ||
    (page.scenario_catalog !== undefined && !Array.isArray(page.scenario_catalog)) ||
    !Array.isArray(page.events) ||
    !Array.isArray(page.audit)
  ) {
    throw new ApiError("localhost lab_mode を確認できない同期応答を拒否しました。");
  }

  for (const groupName of ["cursors", "high_watermarks", "oldest_available"]) {
    syncCounter(page[groupName], "events", groupName);
    syncCounter(page[groupName], "audit", groupName);
  }
  for (const groupName of ["cursor_reset", "has_more"]) {
    if (
      typeof page[groupName]?.events !== "boolean" ||
      typeof page[groupName]?.audit !== "boolean"
    ) {
      throw new ApiError(`${groupName} が不正な同期応答を拒否しました。`);
    }
  }
  for (const [historyName, records] of [
    ["events", page.events],
    ["audit", page.audit],
  ]) {
    let previousSequence = 0;
    for (const record of records) {
      if (
        !record ||
        !Number.isSafeInteger(record.sequence) ||
        record.sequence < 1 ||
        record.sequence <= previousSequence
      ) {
        throw new ApiError(`${historyName} が昇順でない同期応答を拒否しました。`);
      }
      previousSequence = record.sequence;
    }
  }
  return page;
}

function syncPath(cursors) {
  return `/lab/sync?events_after=${cursors.events}&audit_after=${cursors.audit}&limit=${SYNC_PAGE_SIZE}`;
}

function mergeHistoryRecords(existing, incoming, reset = false) {
  const bySequence = new Map();
  if (!reset) {
    for (const record of existing) bySequence.set(record.sequence, record);
  }
  for (const record of incoming) bySequence.set(record.sequence, record);
  return Array.from(bySequence.values())
    .sort((left, right) => left.sequence - right.sequence)
    .slice(-MAX_HISTORY_RECORDS);
}

function recordText(record, ...keys) {
  if (!record || typeof record !== "object" || Array.isArray(record)) return "";
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return "";
}

function techniqueLabel(technique) {
  if (typeof technique === "string") return technique.trim();
  if (!technique || typeof technique !== "object" || Array.isArray(technique)) return "";
  const id = recordText(technique, "id", "technique_id");
  const name = recordText(technique, "name", "title");
  if (id && name && id !== name) return `${id} · ${name}`;
  return id || name;
}

function techniqueLabels(techniques) {
  if (!Array.isArray(techniques)) return [];
  return Array.from(new Set(techniques.map(techniqueLabel).filter(Boolean))).slice(0, 32);
}

function normalizeTechniqueRecords(techniques) {
  const byId = new Map();
  for (const technique of Array.isArray(techniques) ? techniques : []) {
    const rawId = typeof technique === "string"
      ? technique.split(" · ", 1)[0].trim()
      : recordText(technique, "id", "technique_id");
    if (!GRAPH_TECHNIQUE_IDS.has(rawId)) continue;
    const name = typeof technique === "string"
      ? technique.split(" · ").slice(1).join(" · ").trim()
      : recordText(technique, "name", "title");
    byId.set(rawId, { id: rawId, name: name || rawId });
  }
  return Array.from(byId.values());
}

function normalizeScenarioScope(scope) {
  const source = scope && typeof scope === "object" && !Array.isArray(scope) ? scope : {};
  return {
    workspace: recordText(source, "workspace"),
    data: recordText(source, "data"),
    host_access: source.host_access === false ? false : null,
    network_access: source.network_access === false ? false : null,
    command_execution: source.command_execution === false ? false : null,
    attack_mapping: recordText(source, "attack_mapping"),
  };
}

function normalizeScenarioDetections(detections) {
  const normalized = [];
  const severityAllowlist = new Set(["info", "low", "medium", "high", "warning"]);
  for (const detection of Array.isArray(detections) ? detections : []) {
    if (!detection || typeof detection !== "object" || Array.isArray(detection)) continue;
    const playbook = recordText(detection, "playbook");
    const techniqueId = recordText(detection, "technique_id");
    if (!GRAPH_PLAYBOOK_IDS.has(playbook) || !GRAPH_TECHNIQUE_IDS.has(techniqueId)) continue;
    const severityCandidate = recordText(detection, "severity").toLowerCase();
    normalized.push({
      id: recordText(detection, "id") || `detection-${normalized.length + 1}`,
      source_id: recordText(detection, "source_id"),
      name: recordText(detection, "name") || "Synthetic detection",
      playbook,
      technique_id: techniqueId,
      signal: recordText(detection, "signal"),
      severity: severityAllowlist.has(severityCandidate) ? severityCandidate : "info",
    });
    if (normalized.length >= 16) break;
  }
  return normalized;
}

function normalizeScenarioCatalog(catalog) {
  const byId = new Map();
  for (const entry of Array.isArray(catalog) ? catalog : []) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) continue;
    const id = recordText(entry, "id", "scenario_id");
    if (!EXERCISE_SCENARIO_IDS.has(id)) continue;
    byId.set(id, {
      id,
      title: recordText(entry, "title", "name") || id,
      description:
        recordText(entry, "description", "summary") ||
        "固定シナリオの検知timelineを観察します。",
      techniques: normalizeTechniqueRecords(entry.techniques || entry.attack_techniques),
      playbooks: Array.from(
        new Set(
          (Array.isArray(entry.playbooks) ? entry.playbooks : [])
            .filter((playbook) => typeof playbook === "string" && GRAPH_PLAYBOOK_IDS.has(playbook)),
        ),
      ).slice(0, 4),
      detections: normalizeScenarioDetections(entry.detections),
      containment_actions: (Array.isArray(entry.containment_actions)
        ? entry.containment_actions
        : []).filter((action) => CONTAINMENT_ACTIONS.has(action)),
      scope: normalizeScenarioScope(entry.scope),
    });
  }
  return FIXED_EXERCISE_SCENARIOS.map((fallback) => ({
    playbooks: [],
    detections: [],
    containment_actions: [],
    scope: normalizeScenarioScope(null),
    ...fallback,
    ...(byId.get(fallback.id) || {}),
  }));
}

function renderTechniqueList(container, techniques, emptyText = "CATALOG PENDING") {
  container.replaceChildren();
  const labels = techniqueLabels(techniques);
  if (labels.length === 0) {
    container.append(makeTextElement("span", "technique-chip technique-chip--empty", emptyText));
    return;
  }
  for (const label of labels) {
    container.append(makeTextElement("span", "technique-chip", label));
  }
}

function updateExerciseScenarioSummary() {
  const selected = currentScenarioCatalog.find(
    (scenario) => scenario.id === elements.exerciseScenarioSelect.value,
  );
  elements.exerciseScenarioHint.textContent =
    selected?.description || "固定シナリオを選択してください。";
  renderTechniqueList(
    elements.exerciseTechniqueList,
    selected?.techniques || [],
    selected ? "TECHNIQUE MAPPING PENDING" : "SCENARIO NOT SELECTED",
  );
}

function renderExerciseCatalog(catalog) {
  const normalized = normalizeScenarioCatalog(catalog);
  const catalogKey = JSON.stringify(normalized);
  currentScenarioCatalog = normalized;
  if (catalogKey !== renderedExerciseCatalogKey) {
    renderedExerciseCatalogKey = catalogKey;
    const previousScenarioId = elements.exerciseScenarioSelect.value;
    elements.exerciseScenarioSelect.replaceChildren();
    for (const scenario of normalized) {
      const label = scenario.title === scenario.id
        ? scenario.id
        : `${scenario.id} · ${scenario.title}`;
      elements.exerciseScenarioSelect.add(new Option(label, scenario.id));
    }
    elements.exerciseScenarioSelect.value = EXERCISE_SCENARIO_IDS.has(previousScenarioId)
      ? previousScenarioId
      : normalized[0]?.id || "";
  }
  updateExerciseScenarioSummary();
}

function eligibleExerciseNodes(nodes) {
  return nodes.filter(
    (node) => node.profile === "purple_lab" && node.session_active !== false,
  );
}

function renderExerciseNodes(nodes) {
  const exerciseNodes = eligibleExerciseNodes(nodes);
  const nodeKey = JSON.stringify(
    exerciseNodes.map((node) => ({
      id: node.id,
      name: node.name,
      status: node.status,
      session_active: node.session_active,
      tasking_paused: node.tasking_paused,
    })),
  );
  if (nodeKey === renderedExerciseNodeKey) return;
  renderedExerciseNodeKey = nodeKey;
  const previousNodeId = elements.exerciseNodeSelect.value;
  elements.exerciseNodeSelect.replaceChildren(new Option("purple_lab Node を選択", ""));
  for (const node of exerciseNodes) {
    const option = new Option(
      `${node.name || node.id} · ${node.tasking_paused ? "TASKING PAUSED" : String(node.status || "unknown").toUpperCase()}`,
      node.id,
    );
    option.disabled = node.status !== "online" || node.tasking_paused === true;
    elements.exerciseNodeSelect.add(option);
  }
  const previousOption = Array.from(elements.exerciseNodeSelect.options).find(
    (option) => option.value === previousNodeId && !option.disabled,
  );
  const firstOnline = Array.from(elements.exerciseNodeSelect.options).find(
    (option) => option.value && !option.disabled,
  );
  elements.exerciseNodeSelect.value = previousOption?.value || firstOnline?.value || "";
}

function selectedExerciseNode() {
  const nodes = Array.isArray(latestOverview?.nodes) ? latestOverview.nodes : [];
  return nodes.find((node) => node.id === elements.exerciseNodeSelect.value) || null;
}

function createExerciseFact(label, value) {
  const fact = document.createElement("span");
  fact.append(
    makeTextElement("small", "", label),
    makeTextElement("strong", "", value || "—"),
  );
  return fact;
}

function exerciseAlertItem(alert) {
  const item = document.createElement("li");
  item.className = "exercise-alert";
  const severity = recordText(alert, "severity", "level", "status").toLowerCase() || "info";
  item.dataset.severity = severity;
  const title = recordText(alert, "name", "rule_id", "title", "id") || "DETECTION ALERT";
  const alertMetadata = [
    recordText(alert, "source_id"),
    recordText(alert, "technique_id"),
    recordText(alert, "signal", "signal_id"),
  ].filter(Boolean);
  const message = alertMetadata.join(" · ") || compactJson(alert);
  item.append(
    makeTextElement("strong", "", title),
    makeTextElement("p", "", message),
    makeTextElement("span", "", severity.toUpperCase()),
  );
  return item;
}

function exerciseTimelineItem(entry, index) {
  const item = document.createElement("li");
  item.className = "exercise-timeline__item";
  const marker = makeTextElement("span", "exercise-timeline__marker", String(index + 1));
  marker.setAttribute("aria-hidden", "true");
  const body = document.createElement("div");
  const title = recordText(entry, "title", "label", "kind", "phase") || `STEP ${index + 1}`;
  const description =
    recordText(entry, "summary", "message", "description", "outcome") || compactJson(entry);
  const metadata = [];
  const time = recordText(entry, "time", "timestamp", "created_at");
  const offset = recordText(entry, "offset_ms");
  const technique = recordText(entry, "technique_id");
  if (time) metadata.push(localTime(time));
  if (offset) metadata.push(`+${offset} ms`);
  if (technique) metadata.push(technique);
  body.append(
    makeTextElement("strong", "", title),
    makeTextElement("p", "", description),
    makeTextElement("small", "", metadata.join(" · ") || "synthetic timeline"),
  );
  item.append(marker, body);
  return item;
}

function canContainExercise(exercise) {
  return (
    String(exercise?.detection_status || "").toLowerCase() === "detected" &&
    String(exercise?.status || "").toLowerCase() !== "contained" &&
    String(exercise?.containment?.status || "").toLowerCase() !== "applied"
  );
}

function createExerciseCard(exercise, nodeNames, scenarioTitles) {
  const card = document.createElement("article");
  card.className = "exercise-card";
  const status = String(exercise.status || "unknown").toLowerCase();
  const detectionStatus = String(exercise.detection_status || "pending").toLowerCase();
  card.dataset.status = status;
  card.dataset.detection = detectionStatus;

  const header = document.createElement("div");
  header.className = "exercise-card__header";
  const identity = document.createElement("div");
  identity.append(
    makeTextElement("span", "exercise-card__scenario", exercise.scenario_id || "UNKNOWN_SCENARIO"),
    makeTextElement(
      "h3",
      "",
      exercise.title || scenarioTitles.get(exercise.scenario_id) || exercise.scenario_id || "演習",
    ),
  );
  const badges = document.createElement("div");
  badges.className = "exercise-card__badges";
  const statusBadge = makeTextElement("span", "exercise-state", status.toUpperCase());
  statusBadge.dataset.status = status;
  const detectionBadge = makeTextElement(
    "span",
    "exercise-detection",
    `DETECTION ${detectionStatus.toUpperCase()}`,
  );
  detectionBadge.dataset.status = detectionStatus;
  badges.append(statusBadge, detectionBadge);
  header.append(identity, badges);

  const taskIds = Array.isArray(exercise.task_ids)
    ? exercise.task_ids.filter((taskId) => typeof taskId === "string").slice(0, 16)
    : [];
  const facts = document.createElement("div");
  facts.className = "exercise-card__facts";
  facts.append(
    createExerciseFact("EXERCISE", exercise.id),
    createExerciseFact("NODE", nodeNames.get(exercise.node_id) || exercise.node_id),
    createExerciseFact("CREATED BY", exercise.created_by),
    createExerciseFact("CREATED", localTime(exercise.created_at)),
    createExerciseFact("COMPLETED", localTime(exercise.completed_at)),
    createExerciseFact("TASK IDS", taskIds.join(" · ") || "—"),
  );

  const techniques = document.createElement("div");
  techniques.className = "technique-list exercise-card__techniques";
  techniques.setAttribute("aria-label", "ATT&CK technique mapping");
  renderTechniqueList(techniques, exercise.techniques || [], "NO TECHNIQUE MAPPING");

  const observations = document.createElement("div");
  observations.className = "exercise-observations";
  const alertsSection = document.createElement("section");
  alertsSection.className = "exercise-observation";
  alertsSection.append(makeTextElement("h4", "", "DETECTION ALERTS"));
  const alertList = document.createElement("ul");
  alertList.className = "exercise-alerts";
  const alerts = Array.isArray(exercise.alerts) ? exercise.alerts : [];
  if (alerts.length === 0) {
    alertList.append(makeTextElement("li", "exercise-observation__empty", "検知alertはまだありません。"));
  } else {
    for (const alert of alerts.slice(0, 50)) alertList.append(exerciseAlertItem(alert));
  }
  alertsSection.append(alertList);

  const timelineSection = document.createElement("section");
  timelineSection.className = "exercise-observation";
  timelineSection.append(makeTextElement("h4", "", "SCENARIO TIMELINE"));
  const timelineList = document.createElement("ol");
  timelineList.className = "exercise-timeline";
  const timeline = Array.isArray(exercise.timeline) ? exercise.timeline : [];
  if (timeline.length === 0) {
    timelineList.append(makeTextElement("li", "exercise-observation__empty", "timelineを待機中です。"));
  } else {
    timeline.slice(0, 50).forEach((entry, index) => {
      timelineList.append(exerciseTimelineItem(entry, index));
    });
  }
  timelineSection.append(timelineList);
  observations.append(alertsSection, timelineSection);

  const containment = document.createElement("div");
  containment.className = "exercise-containment";
  containment.append(
    makeTextElement("span", "", "CONTAINMENT STATE"),
    makeTextElement(
      "p",
      "",
      exercise.containment ? compactJson(exercise.containment) : "未適用",
    ),
  );

  const actions = document.createElement("div");
  actions.className = "exercise-card__actions";
  actions.append(makeTextElement("span", "", "ADMIN CONTAINMENT"));
  const actionDefinitions = [
    ["CANCEL_REMAINING", "残りのタスクを取消"],
    ["PAUSE_NODE_TASKING", "Node taskingを一時停止"],
  ];
  for (const [action, label] of actionDefinitions) {
    const button = makeTextElement("button", "button button--danger-ghost button--compact", label);
    button.type = "button";
    button.dataset.exerciseContain = "true";
    button.dataset.exerciseId = typeof exercise.id === "string" ? exercise.id : "";
    button.dataset.containmentAction = action;
    button.dataset.exerciseContainable = String(canContainExercise(exercise));
    button.addEventListener("click", () => containExercise(exercise.id, action, button));
    actions.append(button);
  }

  card.append(header, facts, techniques, observations, containment, actions);
  return card;
}

function renderExercises(exercises, nodes, catalog) {
  const nodeNames = new Map(nodes.map((node) => [node.id, node.name]));
  const scenarioTitles = new Map(catalog.map((scenario) => [scenario.id, scenario.title]));
  const renderKey = JSON.stringify({ exercises, nodes: Array.from(nodeNames), catalog });
  if (renderKey === renderedExerciseKey) {
    updateControls();
    return;
  }
  renderedExerciseKey = renderKey;
  elements.exerciseList.replaceChildren();
  elements.exerciseCountBadge.textContent = `${exercises.length} EXERCISES`;
  elements.exercisesEmpty.classList.toggle("is-visible", exercises.length === 0);
  for (const exercise of exercises) {
    if (!exercise || typeof exercise !== "object" || Array.isArray(exercise)) continue;
    elements.exerciseList.append(createExerciseCard(exercise, nodeNames, scenarioTitles));
  }
  updateControls();
}

function normalizedGraphStatus(value, fallback = "unknown") {
  const candidate = String(value || "").toLowerCase();
  return GRAPH_STATUSES.has(candidate) ? candidate : fallback;
}

function graphEntity(id, kind, label, subtitle, status, layer, details, action = null) {
  return {
    id,
    kind,
    label: String(label || "—"),
    subtitle: String(subtitle || ""),
    status: normalizedGraphStatus(status),
    layer,
    details,
    action,
  };
}

function graphRelation(id, from, to, label, observed) {
  return { id, from, to, label, observed: observed === true };
}

function teamserverGraphStatus(overview) {
  const status = overview?.connection_status;
  return ["online", "offline", "unknown"].includes(status) ? status : "offline";
}

function retainedTechniqueIdsForTask(timeline, taskId) {
  return new Set(
    (Array.isArray(timeline) ? timeline : [])
      .filter((item) => item?.task_id === taskId && item.kind === "technique.observed")
      .map((item) => item.technique_id)
      .filter((techniqueId) => GRAPH_TECHNIQUE_IDS.has(techniqueId)),
  );
}

function groupGraphTasksByOperation(tasks) {
  const ordered = [];
  const emittedOperations = new Set();
  for (const task of tasks) {
    const operationId = typeof task.operation_id === "string" ? task.operation_id : "";
    if (!operationId) {
      ordered.push(task);
      continue;
    }
    if (emittedOperations.has(operationId)) continue;
    emittedOperations.add(operationId);
    ordered.push(
      ...tasks
        .filter((candidate) => candidate.operation_id === operationId)
        .sort((left, right) => (left.operation_step || 0) - (right.operation_step || 0)),
    );
  }
  return ordered;
}

function graphTaskSelection(tasks) {
  const uniqueTasks = Array.from(new Set(Array.isArray(tasks) ? tasks : []));
  const isActive = (task) => ["queued", "dispatched"].includes(task.status);
  const active = groupGraphTasksByOperation(uniqueTasks.filter(isActive));
  const terminal = groupGraphTasksByOperation(uniqueTasks.filter((task) => !isActive(task)));
  const selectedTasks = new Set(active.slice(0, MAX_GRAPH_TASKS));
  for (const task of terminal) {
    if (selectedTasks.size >= MAX_GRAPH_TASKS) break;
    selectedTasks.add(task);
  }
  const selectedSeeds = active
    .filter((task) => selectedTasks.has(task))
    .concat(terminal.filter((task) => selectedTasks.has(task)));
  return groupGraphTasksByOperation(selectedSeeds);
}

function buildTopologyGraph(overview, focusValue) {
  const allNodes = (Array.isArray(overview.nodes) ? overview.nodes : [])
    .filter((node) => node && typeof node === "object" && typeof node.id === "string")
    .slice(0, MAX_GRAPH_NODES);
  const focusedNodeId = focusValue.startsWith("node:") ? focusValue.slice(5) : "";
  const nodes = focusedNodeId
    ? allNodes.filter((node) => node.id === focusedNodeId)
    : allNodes;
  const nodeIds = new Set(nodes.map((node) => node.id));
  const taskPool = [];
  const seenTaskIds = new Set();
  for (const task of Array.isArray(overview.tasks) ? overview.tasks : []) {
    if (
      !task ||
      typeof task.id !== "string" ||
      !task.id ||
      seenTaskIds.has(task.id) ||
      !nodeIds.has(task.node_id)
    ) continue;
    seenTaskIds.add(task.id);
    taskPool.push(task);
  }
  const tasks = graphTaskSelection(taskPool);
  const entities = [
    graphEntity(
      "topology:teamserver",
      "teamserver",
      "Teamserver",
      overview.protocol || "loopback-http-poll/v1",
      teamserverGraphStatus(overview),
      0,
      [
        ["Mode", "localhost lab"],
        ["Protocol", overview.protocol || "loopback-http-poll/v1"],
        ["Projection", "read-only / current sync"],
      ],
    ),
  ];
  const edges = [];

  for (const node of nodes) {
    const nodeStatus = node.tasking_paused
      ? "paused"
      : normalizedGraphStatus(node.status, "unknown");
    entities.push(
      graphEntity(
        `node:${node.id}`,
        "node",
        node.name || node.id,
        `${node.profile || "unknown"} · ${String(node.status || "unknown").toUpperCase()}`,
        nodeStatus,
        1,
        [
          ["Node ID", node.id],
          ["Capability profile", node.profile || "—"],
          ["Session", node.session_active === false ? "closed" : "active"],
          ["Tasking", node.tasking_paused ? "paused" : "enabled"],
          ["Poll", Number.isInteger(node.poll_interval_ms) ? `${node.poll_interval_ms} ms` : "—"],
          ["Jitter", Number.isInteger(node.jitter_percent) ? `${node.jitter_percent}%` : "—"],
          ["Capabilities", Array.isArray(node.capabilities) ? node.capabilities.join(" · ") : "—"],
        ],
        { kind: "node", nodeId: node.id },
      ),
    );
    edges.push(
      graphRelation(
        `topology:teamserver:${node.id}`,
        "topology:teamserver",
        `node:${node.id}`,
        "POLL / RESULT",
        true,
      ),
    );
  }

  for (const task of tasks) {
    const type = recordText(task, "type") || "TASK";
    const status = normalizedGraphStatus(task.status, "unknown");
    const playbook = type === "RUN_PLAYBOOK" && GRAPH_PLAYBOOK_IDS.has(task.payload?.playbook)
      ? task.payload.playbook
      : "";
    entities.push(
      graphEntity(
        `task:${task.id}`,
        "task",
        type,
        `${task.id} · ${status.toUpperCase()}`,
        status,
        2,
        [
          ["Task ID", task.id],
          ["Correlation", task.correlation_id || "—"],
          ["Operation", task.operation_id || "—"],
          ["Operation step", Number.isInteger(task.operation_step) ? task.operation_step : "—"],
          ["Node", task.node_id || "—"],
          ["Created by", task.created_by || "—"],
          ["Status", status],
          ["Delivery attempts", Number.isInteger(task.delivery_attempts) ? task.delivery_attempts : "—"],
        ],
        { kind: "task", taskId: task.id },
      ),
    );
    edges.push(
      graphRelation(
        `topology:${task.node_id}:${task.id}`,
        `node:${task.node_id}`,
        `task:${task.id}`,
        status.toUpperCase(),
        true,
      ),
    );
    if (playbook) {
      const playbookId = `topology-playbook:${task.id}:${playbook}`;
      const playbookObserved = status === "completed";
      entities.push(
        graphEntity(
          playbookId,
          "playbook",
          playbook,
          `${task.id} · FIXED PLAYBOOK`,
          playbookObserved ? "observed" : "planned",
          3,
          [
            ["Playbook", playbook],
            ["Task", task.id],
            ["Operation", task.operation_id || "—"],
            ["Operation step", Number.isInteger(task.operation_step) ? task.operation_step : "—"],
            ["Execution", playbookObserved ? "validated result received" : status],
          ],
          { kind: "playbook", playbook, nodeId: task.node_id || "" },
        ),
      );
      edges.push(
        graphRelation(
          `topology:${task.id}:${playbook}`,
          `task:${task.id}`,
          playbookId,
          "RUNS",
          playbookObserved,
        ),
      );
    }
  }

  const omitted = Math.max(0, taskPool.length - tasks.length);
  const playbookCount = entities.filter((entity) => entity.kind === "playbook").length;
  return {
    mode: "topology",
    entities,
    edges,
    summary: `${nodes.length} Node · ${tasks.length}/${taskPool.length} Task · ${playbookCount} Playbook${omitted ? ` · ${omitted}件省略` : ""}`,
  };
}

function scenarioTechnique(scenario, techniqueId, fallbackName = "") {
  const technique = (Array.isArray(scenario.techniques) ? scenario.techniques : []).find(
    (candidate) => candidate.id === techniqueId,
  );
  return { id: techniqueId, name: technique?.name || fallbackName || techniqueId };
}

function buildAttackGraph(overview, focusValue) {
  const exercises = Array.isArray(overview.exercises) ? overview.exercises : [];
  const exercise = focusValue.startsWith("exercise:")
    ? exercises.find((candidate) => candidate.id === focusValue.slice(9)) || null
    : null;
  const scenarioId = exercise?.scenario_id || (
    focusValue.startsWith("scenario:") ? focusValue.slice(9) : ""
  );
  const scenario = currentScenarioCatalog.find((candidate) => candidate.id === scenarioId);
  if (!scenario) {
    return { mode: "attack", entities: [], edges: [], summary: "固定scenarioを選択してください。" };
  }

  const nodes = Array.isArray(overview.nodes) ? overview.nodes : [];
  const previewNodeId = elements.exerciseNodeSelect.value;
  const node = nodes.find(
    (candidate) => candidate.id === (exercise?.node_id || previewNodeId),
  ) || null;
  const sourceId = node ? `node:${node.id}` : `attack-node:${scenario.id}`;
  const sourceObserved = Boolean(exercise && node);
  const entities = [
    graphEntity(
      sourceId,
      "node",
      node?.name || "Purple Lab Node",
      exercise ? `Exercise ${exercise.id}` : "SCENARIO PLACEHOLDER",
      sourceObserved ? normalizedGraphStatus(node?.status, "unknown") : "planned",
      0,
      [
        ["Scenario", scenario.id],
        ["Exercise", exercise?.id || "catalog preview"],
        ["Node", node?.id || "開始時に選択"],
        ["Workspace", scenario.scope?.workspace || "—"],
        ["Data", scenario.scope?.data || "—"],
        ["Host access", scenario.scope?.host_access === false ? "false" : "unverified"],
        ["Network access", scenario.scope?.network_access === false ? "false" : "unverified"],
      ],
      node ? { kind: "node", nodeId: node.id } : null,
    ),
  ];
  const edges = [];
  const allTasks = Array.isArray(overview.tasks) ? overview.tasks : [];
  const tasksById = new Map(allTasks.map((task) => [task.id, task]));
  const exerciseTaskIds = Array.isArray(exercise?.task_ids) ? exercise.task_ids : [];
  const exerciseTimeline = Array.isArray(exercise?.timeline) ? exercise.timeline : [];
  const alerts = Array.isArray(exercise?.alerts) ? exercise.alerts : [];
  const playbooks = (Array.isArray(scenario.playbooks) ? scenario.playbooks : [])
    .filter((playbook) => GRAPH_PLAYBOOK_IDS.has(playbook))
    .slice(0, 4);
  let anchors = [{ id: sourceId, observed: sourceObserved }];
  const containmentAnchors = [];
  let layer = 1;

  playbooks.forEach((playbook, playbookIndex) => {
    const expectedTaskId = exerciseTaskIds[playbookIndex] || "";
    const expectedTask = tasksById.get(expectedTaskId);
    const task = expectedTask?.type === "RUN_PLAYBOOK" && expectedTask?.payload?.playbook === playbook
      ? expectedTask
      : allTasks.find(
        (candidate) => exerciseTaskIds.includes(candidate.id) && candidate.payload?.playbook === playbook,
      );
    const representedTaskId = task?.id || expectedTaskId;
    const retainedTerminalItem = exerciseTimeline.find(
      (item) => item?.task_id === representedTaskId && [
        "task.completed",
        "task.failed",
        "task.cancelled",
        "task.timeout",
        "task.expired",
      ].includes(item.kind),
    );
    const retainedTaskStatus = retainedTerminalItem?.kind?.slice(5) || "";
    const taskStatus = task?.status || retainedTaskStatus || (representedTaskId ? "unknown" : "planned");
    const taskCompleted = taskStatus === "completed";
    const taskObserved = Boolean(task || representedTaskId);
    const taskId = representedTaskId
      ? `task:${representedTaskId}`
      : `attack-task:${focusValue}:${playbookIndex}`;
    entities.push(
      graphEntity(
        taskId,
        "task",
        representedTaskId || `Planned task ${playbookIndex + 1}`,
        taskObserved ? taskStatus.toUpperCase() : "NOT QUEUED",
        taskObserved ? normalizedGraphStatus(taskStatus, "unknown") : "planned",
        layer,
        [
          ["Task ID", representedTaskId || "未作成"],
          ["Playbook", playbook],
          ["Status", taskStatus],
          ["Correlation", task?.correlation_id || "—"],
          ["Operation", task?.operation_id || "—"],
          ["Operation step", Number.isInteger(task?.operation_step) ? task.operation_step : "—"],
          ["Evidence", task ? "current task record" : retainedTerminalItem ? "retained exercise timeline" : "catalog preview"],
        ],
        task ? { kind: "task", taskId: task.id } : null,
      ),
    );
    for (const anchor of anchors) {
      edges.push(
        graphRelation(
          `attack:${anchor.id}:${taskId}`,
          anchor.id,
          taskId,
          playbookIndex === 0 ? "EXECUTES" : "NEXT STEP",
          taskObserved,
        ),
      );
    }

    const playbookId = `attack-playbook:${focusValue}:${playbookIndex}:${playbook}`;
    entities.push(
      graphEntity(
        playbookId,
        "playbook",
        playbook,
        "FIXED PURPLE LAB PLAYBOOK",
        taskCompleted ? "observed" : "planned",
        layer + 1,
        [
          ["Playbook", playbook],
          ["Input", "fixed identifier only"],
          ["Task", representedTaskId || "catalog preview"],
          [
            "Execution",
            taskCompleted
              ? task ? "validated result received" : "retained exercise evidence"
              : "planned",
          ],
        ],
        { kind: "playbook", playbook, nodeId: node?.id || "" },
      ),
    );
    edges.push(
      graphRelation(
        `attack:${taskId}:${playbookId}`,
        taskId,
        playbookId,
        "RUNS",
        taskCompleted,
      ),
    );

    const resultTechniques = task?.status === "completed"
      ? normalizeTechniqueRecords(task?.result?.attack_techniques)
      : [];
    const resultTechniqueIds = new Set(resultTechniques.map((technique) => technique.id));
    const retainedTechniqueIds = retainedTechniqueIdsForTask(
      exerciseTimeline,
      representedTaskId,
    );
    const taskAlerts = alerts.filter(
      (candidate) => !representedTaskId || candidate.task_id === representedTaskId,
    );
    const alertTechniqueIds = new Set(
      taskAlerts
        .map((candidate) => candidate.technique_id)
        .filter((techniqueId) => GRAPH_TECHNIQUE_IDS.has(techniqueId)),
    );
    const observedTechniqueIds = new Set([
      ...resultTechniqueIds,
      ...retainedTechniqueIds,
      ...alertTechniqueIds,
    ]);
    const rules = (Array.isArray(scenario.detections) ? scenario.detections : [])
      .filter((detection) => detection.playbook === playbook);
    const techniqueIds = Array.from(new Set([
      ...rules.map((rule) => rule.technique_id),
      ...resultTechniques.map((technique) => technique.id),
      ...retainedTechniqueIds,
      ...alertTechniqueIds,
    ])).filter((techniqueId) => GRAPH_TECHNIQUE_IDS.has(techniqueId));
    const techniqueAnchors = [];
    for (const techniqueId of techniqueIds) {
      const resultTechnique = resultTechniques.find((candidate) => candidate.id === techniqueId);
      const technique = scenarioTechnique(scenario, techniqueId, resultTechnique?.name);
      const techniqueObserved = observedTechniqueIds.has(techniqueId);
      const techniqueEntityId = `attack-technique:${focusValue}:${playbookIndex}:${techniqueId}`;
      entities.push(
        graphEntity(
          techniqueEntityId,
          "technique",
          technique.id,
          technique.name,
          techniqueObserved ? "observed" : "planned",
          layer + 2,
          [
            ["Technique", technique.id],
            ["Name", technique.name],
            ["Mapping", "educational-only"],
            [
              "Source",
              resultTechniqueIds.has(techniqueId)
                ? "validated task result"
                : retainedTechniqueIds.has(techniqueId)
                  ? "retained exercise timeline"
                  : alertTechniqueIds.has(techniqueId)
                    ? "retained exercise alert"
                    : "fixed scenario catalog",
            ],
          ],
        ),
      );
      edges.push(
        graphRelation(
          `attack:${playbookId}:${techniqueEntityId}`,
          playbookId,
          techniqueEntityId,
          "MAPS TO",
          techniqueObserved,
        ),
      );
      techniqueAnchors.push({ id: techniqueEntityId, observed: techniqueObserved });
    }

    const detectionAnchors = [];
    for (const rule of rules) {
      const alert = alerts.find(
        (candidate) => candidate.rule_id === rule.id && (
          !representedTaskId || candidate.task_id === representedTaskId
        ),
      );
      const detectionObserved = Boolean(alert);
      const detectionId = `attack-detection:${focusValue}:${rule.id}`;
      entities.push(
        graphEntity(
          detectionId,
          "detection",
          rule.name,
          [rule.source_id, rule.technique_id].filter(Boolean).join(" · "),
          detectionObserved
            ? alert?.status === "contained" ? "contained" : "detected"
            : "planned",
          layer + 3,
          [
            ["Rule", rule.id],
            ["Source", rule.source_id || "—"],
            ["Signal", rule.signal || "—"],
            ["Severity", rule.severity || "—"],
            ["Alert", alert?.id || "not observed"],
          ],
        ),
      );
      const techniqueAnchor = techniqueAnchors.find(
        (candidate) => candidate.id.endsWith(`:${rule.technique_id}`),
      ) || { id: playbookId, observed: taskCompleted };
      edges.push(
        graphRelation(
          `attack:${techniqueAnchor.id}:${detectionId}`,
          techniqueAnchor.id,
          detectionId,
          "OBSERVED BY",
          detectionObserved,
        ),
      );
      detectionAnchors.push({ id: detectionId, observed: detectionObserved });
    }
    containmentAnchors.push(...detectionAnchors);
    anchors = detectionAnchors.length
      ? detectionAnchors
      : techniqueAnchors.length
        ? techniqueAnchors
        : [{ id: playbookId, observed: taskCompleted }];
    layer += 4;
  });

  const containmentActions = Array.isArray(scenario.containment_actions)
    ? scenario.containment_actions
    : [];
  if (containmentActions.length) {
    const containmentApplied = exercise?.containment?.status === "applied";
    const containmentId = `attack-containment:${focusValue}`;
    entities.push(
      graphEntity(
        containmentId,
        "containment",
        containmentApplied ? "Containment applied" : "Containment options",
        containmentApplied
          ? exercise.containment.action || "APPLIED"
          : containmentActions.join(" · "),
        containmentApplied ? "contained" : "planned",
        layer,
        [
          ["State", containmentApplied ? "applied" : "not started"],
          ["Action", exercise?.containment?.action || "—"],
          ["Allowed", containmentActions.join(" · ")],
          ["Actor", exercise?.containment?.actor || "—"],
        ],
      ),
    );
    const containmentSources = containmentAnchors.length ? containmentAnchors : anchors;
    for (const anchor of containmentSources) {
      edges.push(
        graphRelation(
          `attack:${anchor.id}:${containmentId}`,
          anchor.id,
          containmentId,
          "CONTAINED BY",
          containmentApplied && anchor.observed,
        ),
      );
    }
  }

  const observedCount = edges.filter((edge) => edge.observed).length;
  return {
    mode: "attack",
    entities,
    edges,
    summary: `${scenario.id} · ${exercise ? `Exercise ${exercise.id}` : "catalog preview"} · ${observedCount} OBSERVED / ${edges.length - observedCount} PLANNED`,
  };
}

function renderGraphFocusOptions(overview) {
  const view = elements.graphViewSelect.value;
  const nodes = (Array.isArray(overview.nodes) ? overview.nodes : []).slice(0, MAX_GRAPH_NODES);
  const exercises = (Array.isArray(overview.exercises) ? overview.exercises : [])
    .slice(0, MAX_GRAPH_EXERCISES);
  const focusKey = JSON.stringify({
    view,
    nodes: nodes.map((node) => [node.id, node.name, node.status]),
    exercises: exercises.map((exercise) => [exercise.id, exercise.scenario_id, exercise.status]),
    scenarios: currentScenarioCatalog.map((scenario) => [scenario.id, scenario.title]),
  });
  if (focusKey === renderedGraphFocusKey) return;
  renderedGraphFocusKey = focusKey;
  const previousValue = elements.graphFocusSelect.value;
  elements.graphFocusSelect.replaceChildren();

  if (view === "topology") {
    elements.graphFocusSelect.add(new Option("すべてのNode", "all"));
    for (const node of nodes) {
      elements.graphFocusSelect.add(
        new Option(`${node.name || node.id} · ${String(node.status || "unknown").toUpperCase()}`, `node:${node.id}`),
      );
    }
  } else {
    if (exercises.length) {
      const group = document.createElement("optgroup");
      group.label = "Retained exercises";
      for (const exercise of exercises) {
        group.append(
          new Option(
            `${exercise.scenario_id} · ${exercise.id} · ${String(exercise.status || "unknown").toUpperCase()}`,
            `exercise:${exercise.id}`,
          ),
        );
      }
      elements.graphFocusSelect.append(group);
    }
    const catalogGroup = document.createElement("optgroup");
    catalogGroup.label = "Planned scenario preview";
    for (const scenario of currentScenarioCatalog) {
      catalogGroup.append(new Option(`${scenario.id} · PLANNED`, `scenario:${scenario.id}`));
    }
    elements.graphFocusSelect.append(catalogGroup);
  }

  const values = Array.from(elements.graphFocusSelect.options).map((option) => option.value);
  const defaultScenario = EXERCISE_SCENARIO_IDS.has(elements.exerciseScenarioSelect.value)
    ? `scenario:${elements.exerciseScenarioSelect.value}`
    : `scenario:${currentScenarioCatalog[0]?.id || ""}`;
  const nextValue = values.includes(previousValue)
    ? previousValue
    : view === "topology"
      ? "all"
      : exercises.length
        ? `exercise:${exercises[0].id}`
        : defaultScenario;
  elements.graphFocusSelect.value = nextValue;
  if (nextValue !== previousValue) graphSelectedEntityId = "";
}

function layoutGraphModel(model) {
  const layers = new Map();
  for (const entity of model.entities) {
    const layer = Number.isInteger(entity.layer) ? entity.layer : 0;
    if (!layers.has(layer)) layers.set(layer, []);
    layers.get(layer).push(entity);
  }
  const positioned = [];
  const maxRows = model.mode === "topology" ? 12 : 8;
  let nextColumn = 0;
  let tallestColumn = 1;
  for (const layer of Array.from(layers.keys()).sort((left, right) => left - right)) {
    const entities = layers.get(layer);
    const chunks = Math.max(1, Math.ceil(entities.length / maxRows));
    entities.forEach((entity, index) => {
      const row = index % maxRows;
      const column = nextColumn + Math.floor(index / maxRows);
      tallestColumn = Math.max(tallestColumn, row + 1);
      positioned.push({ ...entity, x: 36 + column * 280, y: 36 + row * 102, width: 232, height: 74 });
    });
    nextColumn += chunks;
  }
  return {
    entities: positioned,
    edges: model.edges,
    width: Math.max(760, nextColumn * 280 + 24),
    height: Math.max(360, tallestColumn * 102 + 28),
  };
}

function createSvgElement(tag, className = "") {
  const element = document.createElementNS(SVG_NS, tag);
  if (className) element.setAttribute("class", className);
  return element;
}

function graphCurve(from, to) {
  const startX = from.x + from.width;
  const startY = from.y + from.height / 2;
  const endX = to.x;
  const endY = to.y + to.height / 2;
  const bend = Math.max(50, Math.abs(endX - startX) / 2);
  return `M ${startX} ${startY} C ${startX + bend} ${startY}, ${endX - bend} ${endY}, ${endX} ${endY}`;
}

function graphActionHint(entity) {
  if (entity.action?.kind === "node") return "選択するとtask composerへ移動します。";
  if (entity.action?.kind === "task") return "選択するとtask detailを開きます。";
  if (entity.action?.kind === "playbook") return "選択するとRUN_PLAYBOOK composerへ設定します。";
  return "読み取り専用metadataです。";
}

function renderGraphInspector(model) {
  const entity = model.entities.find((candidate) => candidate.id === graphSelectedEntityId);
  elements.graphInspector.replaceChildren();
  elements.graphInspector.dataset.kind = entity?.kind || "empty";
  elements.graphInspector.append(makeTextElement("span", "graph-inspector__eyebrow", "GRAPH INSPECTOR"));
  if (!entity) {
    elements.graphInspector.append(
      makeTextElement("h3", "", "Entityを選択"),
      makeTextElement("p", "", "SVGまたは下の関係リストから選択すると、安全な同期済みmetadataを表示します。"),
    );
    return;
  }
  const heading = document.createElement("div");
  heading.className = "graph-inspector__heading";
  const copy = document.createElement("div");
  copy.append(
    makeTextElement("h3", "", entity.label),
    makeTextElement("p", "", entity.subtitle || entity.kind.toUpperCase()),
  );
  const badge = makeTextElement("span", "graph-inspector__status", entity.status.toUpperCase());
  badge.dataset.status = entity.status;
  heading.append(copy, badge);
  const details = document.createElement("dl");
  details.className = "graph-inspector__details";
  for (const [label, value] of entity.details.slice(0, 12)) {
    const row = document.createElement("div");
    row.append(
      makeTextElement("dt", "", label),
      makeTextElement("dd", "", value === null || value === undefined || value === "" ? "—" : value),
    );
    details.append(row);
  }
  elements.graphInspector.append(
    heading,
    details,
    makeTextElement("p", "graph-inspector__action-hint", graphActionHint(entity)),
  );
}

function primeGraphPlaybook(playbook, nodeId) {
  if (!GRAPH_PLAYBOOK_IDS.has(playbook) || !nodeId) {
    showToast("ONLINEなpurple_lab Nodeを選択してからplaybookを設定してください。", "error");
    return;
  }
  const nodeOption = Array.from(elements.taskNodeSelect.options).find(
    (option) => option.value === nodeId && !option.disabled,
  );
  if (!nodeOption) {
    showToast("このNodeはtask composerの送信先に選択できません。", "error");
    return;
  }
  elements.taskNodeSelect.value = nodeId;
  updateTaskCapabilities();
  const playbookTaskOption = Array.from(elements.taskTypeSelect.options).find(
    (option) => option.value === "RUN_PLAYBOOK" && !option.disabled,
  );
  if (!playbookTaskOption) {
    showToast("選択したNodeのprofileではRUN_PLAYBOOKを利用できません。", "error");
    return;
  }
  elements.taskTypeSelect.value = "RUN_PLAYBOOK";
  applyTaskTemplate();
  elements.playbookSelect.value = playbook;
  syncTaskPayloadPreview();
  updateControls();
  scrollToSection("dispatch");
  elements.playbookSelect.focus({ preventScroll: true });
}

function graphEntityActionCanHandoff(entity) {
  const action = entity?.action;
  if (!action) return false;
  if (action.kind === "node") {
    return Array.from(elements.taskNodeSelect.options).some(
      (option) => option.value === action.nodeId && !option.disabled,
    );
  }
  if (action.kind === "task") {
    return Boolean(
      latestOverview?.tasks?.some((candidate) => candidate.id === action.taskId),
    );
  }
  if (action.kind === "playbook") {
    if (!GRAPH_PLAYBOOK_IDS.has(action.playbook) || !action.nodeId) return false;
    const nodeOption = Array.from(elements.taskNodeSelect.options).find(
      (option) => option.value === action.nodeId && !option.disabled,
    );
    const node = latestOverview?.nodes?.find((candidate) => candidate.id === action.nodeId);
    return Boolean(
      nodeOption &&
      node?.session_active !== false &&
      node?.tasking_paused !== true &&
      Array.isArray(node?.capabilities) &&
      node.capabilities.includes("RUN_PLAYBOOK"),
    );
  }
  return false;
}

function performGraphEntityAction(entity) {
  if (!entity?.action) return;
  if (entity.action.kind === "node") {
    selectNodeForTask(entity.action.nodeId);
    return;
  }
  if (entity.action.kind === "task") {
    const task = latestOverview?.tasks?.find((candidate) => candidate.id === entity.action.taskId);
    if (!task) return;
    const node = latestOverview.nodes.find((candidate) => candidate.id === task.node_id);
    showTaskDetail(task, node?.name || task.node_id);
    return;
  }
  if (entity.action.kind === "playbook") {
    primeGraphPlaybook(entity.action.playbook, entity.action.nodeId);
  }
}

function activateGraphEntity(entityId) {
  const entity = currentGraphModel?.entities.find((candidate) => candidate.id === entityId);
  if (!entity) return;
  graphSelectedEntityId = entityId;
  renderedGraphKey = "";
  if (latestOverview) {
    renderGraph(latestOverview, {
      restorePathFocus: !graphEntityActionCanHandoff(entity),
    });
  }
  performGraphEntityAction(entity);
}

function renderGraphSvg(model, layout) {
  const title = createSvgElement("title");
  title.id = "graphSvgTitle";
  title.textContent = model.mode === "attack"
    ? "Synthetic Attack Path"
    : "C2 topology graph";
  const description = createSvgElement("desc");
  description.id = "graphSvgDescription";
  description.textContent = `${model.summary}。固定LAB同期データの読み取り専用projectionです。`;
  const definitions = createSvgElement("defs");
  const marker = createSvgElement("marker");
  marker.id = "graphArrow";
  marker.setAttribute("viewBox", "0 0 10 10");
  marker.setAttribute("refX", "9");
  marker.setAttribute("refY", "5");
  marker.setAttribute("markerWidth", "6");
  marker.setAttribute("markerHeight", "6");
  marker.setAttribute("orient", "auto-start-reverse");
  const arrow = createSvgElement("path");
  arrow.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
  marker.append(arrow);
  definitions.append(marker);
  const edgeLayer = createSvgElement("g", "graph-svg__edges");
  const entityLayer = createSvgElement("g", "graph-svg__entities");
  const positions = new Map(layout.entities.map((entity) => [entity.id, entity]));

  for (const edge of layout.edges) {
    const from = positions.get(edge.from);
    const to = positions.get(edge.to);
    if (!from || !to) continue;
    const group = createSvgElement("g", "graph-edge");
    group.dataset.observed = String(edge.observed);
    const path = createSvgElement("path", "graph-edge__path");
    path.setAttribute("d", graphCurve(from, to));
    path.setAttribute("marker-end", "url(#graphArrow)");
    const label = createSvgElement("text", "graph-edge__label");
    label.setAttribute("x", String((from.x + from.width + to.x) / 2));
    label.setAttribute("y", String((from.y + from.height / 2 + to.y + to.height / 2) / 2 - 6));
    label.textContent = edge.label;
    group.append(path, label);
    edgeLayer.append(group);
  }

  for (const entity of layout.entities) {
    const group = createSvgElement("g", "graph-entity");
    group.dataset.graphEntityId = entity.id;
    group.dataset.kind = entity.kind;
    group.dataset.status = entity.status;
    group.classList.toggle("is-selected", entity.id === graphSelectedEntityId);
    group.setAttribute("transform", `translate(${entity.x} ${entity.y})`);
    const box = createSvgElement("rect", "graph-entity__box");
    box.setAttribute("width", String(entity.width));
    box.setAttribute("height", String(entity.height));
    box.setAttribute("rx", "10");
    const kind = createSvgElement("text", "graph-entity__kind");
    kind.setAttribute("x", "14");
    kind.setAttribute("y", "20");
    kind.textContent = entity.kind.toUpperCase();
    const label = createSvgElement("text", "graph-entity__label");
    label.setAttribute("x", "14");
    label.setAttribute("y", "43");
    label.textContent = entity.label.length > 27 ? `${entity.label.slice(0, 26)}…` : entity.label;
    const subtitle = createSvgElement("text", "graph-entity__subtitle");
    subtitle.setAttribute("x", "14");
    subtitle.setAttribute("y", "62");
    subtitle.textContent = entity.subtitle.length > 34
      ? `${entity.subtitle.slice(0, 33)}…`
      : entity.subtitle;
    group.append(box, kind, label, subtitle);
    group.addEventListener("click", () => activateGraphEntity(entity.id));
    entityLayer.append(group);
  }

  elements.graphSvg.replaceChildren(title, description, definitions, edgeLayer, entityLayer);
  elements.graphSvg.setAttribute("viewBox", `0 0 ${layout.width} ${layout.height}`);
}

function renderGraphPathList(model) {
  elements.graphPathList.replaceChildren();
  const entities = new Map(model.entities.map((entity) => [entity.id, entity]));
  const destinationIds = new Set(model.edges.map((edge) => edge.to));
  for (const entity of model.entities.filter((candidate) => !destinationIds.has(candidate.id))) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "graph-path-button";
    button.dataset.observed = String(entity.status !== "planned");
    button.dataset.graphEntityId = entity.id;
    button.dataset.graphEdgeId = `entity:${entity.id}`;
    button.setAttribute("aria-label", `${entity.label}、グラフの開始Entity`);
    const relation = document.createElement("span");
    relation.className = "graph-path-button__relation";
    relation.append(
      makeTextElement("strong", "", entity.label),
      makeTextElement("i", "", "START ENTITY"),
      makeTextElement("strong", "", entity.subtitle || entity.kind),
    );
    button.append(
      relation,
      makeTextElement("span", "graph-path-button__state", "DETAIL"),
    );
    button.addEventListener("click", () => activateGraphEntity(entity.id));
    item.append(button);
    elements.graphPathList.append(item);
  }
  if (!model.edges.length) {
    if (!model.entities.length) {
      elements.graphPathList.append(
        makeTextElement("li", "graph-path-list__empty", "表示できる関係を待機中です。"),
      );
    }
    return;
  }
  for (const edge of model.edges) {
    const from = entities.get(edge.from);
    const to = entities.get(edge.to);
    if (!from || !to) continue;
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "graph-path-button";
    button.dataset.observed = String(edge.observed);
    button.dataset.graphEntityId = to.id;
    button.dataset.graphEdgeId = edge.id;
    button.setAttribute(
      "aria-label",
      `${from.label}から${to.label}、関係${edge.label}、${edge.observed ? "observed" : "planned"}`,
    );
    const relation = document.createElement("span");
    relation.className = "graph-path-button__relation";
    relation.append(
      makeTextElement("strong", "", from.label),
      makeTextElement("i", "", edge.label),
      makeTextElement("strong", "", to.label),
    );
    button.append(
      relation,
      makeTextElement(
        "span",
        "graph-path-button__state",
        edge.observed ? "OBSERVED" : "PLANNED",
      ),
    );
    button.addEventListener("click", () => activateGraphEntity(to.id));
    item.append(button);
    elements.graphPathList.append(item);
  }
}

function applyGraphZoom() {
  graphZoomPercent = Math.min(
    GRAPH_ZOOM_MAX,
    Math.max(GRAPH_ZOOM_MIN, Math.round(graphZoomPercent / GRAPH_ZOOM_STEP) * GRAPH_ZOOM_STEP),
  );
  elements.graphZoomInput.value = String(graphZoomPercent);
  elements.graphZoomOutput.value = `${graphZoomPercent}%`;
  elements.graphZoomOutput.textContent = `${graphZoomPercent}%`;
  const scale = graphZoomPercent / 100;
  elements.graphSvg.style.width = `${Math.round(currentGraphDimensions.width * scale)}px`;
  elements.graphSvg.style.height = `${Math.round(currentGraphDimensions.height * scale)}px`;
}

function fitGraphToViewport() {
  if (!currentGraphDimensions.width) return;
  const availableWidth = Math.max(320, elements.graphViewport.clientWidth - 24);
  const availableHeight = Math.max(240, elements.graphViewport.clientHeight - 24);
  graphZoomPercent = Math.floor(
    Math.min(
      100,
      (availableWidth / currentGraphDimensions.width) * 100,
      (availableHeight / currentGraphDimensions.height) * 100,
    ) / GRAPH_ZOOM_STEP,
  ) * GRAPH_ZOOM_STEP;
  applyGraphZoom();
  elements.graphViewport.scrollTo({ top: 0, left: 0, behavior: "smooth" });
}

function renderGraph(overview, { restorePathFocus = true } = {}) {
  renderGraphFocusOptions(overview);
  const model = elements.graphViewSelect.value === "attack"
    ? buildAttackGraph(overview, elements.graphFocusSelect.value)
    : buildTopologyGraph(overview, elements.graphFocusSelect.value);
  currentGraphModel = model;
  if (!model.entities.some((entity) => entity.id === graphSelectedEntityId)) {
    graphSelectedEntityId = "";
  }
  const renderKey = JSON.stringify({ model, selected: graphSelectedEntityId });
  if (renderKey === renderedGraphKey) {
    applyGraphZoom();
    updateControls();
    return;
  }
  renderedGraphKey = renderKey;
  const activePathEdge = restorePathFocus && elements.graphPathList.contains(document.activeElement)
    ? document.activeElement.dataset.graphEdgeId || ""
    : "";
  const layout = layoutGraphModel(model);
  currentGraphDimensions = { width: layout.width, height: layout.height };
  const hasRelations = model.edges.length > 0;
  elements.graphEntityCountBadge.textContent = `${model.entities.length} ENTITIES`;
  elements.graphStatus.textContent = model.summary;
  elements.graphEmpty.classList.toggle("is-visible", !hasRelations);
  elements.graphSvg.hidden = !hasRelations;
  renderGraphSvg(model, layout);
  renderGraphPathList(model);
  renderGraphInspector(model);
  applyGraphZoom();
  if (activePathEdge) {
    window.queueMicrotask(() => {
      const target = Array.from(elements.graphPathList.querySelectorAll("button")).find(
        (button) => button.dataset.graphEdgeId === activePathEdge,
      );
      target?.focus({ preventScroll: true });
    });
  }
  updateControls();
}

function operationNodeEligible(node) {
  return Boolean(
    node &&
    node.profile === "purple_lab" &&
    node.status === "online" &&
    node.session_active !== false &&
    node.tasking_paused !== true &&
    Array.isArray(node.capabilities) &&
    node.capabilities.includes("RUN_PLAYBOOK"),
  );
}

function eligibleOperationNodes(nodes) {
  return (Array.isArray(nodes) ? nodes : []).filter(operationNodeEligible);
}

function selectedOperationNode() {
  const nodes = Array.isArray(latestOverview?.nodes) ? latestOverview.nodes : [];
  return nodes.find((node) => node.id === elements.operationNodeSelect.value) || null;
}

function preservedOperationNodeId(eligible, previousNodeId) {
  return eligible.some((node) => node.id === previousNodeId) ? previousNodeId : "";
}

function renderOperationNodes(nodes) {
  const eligible = eligibleOperationNodes(nodes);
  const nodeKey = JSON.stringify(
    eligible.map((node) => [node.id, node.name, node.status, node.session_active, node.tasking_paused]),
  );
  if (nodeKey === renderedOperationNodeKey) return;
  renderedOperationNodeKey = nodeKey;
  const previousNodeId = elements.operationNodeSelect.value;
  elements.operationNodeSelect.replaceChildren(
    new Option("ONLINEなpurple_lab Nodeを選択", ""),
  );
  for (const node of eligible) {
    elements.operationNodeSelect.add(
      new Option(`${node.name || node.id} · ${node.id}`, node.id),
    );
  }
  const selectedId = preservedOperationNodeId(eligible, previousNodeId);
  elements.operationNodeSelect.value = selectedId;
  syncOperationPreview();
}

function operationRequestBody() {
  return {
    node_id: elements.operationNodeSelect.value,
    steps: operationSteps.map((playbook) => ({ playbook })),
    queue_ttl_seconds: Number(elements.operationTtlSelect.value),
  };
}

function operationComposerIsValid() {
  const node = selectedOperationNode();
  const ttl = Number(elements.operationTtlSelect.value);
  return (
    operationNodeEligible(node) &&
    Number.isInteger(ttl) &&
    [30, 300, 1800].includes(ttl) &&
    operationSteps.length >= 1 &&
    operationSteps.length <= MAX_OPERATION_STEPS &&
    operationSteps.every((playbook) => GRAPH_PLAYBOOK_IDS.has(playbook))
  );
}

function syncOperationPreview() {
  elements.operationPreviewInput.value = JSON.stringify(operationRequestBody(), null, 2);
  setText(
    elements.operationStepCount,
    `${operationSteps.length} / ${MAX_OPERATION_STEPS} STEPS`,
  );
  elements.operationError.textContent = "";
}

function moveOperationStep(fromIndex, toIndex) {
  if (
    !Number.isInteger(fromIndex) ||
    !Number.isInteger(toIndex) ||
    fromIndex < 0 ||
    toIndex < 0 ||
    fromIndex >= operationSteps.length ||
    toIndex >= operationSteps.length
  ) return;
  const next = [...operationSteps];
  const [step] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, step);
  operationSteps = next;
  renderOperationSteps({ focusIndex: toIndex });
}

function renderOperationSteps({ focusIndex = -1 } = {}) {
  elements.operationStepList.replaceChildren();
  operationSteps.forEach((playbook, index) => {
    const item = document.createElement("li");
    item.className = "operation-step";
    item.dataset.operationStepIndex = String(index);
    const sequence = makeTextElement("span", "operation-step__sequence", String(index + 1));
    sequence.setAttribute("aria-hidden", "true");

    const label = document.createElement("label");
    const selectId = `operationStepSelect${index}`;
    label.className = "field operation-step__field";
    label.htmlFor = selectId;
    label.append(makeTextElement("span", "visually-hidden", `Step ${index + 1} playbook`));
    const select = document.createElement("select");
    select.id = selectId;
    select.dataset.operationStepSelect = String(index);
    for (const candidate of OPERATION_PLAYBOOKS) select.add(new Option(candidate, candidate));
    select.value = playbook;
    select.addEventListener("change", () => {
      if (!GRAPH_PLAYBOOK_IDS.has(select.value)) return;
      operationSteps[index] = select.value;
      syncOperationPreview();
      updateControls();
    });
    label.append(select);

    const actions = document.createElement("div");
    actions.className = "operation-step__actions";
    const up = makeTextElement("button", "icon-button", "↑");
    up.type = "button";
    up.dataset.operationMove = "up";
    up.setAttribute("aria-label", `Step ${index + 1}を上へ移動`);
    up.disabled = index === 0;
    up.addEventListener("click", () => moveOperationStep(index, index - 1));
    const down = makeTextElement("button", "icon-button", "↓");
    down.type = "button";
    down.dataset.operationMove = "down";
    down.setAttribute("aria-label", `Step ${index + 1}を下へ移動`);
    down.disabled = index === operationSteps.length - 1;
    down.addEventListener("click", () => moveOperationStep(index, index + 1));
    const remove = makeTextElement("button", "button button--danger-ghost button--compact", "削除");
    remove.type = "button";
    remove.dataset.operationRemove = String(index);
    remove.setAttribute("aria-label", `Step ${index + 1}を削除`);
    remove.disabled = operationSteps.length === 1;
    remove.addEventListener("click", () => {
      if (operationSteps.length === 1) return;
      operationSteps = operationSteps.filter((_candidate, candidateIndex) => candidateIndex !== index);
      renderOperationSteps({ focusIndex: Math.min(index, operationSteps.length - 1) });
    });
    actions.append(up, down, remove);
    item.append(sequence, label, actions);
    elements.operationStepList.append(item);
  });
  syncOperationPreview();
  updateControls();
  if (focusIndex >= 0) {
    window.queueMicrotask(() => {
      elements.operationStepList.querySelector(
        `[data-operation-step-select="${focusIndex}"]`,
      )?.focus({ preventScroll: true });
    });
  }
}

function focusedAttackScenario() {
  if (elements.graphViewSelect.value !== "attack") return null;
  const focus = elements.graphFocusSelect.value;
  if (focus.startsWith("scenario:")) {
    return currentScenarioCatalog.find((scenario) => scenario.id === focus.slice(9)) || null;
  }
  if (focus.startsWith("exercise:")) {
    const exercise = latestOverview?.exercises?.find(
      (candidate) => candidate.id === focus.slice(9),
    );
    return currentScenarioCatalog.find(
      (scenario) => scenario.id === exercise?.scenario_id,
    ) || null;
  }
  return null;
}

function loadFocusedAttackPathIntoOperation() {
  const scenario = focusedAttackScenario();
  const playbooks = (scenario?.playbooks || [])
    .filter((playbook) => GRAPH_PLAYBOOK_IDS.has(playbook))
    .slice(0, MAX_OPERATION_STEPS);
  if (!playbooks.length) {
    showToast("Synthetic Attack Pathを表示してから読み込んでください。", "error");
    return;
  }
  operationSteps = playbooks;
  const focus = elements.graphFocusSelect.value;
  const exercise = focus.startsWith("exercise:")
    ? latestOverview?.exercises?.find((candidate) => candidate.id === focus.slice(9))
    : null;
  const preferredNodeId = exercise?.node_id || elements.exerciseNodeSelect.value;
  if (eligibleOperationNodes(latestOverview?.nodes).some((node) => node.id === preferredNodeId)) {
    elements.operationNodeSelect.value = preferredNodeId;
  }
  renderOperationSteps();
  elements.operationBuilder.scrollIntoView({ behavior: "smooth", block: "start" });
  elements.operationStepList.querySelector("select")?.focus({ preventScroll: true });
  showToast(`${scenario.id} の ${playbooks.length} stepをOperationへ読み込みました。`, "success");
}

function setMetric(element, value) {
  setText(element, Number.isFinite(value) ? String(value) : "—");
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
  if (node.tasking_paused === true) {
    badges.append(makeTextElement("span", "session-closed-badge", "TASKING PAUSED"));
  }
  header.append(statusLight, identity, badges);

  const meta = document.createElement("div");
  meta.className = "node-meta";
  meta.append(
    createMetaItem("VERSION", node.version),
    createMetaItem("CAPABILITY PROFILE", node.profile),
    createMetaItem("POLL", Number.isFinite(node.poll_interval_ms)
      ? `${node.poll_interval_ms} ms${node.jitter_percent ? ` ±${node.jitter_percent}%` : ""}`
      : "—"),
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
  selectButton.disabled = node.session_active === false || node.tasking_paused === true;
  selectButton.setAttribute("aria-label", `${node.name || "Node"} を選択して固定タスクを作成`);
  if (selectButton.disabled) {
    selectButton.title = node.tasking_paused
      ? "封じ込めによりNode taskingが一時停止中です"
      : "このNodeセッションは終了しています";
  }
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
      node.session_active === false
        ? `${label} · SESSION CLOSED`
        : node.tasking_paused
          ? `${label} · TASKING PAUSED`
          : label,
      node.id,
    );
    option.disabled = node.session_active === false || node.tasking_paused === true;
    elements.taskNodeSelect.add(option);
  }

  if (nodes.some(
    (node) =>
      node.id === previousNodeId &&
      node.session_active !== false &&
      node.tasking_paused !== true,
  )) {
    elements.taskNodeSelect.value = previousNodeId;
  } else if (nodes.some(
    (node) => node.session_active !== false && node.tasking_paused !== true,
  )) {
    const preferred =
      nodes.find(
        (node) =>
          node.status === "online" &&
          node.session_active !== false &&
          node.tasking_paused !== true,
      ) ||
      nodes.find(
        (node) => node.session_active !== false && node.tasking_paused !== true,
      );
    elements.taskNodeSelect.value = preferred.id;
  }
  const selectedNodeChanged = elements.taskNodeSelect.value !== previousNodeId;
  updateTaskCapabilities();
  if (
    selectedNodeChanged &&
    ["SLEEP", "EXIT"].includes(elements.taskTypeSelect.value)
  ) {
    applyTaskTemplate();
  }
}

function selectedNode() {
  const nodes = Array.isArray(latestOverview?.nodes) ? latestOverview.nodes : [];
  return nodes.find((node) => node.id === elements.taskNodeSelect.value) || null;
}

function updateSelectedNodeSummary(node) {
  elements.selectedNodeSummary.dataset.state = node ? "selected" : "empty";
  setText(elements.selectedNodeName, node?.name || "Node 未選択");
  setText(
    elements.selectedNodeStatus,
    node ? String(node.status || "unknown").toUpperCase() : "—",
  );
  elements.selectedNodeStatus.dataset.status = node?.status || "unknown";
  setText(elements.selectedNodeProfile, node?.profile || "—");
  setText(
    elements.selectedNodeSession,
    node
      ? node.session_active === false
        ? "CLOSED"
        : node.tasking_paused
          ? "TASKING PAUSED"
          : "ACTIVE"
      : "—",
  );
  const capabilities = Array.isArray(node?.capabilities) ? node.capabilities : [];
  setText(
    elements.selectedNodeCapabilities,
    node
      ? `固定タスク: ${capabilities.length ? capabilities.join(" · ") : "なし"}`
      : "登録済みNodeを選択すると、実行可能な固定タスクを確認できます。",
  );

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
  if (["SLEEP", "EXIT"].includes(elements.taskTypeSelect.value)) applyTaskTemplate();
  scrollToSection("dispatch");
  elements.taskNodeSelect.focus({ preventScroll: true });
}

function updateTaskCapabilities() {
  const node = selectedNode();
  const capabilities = new Set(Array.isArray(node?.capabilities) ? node.capabilities : []);
  const sessionActive =
    Boolean(node) && node.session_active !== false && node.tasking_paused !== true;
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

function createTableCell(className, text, label) {
  const cell = document.createElement("td");
  if (label) cell.dataset.label = label;
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
      task.operation_id,
      task.operation_step,
      task.type,
      task.status,
      task.node_id,
      nodeNames.get(task.node_id),
      task.created_by,
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
    const correlationDisplay = task.operation_id
      ? `${task.operation_id} · STEP ${task.operation_step || "?"}`
      : task.correlation_id || "—";
    row.append(
      createTableCell("task-id", task.id || "—", "TASK"),
      createTableCell("correlation-id", correlationDisplay, "CORRELATION / OPERATION"),
      createTableCell("task-type", task.type || "—", "TYPE"),
      createTableCell("task-node", nodeNames.get(task.node_id) || task.node_id || "—", "NODE"),
    );

    const statusCell = document.createElement("td");
    statusCell.dataset.label = "STATUS";
    const statusName = String(task.status || "unknown");
    const status = makeTextElement("span", "status-badge", statusName.toUpperCase());
    status.dataset.status = statusName;
    statusCell.append(status);
    row.append(
      statusCell,
      createTableCell("task-actor", task.created_by || "—", "OPERATOR"),
      createTableCell("task-time", localTime(task.created_at), "CREATED"),
    );

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

function eventDescription(event) {
  if (event.kind === "operator.note" && typeof event.data?.message === "string") {
    return event.data.message;
  }
  return compactJson(event.data);
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
  const historyKey = JSON.stringify({
    auditView,
    query,
    level,
    actor,
    historyRowLimit,
    events,
  });
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
      eventDescription(event),
    ]
      .map(normalizedSearch)
      .join(" ");
    return searchable.includes(query);
  });
  const recent = matches.slice(0, historyRowLimit);
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
      makeTextElement("p", "", eventDescription(event)),
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

function renderOverview(overview, eventHistory, auditHistory) {
  const nodes = overview.nodes;
  const tasks = overview.tasks;
  const exercises = overview.exercises;
  const events = [...eventHistory].reverse();
  const auditEntries = [...auditHistory].reverse();
  renderExerciseCatalog(overview.scenario_catalog);
  latestOverview = {
    ...overview,
    connection_status: "online",
    nodes,
    tasks,
    exercises,
    scenario_catalog: currentScenarioCatalog,
    events,
    audit: auditEntries,
  };

  elements.labModeValue.textContent = "LOCALHOST LAB";
  elements.protocolValue.textContent = overview.protocol || "unknown";
  renderMetrics(overview.counts || {});
  renderNodes(nodes);
  renderExerciseNodes(nodes);
  renderOperationNodes(nodes);
  renderTasks(tasks, nodes);
  refreshOpenTaskDetail(tasks, nodes);
  renderExercises(exercises, nodes, currentScenarioCatalog);
  renderGraph(latestOverview);
  renderHistory();
  elements.lastUpdated.textContent = `最終更新 ${updateTimeFormatter.format(new Date())}`;
  setConnectedLayout(true);
}

function clearOverview() {
  closeTaskDetail();
  setConnectedLayout(false);
  clearOperatorSession();
  resetSyncState();
  latestOverview = null;
  elements.labModeValue.textContent = "LOCALHOST LAB";
  elements.protocolValue.textContent = "loopback-http-poll/v1";
  renderMetrics();
  renderNodes([]);
  renderExerciseCatalog([]);
  renderExerciseNodes([]);
  renderedOperationNodeKey = "";
  renderOperationNodes([]);
  renderTasks([], []);
  renderExercises([], [], currentScenarioCatalog);
  renderedGraphFocusKey = "";
  renderedGraphKey = "";
  graphSelectedEntityId = "";
  renderGraph({
    connection_status: "offline",
    protocol: "loopback-http-poll/v1",
    nodes: [],
    tasks: [],
    exercises: [],
  });
  renderEvents([], { auditView: elements.activitySourceFilter.value === "audit" });
  elements.lastUpdated.textContent = "未取得";
  updateControls();
}

function updateControls() {
  const hasToken = Boolean(operatorToken);
  const canWriteTasks = hasPermission("task_write");
  const canWriteNotes = hasPermission("note_write");
  const canWriteExercises = hasPermission("exercise_write");
  const canContainExercises =
    currentRole === "admin" && hasPermission("containment_write");
  const canReset = hasPermission("reset");
  const noteBusy = elements.noteSubmitButton.dataset.busy === "true";
  const noteMessage = elements.noteInput.value.trim();
  const noteLength = unicodeLength(noteMessage);
  const exerciseBusy = elements.createExerciseButton.dataset.busy === "true";
  const operationBusy = elements.operationSubmitButton.dataset.busy === "true";
  const operationNode = selectedOperationNode();
  const operationValid = operationComposerIsValid();
  const exerciseNode = selectedExerciseNode();
  const exerciseScenarioAllowed = EXERCISE_SCENARIO_IDS.has(
    elements.exerciseScenarioSelect.value,
  );
  const exerciseNodeAllowed =
    exerciseNode?.profile === "purple_lab" &&
    exerciseNode.session_active !== false &&
    exerciseNode.tasking_paused !== true &&
    exerciseNode.status === "online";
  const node = selectedNode();
  const selectedType = elements.taskTypeSelect.value;
  const typeAllowed =
    Boolean(node) &&
    node.session_active !== false &&
    node.tasking_paused !== true &&
    Array.isArray(node.capabilities) &&
    node.capabilities.includes(selectedType);
  const composerValid = taskComposerIsValid();
  elements.refreshButton.disabled =
    !hasToken || refreshInFlight || elements.refreshButton.dataset.busy === "true";
  elements.connectButton.disabled = elements.connectButton.dataset.busy === "true";
  elements.resetButton.disabled =
    !hasToken || !canReset || elements.resetButton.dataset.busy === "true";
  elements.createTaskButton.disabled =
    !hasToken ||
    !canWriteTasks ||
    !node ||
    !typeAllowed ||
    !composerValid ||
    elements.createTaskButton.dataset.busy === "true";
  elements.noteInput.disabled = !hasToken || !canWriteNotes || noteBusy;
  elements.noteSubmitButton.disabled =
    !hasToken ||
    !canWriteNotes ||
    noteBusy ||
    !noteMessage ||
    noteLength > MAX_NOTE_LENGTH;
  elements.createExerciseButton.disabled =
    !hasToken ||
    !canWriteExercises ||
    !exerciseNodeAllowed ||
    !exerciseScenarioAllowed ||
    exerciseBusy;
  elements.operationSubmitButton.disabled =
    !hasToken ||
    !canWriteTasks ||
    operationBusy ||
    !operationValid;
  elements.operationNodeSelect.disabled = operationBusy;
  elements.operationTtlSelect.disabled = operationBusy;
  elements.operationAddStepButton.disabled =
    operationBusy || operationSteps.length >= MAX_OPERATION_STEPS;
  elements.operationPlaybookSelect.disabled =
    operationBusy || operationSteps.length >= MAX_OPERATION_STEPS;
  elements.operationLoadPathButton.disabled = operationBusy || !focusedAttackScenario();
  for (const control of elements.operationStepList.querySelectorAll("select, button")) {
    if (operationBusy) {
      control.disabled = true;
    } else if (control.dataset.operationMove === "up") {
      control.disabled = Number(control.closest("li")?.dataset.operationStepIndex) === 0;
    } else if (control.dataset.operationMove === "down") {
      control.disabled =
        Number(control.closest("li")?.dataset.operationStepIndex) === operationSteps.length - 1;
    } else if (control.dataset.operationRemove !== undefined) {
      control.disabled = operationSteps.length === 1;
    }
  }
  elements.operationForm.dataset.permission = canWriteTasks ? "allowed" : "denied";
  if (!hasToken) {
    elements.operationHint.textContent =
      "接続後、task_write権限を持つOperatorだけがOperationを登録できます。";
  } else if (!canWriteTasks) {
    elements.operationHint.textContent =
      `${currentRole.toUpperCase()} roleはOperationを編集できますが、登録にはtask_write権限が必要です。`;
  } else if (!operationNodeEligible(operationNode)) {
    elements.operationHint.textContent =
      "ONLINE・session有効・tasking可能なpurple_lab Nodeを選択してください。";
  } else {
    elements.operationHint.textContent =
      "全stepを検証してからatomicに登録します。Nodeには通常taskとして順番にdispatchされます。";
  }
  elements.exerciseForm.dataset.permission = canWriteExercises ? "allowed" : "denied";
  if (!hasToken) {
    elements.exercisePermissionHint.textContent =
      "接続後、exercise_write権限を持つOperatorだけが開始できます。";
  } else if (!currentRole) {
    elements.exercisePermissionHint.textContent = "Operatorの演習権限を確認中です。";
  } else if (!canWriteExercises) {
    elements.exercisePermissionHint.textContent =
      `${currentRole.toUpperCase()} roleは演習を閲覧できますが、開始にはexercise_write権限が必要です。`;
  } else if (!exerciseNodeAllowed) {
    elements.exercisePermissionHint.textContent =
      "ONLINEかつsession有効なpurple_lab Nodeを選択してください。";
  } else {
    elements.exercisePermissionHint.textContent =
      "固定シナリオだけをNode-private synthetic workspaceで開始します。";
  }
  setText(elements.noteCharacterCount, `${noteLength} / ${MAX_NOTE_LENGTH}`);
  elements.noteCharacterCount.dataset.limit = noteLength > MAX_NOTE_LENGTH ? "exceeded" : "ok";
  elements.noteForm.dataset.permission = canWriteNotes ? "allowed" : "denied";
  if (!hasToken) {
    elements.notePermissionHint.textContent = "接続後、note_write権限を持つOperatorだけが投稿できます。";
  } else if (!currentRole) {
    elements.notePermissionHint.textContent = "Operatorの権限情報を確認中です。";
  } else if (!canWriteNotes) {
    elements.notePermissionHint.textContent = `${currentRole.toUpperCase()} roleはメモを閲覧できますが、投稿にはnote_write権限が必要です。`;
  } else {
    elements.notePermissionHint.textContent = "最大240文字のplain textとして共有履歴へ記録します。";
  }
  elements.noteInput.setAttribute("aria-disabled", String(elements.noteInput.disabled));
  elements.noteSubmitButton.setAttribute(
    "aria-disabled",
    String(elements.noteSubmitButton.disabled),
  );
  elements.createExerciseButton.setAttribute(
    "aria-disabled",
    String(elements.createExerciseButton.disabled),
  );
  elements.operationSubmitButton.setAttribute(
    "aria-disabled",
    String(elements.operationSubmitButton.disabled),
  );
  elements.operationSubmitButton.title =
    currentRole && !canWriteTasks ? "task_write 権限が必要です。" : "";
  elements.createExerciseButton.title =
    currentRole && !canWriteExercises ? "exercise_write 権限が必要です。" : "";
  elements.resetButton.setAttribute("aria-disabled", String(elements.resetButton.disabled));
  elements.createTaskButton.setAttribute("aria-disabled", String(elements.createTaskButton.disabled));
  elements.resetButton.title = currentRole && !canReset ? "reset 権限が必要です。" : "";
  elements.createTaskButton.title =
    currentRole && !canWriteTasks ? "task_write 権限が必要です。" : "";
  elements.resetButton.setAttribute(
    "aria-label",
    currentRole && !canReset ? "リセット（reset 権限が必要）" : "リセット",
  );
  elements.createTaskButton.setAttribute(
    "aria-label",
    currentRole && !canWriteTasks
      ? "許可タスクをキューへ送信（task_write 権限が必要）"
      : "許可タスクをキューへ送信",
  );
  for (const cancelButton of elements.taskDetailBody.querySelectorAll("[data-task-cancel]")) {
    cancelButton.disabled = !hasToken || !canWriteTasks || cancelButton.dataset.busy === "true";
    cancelButton.setAttribute("aria-disabled", String(cancelButton.disabled));
    cancelButton.title = canWriteTasks ? "" : "task_write 権限が必要です。";
    cancelButton.setAttribute(
      "aria-label",
      canWriteTasks ? "待機タスクを取り消す" : "待機タスクを取り消す（task_write 権限が必要）",
    );
  }
  for (const containButton of elements.exerciseList.querySelectorAll("[data-exercise-contain]")) {
    const actionAllowed = CONTAINMENT_ACTIONS.has(containButton.dataset.containmentAction);
    const containable = containButton.dataset.exerciseContainable === "true";
    containButton.disabled =
      !hasToken ||
      !canContainExercises ||
      !actionAllowed ||
      !containButton.dataset.exerciseId ||
      !containable ||
      containButton.dataset.busy === "true";
    containButton.setAttribute("aria-disabled", String(containButton.disabled));
    containButton.title = !canContainExercises
      ? "admin roleとcontainment_write権限が必要です。"
      : !containable
        ? "検知後、未封じ込めの演習にだけ適用できます。"
        : "";
  }
}

async function refresh({ silent = false } = {}) {
  if (!operatorToken || refreshInFlight) return;
  const requestToken = operatorToken;
  const requestGeneration = tokenGeneration;
  refreshInFlight = true;
  elements.metricGrid.setAttribute("aria-busy", "true");
  elements.nodeList.setAttribute("aria-busy", "true");
  elements.exerciseList.setAttribute("aria-busy", "true");
  elements.graphViewport.setAttribute("aria-busy", "true");
  elements.eventList.setAttribute("aria-busy", "true");
  if (!silent || !latestOverview) setApiState("loading", "更新中");
  updateControls();

  try {
    let nextCursors = { ...syncCursors };
    let nextHistory = {
      events: [...retainedHistory.events],
      audit: [...retainedHistory.audit],
    };
    let nextStreamId = syncStreamId;
    let overview = null;
    const needsSession = sessionGeneration !== requestGeneration;
    const sessionRequest = needsSession ? api("/lab/session") : Promise.resolve(null);

    for (let pageIndex = 0; pageIndex < MAX_SYNC_PAGES; pageIndex += 1) {
      let page;
      let session = null;
      if (pageIndex === 0) {
        [page, session] = await Promise.all([api(syncPath(nextCursors)), sessionRequest]);
      } else {
        page = await api(syncPath(nextCursors));
      }
      if (requestIsStale(requestGeneration, requestToken)) return;
      validateSyncPage(page);
      if (nextStreamId && page.stream_id !== nextStreamId) {
        nextStreamId = page.stream_id;
        nextCursors = { events: 0, audit: 0 };
        nextHistory = { events: [], audit: [] };
        overview = null;
        sessionGeneration = -1;
        clearOperatorSession();
        continue;
      }
      nextStreamId = page.stream_id;
      if (session) {
        renderOperatorSession(session);
        sessionGeneration = requestGeneration;
      }

      const previousCursors = nextCursors;
      nextHistory = {
        events: mergeHistoryRecords(
          nextHistory.events,
          page.events,
          page.cursor_reset.events,
        ),
        audit: mergeHistoryRecords(
          nextHistory.audit,
          page.audit,
          page.cursor_reset.audit,
        ),
      };
      nextCursors = {
        events: syncCounter(page.cursors, "events", "cursors"),
        audit: syncCounter(page.cursors, "audit", "cursors"),
      };
      if (
        (page.has_more.events && nextCursors.events === previousCursors.events) ||
        (page.has_more.audit && nextCursors.audit === previousCursors.audit)
      ) {
        throw new ApiError("同期カーソルが進まない応答を拒否しました。");
      }
      overview = page;
      if (!page.has_more.events && !page.has_more.audit) break;
    }

    if (!overview || requestIsStale(requestGeneration, requestToken)) return;
    syncStreamId = nextStreamId;
    syncCursors = nextCursors;
    retainedHistory = nextHistory;
    renderOverview(overview, retainedHistory.events, retainedHistory.audit);
    setApiState("online", "localhost 接続中");
  } catch (error) {
    if (requestIsStale(requestGeneration, requestToken)) return;
    if (error?.status === 401) {
      clearOverview();
    } else if (latestOverview) {
      latestOverview = { ...latestOverview, connection_status: "unknown" };
      renderedGraphKey = "";
      renderGraph(latestOverview);
    }
    setApiState("error", error?.status === 401 ? "認証エラー" : "接続エラー");
    if (!silent) showToast(humanError(error), "error");
  } finally {
    const tokenChangedDuringRequest = requestIsStale(requestGeneration, requestToken);
    refreshInFlight = false;
    elements.metricGrid.setAttribute("aria-busy", "false");
    elements.nodeList.setAttribute("aria-busy", "false");
    elements.exerciseList.setAttribute("aria-busy", "false");
    elements.graphViewport.setAttribute("aria-busy", "false");
    elements.eventList.setAttribute("aria-busy", "false");
    updateControls();
    if (tokenChangedDuringRequest && operatorToken) {
      window.queueMicrotask(() => refresh());
    }
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

function isPlainRecord(value) {
  return Boolean(value) && !Array.isArray(value) && typeof value === "object";
}

function playbookDisplayValue(value) {
  if (value === true) return "有効";
  if (value === false) return "無効";
  if (value === null || value === undefined || value === "") return "—";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function addPlaybookResult(container, result) {
  const isStructuredResult =
    isPlainRecord(result) &&
    typeof result.playbook === "string" &&
    isPlainRecord(result.scope) &&
    Array.isArray(result.attack_techniques) &&
    Array.isArray(result.steps) &&
    Array.isArray(result.evidence);
  if (!isStructuredResult) {
    addJsonBlock(container, "Result", result);
    return;
  }

  const resultSection = document.createElement("section");
  resultSection.className = "playbook-result";
  const heading = document.createElement("div");
  heading.className = "playbook-result__heading";
  const headingCopy = document.createElement("div");
  headingCopy.append(
    makeTextElement("span", "", "STRUCTURED PLAYBOOK RESULT"),
    makeTextElement("h3", "", result.playbook || "Playbook result"),
  );
  heading.append(headingCopy, makeTextElement("span", "playbook-result__verified", "VALIDATED RESULT"));
  resultSection.append(heading);

  const scope = isPlainRecord(result.scope) ? result.scope : {};
  const scopeSection = document.createElement("section");
  scopeSection.className = "playbook-result__section";
  scopeSection.append(makeTextElement("h4", "", "実行スコープ"));
  const scopeGrid = document.createElement("div");
  scopeGrid.className = "playbook-scope-grid";
  const scopeLabels = {
    workspace: "Workspace",
    data: "Data",
    host_access: "Host access",
    network_access: "Network access",
  };
  for (const [key, value] of Object.entries(scope)) {
    const item = document.createElement("div");
    item.dataset.restricted = String(
      (key === "host_access" || key === "network_access") && value === false,
    );
    item.append(
      makeTextElement("span", "", scopeLabels[key] || key),
      makeTextElement("strong", "", playbookDisplayValue(value)),
    );
    scopeGrid.append(item);
  }
  if (!scopeGrid.childElementCount) {
    scopeGrid.append(makeTextElement("p", "playbook-result__empty", "スコープ情報はありません。"));
  }
  scopeSection.append(scopeGrid);
  resultSection.append(scopeSection);

  const attackSection = document.createElement("section");
  attackSection.className = "playbook-result__section";
  attackSection.append(makeTextElement("h4", "", "ATT&CK マッピング"));
  const attackList = document.createElement("div");
  attackList.className = "playbook-attack-list";
  const techniques = Array.isArray(result.attack_techniques) ? result.attack_techniques : [];
  for (const technique of techniques) {
    if (!isPlainRecord(technique)) continue;
    const item = document.createElement("article");
    item.append(
      makeTextElement("strong", "", technique.id || "Technique"),
      makeTextElement("span", "", technique.name || "—"),
      makeTextElement("small", "", `Emulation: ${technique.emulation || "—"}`),
    );
    attackList.append(item);
  }
  if (!attackList.childElementCount) {
    attackList.append(makeTextElement("p", "playbook-result__empty", "このplaybookにATT&CKマッピングはありません。"));
  }
  attackSection.append(attackList);
  resultSection.append(attackSection);

  const stepsSection = document.createElement("section");
  stepsSection.className = "playbook-result__section";
  stepsSection.append(makeTextElement("h4", "", "実行ステップ"));
  const stepsList = document.createElement("ol");
  stepsList.className = "playbook-step-list";
  const resultSteps = Array.isArray(result.steps) ? result.steps : [];
  for (const stepDefinition of resultSteps) {
    if (!isPlainRecord(stepDefinition)) continue;
    const step = document.createElement("li");
    const marker = makeTextElement("span", "playbook-step-list__marker", "✓");
    marker.setAttribute("aria-hidden", "true");
    const copy = document.createElement("div");
    copy.append(
      makeTextElement("strong", "", stepDefinition.name || "step"),
      makeTextElement("span", "", stepDefinition.observation || "—"),
      makeTextElement("small", "", String(stepDefinition.status || "unknown").toUpperCase()),
    );
    step.append(marker, copy);
    stepsList.append(step);
  }
  if (!stepsList.childElementCount) {
    stepsList.append(makeTextElement("li", "playbook-result__empty", "ステップ情報はありません。"));
  }
  stepsSection.append(stepsList);
  resultSection.append(stepsSection);

  const evidenceSection = document.createElement("section");
  evidenceSection.className = "playbook-result__section";
  evidenceSection.append(makeTextElement("h4", "", "Evidence"));
  const evidenceList = document.createElement("div");
  evidenceList.className = "playbook-evidence-list";
  const evidence = Array.isArray(result.evidence) ? result.evidence : [];
  for (const evidenceDefinition of evidence) {
    if (!isPlainRecord(evidenceDefinition)) continue;
    const card = document.createElement("article");
    const artifact = evidenceDefinition.artifact || "evidence";
    card.append(makeTextElement("strong", "", artifact));
    const facts = document.createElement("dl");
    for (const [key, value] of Object.entries(evidenceDefinition)) {
      if (key === "artifact") continue;
      const fact = document.createElement("div");
      fact.append(
        makeTextElement("dt", "", key),
        makeTextElement("dd", key === "sha256" ? "evidence-digest" : "", playbookDisplayValue(value)),
      );
      facts.append(fact);
    }
    card.append(facts);
    evidenceList.append(card);
  }
  if (!evidenceList.childElementCount) {
    evidenceList.append(makeTextElement("p", "playbook-result__empty", "Evidenceはありません。"));
  }
  evidenceSection.append(evidenceList);
  resultSection.append(evidenceSection);

  const rawDetails = document.createElement("details");
  rawDetails.className = "playbook-result__raw";
  rawDetails.append(makeTextElement("summary", "", "検証済みraw JSONを表示"));
  addJsonBlock(rawDetails, "Result JSON", result);
  resultSection.append(rawDetails);
  container.append(resultSection);
}

function formattedMilliseconds(value) {
  if (!Number.isSafeInteger(value) || value < 0) return "—";
  if (value >= 60000) return `${(value / 60000).toFixed(1)} min (${value} ms)`;
  if (value >= 1000) return `${(value / 1000).toFixed(value % 1000 === 0 ? 0 : 2)} s (${value} ms)`;
  return `${value} ms`;
}

function addRawTaskData(container, task, { includeResult = true } = {}) {
  const rawDetails = document.createElement("details");
  rawDetails.className = "task-result-raw";
  rawDetails.append(makeTextElement("summary", "", "Raw payload / resultを確認"));
  addJsonBlock(rawDetails, "Payload JSON", task.payload);
  if (includeResult) addJsonBlock(rawDetails, "Result JSON", task.result);
  container.append(rawDetails);
}

function addTaskResultSummary(container, task) {
  const result = isPlainRecord(task.result) ? task.result : {};
  const status = String(task.status || "unknown").toLowerCase();
  const section = document.createElement("section");
  section.className = "task-result-summary";
  section.dataset.status = status;

  const heading = document.createElement("div");
  heading.className = "task-result-summary__heading";
  const headingCopy = document.createElement("div");
  headingCopy.append(
    makeTextElement("span", "", "RESULT SUMMARY"),
    makeTextElement("h3", "", `${task.type || "TASK"} の結果`),
  );
  heading.append(
    headingCopy,
    makeTextElement("span", "task-result-summary__status", status.toUpperCase()),
  );
  section.append(heading);

  const grid = document.createElement("div");
  grid.className = "detail-grid task-result-summary__grid";
  if (status !== "completed") {
    const statusDescriptions = {
      queued: "Nodeへのdispatchを待っています。",
      dispatched: "Nodeが処理中です。",
      failed: "Nodeまたは制御面が安全に失敗終了しました。",
      timeout: "dispatch後、期限内にNodeから結果が返りませんでした。",
      cancelled: "dispatch前に待機タスクが取り消されました。",
      expired: "Queue TTL内にdispatchされず期限切れになりました。",
    };
    addDetailValue(grid, "状態の意味", statusDescriptions[status] || "結果を確認できません。");
    if (result.reason) addDetailValue(grid, "Reason", result.reason);
    if (result.error_code) addDetailValue(grid, "Error code", result.error_code);
    if (result.error) addDetailValue(grid, "Error", result.error);
  } else if (task.type === "PING") {
    addDetailValue(grid, "Node応答", result.reply);
    addDetailValue(grid, "確認内容", "Teamserver → Node → result の往復が完了");
  } else if (task.type === "RUNTIME_STATUS") {
    addDetailValue(grid, "Version", result.version);
    addDetailValue(grid, "Profile", result.profile);
    addDetailValue(grid, "Uptime", formattedMilliseconds(result.uptime_ms));
    addDetailValue(grid, "完了タスク", result.tasks_completed);
    addDetailValue(grid, "Poll間隔", formattedMilliseconds(result.poll_interval_ms));
    addDetailValue(grid, "Jitter", Number.isInteger(result.jitter_percent) ? `${result.jitter_percent}%` : "—");
  } else if (task.type === "ECHO_TEXT") {
    addDetailValue(grid, "返却テキスト", result.echo);
  } else if (task.type === "HASH_TEXT") {
    addDetailValue(grid, "Algorithm", result.algorithm);
    addDetailValue(grid, "SHA-256 digest", result.digest);
  } else if (task.type === "WAIT") {
    addDetailValue(grid, "実際の待機時間", formattedMilliseconds(result.waited_ms));
  } else if (task.type === "GENERATE_EVENT") {
    addDetailValue(grid, "記録", result.recorded === true ? "記録済み" : "未確認");
    addDetailValue(grid, "Category", result.category);
    addDetailValue(grid, "Severity", result.severity);
    addDetailValue(grid, "Message", result.message);
  } else if (task.type === "SLEEP") {
    addDetailValue(grid, "変更前のPoll", formattedMilliseconds(result.previous_interval_ms));
    addDetailValue(grid, "変更後のPoll", formattedMilliseconds(result.new_interval_ms));
    addDetailValue(grid, "新しいJitter", Number.isInteger(result.jitter_percent) ? `${result.jitter_percent}%` : "—");
    addDetailValue(grid, "適用タイミング", "result acknowledgement後");
  } else if (task.type === "EXIT") {
    addDetailValue(grid, "停止ACK", result.acknowledged === true ? "受信済み" : "未確認");
    addDetailValue(grid, "影響", "foreground Nodeが正常終了し、sessionを切断");
  } else {
    addDetailValue(grid, "結果", "検証済みresultを受信しました。");
  }
  section.append(grid);
  container.append(section);
  addRawTaskData(container, task);
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

function renderTaskDetail(task, nodeName) {
  const detailKey = JSON.stringify({ task, nodeName: nodeName || "" });
  if (detailKey === renderedTaskDetailKey) {
    updateControls();
    return;
  }
  renderedTaskDetailKey = detailKey;
  elements.taskDetailBody.replaceChildren();
  elements.taskDetailBody.append(createTaskLifecycle(task));
  const grid = document.createElement("div");
  grid.className = "detail-grid";
  addDetailValue(grid, "Task ID", task.id);
  addDetailValue(grid, "Correlation ID", task.correlation_id);
  if (task.operation_id) {
    addDetailValue(grid, "Operation", task.operation_id);
    addDetailValue(grid, "Operation step", task.operation_step);
  }
  addDetailValue(grid, "Type", task.type);
  addDetailValue(grid, "Status", String(task.status || "unknown").toUpperCase());
  addDetailValue(grid, "Delivery attempts", task.delivery_attempts);
  addDetailValue(grid, "Node", nodeName || task.node_id);
  addDetailValue(grid, "Created by", task.created_by);
  addDetailValue(grid, "Created", localTime(task.created_at));
  addDetailValue(grid, "Dispatched", localTime(task.dispatched_at));
  addDetailValue(grid, "Completed", localTime(task.completed_at));
  addDetailValue(
    grid,
    "Queue TTL",
    Number.isFinite(task.queue_ttl_seconds) ? `${task.queue_ttl_seconds} s` : "—",
  );
  elements.taskDetailBody.append(grid);
  if (task.type === "RUN_PLAYBOOK" && task.status === "completed") {
    addPlaybookResult(elements.taskDetailBody, task.result);
    addRawTaskData(elements.taskDetailBody, task, { includeResult: false });
  } else {
    addTaskResultSummary(elements.taskDetailBody, task);
  }
  if (task.status === "queued") {
    const actions = document.createElement("div");
    actions.className = "detail-actions";
    const cancelButton = makeTextElement(
      "button",
      "button button--danger-ghost",
      "待機タスクを取り消す",
    );
    cancelButton.type = "button";
    cancelButton.dataset.taskCancel = "true";
    cancelButton.addEventListener("click", async () => {
      if (!hasPermission("task_write")) {
        showToast("待機タスクの取消には task_write 権限が必要です。", "error");
        return;
      }
      const requestGeneration = tokenGeneration;
      const requestToken = operatorToken;
      cancelButton.dataset.busy = "true";
      updateControls();
      try {
        await api(`/lab/tasks/${task.id}/cancel`, { method: "POST", body: {} });
        if (requestIsStale(requestGeneration, requestToken)) return;
        closeTaskDetail();
        showToast(`タスク ${task.id} を取り消しました。`, "success");
        await refresh({ silent: true });
      } catch (error) {
        if (requestIsStale(requestGeneration, requestToken)) return;
        showToast(humanError(error), "error");
      } finally {
        delete cancelButton.dataset.busy;
        updateControls();
      }
    });
    actions.append(cancelButton);
    elements.taskDetailBody.append(actions);
    updateControls();
  }
}

function showTaskDetail(task, nodeName) {
  openTaskDetailId = typeof task.id === "string" ? task.id : "";
  renderedTaskDetailKey = "";
  renderTaskDetail(task, nodeName);
  if (!elements.taskDetailDialog.open) elements.taskDetailDialog.showModal();
}

function refreshOpenTaskDetail(tasks, nodes) {
  if (!elements.taskDetailDialog.open || !openTaskDetailId) return;
  const task = tasks.find((candidate) => candidate.id === openTaskDetailId);
  if (!task) {
    closeTaskDetail();
    return;
  }
  const node = nodes.find((candidate) => candidate.id === task.node_id);
  renderTaskDetail(task, node?.name);
}

function closeTaskDetail() {
  openTaskDetailId = "";
  renderedTaskDetailKey = "";
  if (elements.taskDetailDialog.open) elements.taskDetailDialog.close();
}

function applyTaskTemplate() {
  const template = TASK_TEMPLATES[elements.taskTypeSelect.value];
  if (!template) return;
  const type = elements.taskTypeSelect.value;
  const isText = type === "ECHO_TEXT" || type === "HASH_TEXT";
  const isWait = type === "WAIT";
  const isEvent = type === "GENERATE_EVENT";
  const isSleep = type === "SLEEP";
  const isExit = type === "EXIT";
  const isPlaybook = type === "RUN_PLAYBOOK";
  const hasNoInput = type === "PING" || type === "RUNTIME_STATUS";

  elements.taskNoInput.hidden = !hasNoInput;
  elements.taskTextField.hidden = !isText;
  elements.taskTextInput.disabled = !isText;
  elements.taskTextInput.required = isText;
  elements.waitField.hidden = !isWait;
  elements.waitRangeInput.disabled = !isWait;
  elements.waitNumberInput.disabled = !isWait;
  elements.waitNumberInput.required = isWait;
  elements.eventFields.hidden = !isEvent;
  elements.eventCategorySelect.disabled = !isEvent;
  elements.eventSeveritySelect.disabled = !isEvent;
  elements.eventMessageInput.disabled = !isEvent;
  elements.eventMessageInput.required = isEvent;
  elements.sleepFields.hidden = !isSleep;
  elements.sleepIntervalRangeInput.disabled = !isSleep;
  elements.sleepIntervalNumberInput.disabled = !isSleep;
  elements.sleepIntervalNumberInput.required = isSleep;
  elements.sleepJitterRangeInput.disabled = !isSleep;
  elements.sleepJitterNumberInput.disabled = !isSleep;
  elements.sleepJitterNumberInput.required = isSleep;
  elements.exitField.hidden = !isExit;
  elements.exitConfirmInput.disabled = !isExit;
  elements.exitConfirmInput.required = isExit;
  elements.playbookField.hidden = !isPlaybook;
  elements.playbookSelect.disabled = !isPlaybook;

  if (isText) elements.taskTextInput.value = template.payload.text;
  if (isWait) {
    elements.waitRangeInput.value = String(template.payload.milliseconds);
    elements.waitNumberInput.value = String(template.payload.milliseconds);
  }
  if (isEvent) {
    elements.eventCategorySelect.value = template.payload.category;
    elements.eventSeveritySelect.value = template.payload.severity;
    elements.eventMessageInput.value = template.payload.message;
  }
  if (isSleep) {
    const node = selectedNode();
    const currentInterval = Number.isInteger(node?.poll_interval_ms) &&
      node.poll_interval_ms >= 250 && node.poll_interval_ms <= 3000
      ? node.poll_interval_ms
      : template.payload.interval_ms;
    const currentJitter = Number.isInteger(node?.jitter_percent) &&
      node.jitter_percent >= 0 && node.jitter_percent <= 50
      ? node.jitter_percent
      : template.payload.jitter_percent;
    elements.sleepIntervalRangeInput.value = String(currentInterval);
    elements.sleepIntervalNumberInput.value = String(currentInterval);
    elements.sleepJitterRangeInput.value = String(currentJitter);
    elements.sleepJitterNumberInput.value = String(currentJitter);
  }
  if (isExit) {
    elements.exitConfirmInput.checked = false;
    exitConfirmedNodeId = "";
  }
  if (isPlaybook) elements.playbookSelect.value = template.payload.playbook;

  const guidance = TASK_GUIDANCE[type];
  setText(elements.taskGuidanceTitle, type);
  setText(elements.taskGuidanceAction, guidance?.action || "固定タスクを実行します。");
  setText(elements.taskGuidanceAdjustable, guidance?.adjustable || "追加設定はありません。");
  setText(elements.taskGuidanceSafety, guidance?.safety || "profile allowlistの範囲だけで実行します。");
  const sleepNode = isSleep ? selectedNode() : null;
  setText(
    elements.payloadHint,
    sleepNode
      ? `現在は ${sleepNode.poll_interval_ms}ms ±${sleepNode.jitter_percent}% です。変更はresult acknowledgement後に適用されます。`
      : template.hint,
  );
  syncTaskPayloadPreview({ showErrors: isExit });
}

function taskPayloadFromControls() {
  const type = elements.taskTypeSelect.value;
  if (type === "ECHO_TEXT" || type === "HASH_TEXT") {
    return { text: elements.taskTextInput.value.trim() };
  }
  if (type === "WAIT") {
    return { milliseconds: elements.waitNumberInput.valueAsNumber };
  }
  if (type === "GENERATE_EVENT") {
    return {
      category: elements.eventCategorySelect.value,
      severity: elements.eventSeveritySelect.value,
      message: elements.eventMessageInput.value.trim(),
    };
  }
  if (type === "SLEEP") {
    return {
      interval_ms: elements.sleepIntervalNumberInput.valueAsNumber,
      jitter_percent: elements.sleepJitterNumberInput.valueAsNumber,
    };
  }
  if (type === "RUN_PLAYBOOK") {
    return { playbook: elements.playbookSelect.value };
  }
  return {};
}

function allTaskValidationControls() {
  return [
    elements.taskTextInput,
    elements.waitNumberInput,
    elements.eventCategorySelect,
    elements.eventSeveritySelect,
    elements.eventMessageInput,
    elements.sleepIntervalNumberInput,
    elements.sleepJitterNumberInput,
    elements.exitConfirmInput,
    elements.playbookSelect,
  ];
}

function invalidTaskValidationControls() {
  const type = elements.taskTypeSelect.value;
  if (type === "ECHO_TEXT" || type === "HASH_TEXT") {
    const text = elements.taskTextInput.value.trim();
    const length = unicodeLength(text);
    return length < 1 || length > 240 || !hasSupportedTextCharacters(text)
      ? [elements.taskTextInput]
      : [];
  }
  if (type === "WAIT") {
    const value = elements.waitNumberInput.valueAsNumber;
    return !Number.isInteger(value) || value < 0 || value > 2000
      ? [elements.waitNumberInput]
      : [];
  }
  if (type === "GENERATE_EVENT") {
    const invalid = [];
    if (!["training", "telemetry", "policy"].includes(elements.eventCategorySelect.value)) {
      invalid.push(elements.eventCategorySelect);
    }
    if (!["info", "warning"].includes(elements.eventSeveritySelect.value)) {
      invalid.push(elements.eventSeveritySelect);
    }
    const message = elements.eventMessageInput.value.trim();
    const length = unicodeLength(message);
    if (length < 1 || length > 240 || !hasSupportedTextCharacters(message)) {
      invalid.push(elements.eventMessageInput);
    }
    return invalid;
  }
  if (type === "SLEEP") {
    const invalid = [];
    const interval = elements.sleepIntervalNumberInput.valueAsNumber;
    const jitter = elements.sleepJitterNumberInput.valueAsNumber;
    if (!Number.isInteger(interval) || interval < 250 || interval > 3000) {
      invalid.push(elements.sleepIntervalNumberInput);
    }
    if (!Number.isInteger(jitter) || jitter < 0 || jitter > 50) {
      invalid.push(elements.sleepJitterNumberInput);
    }
    return invalid;
  }
  if (type === "EXIT") {
    return elements.exitConfirmInput.checked &&
      elements.taskNodeSelect.value &&
      exitConfirmedNodeId === elements.taskNodeSelect.value
      ? []
      : [elements.exitConfirmInput];
  }
  if (type === "RUN_PLAYBOOK") {
    return ["DISCOVERY_FIXTURES", "COLLECT_AND_STAGE", "CREATE_CANARY", "CLEANUP"].includes(
      elements.playbookSelect.value,
    )
      ? []
      : [elements.playbookSelect];
  }
  return [];
}

function setTaskValidationState(valid) {
  for (const control of allTaskValidationControls()) control.removeAttribute("aria-invalid");
  if (!valid) {
    for (const control of invalidTaskValidationControls()) {
      control.setAttribute("aria-invalid", "true");
    }
  }
}

function syncTaskPayloadPreview({ showErrors = true } = {}) {
  const textLength = unicodeLength(elements.taskTextInput.value.trim());
  const messageLength = unicodeLength(elements.eventMessageInput.value.trim());
  setText(elements.taskTextCharacterCount, `${textLength} / 240`);
  setText(elements.eventMessageCharacterCount, `${messageLength} / 240`);
  elements.taskTextCharacterCount.dataset.limit = textLength > 240 ? "exceeded" : "ok";
  elements.eventMessageCharacterCount.dataset.limit = messageLength > 240 ? "exceeded" : "ok";
  elements.taskPayloadInput.value = JSON.stringify(taskPayloadFromControls(), null, 2);
  try {
    readPayload();
    elements.payloadError.textContent = "";
    setTaskValidationState(true);
    return true;
  } catch (error) {
    elements.payloadError.textContent = showErrors ? error.message : "";
    setTaskValidationState(false);
    return false;
  }
}

function readQueueTtlSeconds() {
  const queueTtlSeconds = elements.queueTtlNumberInput.valueAsNumber;
  if (!Number.isInteger(queueTtlSeconds) || queueTtlSeconds < 5 || queueTtlSeconds > 86400) {
    throw new Error("キュー待機期限は5〜86400秒の整数にしてください。");
  }
  return queueTtlSeconds;
}

function updateQueueTtlControl({ showErrors = true } = {}) {
  const custom = elements.queueTtlPresetSelect.value === "custom";
  elements.queueTtlCustomField.hidden = !custom;
  elements.queueTtlNumberInput.disabled = !custom;
  if (!custom) elements.queueTtlNumberInput.value = elements.queueTtlPresetSelect.value;
  try {
    readQueueTtlSeconds();
    elements.queueTtlError.textContent = "";
    elements.queueTtlNumberInput.removeAttribute("aria-invalid");
    return true;
  } catch (error) {
    elements.queueTtlError.textContent = showErrors ? error.message : "";
    elements.queueTtlNumberInput.setAttribute("aria-invalid", "true");
    return false;
  }
}

function taskComposerIsValid() {
  try {
    readPayload();
    readQueueTtlSeconds();
    return true;
  } catch {
    return false;
  }
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
    payload = taskPayloadFromControls();
  }
  if (!payload || Array.isArray(payload) || typeof payload !== "object") {
    throw new Error("payload はJSONオブジェクトにしてください。");
  }

  if (type === "PING" || type === "RUNTIME_STATUS") {
    exactPayloadKeys(payload, []);
  } else if (type === "ECHO_TEXT" || type === "HASH_TEXT") {
    exactPayloadKeys(payload, ["text"]);
    const textLength = typeof payload.text === "string" ? unicodeLength(payload.text.trim()) : 0;
    if (typeof payload.text !== "string" || textLength < 1 || textLength > 240) {
      throw new Error("text は1〜240文字にしてください。");
    }
    if (!hasSupportedTextCharacters(payload.text)) {
      throw new Error("text に未対応の制御文字または区切り文字が含まれています。");
    }
  } else if (type === "WAIT") {
    exactPayloadKeys(payload, ["milliseconds"]);
    if (!Number.isInteger(payload.milliseconds) || payload.milliseconds < 0 || payload.milliseconds > 2000) {
      throw new Error("待機時間は0〜2000msの整数にしてください。");
    }
  } else if (type === "GENERATE_EVENT") {
    exactPayloadKeys(payload, ["category", "severity", "message"]);
    if (!["training", "telemetry", "policy"].includes(payload.category)) {
      throw new Error("category は training / telemetry / policy から選んでください。");
    }
    if (!["info", "warning"].includes(payload.severity)) {
      throw new Error("severity は info / warning から選んでください。");
    }
    const messageLength = typeof payload.message === "string"
      ? unicodeLength(payload.message.trim())
      : 0;
    if (typeof payload.message !== "string" || messageLength < 1 || messageLength > 240) {
      throw new Error("message は1〜240文字にしてください。");
    }
    if (!hasSupportedTextCharacters(payload.message)) {
      throw new Error("message に未対応の制御文字または区切り文字が含まれています。");
    }
  } else if (type === "SLEEP") {
    exactPayloadKeys(payload, ["interval_ms", "jitter_percent"]);
    if (!Number.isInteger(payload.interval_ms) || payload.interval_ms < 250 || payload.interval_ms > 3000) {
      throw new Error("poll間隔は250〜3000msの整数にしてください。");
    }
    if (!Number.isInteger(payload.jitter_percent) || payload.jitter_percent < 0 || payload.jitter_percent > 50) {
      throw new Error("jitterは0〜50%の整数にしてください。");
    }
  } else if (type === "EXIT") {
    exactPayloadKeys(payload, []);
    if (
      !elements.exitConfirmInput.checked ||
      !elements.taskNodeSelect.value ||
      exitConfirmedNodeId !== elements.taskNodeSelect.value
    ) {
      throw new Error("選択中のNode停止の影響を確認してから送信してください。");
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
  const changed = nextToken !== operatorToken;
  if (changed) {
    tokenGeneration += 1;
    pendingTaskSubmission = null;
    pendingOperationSubmission = null;
    pendingNoteSubmission = null;
    pendingExerciseSubmission = null;
    elements.exitConfirmInput.checked = false;
    exitConfirmedNodeId = "";
    elements.noteInput.value = "";
    resetSyncState();
    clearOperatorSession();
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
  return changed;
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
  updateControls();
  try {
    return await action();
  } finally {
    button.replaceChildren(...originalNodes);
    delete button.dataset.busy;
    updateControls();
  }
}

async function containExercise(exerciseId, action, button) {
  if (currentRole !== "admin" || !hasPermission("containment_write")) {
    showToast("封じ込めにはadmin roleとcontainment_write権限が必要です。", "error");
    return;
  }
  if (typeof exerciseId !== "string" || !CONTAINMENT_ACTIONS.has(action)) {
    showToast("固定封じ込めactionを確認できません。", "error");
    return;
  }
  const exercise = latestOverview?.exercises?.find((candidate) => candidate.id === exerciseId);
  if (!exercise || !canContainExercise(exercise)) {
    showToast("検知後、未封じ込めの演習を選択してください。", "error");
    return;
  }
  const confirmation = action === "PAUSE_NODE_TASKING"
    ? "このNodeの新規task dispatchをTeamserver上で一時停止しますか？解除はラボresetで行います。"
    : "この演習で未完了の固定タスクを取り消しますか？";
  if (!window.confirm(confirmation)) return;

  const requestGeneration = tokenGeneration;
  const requestToken = operatorToken;
  await runWithBusyButton(button, "適用中…", async () => {
    try {
      await api(`/lab/exercises/${encodeURIComponent(exerciseId)}/contain`, {
        method: "POST",
        body: { action },
      });
      if (requestIsStale(requestGeneration, requestToken)) return;
      showToast(`封じ込め ${action} を適用しました。`, "success");
      await refresh({ silent: true });
    } catch (error) {
      if (requestIsStale(requestGeneration, requestToken)) return;
      showToast(humanError(error), "error");
    }
  });
}

elements.tokenForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const tokenChanged = saveToken(elements.tokenInput.value);
  if (tokenChanged) clearOverview();
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
elements.taskNodeSelect.addEventListener("change", () => {
  updateTaskCapabilities();
  if (["SLEEP", "EXIT"].includes(elements.taskTypeSelect.value)) applyTaskTemplate();
});
elements.taskTypeSelect.addEventListener("change", () => {
  applyTaskTemplate();
  updateControls();
});
elements.restoreTemplateButton.addEventListener("click", () => {
  applyTaskTemplate();
  updateControls();
});
elements.taskTextInput.addEventListener("input", () => {
  syncTaskPayloadPreview();
  updateControls();
});
elements.waitRangeInput.addEventListener("input", () => {
  elements.waitNumberInput.value = elements.waitRangeInput.value;
  syncTaskPayloadPreview();
  updateControls();
});
elements.waitNumberInput.addEventListener("input", () => {
  elements.waitRangeInput.value = elements.waitNumberInput.value;
  syncTaskPayloadPreview();
  updateControls();
});
for (const eventControl of [
  elements.eventCategorySelect,
  elements.eventSeveritySelect,
  elements.eventMessageInput,
]) {
  eventControl.addEventListener("input", () => {
    syncTaskPayloadPreview();
    updateControls();
  });
}
elements.sleepIntervalRangeInput.addEventListener("input", () => {
  elements.sleepIntervalNumberInput.value = elements.sleepIntervalRangeInput.value;
  syncTaskPayloadPreview();
  updateControls();
});
elements.sleepIntervalNumberInput.addEventListener("input", () => {
  elements.sleepIntervalRangeInput.value = elements.sleepIntervalNumberInput.value;
  syncTaskPayloadPreview();
  updateControls();
});
elements.sleepJitterRangeInput.addEventListener("input", () => {
  elements.sleepJitterNumberInput.value = elements.sleepJitterRangeInput.value;
  syncTaskPayloadPreview();
  updateControls();
});
elements.sleepJitterNumberInput.addEventListener("input", () => {
  elements.sleepJitterRangeInput.value = elements.sleepJitterNumberInput.value;
  syncTaskPayloadPreview();
  updateControls();
});
elements.exitConfirmInput.addEventListener("change", () => {
  exitConfirmedNodeId = elements.exitConfirmInput.checked
    ? elements.taskNodeSelect.value
    : "";
  syncTaskPayloadPreview();
  updateControls();
});
elements.playbookSelect.addEventListener("change", () => {
  syncTaskPayloadPreview();
  updateControls();
});
elements.queueTtlPresetSelect.addEventListener("change", () => {
  updateQueueTtlControl();
  updateControls();
});
elements.queueTtlNumberInput.addEventListener("input", () => {
  updateQueueTtlControl();
  updateControls();
});

elements.autoRefreshSelect.addEventListener("change", () => {
  const selectedInterval = Number(elements.autoRefreshSelect.value);
  if (!AUTO_REFRESH_OPTIONS.has(selectedInterval)) return;
  refreshIntervalMs = selectedInterval;
  saveUiSettings();
  scheduleAutoRefresh();
  updateAutoRefreshStatus();
});
elements.historyLimitSelect.addEventListener("change", () => {
  const selectedLimit = Number(elements.historyLimitSelect.value);
  if (!HISTORY_LIMIT_OPTIONS.has(selectedLimit)) return;
  historyRowLimit = selectedLimit;
  renderedHistoryKey = "";
  saveUiSettings();
  if (latestOverview) renderHistory();
});
elements.densitySelect.addEventListener("change", () => {
  const selectedDensity = elements.densitySelect.value;
  if (!DENSITY_OPTIONS.has(selectedDensity)) return;
  interfaceDensity = selectedDensity;
  document.body.dataset.density = interfaceDensity;
  saveUiSettings();
});

elements.graphViewSelect.addEventListener("change", () => {
  renderedGraphFocusKey = "";
  renderedGraphKey = "";
  graphSelectedEntityId = "";
  if (latestOverview) renderGraph(latestOverview);
  updateControls();
});
elements.graphFocusSelect.addEventListener("change", () => {
  renderedGraphKey = "";
  graphSelectedEntityId = "";
  if (latestOverview) renderGraph(latestOverview);
  updateControls();
});
elements.graphZoomInput.addEventListener("input", () => {
  graphZoomPercent = Number(elements.graphZoomInput.value);
  applyGraphZoom();
});
elements.graphZoomOutButton.addEventListener("click", () => {
  graphZoomPercent -= GRAPH_ZOOM_STEP;
  applyGraphZoom();
});
elements.graphZoomInButton.addEventListener("click", () => {
  graphZoomPercent += GRAPH_ZOOM_STEP;
  applyGraphZoom();
});
elements.graphFitButton.addEventListener("click", fitGraphToViewport);

elements.operationNodeSelect.addEventListener("change", () => {
  syncOperationPreview();
  updateControls();
});
elements.operationTtlSelect.addEventListener("change", () => {
  syncOperationPreview();
  updateControls();
});
elements.operationAddStepButton.addEventListener("click", () => {
  if (operationSteps.length >= MAX_OPERATION_STEPS) return;
  const playbook = elements.operationPlaybookSelect.value;
  if (!GRAPH_PLAYBOOK_IDS.has(playbook)) return;
  operationSteps = [...operationSteps, playbook];
  renderOperationSteps({ focusIndex: operationSteps.length - 1 });
});
elements.operationLoadPathButton.addEventListener(
  "click",
  loadFocusedAttackPathIntoOperation,
);
elements.operationForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!hasPermission("task_write")) {
    showToast("Operationの登録にはtask_write権限が必要です。", "error");
    return;
  }
  const requestBody = operationRequestBody();
  if (!operationComposerIsValid()) {
    elements.operationError.textContent =
      "ONLINEなpurple_lab Nodeと、1〜3件の固定playbookを確認してください。";
    if (!operationNodeEligible(selectedOperationNode())) {
      elements.operationNodeSelect.focus();
    } else {
      elements.operationStepList.querySelector("select")?.focus();
    }
    return;
  }

  const requestGeneration = tokenGeneration;
  const requestToken = operatorToken;
  await runWithBusyButton(elements.operationSubmitButton, "Operation登録中…", async () => {
    const signature = JSON.stringify(requestBody);
    if (!pendingOperationSubmission || pendingOperationSubmission.signature !== signature) {
      pendingOperationSubmission = { signature, key: newIdempotencyKey() };
    }
    try {
      const operation = await api("/lab/operations", {
        method: "POST",
        body: requestBody,
        idempotencyKey: pendingOperationSubmission.key,
      });
      if (requestIsStale(requestGeneration, requestToken)) return;
      pendingOperationSubmission = null;
      elements.operationError.textContent = "";
      showToast(
        `${operation.id || "Operation"} を ${operation.tasks?.length || operationSteps.length} stepで登録しました。`,
        "success",
      );
      await refresh({ silent: true });
    } catch (error) {
      if (requestIsStale(requestGeneration, requestToken)) return;
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        pendingOperationSubmission = null;
      }
      elements.operationError.textContent = humanError(error);
      showToast(humanError(error), "error");
    }
  });
});

elements.exerciseNodeSelect.addEventListener("change", () => {
  updateControls();
  if (
    latestOverview &&
    elements.graphViewSelect.value === "attack" &&
    elements.graphFocusSelect.value.startsWith("scenario:")
  ) {
    renderedGraphKey = "";
    renderGraph(latestOverview);
  }
});
elements.exerciseScenarioSelect.addEventListener("change", () => {
  updateExerciseScenarioSummary();
  updateControls();
  if (
    latestOverview &&
    elements.graphViewSelect.value === "attack" &&
    elements.graphFocusSelect.value.startsWith("scenario:")
  ) {
    elements.graphFocusSelect.value = `scenario:${elements.exerciseScenarioSelect.value}`;
    renderedGraphKey = "";
    renderGraph(latestOverview);
  }
});
elements.exerciseForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!hasPermission("exercise_write")) {
    showToast("演習の開始には exercise_write 権限が必要です。", "error");
    return;
  }
  const node = selectedExerciseNode();
  if (
    !node ||
    node.profile !== "purple_lab" ||
    node.session_active === false ||
    node.tasking_paused === true ||
    node.status !== "online"
  ) {
    showToast("ONLINEかつsession有効なpurple_lab Nodeを選択してください。", "error");
    return;
  }
  const scenarioId = elements.exerciseScenarioSelect.value;
  if (!EXERCISE_SCENARIO_IDS.has(scenarioId)) {
    showToast("固定scenario catalogから選択してください。", "error");
    return;
  }

  const requestGeneration = tokenGeneration;
  const requestToken = operatorToken;
  await runWithBusyButton(elements.createExerciseButton, "開始中…", async () => {
    const requestBody = { node_id: node.id, scenario_id: scenarioId };
    const signature = JSON.stringify(requestBody);
    if (!pendingExerciseSubmission || pendingExerciseSubmission.signature !== signature) {
      pendingExerciseSubmission = { signature, key: newIdempotencyKey() };
    }
    try {
      const exercise = await api("/lab/exercises", {
        method: "POST",
        body: requestBody,
        idempotencyKey: pendingExerciseSubmission.key,
      });
      if (requestIsStale(requestGeneration, requestToken)) return;
      pendingExerciseSubmission = null;
      showToast(`演習 ${exercise.id || scenarioId} を開始しました。`, "success");
      await refresh({ silent: true });
    } catch (error) {
      if (requestIsStale(requestGeneration, requestToken)) return;
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        pendingExerciseSubmission = null;
      }
      showToast(humanError(error), "error");
    }
  });
});

elements.noteInput.addEventListener("input", updateControls);
elements.noteForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!hasPermission("note_write")) {
    showToast("メモの共有には note_write 権限が必要です。", "error");
    return;
  }
  const message = elements.noteInput.value.trim();
  if (!message || unicodeLength(message) > MAX_NOTE_LENGTH) {
    showToast("メモは1〜240文字のplain textにしてください。", "error");
    elements.noteInput.focus();
    return;
  }

  const requestGeneration = tokenGeneration;
  const requestToken = operatorToken;
  await runWithBusyButton(elements.noteSubmitButton, "共有中…", async () => {
    const requestBody = { message };
    const signature = JSON.stringify(requestBody);
    if (!pendingNoteSubmission || pendingNoteSubmission.signature !== signature) {
      pendingNoteSubmission = { signature, key: newIdempotencyKey() };
    }
    try {
      await api("/lab/notes", {
        method: "POST",
        body: { message },
        idempotencyKey: pendingNoteSubmission.key,
      });
      if (requestIsStale(requestGeneration, requestToken)) return;
      pendingNoteSubmission = null;
      elements.noteInput.value = "";
      updateControls();
      showToast("共同作業メモを共有しました。", "success");
      await refresh({ silent: true });
    } catch (error) {
      if (requestIsStale(requestGeneration, requestToken)) return;
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        pendingNoteSubmission = null;
      }
      showToast(humanError(error), "error");
    }
  });
});

elements.taskForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!hasPermission("task_write")) {
    showToast("タスク作成には task_write 権限が必要です。", "error");
    return;
  }
  const node = selectedNode();
  if (!node) {
    showToast("送信先 Node を選択してください。", "error");
    return;
  }

  let payload;
  let queueTtlSeconds;
  try {
    payload = readPayload();
    elements.payloadError.textContent = "";
    setTaskValidationState(true);
  } catch (error) {
    elements.payloadError.textContent = error.message;
    setTaskValidationState(false);
    const focusTarget = {
      ECHO_TEXT: elements.taskTextInput,
      HASH_TEXT: elements.taskTextInput,
      WAIT: elements.waitNumberInput,
      GENERATE_EVENT: elements.eventMessageInput,
      SLEEP: elements.sleepIntervalNumberInput,
      EXIT: elements.exitConfirmInput,
      RUN_PLAYBOOK: elements.playbookSelect,
    }[elements.taskTypeSelect.value] || elements.taskTypeSelect;
    focusTarget.focus();
    return;
  }
  try {
    queueTtlSeconds = readQueueTtlSeconds();
    elements.queueTtlError.textContent = "";
    elements.queueTtlNumberInput.removeAttribute("aria-invalid");
  } catch (error) {
    elements.queueTtlError.textContent = error.message;
    elements.queueTtlNumberInput.setAttribute("aria-invalid", "true");
    (elements.queueTtlCustomField.hidden
      ? elements.queueTtlPresetSelect
      : elements.queueTtlNumberInput).focus();
    return;
  }

  const requestGeneration = tokenGeneration;
  const requestToken = operatorToken;
  await runWithBusyButton(elements.createTaskButton, "キューへ送信中…", async () => {
    const requestBody = {
      node_id: node.id,
      type: elements.taskTypeSelect.value,
      payload,
      queue_ttl_seconds: queueTtlSeconds,
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
      if (requestIsStale(requestGeneration, requestToken)) return;
      pendingTaskSubmission = null;
      if (task.type === "EXIT") {
        elements.exitConfirmInput.checked = false;
        exitConfirmedNodeId = "";
      }
      showToast(`タスクを追加しました。相関ID: ${task.correlation_id}`, "success");
      await refresh({ silent: true });
    } catch (error) {
      if (requestIsStale(requestGeneration, requestToken)) return;
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
  if (!hasPermission("reset")) {
    showToast("ラボのリセットには reset 権限が必要です。", "error");
    return;
  }
  const confirmed = window.confirm(
    "登録Node、タスク、イベントを消去し、現在のNodeセッションを無効化しますか？",
  );
  if (!confirmed) return;

  const requestGeneration = tokenGeneration;
  const requestToken = operatorToken;
  await runWithBusyButton(elements.resetButton, "リセット中…", async () => {
    try {
      await api("/lab/reset", { method: "POST", body: {} });
      if (requestIsStale(requestGeneration, requestToken)) return;
      showToast("ラボをリセットしました。foreground Node は自動で再登録します。", "success");
      await refresh({ silent: true });
    } catch (error) {
      if (requestIsStale(requestGeneration, requestToken)) return;
      showToast(humanError(error), "error");
    }
  });
});

elements.closeTaskDetailButton.addEventListener("click", closeTaskDetail);
elements.taskDetailDialog.addEventListener("click", (event) => {
  if (event.target === elements.taskDetailDialog) closeTaskDetail();
});
elements.taskDetailDialog.addEventListener("close", () => {
  openTaskDetailId = "";
  renderedTaskDetailKey = "";
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") refresh({ silent: true });
});

document.querySelectorAll("[data-boundary-filter]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const filter = btn.dataset.boundaryFilter;
    document.querySelectorAll("[data-boundary-filter]").forEach((candidate) => {
      const selected = candidate === btn;
      candidate.classList.toggle("is-active", selected);
      candidate.setAttribute("aria-pressed", String(selected));
    });
    document.querySelectorAll("#boundaryTable tbody tr").forEach((row) => {
      const group = row.dataset.boundaryGroup;
      row.classList.toggle("is-hidden", filter !== "all" && group !== filter);
    });
  });
});

const tokenFromHash = consumeHashToken();
initializeUiSettings();
saveToken(tokenFromHash === null ? storedToken() : tokenFromHash);
updateQueueTtlControl({ showErrors: false });
applyTaskTemplate();
renderOperationSteps();
clearOverview();
if (operatorToken) refresh();
scheduleAutoRefresh();
