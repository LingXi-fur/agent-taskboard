/* atb console — vanilla JS, no build step, no network beyond localhost. */
"use strict";

const state = {
  lang: null,
  texts: {},
  tasks: [],
  overview: null,
  sessions: null,
  events: [],
  eventsRange: null,
  selected: null,
  detail: null,
  showCompleted: false,
  themes: ["auto", "light", "dark", "contrast"],
};

/* ---------------------------------------------------------------- helpers */

function $(id) { return document.getElementById(id); }

function fmt(template, params) {
  return String(template).replace(/\{(\w+)\}/g, (whole, key) =>
    params && params[key] !== undefined ? params[key] : whole);
}

function t(path, params) {
  let node = state.texts;
  for (const part of path.split(".")) {
    if (node == null || typeof node !== "object") return path;
    node = node[part];
  }
  if (typeof node !== "string") return path;
  return params ? fmt(node, params) : node;
}

async function fetchJSON(url) {
  const response = await fetch(url);
  if (!response.ok) {
    let detail = "";
    try { detail = (await response.json()).error || ""; } catch (e) { /* ignore */ }
    throw new Error(`${url} → ${response.status} ${detail}`);
  }
  return response.json();
}

const STATUS_ICON = {
  todo: "○", doing: "◐", review: "◔", rework: "↺", done: "✓", dropped: "✗",
};

function statusBadge(status) {
  const span = document.createElement("span");
  span.className = `badge ${status}`;
  span.innerHTML =
    `<span class="badge-icon" aria-hidden="true">${STATUS_ICON[status] || "•"}</span>` +
    `<span class="badge-text">${escapeHTML(t(`summary.${status}`))}</span>`;
  return span;
}

function localDate() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

