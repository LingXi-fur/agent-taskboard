# Changelog

All notable changes to `atb` are documented in this file.
`atb` 的重要变更记录在此文件中。

## [Unreleased]

### Added / 新增

- `atb done --merge` refuses tasks that are not ready, using the same check as `review --json` (scope, uncommitted changes, tests, conflicts, doctor). `--force` merges anyway and lists the ignored blockers; `require_ready_for_done = false` turns the refusal into a warning. / `atb done --merge` 拒绝合并未就绪的任务，判断与 `review --json` 完全相同（范围、未提交改动、测试、冲突、doctor）。`--force` 仍可合并并列出被忽略的 blocker；`require_ready_for_done = false` 改为只警告。
- `atb stats` summarizes each agent: done, dropped and active tasks, rework rate, median and P90 cycle time, time waiting for review, and test-failure rate; `--since`, `--agent`, `--json`, `--lang`. Tasks without history show “—”. / `atb stats` 按 agent 汇总：完成、放弃、进行中数，返工率，周期中位数和 P90，等待审查时长，测试失败率；支持 `--since`、`--agent`、`--json`、`--lang`。缺少历史的任务显示“—”。
- `atb sync ID` brings a task branch up to date with the target (rebase by default, `--merge` optional, `--dry-run` predicts conflicts). It refuses a dirty worktree, aborts and restores on conflict, never pushes, and reminds you when the branch is already on a remote. `atb status` and `atb prompt` suggest it for tasks that are behind. / `atb sync ID` 把目标分支同步进任务分支（默认 rebase，可选 `--merge`，`--dry-run` 预测冲突）。worktree 有未提交改动时拒绝；有冲突时自动撤回并恢复；从不 push；分支已在远端时会提醒。`atb status` 和 `atb prompt` 会对落后的任务给出建议。
- `atb sessions` lists the agent sessions on this machine, read-only, from Claude Code, Codex, Gemini CLI and OpenCode, and maps each to a task by working directory or branch (others are “free sessions”). Third-party adapters plug in through the `agent_taskboard.adapters` entry point. Message summaries are cut to 120 characters and `--redact` hides them; a broken store marks only its adapter as failed. `atb doctor` reports each adapter. / `atb sessions` 只读地列出本机 Claude Code、Codex、Gemini CLI 和 OpenCode 的 agent 会话，并按工作目录或分支对应到任务（对不上的算“自由会话”）。第三方 adapter 通过 `agent_taskboard.adapters` entry point 接入。消息摘要截断到 120 字，`--redact` 完全隐藏；某个存储损坏只会让对应的 adapter 标记失败。`atb doctor` 会显示每个 adapter 的状态。
- `atb serve` runs a local, read-only live console and JSON API v1 (`/api/v1/...`, documented in `docs/api.md`): task table with unfinished tasks first and done tasks collapsed, today's swimlanes by agent (falling back to the last 7 days), and agent sessions grouped by project with working directory and decoded model names. It answers only local Host headers (127.0.0.1, localhost, [::1] and an explicit `--host`), which blocks DNS rebinding; light, dark and high-contrast themes; English and Chinese. Codex sessions now keep their working directory when the first metadata line is very long, and routed CCR model names (`…-h<hex>`) are decoded in every adapter. / `atb serve` 提供本地只读的实时控制台和 JSON API v1（`/api/v1/...`，见 `docs/api.md`）：任务表把未完成的任务排在前面，已完成的折叠；今日泳道按 agent 分组（当天没有事件时显示最近 7 天）；agent 会话按项目分组，显示工作目录和解码后的模型名。只响应本机 Host（127.0.0.1、localhost、[::1] 和显式指定的 `--host`），可防 DNS rebinding；支持亮色、暗色、高对比主题和中英文。Codex 会话的首行元数据很长时也能读到工作目录；所有 adapter 都会解码 CCR 路由的模型名（`…-h<hex>`）。
- `atb note ID [TEXT|@FILE] [--by NAME] [--json]` appends a short note to a task, or lists its notes. Notes are stored in the main worktree's `.taskboard/notes/` (not tracked by git) even when written from a task worktree, appear as `note` events in `atb log`, the last three in `atb review`, and as markers in the console swimlanes (`/api/v1/events`). / `atb note ID [TEXT|@FILE] [--by NAME] [--json]` 给任务追加一条备注，不带文本时列出备注。即使在任务 worktree 里写，备注也存到主 worktree 的 `.taskboard/notes/`（不进 git）；`atb log` 里显示为 `note` 事件，`atb review` 显示最近 3 条，控制台泳道里显示为标记（`/api/v1/events`）。
- Token usage per task: every session adapter (Claude Code, Codex, Gemini CLI, OpenCode) records `input`, `output`, `cache_read`, `cache_write` and `total` when the tool stores them (missing counters stay absent, never zero; Claude Code streaming duplicates are de-duplicated by message and request id). Sessions mapped to a task are summed and shown in `atb review` (text and `--json`), as average tokens per done task in `atb stats`, and in the console task detail and `/api/v1/tasks/ID`. Token counts only, no prices; sessions are scanned over the last 7 days (`ATB_USAGE_DAYS`). / 按任务统计 token 用量：各会话 adapter（Claude Code、Codex、Gemini CLI、OpenCode）在工具有记录时读取 `input`、`output`、`cache_read`、`cache_write`、`total`（没有记录的字段不写，不填 0；Claude Code 流式写入的重复记录按消息 ID 和请求 ID 去重）。对应到同一任务的会话会累加，显示在 `atb review`（文本和 `--json`）、`atb stats` 的每个已完成任务平均 token，以及控制台任务详情和 `/api/v1/tasks/ID` 中。只统计 token，不换算金额；默认扫描最近 7 天的会话（`ATB_USAGE_DAYS`）。
- `atb prompt` now includes a short "Progress notes" section asking agents to run `atb note ID` when they start, when they are blocked, and before review. `atb serve --observe` starts in any directory (no git repository or `.taskboard/` needed) and shows only local agent sessions and their last-24-hour swimlanes; outside an atb repository `atb serve` falls back to this mode with a hint. Task endpoints return 404 in observe mode; `/api/v1/overview` reports `mode`. / `atb prompt` 新增“进度备注”一节，提醒 agent 在开工、遇到阻塞和提交评审前运行 `atb note ID`。`atb serve --observe` 可在任意目录启动（不需要 git 仓库或 `.taskboard/`），只显示本机 agent 会话及最近 24 小时的泳道；在非 atb 仓库中运行 `atb serve` 会提示并自动进入该模式。观察模式下任务相关接口返回 404，`/api/v1/overview` 返回 `mode`。
- Token budgets per agent: optional `[budgets.<agent>]` tables in `.taskboard/config.toml` with `tokens_per_day` and `tokens_per_week`. `atb stats` shows used, limit and percent with icon and text (near limit from 80%, over budget at 100%, "no data" instead of zero), the console shows a budget panel, and `/api/v1/overview` returns `budgets`. Days and weeks follow the local timezone; weeks start on Monday. `atb doctor` reports invalid entries. / 按 agent 设置 token 预算：在 `.taskboard/config.toml` 中可选配置 `[budgets.<agent>]` 的 `tokens_per_day`、`tokens_per_week`。`atb stats` 显示已用量、上限和百分比，并用图标加文字标注状态（80% 起为接近上限，100% 起为已超出；没有数据时显示“无数据”而不是 0）；控制台显示预算面板，`/api/v1/overview` 返回 `budgets`。按本地时区划分天和周，一周从周一开始；`atb doctor` 会报告无效配置。
- `atb export --json [--since DATE] [--max-days N]`: a stable, schema-versioned, read-only export of tasks (status, merge commit, rework count), their mapped agent sessions with each tool's native session id, and token usage, for other tools to consume. Documented in `docs/export.md`. / `atb export --json [--since DATE] [--max-days N]`：稳定、带 schema 版本号的只读导出，包含任务（状态、合并提交、返工次数）、对应的 agent 会话（各工具原生会话 ID）和 token 用量，供其他工具读取。字段说明见 `docs/export.md`。
- Design document for the local console: `docs/design/console.md`, with a Chinese translation in `docs/design/console.zh-CN.md`. / 本地控制台设计文档：`docs/design/console.md`，中文版见 `docs/design/console.zh-CN.md`。
- Contributor files for the open-source release: `CONTRIBUTING.zh-CN.md`, GitHub issue forms (bug report, feature request, adapter request) and a pull request template. / 面向开源发布的贡献者文件：`CONTRIBUTING.zh-CN.md`，GitHub issue 表单（问题报告、功能建议、新增 agent 适配）和 PR 模板。

