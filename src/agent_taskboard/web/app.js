/* atb console — vanilla JS, no build step, no network beyond localhost. */
"use strict";

const state = {
  lang: null,
  texts: {},
  tasks: [],
  overview: null,
  sessions: null,
  selected: null,
  detail: null,
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
  const label = t(`summary.${status}`);
  const icon = STATUS_ICON[status] || "•";
  const span = document.createElement("span");
  span.className = `badge ${status}`;
  span.textContent = `${icon} ${label}`;
  return span;
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

/* ------------------------------------------------------------------ boot */

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
  document.body.dataset.theme = theme;
  renderThemeLabel();
}

function renderThemeLabel() {
  const theme = document.body.dataset.theme || "auto";
  const button = $("theme-toggle");
  if (button) button.textContent = t(`theme.${theme}`);
}

function cycleTheme() {
  const current = document.body.dataset.theme || "auto";
  const next = state.themes[(state.themes.indexOf(current) + 1) % state.themes.length];
  localStorage.setItem("atb-theme", next);
  document.body.dataset.theme = next;
  renderThemeLabel();
}

async function fetchAll() {
  const day = new Date().toISOString().slice(0, 10);
  const [overview, tasks, events, sessions] = await Promise.all([
    fetchJSON("/api/v1/overview"),
    fetchJSON("/api/v1/tasks"),
    fetchJSON(`/api/v1/events?day=${day}`),
    fetchJSON("/api/v1/sessions"),
  ]);
  state.overview = overview;
  state.tasks = tasks.tasks;
  state.events = events.events;
  state.sessions = sessions;
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
    const num = document.createElement("div");
    num.className = "num";
    num.textContent = String(value);
    const label = document.createElement("div");
    label.className = "lbl";
    label.textContent = t(`summary.${key}`);
    metric.append(num, label);
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
  for (const row of state.tasks) {
    const tr = document.createElement("tr");
    tr.tabIndex = 0;
    tr.dataset.id = row.id;
    if (state.selected === row.id) tr.classList.add("selected");

    const cells = [
      row.id,
      null, // badge below
      row.agent || "—",
      row.title,
      row.missing ? "—" : (row.branch || "—"),
      row.ahead == null ? "—" : `+${row.ahead}/-${row.behind}`,
      null, // ready cell
    ];
    cells.forEach((value, index) => {
      const td = document.createElement("td");
      if (index === 1) {
        td.append(statusBadge(row.status));
      } else if (index === 6) {
        td.append(readyCell(row));
      } else {
        td.textContent = String(value);
      }
      tr.append(td);
    });

    tr.addEventListener("click", () => selectTask(row.id));
    tr.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectTask(row.id);
      }
    });
    body.append(tr);
  }
}

function selectTask(id) {
  state.selected = id;
  document.querySelectorAll("#task-rows tr").forEach((tr) => {
    tr.classList.toggle("selected", tr.dataset.id === id);
  });
  renderDetail(id);
}

async function renderDetail(id) {
  try {
    const detail = await fetchJSON(`/api/v1/tasks/${encodeURIComponent(id)}`);
    state.detail = detail;
  } catch (error) {
    showError(error);
    return;
  }
  const detail = state.detail;
  const section = $("detail-sec");
  section.hidden = false;
  $("detail-heading").textContent = `${detail.id} ${detail.title}`;

  const chips = [];
  const scopeBad = (detail.scope && (detail.scope.denied.length || detail.scope.outside.length));
  chips.push([
    "scope",
    scopeBad ? "bad" : "ok",
    scopeBad
      ? fmt(t("chips.scopeBad"), {
          count: detail.scope.denied.length + detail.scope.outside.length,
        })
      : t("chips.scopeOk"),
  ]);
  const uncommitted = (detail.scope && detail.scope.uncommitted) || [];
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
    "conflict",
    mergeBad ? "bad" : "ok",
    mergeBad ? t("chips.conflictBad") : t("chips.conflictOk"),
  ]);
  chips.push([
    "dirty",
    uncommitted.length ? "bad" : "ok",
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
    "blockers",
    blockers.length ? "bad" : "ok",
    blockers.length
      ? blockers.map((item) => `[${item.code}] ${item.message}`).join("; ")
      : t("chips.none"),
  ]);

  const statusHistory = (detail.activity && detail.activity.status_history) || [];
  const rowsHTML = [
    `<div class="chips">` + chips.map(([key, cls, text]) =>
      `<span class="chip ${cls}"><b>${escapeHTML(t(`chips.${key}`))}</b>${escapeHTML(text)}</span>`
    ).join("") + `</div>`,
    `<h3>${escapeHTML(t("history"))}</h3>`,
    `<ul class="history">` + statusHistory.map((item) =>
      `<li>${escapeHTML(item.time_local || "")} ${escapeHTML(item.text || "")}</li>`
    ).join("") + `</ul>`,
    `<h3>${escapeHTML(t("acceptance"))}</h3>`,
    `<ul class="acceptance">` +
      (detail.acceptance || [])
        .map((item) => `<li><span class="box">${item.checked ? "☑" : "☐"}</span>${escapeHTML(item.text)}</li>`)
        .join("") +
      `</ul>`,
    `<div class="copy-row"><button id="copy-handoff" type="button">${escapeHTML(t("copyHandoff"))}</button> ` +
      `<span id="copy-result"></span></div>`,
  ].join("");

  $("detail").innerHTML = rowsHTML;
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

