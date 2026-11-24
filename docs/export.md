# `atb export --json` — stable read-only export (schema 1)

`atb export --json` prints one JSON object on stdout for other tools (for
example a cost dashboard) to consume via a subprocess. It is **read-only**:
it never writes an agent session store and never changes `.taskboard/`.

```bash
atb export --json > export.json
atb export --json --since 2026-11-01          # tasks active on/after a date
atb export --json --max-days 30               # bound the session scan
```

Without `--json` the command prints a usage line and exits with code 2 —
JSON is the only output format, and stdout contains nothing else.

## Stability contract

- `schema` identifies the payload shape. **Within one schema version fields
  are only added** — never removed, renamed, or given a different meaning.
- A breaking change bumps `schema` to the next integer. Consumers should
  refuse a schema they do not know.
- Unknown extra fields must be ignored by consumers.
- The file stays UTF-8 JSON; no HTML, no NDJSON.

## Fields

| Field | Type | Meaning |
|---|---|---|
| `schema` | int | Export format version (currently `1`). |
| `generated` | ISO-8601 string (UTC) | When the export was produced. |
| `repo` | string | Absolute path of the **main** worktree (even when run from a linked task worktree). |
| `target` | string | Target branch from `config.toml`. |
| `tasks` | array | One object per task, ordered by id. |
| `tasks[].id` | string | Task id, e.g. `T036`. |
| `tasks[].title` | string | Sheet title. |
| `tasks[].agent` | string | Agent/window from the sheet. |
| `tasks[].status` | string | `todo`, `doing`, `review`, `rework`, `done`, `dropped`. |
| `tasks[].branch` | string \| null | Task branch, `null` when never started. |
| `tasks[].created` | ISO-8601 string (UTC) \| null | Sheet creation time. |
| `tasks[].done_at` | ISO-8601 string (UTC) \| null | When the sheet last moved to `done`. |
| `tasks[].merged` | bool | A merge commit for this task exists on the target. |
| `tasks[].merge_commit` | string \| null | That merge commit's full sha. |
| `tasks[].rework_count` | int | Number of rework notes in the sheet. |
| `tasks[].sessions` | array | Agent sessions mapped to this task (worktree cwd first, branch second). |
| `tasks[].sessions[].adapter` | string | `claude-code`, `codex`, `gemini-cli`, `opencode`, or a plugin adapter name. |
| `tasks[].sessions[].id` | string | The tool's **native** session id (Claude Code `sessionId`, Codex rollout session id, Gemini `sessionId`, OpenCode session id) — use it to join your own data. |
| `tasks[].sessions[].started_at` | ISO-8601 string (UTC) \| null | Session start. |
| `tasks[].sessions[].last_active_at` | ISO-8601 string (UTC) \| null | Last activity. |
| `tasks[].usage` | object \| null | Token totals of the mapped sessions, `null` when no session recorded tokens. Keys are only present when recorded: `input`, `output`, `cache_read`, `cache_write`, `total`. Tokens only — atb has no price table. |

## Windows and performance

- Like every atb command, `export` runs on Windows, macOS and Linux; paths
  in the payload are absolute paths of the machine that produced it.
- Sessions are scanned from all history by default. On a machine with a
  large Claude Code store this is the slow part (seconds); `--max-days N`
  bounds the scan to sessions active in the last N days.
- Session metadata is read through the adapters' bounded head/tail windows;
  usage is resolved only for sessions that map to a task.