### Fixed / 修复

- `done --merge`, `review`, `status` and `board` only treat conflicts with the target branch as blockers. Overlap with another active task is now a warning (`warnings` in `review --json`), so two tasks that touch the same file no longer block each other. / `done --merge`、`review`、`status` 和 `board` 只把与目标分支的冲突当作阻止合并的问题；与其他活跃任务改动重叠改为提示（`review --json` 中的 `warnings`），两个改了同一文件的任务不再互相卡住。
- Console: the 7-day swimlane fallback shows its title and date range instead of a raw key, today's time scale shows the first and last event, narrow segments show only an icon with details on hover, and free sessions no longer repeat a label on every row. / 控制台：7 天泳道回退时显示标题和日期范围，不再显示原始键名；今日时间轴显示第一个和最后一个事件的时间；过窄的事件条只显示图标，详情在悬停提示里；自由会话不再每行重复同一个标签。
- Scope checks (`check`, `review`, `done --merge`, `board`) now diff the task branch from its merge-base with the target instead of the base recorded at `start`, so target commits absorbed by `atb sync` or a fast-forward are no longer reported as outside the allowed scope. Ahead/behind in `check` is now relative to the target. Falls back to the recorded base when the target branch does not exist. / 范围检查（`check`、`review`、`done --merge`、`board`）改为从任务分支与目标分支的 merge-base 开始比较，不再用 `start` 时记录的 base；分支通过 `atb sync` 或快进吸收的目标分支提交不再被误报为越界。`check` 的领先/落后数改为相对目标分支计算。目标分支不存在时退回记录的 base。