function renderSwimlanes() {
  const box = $("swimlanes");
  box.textContent = "";
  const events = state.events || [];
  if (!events.length) {
    const empty = document.createElement("p");
    empty.className = "lane-sub";
    empty.textContent = t("lanesEmpty");
    box.append(empty);
    return;
  }

  const now = new Date();
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const dayEnd = new Date(midnight.getTime() + 86400000);
  const span = 86400000;

  const byAgent = new Map();
  for (const event of events) {
    const ts = event.timestamp ? new Date(event.timestamp) : null;
    if (!ts || Number.isNaN(ts.getTime())) continue;
    if (!byAgent.has(event.agent)) byAgent.set(event.agent, []);
    byAgent.get(event.agent).push({ ...event, ts });
  }

  for (const [agent, agentEvents] of [...byAgent.entries()].sort()) {
    const lane = document.createElement("div");
    lane.className = "lane";
    const head = document.createElement("div");
    head.className = "lane-head";
    const name = document.createElement("span");
    name.className = "lane-agent";
    name.textContent = agent;
    const sub = document.createElement("span");
    sub.className = "lane-sub";
    sub.textContent = `${agentEvents.length} event(s)`;
    head.append(name, sub);
    lane.append(head);

    // one mini track per task: overlapping tasks never collide
    const byTask = new Map();
    for (const event of agentEvents) {
      if (!byTask.has(event.task)) byTask.set(event.task, []);
      byTask.get(event.task).push(event);
    }
    for (const [taskId, taskEvents] of byTask) {
      taskEvents.sort((a, b) => a.ts - b.ts);
      const track = document.createElement("div");
      track.className = "lane-track";
      track.title = `${taskId} ${taskEvents[0].title || ""}`;
      for (let index = 0; index < taskEvents.length; index += 1) {
        const event = taskEvents[index];
        const next = taskEvents[index + 1];
        const start = event.ts;
        const end = next ? next.ts : now;
        if (end <= start) continue;
        const left = Math.max(0, (start - midnight) / span * 100);
        const width = Math.min(100 - left, (end - start) / span * 100);
        if (width <= 0) continue;
        const segment = document.createElement("div");
        segment.className = "seg";
        segment.dataset.kind = event.kind === "status" ? (event.to || "doing") : event.kind;
        const label = event.kind === "status"
          ? `${STATUS_ICON[event.to] || ""}${t(`summary.${event.to}`)}`
          : t(`eventsShort.${event.kind}`,
              { code: event.exit_code == null ? "?" : event.exit_code });
        segment.textContent = `${taskId} ${label} ${localTime(event.timestamp)}`;
        segment.style.left = `${left}%`;
        segment.style.width = `${Math.max(width, 0.6)}%`;
        track.append(segment);
      }
      lane.append(track);
    }
    box.append(lane);
  }

  const scale = document.createElement("div");
  scale.className = "lane-scale";
  scale.append(
    document.createTextNode(fmt(t("laneScale"), {
      start: midnight.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      end: dayEnd.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    }))
  );
  box.append(scale);
}

/* --------------------------------------------------------------- sessions */

function renderSessions() {
  const payload = state.sessions;
  const section = $("sessions-sec");
  const box = $("sessions");
  box.textContent = "";
  const rows = (payload && payload.sessions) || [];
  if (!rows.length) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  const freeLabel = state.lang === "zh" ? "自由会话" : "free session";
  for (const row of rows) {
    const line = document.createElement("div");
    line.className = "session-row";
    const cells = [
      row.adapter,
      t(`states.${row.state}`),
      row.task || freeLabel,
      `${row.model || "—"} · ${localTime(row.last_active_at)}`,
    ];
    for (const cell of cells) {
      const span = document.createElement("span");
      span.textContent = cell;
      if (cell === cells[3]) span.className = "model";
      line.append(span);
    }
    box.append(line);
  }
}

/* --------------------------------------------------------------- keyboard */

function onKeyDown(event) {
  if (event.target && ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
  const rows = [...document.querySelectorAll("#task-rows tr")];
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
  startStream();
}

main().catch(showError);
