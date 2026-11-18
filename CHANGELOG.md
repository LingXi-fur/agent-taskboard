# Changelog

All notable changes to `atb` are documented in this file.
`atb` 的重要变更记录在此文件中。

## [Unreleased]

### Added / 新增

- `atb review` gains an “Activity and history” section: uncommitted file count, idle time, rework count, last test run (time, exit code, duration), the last 10 status changes, and any `atb doctor` FAIL that names this task. When the worktree still has uncommitted files, a warning at the top says the review covers committed changes only. / `atb review` 新增“活动与历史”段：未提交文件数、空闲时长、返工次数、最近一次测试（时间、退出码、耗时）、最近 10 次状态变化，以及点名该任务的 `atb doctor` FAIL；worktree 还有未提交文件时，顶部提示本次审查只覆盖已提交部分。
- PyPI readiness (nothing is published): `MANIFEST.in` keeps the sdist to the package, README and LICENSE; `docs/release.md` / `docs/release.zh-CN.md` give the manual build, `twine check` and upload steps; `tests/test_packaging.py` guards the metadata. / PyPI 发布准备（不发布）：`MANIFEST.in` 让 sdist 只含包、README 和 LICENSE；`docs/release.md` / `docs/release.zh-CN.md` 写明手动构建、`twine check` 和上传步骤；`tests/test_packaging.py` 守住元数据。

### Changed / 变更

- Packaging metadata follows PEP 639: `license = "MIT"` with `license-files`, no `License ::` classifier, build requirement `setuptools>=77.0`; adds project URLs (GitHub repository, changelog, issues). README links are absolute so they work on PyPI. / 打包元数据按 PEP 639：`license = "MIT"` 加 `license-files`，去掉 `License ::` 分类，构建依赖改为 `setuptools>=77`；新增项目链接（GitHub 仓库、更新日志、问题）。README 链接改为绝对地址，在 PyPI 上也能打开。

### Fixed / 修复

- atb no longer takes git's optional index lock in agents' worktrees: every git call runs with `GIT_OPTIONAL_LOCKS=0`. Before, `atb status`/`board`/`watch` polling a worktree could make the agent's own `git commit` fail with “Unable to create index.lock” (seen on CI as a flaky `test_branch_rewritten`). / atb 不再在 Agent 的 worktree 里拿 git 的可选 index 锁：所有 git 调用都带 `GIT_OPTIONAL_LOCKS=0`。之前 `atb status`/`board`/`watch` 轮询 worktree 时，可能让 Agent 自己的 `git commit` 因 index.lock 失败（CI 上表现为偶发的 `test_branch_rewritten` 失败）。
- CI builds with `setuptools>=77.0` and runs `twine check --strict` on the built distributions. / CI 改用 `setuptools>=77.0` 构建，并对产物跑 `twine check --strict`。

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