## [0.2.0] - YYYY-MM-DD

### Added / 新增

- **Enriched `atb review` and `atb review --json`**: `atb review` gains an “Activity and history” section (uncommitted file count, idle time, rework count, last test run, last 10 status changes, and task-specific `atb doctor` FAILs) plus a top warning when uncommitted files exist. `atb review --json` outputs a structured review (scope, test result, activity, structured per-file diffstat, conflicts, acceptance checklist, `ready`, and `blockers` with stable codes `scope_violation`, `uncommitted`, `test_not_passed`, `merge_conflict`, `doctor_fail`). / **增强 `atb review` 与新增 `atb review --json`**：`atb review` 新增“活动与历史”段（未提交文件数、空闲时长、返工次数、最近一次测试、最近 10 次状态变化及点名该任务的 `atb doctor` FAIL），worktree 有未提交改动时在顶部提示只覆盖已提交部分；`atb review --json` 输出面向工具的结构化审查（范围、测试、活动、逐文件 diffstat、冲突、验收清单、`ready` 及带固定代码的 `blockers`：`scope_violation`、`uncommitted`、`test_not_passed`、`merge_conflict`、`doctor_fail`）。
- **Review readiness in `atb status` and `board --html`**: `atb status` and `board --html` add a `READY` column for tasks in `review` (`✓`, or `✗` with the first blocker code; HTML hover lists all blockers; other statuses show “—”), and `status --json` rows include `ready` and `blockers`. `status`, `board`, and `review --json` share one readiness check; a missing branch or worktree reports `missing_branch`. / **`atb status` 与 `board --html` 新增 `READY` 列**：为 `review` 状态任务显示 `✓` 或 `✗` 加第一个 blocker 代码（HTML 悬停显示全部 blocker；非 `review` 任务显示“—”），`status --json` 每行附带 `ready` 和 `blockers`。`status`、`board` 与 `review --json` 共用同一套就绪判断，分支或 worktree 缺失时报 `missing_branch`。
- **Push workflow and unpushed-commit visibility**: `atb done --merge --push` pushes the target branch after merging (never uses `--force`; a rejected push keeps the merge and explains how to resolve it). Setting `push_after_done = true` in `.taskboard/config.toml` enables it by default, and `--no-push` overrides it. `atb status`, `board --html`, and `atb doctor` (WARN) show how many target commits are unpushed from local refs only. / **合并后推送与未推送提交数**：`atb done --merge --push` 在合并后推送目标分支（从不 `--force`；推送被拒时保留合并并给出处理提示），在 `.taskboard/config.toml` 设置 `push_after_done = true` 可设为默认，`--no-push` 可覆盖。`atb status`、`board --html` 和 `atb doctor`（WARN）仅凭本地引用显示目标分支未推送提交数。
- **Windows support and CI matrix**: CI runs on Windows, macOS, and Ubuntu (Python 3.11–3.13). All subprocess and console I/O uses UTF-8 (Chinese titles no longer fail on cp1252 consoles), `--isolate-home` also redirects `USERPROFILE`/`APPDATA`/`LOCALAPPDATA`, and recorded paths are normalized to `/`. / **支持 Windows 与跨平台 CI**：CI 覆盖 Windows、macOS、Ubuntu（Python 3.11–3.13）。所有子进程与终端输出统一 UTF-8（中文标题在 cp1252 终端不再报错），`--isolate-home` 同时重定向 `USERPROFILE`/`APPDATA`/`LOCALAPPDATA`，记录的路径统一使用 `/`。
- **Documentation refresh**: English and Chinese guides (`docs/guide.md`, `docs/guide.zh-CN.md`) and READMEs cover the full coordinator workflow (`doctor`, `log`, `gc`, `watch`, enriched `review`, `review --json`, `done --merge --push`, and the `READY` column), and document GitHub installation via `pipx install git+https://github.com/LingXi-fur/agent-taskboard`. / **文档补齐**：中英文指南（`docs/guide.md`、`docs/guide.zh-CN.md`）与 README 补全完整协调者工作流（`doctor`、`log`、`gc`、`watch`、增强后的 `review`、`review --json`、`done --merge --push` 与 `READY` 列），并说明通过 `pipx install git+https://github.com/LingXi-fur/agent-taskboard` 从 GitHub 安装。
- **Write and edit task sheets without hand-editing**: `atb new ... --body @FILE` (or `--body -` for stdin) writes the sheet body at creation. `atb edit ID` changes the body, title, agent and allow/deny globs (`--add-allow`, `--remove-allow`, `--add-deny`, `--remove-deny`, repeatable) without touching `id`, `status`, `branch` or `base`; done and dropped tasks are refused. Both accept `--copy-to DIR` to also write `DIR/ATB_TASK_<ID>.md` (the directory must exist). Bodies are normalized on write (one blank line after the header, one trailing newline), and a sheet that would not parse back is never written. / `atb new ... --body @FILE`（或 `--body -` 从 stdin 读）在创建时写入正文。`atb edit ID` 可修改正文、标题、agent 和 allow/deny（`--add-allow`、`--remove-allow`、`--add-deny`、`--remove-deny`，可重复），不改 `id`、`status`、`branch`、`base`；done 和 dropped 任务拒绝编辑。两者都支持 `--copy-to DIR`，额外写一份 `DIR/ATB_TASK_<ID>.md`（目录必须已存在）。写入时规范化正文（头部后一个空行、结尾一个换行），解析不回来的任务单一律不写。

