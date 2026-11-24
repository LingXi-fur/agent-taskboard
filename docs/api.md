# The `atb serve` JSON API (v1)

`atb serve` (default <http://127.0.0.1:8766>) exposes a small versioned JSON
API. It is read-only, standard-library-only, and reuses the exact payload
builders of `atb status`, `atb review --json`, `atb log` and `atb sessions`:
what the CLI prints is what the API returns. The web UI under `/` is a client
of this API and the extension point for other tools.

All timestamps are ISO-8601 with offset (`...+00:00`); `time_local` fields are
rendered in the server's local timezone. Every response is `application/json;
charset=utf-8`.

## Conventions

- Unknown routes return `404` with `{"error": "..."}`; unknown task ids too.
- Unexpected failures return `500` with `{"error": "..."}` and never kill the
  server.
- Every request's `Host` header must be one of `127.0.0.1:<port>`,
  `localhost:<port>`, `[::1]:<port>` or the address given with `--host`;
  anything else — including a DNS name that resolves to 127.0.0.1 (DNS
  rebinding) — is rejected with `403` before any data is read. Requests
  carrying a non-local `Origin` header are rejected the same way. Requests
  without `Origin` (curl, tests) are fine.
- The server binds to `127.0.0.1` unless `--host` says otherwise; any
  non-loopback `--host` prints a warning.

## Observe mode

`atb serve --observe` (or `atb serve` outside an atb repository, which falls
back automatically with a printed hint) serves **sessions only**: no git
repository and no `.taskboard/` are needed.

- `GET /api/v1/overview` returns `{"mode": "observe", "repo_root": null,
  "budgets": [], "generated_at"}` (task mode returns `"mode": "task"`).
- `GET /api/v1/sessions` behaves exactly as in task mode; sessions are all
  unmapped because there are no task worktrees to match.
- Every task endpoint (`/api/v1/tasks`, `/api/v1/tasks/{id}`,
  `/api/v1/tasks/{id}/prompt`, `/api/v1/events`) returns `404` with
  `{"error": "task endpoints are unavailable in observe mode ..."}`.
- `GET /api/v1/stream` keeps sending `refresh` events; in observe mode the
  page refreshes once per poll interval (no repository state to hash).
- The Host allow-list and Origin checks are unchanged.

## `GET /api/v1/overview`

The summary strip: `{"mode", "target", "repo_root", "upstream", "summary",
"active", "awaiting_review", "merged_today", "budgets", "generated_at"}`.

- `target` — the configured target branch (`target` in `config.toml`).
- `repo_root` — the repository being served; the UI uses it to put this
  project's sessions first.
- `upstream` — `{target, upstream, ahead, behind}` from local refs only
  (`upstream` is `null` without a configured upstream; nothing is fetched).
- `summary` — task count per status: `todo`, `doing`, `review`, `rework`,
  `done`, `dropped`.
- `active` — `doing + review + rework`; `awaiting_review` — `review`.
- `merged_today` — commits on the target whose subject matches the merge
  message pattern (`Merge <branch> (<id>: <title>)`), since local midnight.
- `budgets` — one entry per agent with a `[budgets.<agent>]` table in
  `config.toml` (empty list when none): `{agent, tokens_per_day,
  tokens_per_week, used_today, used_week, day_ratio, week_ratio,
  day_status, week_status, status}`. Usage is attributed to the local
  calendar day/week (Monday start) of each session's last activity;
  `used_*` is `null` when no session in that window recorded tokens, and
  `status` is one of `ok`, `near` (>= 80%), `over` (>= 100%), `no_data`.
  Tokens only — atb does not price anything.

## `GET /api/v1/tasks`

`{"tasks": [...], "summary": {...}, "upstream": {...}, "generated_at"}` —
one row per task, the same shape `atb status --json` uses: `id`, `status`,
`agent`, `title`, `branch`, `missing`, `ahead`, `behind`, `changed_files`,
`violations`, `dirty`, `idle_seconds`, `last_test`, `ready` (`true`/`false`/
`null` outside review), `blockers` (`[{code, message}]`, stable codes:
`missing_branch`, `scope_violation`, `uncommitted`, `test_not_passed`,
`merge_conflict`, `doctor_fail`), plus `status_history` for review tasks.

## `GET /api/v1/tasks/{id}`

The `atb review --json` payload, plus the full event list:

`{id, title, agent, status, branch, base, scope{denied, outside,
uncommitted}, test{result, last_run}, activity{dirty, idle_seconds,
rework_count, status_history}, doctor_fails, diffstat{files, totals},
conflicts, mergeable, drift, acceptance[{text, checked}], notes, usage,
ready, blockers, warnings, events}`. `usage` is the token usage of the
agent sessions mapped to this task: `{sessions, with_usage, missing}` plus
any of `input`, `output`, `cache_read`, `cache_write`, `total` that were
recorded (tokens only — atb does not price them). `blockers` are the reasons a merge is
refused (only target-branch conflicts do that); `warnings` are advisory —
overlaps with other active tasks (`code: task_conflict`) that do not stop
the merge.

`events` is the chronological `atb log` event list (`status`, `test`,
`rework`, `merge`), each `{kind, timestamp, time_local, text, ...}`.

## `GET /api/v1/tasks/{id}/prompt`

`{id, text}` — the English handoff prompt for the task (what `atb prompt
--lang en` prints). This is what the UI's "copy handoff" button puts on the
clipboard. No task state is changed.

## `GET /api/v1/events?day=YYYY-MM-DD&days=N&task=ID`

The day's timeline for the swimlanes: `{"events": [{task, title, agent,
kind, timestamp, time_local, text, from, to, exit_code, commit, by, note}],
"count", "range"}`. `kind` is `status`, `test`, `rework`, `merge` or
`note`; `note` events carry the author in `by` and the full text in
`note`. `kind` is `status`, `test`, `rework` or `merge`. With `day`,
events are filtered to the server's local calendar day; with `days=N`, to
the last N local days (`range` says which). With `task`, to one task.
Without any window, all events are returned (this walks each task's
history, so prefer `day`/`days` for polling).

## `GET /api/v1/sessions?since=SPEC&adapter=NAME`

Exactly what `atb sessions --json` prints: `{"since", "since_spec",
"adapters", "sessions", "unmapped"}`. Sessions come from the read-only
adapters (Claude Code, Codex, Gemini CLI, OpenCode, plus plugins registered
under the `agent_taskboard.adapters` entry-point group) and map to tasks by
worktree cwd first, branch second; unmapped sessions are free sessions. See
`atb sessions --help` for `--since` syntax (`6h`, `3d`, a date, ISO). The UI
shows message summaries truncated; redaction is a display choice made by the
client, the API stays faithful to the store.

## `GET /api/v1/stream`

Server-sent events. The server re-checks `.taskboard/` (file mtimes/sizes)
and the target's `HEAD` every `--poll` seconds (default 5) and emits:

```
event: refresh
data: {"type": "refresh", "checksum": "…"}
```

whenever anything changes (also once immediately on connect). Clients refetch
the endpoints they care about; `EventSource` reconnects by itself.
