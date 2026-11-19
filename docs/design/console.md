# Design: the atb console

Status: accepted (2026-11-19). Chinese translation: `console.zh-CN.md` (to be added).

## Goal

One local page that answers, for anyone running several coding agents on one machine:

- Which agents are running right now, in which repository, and what is each one doing?
- What did each task produce, and can it be merged safely?
- What did it cost, and who should get the next task?

…and lets a human step in: send work back, merge, or hand out the next task.

## Who it is for

| Scenario | What they need |
|---|---|
| Solo developer, one agent at a time | See the session, its cost and the review evidence. No task sheets required. |
| One person, many agents (the main case) | Task sheets, scope checks, ready-to-merge gates, a timeline per agent, cost per task, a hint for who gets the next task. |
| Several repositories at once | One console across a workspace of repositories. |
| Mixed tools | Claude Code, Codex, Gemini CLI, OpenCode, Cursor, Aider, Copilot CLI, Antigravity… whatever is installed. |
| Agents launched elsewhere | Desktop apps, IDE panels, terminals, tmux. The console observes them; it does not need to launch them. |
| Optional automation | People who want atb to launch agents headlessly can opt in per agent. The default is manual. |
| Windows, macOS, Linux | Same features everywhere. |

## How it differs from existing tools

Most orchestrators (Vibe Kanban, Conductor, Claude Squad, Agent Deck, AgentGrid, …) **launch** agents themselves and centre on a kanban of parallel runs. atb starts from the other end:

1. **Observe, don't own.** Read each tool's local session store and map sessions to tasks by working directory. Agents keep running wherever the user already runs them.
2. **Merge with evidence.** Scope contracts, ready gates, tests, CI and conflicts are first-class. The question is "is this safe to merge?", not "how many agents can I start?".
3. **Cost per task.** Token usage and money per task, per agent, and the extra cost of rework.
4. **Budget-aware dispatch.** Optional budgets per agent and task conflicts feed a "who should take the next task" hint.
5. **A readable timeline.** Agent notes, test runs, CI results and review verdicts form one timeline per task.
6. **Bilingual from day one** (English and Chinese), with zero runtime dependencies.

## Architecture

```
 session stores ──► adapters ──► sessions ─┐
 (~/.claude, ~/.codex, …)                   ├─► model ──► JSON API ──► web UI
 .taskboard/ (tasks, history, events) ─────┘                └──► CLI (status, board, …)
```

### 1. Adapters (read-only observers)

- Package `agent_taskboard.adapters`. One module per tool, each implementing:
  ```python
  class Adapter(Protocol):
      name: str                    # "claude-code", "codex", "gemini-cli", "opencode", …
      def available(self) -> bool  # store exists on this machine
      def sessions(self, since: datetime) -> Iterable[Session]
  ```
- `Session`: `adapter`, `id`, `cwd`, `started_at`, `last_active_at`, `state` (`working` / `waiting` / `idle` / `unknown`), `model`, `last_message` (summary, truncated, redactable), `usage` (input/output/cache tokens when known).
- Built-ins: Claude Code, Codex, Gemini CLI, OpenCode. Third parties register via the `agent_taskboard.adapters` entry-point group.
- Adapters are read-only, never write to a store, never hold locks (SQLite opened read-only and `immutable` where safe), and fail soft: a broken adapter shows up in `atb doctor` and in the UI as "adapter error". It never breaks the page.
- Store formats change. Each adapter declares the versions it has been tested against and has fixture tests built from real, anonymized samples.

### 2. Mapping sessions to tasks

- A session belongs to a task when its `cwd` is inside the task's worktree; failing that, when it is on the task's branch.
- Unmapped sessions are shown as "free sessions" (still useful in observe-only mode).
- Several sessions may map to one task (for example a coding agent and a reviewer).

### 3. Events and timeline

- Append-only JSONL under `.taskboard/events/`, one file per task: `note` (from `atb note ID "…"`), `status`, `test`, `ci`, `review`, `merge`.
- `atb prompt` asks agents to run `atb note` at milestones. Notes are short, plain text.

### 4. Cost and budgets

- Usage comes from adapters. Prices come from a built-in table, overridable by the user (`pricing.toml`). Unknown models show tokens without a price.
- Optional budgets per agent in config (`[budgets.opencode] usd = 500`, or tokens per day). Without budgets, the panel just shows usage.

### 5. Serve

- `atb serve [--port 8766] [--workspace FILE] [--read-only] [--observe]`. Standard library only (`http.server`), vanilla JS and CSS, no build step, no CDN. Fully offline.
- Binds to `127.0.0.1` by default. Changing it needs an explicit `--host` plus a printed warning.
- Versioned JSON API: `/api/v1/overview`, `/api/v1/tasks`, `/api/v1/tasks/{id}`, `/api/v1/sessions`, `/api/v1/events`. Server-sent events push updates. The API is documented and is the extension point for other UIs.
- Actions (rework, merge, push, copy handoff, dispatch) are POST requests protected by a per-run token printed at startup and checked with `Origin`. `--read-only` disables them.
- `--observe`: no `.taskboard/` needed; shows sessions only.
- Workspace: a list of repositories (`~/.config/atb/workspace.toml`) aggregated on one page.

### 6. Dispatch modes (per agent)

- `manual` (default): "Copy handoff" puts the `atb prompt` text on the clipboard.
- `command` (opt-in): a user-configured command template, for example `opencode run --auto {prompt_file}`, run in the task worktree, with output saved under `.taskboard/runs/`. Off unless configured, with a warning about auto-approval in the docs.

### 7. UI

- Layout: summary strip → per-agent swimlane timeline for today → task inspector (evidence chips, cost, timeline, actions) → budget panel with the dispatch hint.
- Cool palette (teal, blue, slate). Light, dark and high-contrast themes follow `prefers-color-scheme`. Status is never shown by colour alone: icons and text go with it.
- Keyboard navigable. Works at 1280 px and on a narrow laptop. English and Chinese strings live in locale JSON files.

## Privacy and safety

- Nothing leaves the machine. The only optional network calls are `gh` for CI status.
- Session text is truncated, never shown in full by default, and `--redact` hides message text entirely.
- No telemetry.

## Non-goals (for now)

- Hosting, accounts, teams across machines.
- Replacing the agents' own UIs or terminals.
- Running agents by default.

## Phases

1. `atb serve` with the task model that already exists (status, readiness, CI, diffstat, history). Swimlanes come from task history. JSON API v1.
2. Adapter framework, plus the four built-in adapters and session→task mapping. Observe-only mode.
3. `atb note`, events and timeline, cost per task.
4. Budgets, dispatch hint, actions, workspace and multiple repositories; then the opt-in `command` dispatch.