### Changed / 变更

- **PyPI packaging & PEP 639 release readiness** (not yet published): `pyproject.toml` adopts PEP 639 metadata (`license = "MIT"`, `license-files = ["LICENSE"]`, no `License ::` classifier, `setuptools>=77.0`, and project URLs); `MANIFEST.in` limits sdist contents to source, README, and LICENSE; README links use absolute GitHub URLs; `docs/release.md` / `docs/release.zh-CN.md` document manual build, `twine check --strict`, tag-before-upload order, and pre-release checks; `tests/test_packaging.py` and CI (`setuptools>=77.0` + `twine check --strict`) guard the metadata. / **PyPI 打包与 PEP 639 发布准备**（暂未发布）：`pyproject.toml` 采用 PEP 639 元数据（`license = "MIT"`、`license-files = ["LICENSE"]`、移除 `License ::` 分类、`setuptools>=77.0` 及项目链接）；`MANIFEST.in` 限定 sdist 只包含源码、README 和 LICENSE；README 链接改为绝对 GitHub 地址；`docs/release.md` / `docs/release.zh-CN.md` 写明手动构建、`twine check --strict`、先打 tag 再上传顺序及发布前检查清单；`tests/test_packaging.py` 与 CI（`setuptools>=77.0` + `twine check --strict`）守住元数据与产物校验。