function localTime(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function escapeHTML(text) {
  return String(text == null ? "" : text).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
}

/** Middle-truncate a path, keeping the directory name visible. */
function displayCwd(cwd) {
  if (!cwd) return "";
  const parts = cwd.split(/[\\/]+/).filter(Boolean);
  const base = parts[parts.length - 1] || cwd;
  if (cwd.length <= 42) return cwd;
  const head = parts.length > 1 ? parts[0] : "";
  return head ? `${head}/…/${base}` : `…/${base}`;
}

/* ------------------------------------------------------------------ boot */

function applyUrlOverrides() {
  const params = new URLSearchParams(location.search);
  const theme = params.get("theme");
  if (["auto", "light", "dark", "contrast"].includes(theme)) {
    localStorage.setItem("atb-theme", theme);
  }
  const lang = params.get("lang");
  if (["zh", "en"].includes(lang)) {
    localStorage.setItem("atb-lang", lang);
  }
}

function detectLanguage() {
  const saved = localStorage.getItem("atb-lang");
  if (saved === "en" || saved === "zh") return saved;
  return (navigator.language || "en").toLowerCase().startsWith("zh") ? "zh" : "en";
}

async function loadTexts() {
  state.lang = detectLanguage();
  state.texts = await fetchJSON(`/locales/${state.lang}.json`);
  document.documentElement.lang = state.lang === "zh" ? "zh-CN" : "en";
  document.title = `${t("appTitle")} — ${t("taskList")}`;
  $("swimlanes-heading").textContent = t("swimlanes");
  $("sessions-heading").textContent = t("sessions");
  $("task-list-heading").textContent = t("taskList");
  $("detail-heading").textContent = t("detail");
  $("detail-close").title = `${t("close")} (Esc)`;
  $("lang-toggle").textContent = t("language");
  renderThemeLabel();
}

function applyTheme() {
  const theme = localStorage.getItem("atb-theme") || "auto";
  document.documentElement.dataset.theme = theme;
  renderThemeLabel();
}

function renderThemeLabel() {
  const theme = document.documentElement.dataset.theme || "auto";
  const button = $("theme-toggle");
  if (button) button.textContent = t(`theme.${theme}`);
}

function cycleTheme() {
  const current = document.documentElement.dataset.theme || "auto";
  const next = state.themes[(state.themes.indexOf(current) + 1) % state.themes.length];
  localStorage.setItem("atb-theme", next);
  document.documentElement.dataset.theme = next;
  renderThemeLabel();
}

async function fetchAll() {
  const [overview, tasks, sessions] = await Promise.all([
    fetchJSON("/api/v1/overview"),
    fetchJSON("/api/v1/tasks"),
    fetchJSON("/api/v1/sessions"),
  ]);
  state.overview = overview;
  state.tasks = tasks.tasks;
  state.sessions = sessions;

  // today first (the swimlanes are "what happened today"); when nothing
  // happened today, fall back to the last seven days and label the range
  let events = await fetchJSON(`/api/v1/events?day=${localDate()}`);
  if (!events.events.length) {
    events = await fetchJSON("/api/v1/events?days=7");
  }
  state.events = events.events;
  state.eventsRange = events.range || null;

  renderAll();
  if (state.detail) renderDetail(state.detail.id); // refresh the open panel
}

function startStream() {
  const source = new EventSource("/api/v1/stream");
  source.addEventListener("refresh", () => { fetchAll().catch(showError); });
  source.onerror = () => { /* EventSource retries on its own */ };
}

/* --------------------------------------------------------------- render */

function renderAll() {
  renderSummary();
  renderTaskList();
  renderSwimlanes();
  renderSessions();
  $("status-line").textContent = fmt(t("statusLine"), {
    tasks: state.tasks.length,
    time: new Date().toLocaleTimeString(),
  }) + " · " + t("keyboardHint");
}

function renderSummary() {
  const overview = state.overview;
  if (!overview) return;
  const box = $("summary");
  box.textContent = "";
  const metrics = [
    ["active", overview.active],
    ["awaitingReview", overview.awaiting_review],
    ["mergedToday", overview.merged_today],
  ];
  for (const [key, value] of metrics) {
    const metric = document.createElement("div");
    metric.className = "metric";
    metric.innerHTML =
      `<div class="num">${escapeHTML(String(value))}</div>` +
      `<div class="lbl">${escapeHTML(t(`summary.${key}`))}</div>`;
    box.append(metric);
  }

  const info = overview.upstream || {};
  let stateText;
  if (info.upstream == null) {
    stateText = t("upstreamNoUpstream");
  } else if (info.behind) {
    stateText = fmt(t("upstreamBehind"), info);
  } else if (info.ahead) {
    stateText = fmt(t("upstreamAhead"), info);
  } else {
    stateText = fmt(t("upstreamToDate"), info);
  }
  $("upstream").textContent = fmt(t("upstream"), {
    target: overview.target, state: stateText,
  });
}

function readyCell(row) {
  const span = document.createElement("span");
  if (row.ready === true) {
    span.className = "ready-yes";
    span.textContent = t("readyYes");
  } else if (row.ready === false) {
    span.className = "ready-no";
    const first = (row.blockers || [])[0];
    span.textContent = first
      ? `${t("readyNo")} (${first.code})`
      : t("readyNo");
  } else {
    span.textContent = "—";
  }
  return span;
}

const STATUS_RANK = { review: 0, doing: 1, rework: 1, todo: 2, done: 3, dropped: 4 };

function taskRow(row) {
  const tr = document.createElement("tr");
  tr.tabIndex = 0;
  tr.dataset.id = row.id;
  if (state.selected === row.id) tr.classList.add("selected");

  const idCell = document.createElement("td");
  idCell.textContent = row.id;
  tr.append(idCell);

  const statusCell = document.createElement("td");
  statusCell.className = "status-cell";
  statusCell.append(statusBadge(row.status));
  tr.append(statusCell);

  const values = [
    row.agent || "—",
    row.title,
    row.missing ? "—" : (row.branch || "—"),
    row.ahead == null ? "—" : `+${row.ahead}/-${row.behind}`,
  ];
  for (const value of values) {
    const td = document.createElement("td");
    td.textContent = String(value);
    tr.append(td);
  }

  const ready = document.createElement("td");
  ready.append(readyCell(row));
  tr.append(ready);

  tr.addEventListener("click", () => selectTask(row.id));
  tr.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectTask(row.id);
    }
  });
  return tr;
}

function renderTaskList() {
  const headers = ["id", "status", "agent", "title", "branch", "aheadBehind", "ready"];
  const headRow = $("task-headers");
  headRow.textContent = "";
  for (const key of headers) {
    const th = document.createElement("th");
    th.textContent = t(`taskColumns.${key}`);
    headRow.append(th);
  }

  const body = $("task-rows");
  body.textContent = "";

  const unfinished = state.tasks
    .filter((row) => !["done", "dropped"].includes(row.status))
    .sort((a, b) =>
      (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9) ||
      String(a.id).localeCompare(String(b.id)));
  const finished = state.tasks
    .filter((row) => ["done", "dropped"].includes(row.status))
    .sort((a, b) => String(b.id).localeCompare(String(a.id)));

  for (const row of unfinished) body.append(taskRow(row));

  if (finished.length) {
    const toggle = document.createElement("tr");
    toggle.className = "group-toggle";
    toggle.tabIndex = 0;
    const cell = document.createElement("td");
    cell.colSpan = headers.length;
    cell.textContent =
      (state.showCompleted ? "▾ " : "▸ ") +
      fmt(t("completedToggle"), { count: finished.length });
    toggle.append(cell);
    const flip = () => { state.showCompleted = !state.showCompleted; renderTaskList(); };
    toggle.addEventListener("click", flip);
    toggle.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        flip();
      }
    });
    body.append(toggle);
    if (state.showCompleted) {
      for (const row of finished) body.append(taskRow(row));
    }
  }
}

