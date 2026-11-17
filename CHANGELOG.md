# Changelog

All notable changes to `atb` are documented in this file.
`atb` 的重要变更记录在此文件中。

## [Unreleased]

### Fixed / 修复

- `atb gc --apply` now reports what actually happened: each task line says `removed worktree and branch`, `removed worktree; branch kept` or `FAILED`, followed by a `removed N, skipped N, failed N` summary; `--json` adds a per-candidate `result` and a `summary` object. Previously the applied run still printed the dry-run wording `ready to remove`. / `atb gc --apply` 现在输出实际结果：每行写明已删除 worktree 和分支、只删 worktree 保留分支或失败，末尾汇总删除/跳过/失败数；`--json` 新增每项的 `result` 和 `summary`。此前执行后仍显示预览用语 `ready to remove`。

## [0.1.2] - 2026-11-16

### Added / 新增

- `atb status` now shows **`DIRTY`** (uncommitted files in the worktree) and **`IDLE`** (time since the latest branch commit or uncommitted-file mtime, e.g. `3m`/`2h`/`1d`); `--json` gains `dirty` and `idle_seconds`. / `atb status` 新增 **`DIRTY`**（worktree 未提交文件数）与 **`IDLE`**（距最近分支提交或未提交文件 mtime 的时长，如 `3m`/`2h`/`1d`）两列；`--json` 新增 `dirty`、`idle_seconds`。
- **`atb watch [--interval S] [--timeout S] [--json]`** — block until a task status changes, a branch gains commits, or a new task sheet appears, then print a one-line-per-change summary and exit (exit 3 on timeout). / 新增 `atb watch`：阻塞等待任务状态变化、分支新提交或新增任务单，逐行打印变化摘要后退出（超时退出码 3）。
- **`atb gc [ID...] [--apply] [--include-unmerged] [--json]`** — list leftover worktrees and branches of `done`/`dropped` tasks and, with `--apply`, remove them. Dry-run by default; dirty worktrees and unmerged branches are skipped, branches are deleted only with `git branch -d`, and `--include-unmerged` removes the clean worktree but always keeps the branch. / 新增 `atb gc`：列出 `done`/`dropped` 任务残留的 worktree 和分支，加 `--apply` 才删除；默认只预览，有改动的 worktree 和未合并分支跳过，分支只用 `git branch -d` 删除，`--include-unmerged` 只删干净的 worktree、始终保留分支。

### Fixed / 修复

- `atb dispatch`: `todo` tasks now tell the agent to run `start` first and then `prompt`; `doing` still only asks for `prompt`. / `atb dispatch`：`todo` 任务的转发句改为"先 `start` 再 `prompt`"，`doing` 仍只提示 `prompt`。

## [0.1.1] - 2026-11-15

### Added / 新增

- **`atb dispatch [--json] [--lang zh|en]`** — print a per-project forwarding checklist (project → `window` or `agent` → task) listing only `todo`/`doing`/`rework` tasks, each with the exact message to send. / 输出转发清单（项目 → `window` 或 `agent` → 任务），只列 `todo`/`doing`/`rework` 任务，并给出"发这句话"。
- **`atb check --test --isolate-home`** — run tests with a throwaway `HOME` (plus `XDG_CACHE_HOME`/`XDG_DATA_HOME`/`XDG_CONFIG_HOME`); files written under it are listed and fail the check. Can be made the default with `isolate_home = true` in `config.toml`. / 用一次性 `HOME`（及 `XDG_*`）运行测试，写入其中的文件会被列出并判为失败；可在 `config.toml` 用 `isolate_home = true` 设为默认。

### Changed / 变更

- `atb review` now accepts `rework` (and `doing`) and moves the task to `review`, so a reworked task no longer needs a `start` round-trip. / `atb review` 现在接受 `rework`（和 `doing`）并改为 `review`，返工任务不再需要先 `start`。
- `atb prompt` appends a fixed "append commits, then run `atb check ID --test` and report" hint to the rework block. / 返工交接文本末尾追加固定提示：在原分支追加提交，完成后运行 `atb check ID --test` 并回报。
- `atb done` now suggests `atb review ID` when the task is still `doing` or `rework`. / 任务仍为 `doing`/`rework` 时，`atb done` 会提示先运行 `atb review ID`。
- Task sheets accept an optional `window = "..."` header used by `dispatch`; old sheets without it still parse unchanged. / 任务单支持可选 `window` 字段供 `dispatch` 分组，旧任务单照常解析。

## [0.1.0] - 2026-11-14

### Added / 新增

- **`atb init`** — initialize `.taskboard/` and a default `config.toml`, and check the git version. / 初始化 `.taskboard/` 与默认 `config.toml`，并检查 git 版本。
- **`atb new "title" --agent X [--allow G] [--deny G] [--depends ID]`** — create a task sheet from the template. / 按模板生成任务单。
- **`atb list [--status S]` / `atb show ID`** — list task sheets or inspect one. / 列出任务单或查看单个任务单。
- **`atb start ID [--base REV] [--force]`** — validate dependencies, create a git worktree, and record `base`/`branch`. / 校验依赖、创建独立 worktree，并写回 `base`/`branch`。
- **`atb prompt ID [--lang zh|en]`** — print the full agent handoff prompt (rework notes first). / 输出完整交接文本（返工内容置顶）。
- **`atb check ID [--test] [--strict] [--json]`** — scope-check committed and uncommitted changes; optionally run tests in the worktree. / 校验已提交与未提交改动的范围，可选在 worktree 中运行测试。
- **`atb status [--json]`** — show the task board. / 展示任务看板。
- **`atb conflicts [--json]`** — detect overlapping files, real `merge-tree` conflicts and baseline drift across active tasks. / 检测活跃任务的文件重叠、`merge-tree` 真实冲突与基线漂移。
- **`atb review ID [--out FILE] [--lang zh|en]`** — generate a Markdown review sheet (Chinese/English). / 生成 Markdown 审查表（中/英）。
- **`atb rework ID --note TEXT`** — send a task back to `rework` and append dated notes (supports `@file`). / 将任务打回 `rework` 并追加带时间戳的意见（支持 `@file`）。
- **`atb done ID [--merge] [--clean] [--force]`** — verify checks, merge with `--no-ff`, and optionally remove the worktree and branch. / 校验检查后以 `--no-ff` 合并，可选清理 worktree 与分支。
- **`atb board --html FILE`** — export a standalone, dark-first static HTML board. / 导出单文件、暗色优先的静态 HTML 看板。
- **`atb --version`** — print the installed version. / 打印当前版本号。

### Notes / 说明

- Zero third-party Python dependencies; requires Python 3.11+ and Git 2.38+. / 零第三方 Python 依赖；需要 Python 3.11+ 与 Git 2.38+。
- `atb` never pushes, never auto-merges, and never force-deletes branches. / `atb` 从不 push、从不自动合并、也不强制删除分支。