### Performance / 性能

- **Batched `git for-each-ref` in `status`, `board`, and `watch`**: Read every branch's tip, commit time, and ahead/behind counts in one `git for-each-ref` call instead of per-task git invocations (falls back to `rev-list` on git < 2.41). With 200 tasks (20 active): 66–74% fewer git processes, `status` ~1.0 s → 0.35 s, `watch` per poll ~0.7 s → 0.19 s (`scripts/bench_status.py` reproduces the benchmark). / **`status`、`board`、`watch` 批量加速**：改为一次 `git for-each-ref` 读取所有分支的提交、时间和领先/落后数，不再按任务多次调用 git（git < 2.41 时回退到 `rev-list`）。200 个任务（20 个活跃）时 git 进程减少 66–74%，`status` 约 1.0 秒 → 0.35 秒，`watch` 每轮约 0.7 秒 → 0.19 秒（`scripts/bench_status.py` 可复现）。

### Fixed / 修复

- **Avoid git optional index locks in agent worktrees**: Every git invocation now sets `GIT_OPTIONAL_LOCKS=0`, preventing `atb status`/`board`/`watch` polling from colliding with an agent's `git commit` (`Unable to create index.lock`, previously seen as flaky `test_branch_rewritten` on CI). / **避免占用 Agent worktree 的 git 可选 index 锁**：所有 git 调用统一设置 `GIT_OPTIONAL_LOCKS=0`，避免 `atb status`/`board`/`watch` 轮询导致 Agent 的 `git commit` 因 `index.lock` 失败（此前在 CI 上表现为偶发的 `test_branch_rewritten` 失败）。

## [0.1.3] - 2026-11-18

### Added / 新增

- **`atb doctor [--json]`** — one-shot read-only health check: git version (≥ 2.38 for `merge-tree --write-tree`), config and target branch, worktree root writability, every task sheet (parse, id/filename, depends), active tasks' branch and worktree, leftovers of `done`/`dropped` tasks (suggests `atb gc`) and orphan worktrees; exits 1 on any FAIL. / 新增 `atb doctor`：一次性只读体检（git 版本、配置与目标分支、worktree 根目录可写、任务单解析/编号/依赖、活跃任务的分支和 worktree、收尾残留、孤儿 worktree），有 FAIL 时退出 1。
- **`atb log ID [--json] [--verbose]`** — one task's history in time order: status changes from the sheet's git history, rework notes, test runs (output only with `--verbose`, last 5 lines) and the merge into the target branch, found even after the task branch was deleted. / 新增 `atb log`：按时间列出单个任务的状态变化、返工、测试运行（`--verbose` 才显示最后 5 行输出）和合并记录，分支删除后也能找到合并。
- `atb board --html FILE --lang zh|en` (default `zh`): every label of the board follows the language, and `<html lang>` matches. / `atb board --html` 新增 `--lang zh|en`（默认中文），看板全部文案和 `<html lang>` 跟随语言。