function selectTask(id) {
  state.selected = id;
  document.querySelectorAll("#task-rows tr[data-id]").forEach((tr) => {
    tr.classList.toggle("selected", tr.dataset.id === id);
  });
  renderDetail(id);
}

async function renderDetail(id) {
  try {
    state.detail = await fetchJSON(`/api/v1/tasks/${encodeURIComponent(id)}`);
  } catch (error) {
    showError(error);
    return;
  }
  const detail = state.detail;
  $("detail-sec").hidden = false;
  $("detail-heading").textContent = `${detail.id} ${detail.title}`;

  const chips = [];
  const scope = detail.scope || { denied: [], outside: [], uncommitted: [] };
  const scopeCount = scope.denied.length + scope.outside.length;
  chips.push([
    "scope", scopeCount ? "bad" : "ok",
    scopeCount ? fmt(t("chips.scopeBad"), { count: scopeCount }) : t("chips.scopeOk"),
  ]);
  const test = detail.test || {};
  if (test.result === "ok") {
    chips.push(["test", "ok", t("chips.testOk")]);
  } else if (test.last_run && test.last_run.exit_code != null) {
    chips.push(["test", "bad", fmt(t("chips.testBad"), { code: test.last_run.exit_code })]);
  } else {
    chips.push(["test", "warn", t("chips.testNone")]);
  }
  const mergeBad = (detail.blockers || []).some(
    (item) => item.code === "merge_conflict"
  );
  chips.push([
    "conflict", mergeBad ? "bad" : "ok",
    mergeBad ? t("chips.conflictBad") : t("chips.conflictOk"),
  ]);
  const uncommitted = scope.uncommitted || [];
  chips.push([
    "dirty", uncommitted.length ? "bad" : "ok",
    uncommitted.length
      ? fmt(t("chips.dirtyBad"), { count: uncommitted.length })
      : t("chips.dirtyOk"),
  ]);
  const totals = (detail.diffstat || {}).totals;
  if (totals) {
    chips.push([
      "diffstat", "",
      fmt(t("chips.diffstatValue"), {
        files: totals.files, ins: totals.insertions, del: totals.deletions,
      }),
    ]);
  }
  const blockers = detail.blockers || [];
  chips.push([
    "blockers", blockers.length ? "bad" : "ok",
    blockers.length
      ? blockers.map((item) => `[${item.code}] ${item.message}`).join("; ")
      : t("chips.none"),
  ]);

  const statusHistory = (detail.activity && detail.activity.status_history) || [];
  $("detail").innerHTML = [
    `<div class="chips">` + chips.map(([key, cls, text]) =>
      `<span class="chip ${cls}"><b>${escapeHTML(t(`chips.${key}`))}</b>${escapeHTML(text)}</span>`
    ).join("") + `</div>`,
    `<h3>${escapeHTML(t("history"))}</h3>`,
    `<ul class="history">` + statusHistory.map((item) =>
      `<li>${escapeHTML(item.time_local || "")} ${escapeHTML(item.text || "")}</li>`
    ).join("") + `</ul>`,
    `<h3>${escapeHTML(t("acceptance"))}</h3>`,
    `<ul class="acceptance">` +
      (detail.acceptance || []).map((item) =>
        `<li><span class="box">${item.checked ? "☑" : "☐"}</span>${escapeHTML(item.text)}</li>`)
        .join("") +
      `</ul>`,
    `<div class="copy-row"><button id="copy-handoff" type="button">${escapeHTML(t("copyHandoff"))}</button> ` +
      `<span id="copy-result" class="muted"></span></div>`,
  ].join("");

  $("copy-handoff").addEventListener("click", copyHandoff);
}

async function copyHandoff() {
  const id = state.detail && state.detail.id;
  if (!id) return;
  const result = $("copy-result");
  try {
    const payload = await fetchJSON(
      `/api/v1/tasks/${encodeURIComponent(id)}/prompt`
    );
    await navigator.clipboard.writeText(payload.text);
    result.textContent = t("copied");
  } catch (error) {
    result.textContent = fmt(t("copyFailed"), { error });
  }
}

function closeDetail() {
  $("detail-sec").hidden = true;
  $("detail").textContent = "";
}

