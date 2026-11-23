# agent-taskboard (`atb`)

> [中文文档](https://github.com/LingXi-fur/agent-taskboard/blob/main/README.zh-CN.md) · [Workflow Guide](https://github.com/LingXi-fur/agent-taskboard/blob/main/docs/guide.md) · Status: pre-alpha, under active development. See [DESIGN.md](https://github.com/LingXi-fur/agent-taskboard/blob/main/DESIGN.md).

Run several AI coding agents (Claude Code, Codex, OpenCode, Gemini CLI, ...) in parallel without losing control: write a task sheet, hand it off in its own git worktree, check scope and tests, catch cross-task conflicts before merging, and review against an acceptance checklist.

```console
$ atb new "Update greeting message" --agent claude --allow "src/app.py" --deny "src/core.py"
$ atb start T001 && atb prompt T001 --lang en | head -n 1
started T001: branch=claude/update-greeting-message
# Task handoff: T001 Update greeting message
$ atb check T001
denied:
  - src/core.py
$ atb status && atb review T001 | head -n 1
T001  doing   claude    Update greeting message  claude/update-greeting-message  +1/-0         2      1     —
# 审查：T001 Update greeting message
```

## Why `atb`?

- **Agents edit files outside their scope** -> `atb check` validates committed and uncommitted diffs against `allow` / `deny` globs and exits non-zero on violations.
- **Two parallel agents edit the same file** -> `atb conflicts` detects overlapping files across active tasks and runs `git merge-tree` (without touching any worktree) to separate real merge conflicts from clean auto-merges.
- **You don't know if tests actually ran before merging** -> `atb check --test` runs the configured test command inside the task's worktree and logs the result; `atb review` compiles scope checks, test runs, diffstat, and acceptance checkboxes into one Markdown review sheet.
- **Rework feedback gets lost in chat history** -> `atb rework` appends review notes to the task sheet's rework section, and `atb prompt` puts that rework section at the very top of the next handoff.

## Installation

Requires **Python 3.11+** and **Git 2.38+** (for `git merge-tree --write-tree`). Zero third-party Python dependencies.

Install directly from GitHub with `pipx`:

```bash
pipx install git+https://github.com/LingXi-fur/agent-taskboard
```

Or install from source (needs network access once to fetch the build dependency `setuptools>=77.0`):

```bash
git clone https://github.com/LingXi-fur/agent-taskboard.git
cd agent-taskboard
pip install -e .
```

Confirm the install with:

```bash
atb --version
```

If you are offline, skip the install and run straight from `src`:

```bash
PYTHONPATH=src python3 -m agent_taskboard --help
```

A shell alias keeps that short:

```bash
alias atb='PYTHONPATH=/path/to/agent-taskboard/src python3 -m agent_taskboard'
```

To see a complete end-to-end demo in a temporary git repository (no changes made to this repo):

```bash
bash examples/walkthrough.sh
```

To preview the static HTML board (supports `--lang zh|en`, defaults to `zh`, and `--stall MIN`, defaults to `120`), generate it in any repository that has task sheets and open the file in a browser:

```bash
atb board --html board.html --lang en --stall 120
```

Then double-click `board.html`, or run `python3 -m http.server` in that
directory and open the printed URL. The board is a single self-contained file
(inline CSS, dark-first, shows `Dirty` / `Idle` columns and highlights stalled tasks) and loads no external resources.

## Task Sheet Format

Tasks live in `.taskboard/tasks/<ID>.md` as a TOML front-matter block (`+++ ... +++`) followed by a Markdown body:

```markdown
+++
id = "T003"
title = "Add user profile endpoint"
agent = "opencode"
status = "todo"
base = ""
branch = ""
allow = ["src/api/*.py", "tests/**"]
deny = ["src/api/auth.py", "pyproject.toml"]
depends = ["T001"]
test = ""
created = 2026-11-14T08:00:00Z
+++

## Context
...

## Tasks
...

## Acceptance
- [ ] Unit tests pass
```

**Scope glob rules (`allow` / `deny`):**
- `*` matches within a single path segment (does not cross `/`).
- `**` matches across multiple directory levels.
- `deny` always takes precedence over `allow`.
- An empty `allow` list means no restriction (though `deny` still applies).
- File renames are checked against both the old and new paths.

## Commands

| Command | Description | Status |
|---|---|---|
| `atb init` | Initialize `.taskboard/` and default `config.toml`; check git version | Implemented |
| `atb new "title" --agent X [--allow G] [--deny G] [--depends ID] [--body @FILE\|-] [--copy-to DIR]` | Create a task sheet; `--body` writes the Markdown body from a UTF-8 file or stdin, `--copy-to DIR` also copies the sheet to `DIR/ATB_TASK_<ID>.md` | Implemented |
| `atb edit ID [--body @FILE\|-] [--title T] [--agent A] [--add-allow G] [--remove-allow G] [--add-deny G] [--remove-deny G] [--copy-to DIR]` | Edit a sheet's body/title/agent and add/remove allow/deny globs without hand-editing TOML (`done`/`dropped` refused; repeatable options) | Implemented |
| `atb list [--status S]` / `atb show ID` | List task sheets / inspect a single task sheet | Implemented |
| `atb start ID [--base REV] [--force]` | Validate dependencies, create a git worktree, record `base` & `branch` | Implemented |
| `atb prompt ID [--lang zh\|en]` | Generate a full handoff prompt (rework notes first, then scope & body) | Implemented |
| `atb check ID [--test] [--strict] [--isolate-home] [--json]` | Scope-check committed & uncommitted changes; optionally run tests in a throwaway isolated `HOME` | Implemented |
| `atb status [--json]` | Show board: status, branch, ahead/behind, files, dirty (uncommitted), violations, idle time, last test, `READY` (`✓` / `✗ blocker`), and target upstream status | Implemented |
| `atb conflicts [--json]` | Compare active tasks for file overlap, real `merge-tree` conflicts, and base drift | Implemented |
| `atb review ID [--out FILE] [--lang zh\|en] [--json]` | Generate a Markdown or structured JSON (`ready`/`blockers`) review sheet combining checks, tests, activity & history, doctor hints, diffstat, conflicts, and checklist (flips `doing`/`rework` to `review`) | Implemented |
| `atb rework ID --note "..."` | Set status to `rework` and append notes so `atb prompt` highlights them first | Implemented |
| `atb done ID [--merge] [--clean] [--force] [--push \| --no-push]` | Verify checks pass, merge with `--no-ff` — refusing tasks that are not ready (the same blockers as `review --json`; `--force` lists and ignores them, `require_ready_for_done = false` only warns) — optionally clean up worktree/branch, and push only on `--push` (or `push_after_done = true` unless `--no-push`) | Implemented |
| `atb sync ID [--rebase \| --merge] [--dry-run]` | Bring the target up to date inside the task worktree (default `--rebase`; refuses dirty worktrees, aborts conflicts back to the original state, `--dry-run` predicts them with `git merge-tree`) | Implemented |
| `atb stats [--since YYYY-MM-DD] [--agent NAME] [--json] [--lang zh\|en]` | Per-agent throughput, rework rate, cycle median/P90, review-wait median, and last-test failure rate | Implemented |
| `atb sessions [--since SPEC] [--adapter NAME] [--redact] [--json] [--lang zh\|en]` | Read-only observation of local Claude Code / Codex / Gemini CLI / OpenCode sessions, mapped to tasks by cwd or branch (other tools via the `agent_taskboard.adapters` entry point) | Implemented |
| `atb serve [--port 8766] [--host 127.0.0.1] [--poll SECONDS] [--read-only]` | Local live console and JSON API v1 (stdlib only, no CDN, fully offline; see `docs/api.md`) | Implemented |
| `atb gc [ID...] [--apply] [--include-unmerged] [--json]` | Preview or safely clean up leftover worktrees and branches (`git branch -d` only) for `done`/`dropped` tasks | Implemented |
| `atb doctor [--json]` | One-shot read-only health check: git version, config, task sheets, active worktrees, leftovers, and orphans | Implemented |
| `atb log ID [--json] [--verbose]` | Show chronological per-task history: status changes, rework notes, test runs, and merges | Implemented |
| `atb board --html out.html [--stall MIN] [--lang zh\|en]` | Export a standalone static HTML board with `Dirty`/`Idle`/`Ready` columns (hover shows all blockers), stall highlighting, and zh/en localization (default `zh`) | Implemented |
| `atb dispatch [--json] [--lang zh\|en]` | Print a forwarding checklist (project → window/agent → task) for active tasks | Implemented |
| `atb watch [--interval S] [--timeout S] [--stall MIN] [--activity] [--json]` | Block until a task status/commit/new-sheet change, a stall, or an activity change, then print a summary and exit (exit 3 on timeout) | Implemented |

### Watching for progress

`atb watch` is meant to sit in a coordinator's terminal: it blocks until
something worth looking at happens, then exits. Add `--stall` to catch agents
that quietly stopped, and `--activity` to notice when they start editing:

```bash
atb watch --stall 30 --activity --timeout 3600
```

- `--stall MINUTES` — alert when a `doing`/`rework` task has had no activity
  (no new commit and no recent uncommitted-file mtime) for MINUTES. Tasks that
  are already stalled when the watch starts are reported immediately, and each
  task is reported at most once per run.
- `--activity` — alert when a task's uncommitted file count changes
  (e.g. `T009 未提交改动 0 → 5`), i.e. the agent just started editing.
- `--json` — emit structured changes (`id`, `kind`, `from`, `to`, `text`), with
  `kind` one of `status`/`commits`/`new_task`/`stall`/`activity`/`branch_deleted`/`branch_rewritten`/`sheet_error`.

## Principles

- **Local-only & zero dependencies**: Standard-library Python + `git` CLI. All state lives as plain text under `.taskboard/`.
- **Never pushes by default**: `atb` only pushes the target branch on `atb done --merge --push` (or when `push_after_done = true` in `.taskboard/config.toml`, overrideable with `--no-push`), and never uses `--force`.
- **Never auto-merges**: No implicit merges or destructive `reset --hard`.
- **Agent-agnostic**: `atb` never calls model APIs or modifies your agent tools' files; it only produces handoff prompts and verifies git worktrees.

## Compared to Opening Multiple Terminals

Running several agent sessions in separate terminals works fine for ad-hoc tasks, but you still have to manually create worktrees, repeat scope rules in every prompt, spot out-of-scope edits by eye, discover merge conflicts only when merging, and dig through chat logs for review notes. `atb` turns those manual steps into plain-text task sheets and scriptable CLI checks without getting in the way of whichever coding agent you prefer.