- `atb watch --stall MINUTES` — alert when a `doing`/`rework` task has been idle for at least MINUTES; tasks already stalled at start are reported immediately, each task at most once per run, and the message shows the actual idle time (`T009 已 10h 无活动（阈值 30 分钟，未提交 46 个文件）`). / `atb watch --stall MINUTES`：`doing`/`rework` 任务空闲达到阈值时告警；启动时已卡住立即报，每个任务每次运行只报一次，提示里显示实际空闲时长。
- `atb watch --activity` — alert when a task's uncommitted file count changes (an agent just started editing). / `atb watch --activity`：任务未提交文件数变化时告警（代表 Agent 开工了）。
- `atb watch --json` emits structured `changes` (`id`/`kind`/`from`/`to`/`text`). Recommended coordinator usage: `atb watch --stall 30 --activity --timeout 3600`. / `atb watch --json` 输出结构化 `changes`；协调者推荐用法 `atb watch --stall 30 --activity --timeout 3600`。
- `atb board --html FILE --stall MINUTES` (default 120): the board gains `Dirty` and `Idle` columns, marks `doing`/`rework` tasks idle at least MINUTES with a visible “可能卡住” tag (not colour alone), and shows the generation time, threshold and stalled count at the top. / `atb board --html` 新增 `Dirty`、`Idle` 两列；`--stall`（默认 120 分钟）把空闲超阈值的 `doing`/`rework` 任务标为“可能卡住”（文字标记，不只靠颜色），页面顶部显示生成时间、阈值和卡住数量。

### Fixed / 修复

- Unreadable task sheets (invalid UTF-8, permission or I/O errors) no longer crash `atb`: single-task commands report the file and reason, listing commands skip the sheet, `atb watch` treats it as a parse failure, `atb doctor` reports it as FAIL, and `atb log` tolerates invalid bytes in a runs file. / 无法读取的任务单（非 UTF-8、权限或 IO 错误）不再让 `atb` 崩溃：单任务命令报出文件和原因，列表类命令跳过，`atb watch` 视为解析失败，`atb doctor` 判为 FAIL，`atb log` 能容忍测试记录里的坏字节。
- `atb watch` no longer crashes or misreports when a task sheet is mid-write (it keeps the last known state, retries, and reports only after 3 consecutive parse failures; a sheet that recovers is not re-reported as a new task), when a branch is deleted (`分支已删除`), or when a branch is rewritten (`分支被改写`). / `atb watch` 在任务单写到一半时保留上次状态并重试，连续 3 次解析失败才报告，恢复后不会误报"新增任务"；分支被删除、被改写时分别如实报告。
- `IDLE` in `atb status` and `atb watch --stall` also counts the worktree's creation, so a task that was just started is not reported idle since its (possibly hours-old) base commit. / `atb status` 的 `IDLE` 和 `atb watch --stall` 把 worktree 创建时间也算作活动，刚开工的任务不会因为基线提交很旧而被判为空闲。
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

[Unreleased]: https://github.com/LingXi-fur/agent-taskboard/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/LingXi-fur/agent-taskboard/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/LingXi-fur/agent-taskboard/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/LingXi-fur/agent-taskboard/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/LingXi-fur/agent-taskboard/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/LingXi-fur/agent-taskboard/releases/tag/v0.1.0