/* -------------------------------------------------------------- swimlanes */

function rangeStart(range) {
  if (range && range.from) {
    const [year, month, day] = range.from.split("-").map(Number);
    return new Date(year, month - 1, day);
  }
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

function rangeEnd(range, start) {
  if (range && range.to) {
    const [year, month, day] = range.to.split("-").map(Number);
    return new Date(year, month - 1, day + 1);
  }
  return new Date(start.getTime() + 86400000);
}

function renderSwimlanes() {
  const box = $("swimlanes");
  box.textContent = "";
  const events = state.events || [];
  const heading = $("swimlanes-heading");
  const range = state.eventsRange;
  const days = range && range.days ? range.days : 1;
  heading.textContent = days > 1 ? t("swimlanes7") : t("swimlanes");

  if (!events.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = t("lanesEmpty");
    box.append(empty);
    return;
  }

  const start = rangeStart(range);
  const end = rangeEnd(range, start);
  const span = Math.max(1, end.getTime() - start.getTime());

  const byAgent = new Map();
  for (const event of events) {
    const ts = event.timestamp ? new Date(event.timestamp) : null;
    if (!ts || Number.isNaN(ts.getTime())) continue;
    if (!byAgent.has(event.agent)) byAgent.set(event.agent, []);
    byAgent.get(event.agent).push({ ...event, ts });
  }

  for (const [agent, agentEvents] of [...byAgent.entries()].sort()) {
    const lane = document.createElement("div");
    lane.className = "lane card-inner";
    const head = document.createElement("div");
    head.className = "lane-head";
    head.innerHTML =
      `<span class="lane-agent">${escapeHTML(agent)}</span>` +
      `<span class="muted">${fmt(t("laneEvents"), { count: agentEvents.length })}</span>`;
    lane.append(head);

    const byTask = new Map();
    for (const event of agentEvents) {
      if (!byTask.has(event.task)) byTask.set(event.task, []);
      byTask.get(event.task).push(event);
    }
    for (const [taskId, taskEvents] of byTask) {
      taskEvents.sort((a, b) => a.ts - b.ts);
      const row = document.createElement("div");
      row.className = "lane-row";
      const label = document.createElement("span");
      label.className = "lane-task";
      label.textContent = taskId;
      label.title = taskEvents[0].title || "";
      const track = document.createElement("div");
      track.className = "lane-track";
      for (let index = 0; index < taskEvents.length; index += 1) {
        const event = taskEvents[index];
        const next = taskEvents[index + 1];
        const from = event.ts;
        const to = next ? next.ts : new Date();
        if (to <= from) continue;
        const left = Math.max(0, (from - start) / span * 100);
        const width = Math.min(100 - left, (to - from) / span * 100);
        if (width <= 0) continue;
        const segment = document.createElement("div");
        segment.className = "seg";
        segment.dataset.kind = event.kind === "status" ? (event.to || "doing") : event.kind;
        const statusIcon = event.kind === "status"
          ? (STATUS_ICON[event.to] || "")
          : "";
        const labelText = event.kind === "status"
          ? `${statusIcon}${t(`summary.${event.to}`)}`
          : t(`eventsShort.${event.kind}`,
              { code: event.exit_code == null ? "?" : event.exit_code });
        if (event.kind === "note") {
          // a small marker only; the note body lives in title/aria-label,
          // never in innerHTML, so no escaping path is involved
          segment.textContent = "✎";
          const detail = `${taskId} ${t("eventsShort.note")} · ${event.by || "—"} · ${localTime(event.timestamp)} · ${event.note || event.text || ""}`;
          segment.title = detail;
          segment.setAttribute("aria-label", detail);
        } else {
          segment.textContent = `${labelText} ${localTime(event.timestamp)}`;
          segment.title = `${taskId} ${event.title || ""} ${event.text || ""}`;
          segment.setAttribute(
            "aria-label",
            `${taskId} ${labelText} ${localTime(event.timestamp)} ${event.title || ""}`
          );
        }
        segment.style.left = `${left}%`;
        segment.style.width = `${Math.max(width, 0.6)}%`;
        track.append(segment);
      }
      row.append(label, track);
      lane.append(row);
    }
    box.append(lane);
  }

  const scale = document.createElement("div");
  scale.className = "lane-scale";
  const startLabel = start.toLocaleDateString(
    undefined, { month: "short", day: "numeric" }
  );
  const endLabel = new Date(end.getTime() - 86400000).toLocaleDateString(
    undefined, { month: "short", day: "numeric" }
  );
  scale.textContent = days > 1
    ? fmt(t("laneRange"), { from: startLabel, to: endLabel, days })
    : fmt(t("laneScale"), {
        start: start.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        end: end.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      });
  box.append(scale);
}

/* --------------------------------------------------------------- sessions */

function sessionGroups() {
  const payload = state.sessions;
  const rows = (payload && payload.sessions) || [];
  const repoRoot = (state.overview && state.overview.repo_root) || "";
  const groups = new Map();
  for (const row of rows) {
    const cwd = row.cwd || "";
    const key = cwd || "?";
    if (!groups.has(key)) {
      groups.set(key, {
        cwd,
        name: cwd ? cwd.split(/[\\/]+/).filter(Boolean).pop() : "",
        current: cwd === repoRoot || (repoRoot && cwd.startsWith(repoRoot + "/")),
        lastActive: 0,
        rows: [],
      });
    }
    const group = groups.get(key);
    group.rows.push(row);
    const stamp = row.last_active_at ? new Date(row.last_active_at).getTime() : 0;
    group.lastActive = Math.max(group.lastActive, stamp || 0);
  }
  return [...groups.values()].sort(
    (a, b) =>
      Number(b.current) - Number(a.current) ||
      b.lastActive - a.lastActive
  );
}

function renderSessions() {
  const section = $("sessions-sec");
  const box = $("sessions");
  box.textContent = "";
  const payload = state.sessions;
  const rows = (payload && payload.sessions) || [];
  if (!rows.length) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  const freeLabel = state.lang === "zh" ? "自由会话" : "free session";

  for (const group of sessionGroups()) {
    const block = document.createElement("div");
    block.className = "session-group";
    const header = document.createElement("div");
    header.className = "session-group-head";
    const title = document.createElement("span");
    title.className = "session-group-name";
    title.textContent = group.name || t("unknownProject");
    if (group.current) {
      const badge = document.createElement("span");
      badge.className = "repo-badge";
      badge.textContent = t("currentProject");
      title.append(" ", badge);
    }
    const path = document.createElement("span");
    path.className = "muted session-path";
    path.textContent = displayCwd(group.cwd);
    path.title = group.cwd || "";
    header.append(title, path);
    block.append(header);

    for (const row of group.rows) {
      const line = document.createElement("div");
      line.className = "session-row";
      const stateClass = `state-${row.state}`;
      line.innerHTML =
        `<span class="session-adapter">${escapeHTML(row.adapter)}</span>` +
        `<span class="session-state ${stateClass}">${escapeHTML(t(`states.${row.state}`))}</span>` +
        `<span class="session-task">${escapeHTML(row.task || freeLabel)}</span>` +
        `<span class="session-model" title="${escapeHTML(row.model || "")}">${escapeHTML(row.model || "—")}</span>` +
        `<span class="session-time">${escapeHTML(localTime(row.last_active_at))}</span>`;
      block.append(line);
    }
    box.append(block);
  }
}

/* --------------------------------------------------------------- keyboard */

function onKeyDown(event) {
  if (event.target && ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
  const rows = [...document.querySelectorAll("#task-rows tr[data-id]")];
  if (!rows.length) return;
  const index = rows.findIndex((tr) => tr.dataset.id === state.selected);
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const next = index < 0 ? 0
      : Math.min(rows.length - 1, Math.max(0, index + (event.key === "ArrowDown" ? 1 : -1)));
    selectTask(rows[next].dataset.id);
    rows[next].focus();
  } else if (event.key === "Enter") {
    if (index >= 0) renderDetail(state.selected);
  } else if (event.key === "Escape") {
    closeDetail();
  } else if (event.key === "c" || event.key === "C") {
    if (!$("detail-sec").hidden) copyHandoff();
  } else if (event.key === "t" || event.key === "T") {
    cycleTheme();
  }
}

function showError(error) {
  $("status-line").innerHTML =
    `<span class="error">${escapeHTML(String(error && error.message || error))}</span>`;
}

/* ------------------------------------------------------------------- main */

async function main() {
  applyUrlOverrides();
  applyTheme();
  await loadTexts();
  $("theme-toggle").addEventListener("click", cycleTheme);
  $("lang-toggle").addEventListener("click", () => {
    localStorage.setItem("atb-lang", state.lang === "zh" ? "en" : "zh");
    loadTexts().then(renderAll).catch(showError);
  });
  $("detail-close").addEventListener("click", closeDetail);
  document.addEventListener("keydown", onKeyDown);
  await fetchAll();
  // ?live=0 turns off the SSE stream (screenshots, static previews)
  const live = new URLSearchParams(location.search).get("live") !== "0";
  if (live) startStream();
}

main().catch(showError);
