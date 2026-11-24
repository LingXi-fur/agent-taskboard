# agent-taskboard (`atb`)

> [English](https://github.com/LingXi-fur/agent-taskboard/blob/main/README.md) · [协作指南](https://github.com/LingXi-fur/agent-taskboard/blob/main/docs/guide.zh-CN.md) · 状态：pre-alpha，持续开发中。设计文档见 [DESIGN.md](https://github.com/LingXi-fur/agent-taskboard/blob/main/DESIGN.md)。

多个 AI 编程 Agent（Claude Code、Codex、OpenCode、Gemini CLI 等）并行干活时的任务看板：任务单 → 独立 worktree 交接 → 越界与测试检查 → 跨任务冲突检测 → 按验收清单审查。

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

## 为什么需要 `atb`？

- **Agent 改了范围外的文件** → `atb check` 用 `allow` / `deny` 通配符校验已提交和未提交的 diff，越界直接返回非零退出码。
- **两个并行 Agent 改到同一文件** → `atb conflicts` 扫描活跃任务的文件重叠，并用 `git merge-tree`（不碰任何 worktree）区分真正冲突与可自动合并的重叠。
- **合并前不知道测试到底跑过没** → `atb check --test` 在任务自己的 worktree 里执行测试命令并记录结果；`atb review` 把越界检查、测试结果、diffstat 和验收清单汇总成一份审查表。
- **返工意见散落在聊天记录里** → `atb rework` 把审查意见追加写入任务单的返工小节，下次 `atb prompt` 会把返工内容放在最前面。

## 安装

需要 **Python 3.11+** 和 **Git 2.38+**（用于 `git merge-tree --write-tree`），零第三方 Python 依赖。

通过 `pipx` 直接从 GitHub 安装：

```bash
pipx install git+https://github.com/LingXi-fur/agent-taskboard
```

或者从源码安装（首次需要联网获取构建依赖 `setuptools>=77.0`）：

```bash
git clone https://github.com/LingXi-fur/agent-taskboard.git
cd agent-taskboard
pip install -e .
```

确认安装：

```bash
atb --version
```

如果离线，可以不安装，直接从 `src` 运行：

```bash
PYTHONPATH=src python3 -m agent_taskboard --help
```

用 shell alias 会更顺手：

```bash
alias atb='PYTHONPATH=/path/to/agent-taskboard/src python3 -m agent_taskboard'
```

想在临时目录中完整体验一次端到端流程（不会改动当前仓库）：

```bash
bash examples/walkthrough.sh
```

想预览静态 HTML 看板（支持 `--lang zh|en`，默认 `zh`；以及 `--stall MIN`，默认 `120`），可在任意含任务单的仓库中生成后用浏览器打开：

```bash
atb board --html board.html --lang zh --stall 120
```

然后双击 `board.html`，或在该目录运行 `python3 -m http.server` 后打开提示的地址。
看板是单文件、自包含的（内联 CSS，暗色优先，含 `未提交`/`空闲` 列与卡住高亮），不加载任何外部资源。

## 任务单格式

任务单存放在 `.taskboard/tasks/<ID>.md`，由 TOML 头部（`+++ ... +++`）和 Markdown 正文组成：

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

## 背景
……

## 要做的事
……

## 验收
- [ ] 单元测试通过
```

**范围通配符规则（`allow` / `deny`）：**
- `*` 只匹配单级路径（不跨越 `/`）。
- `**` 可以跨越多级目录。
- `deny` 永远优先于 `allow`。
- `allow` 为空表示不限制允许范围（但 `deny` 仍然生效）。
- 文件重命名会同时检查旧路径和新路径。

## 命令一览

| 命令 | 作用 | 状态 |
|---|---|---|
| `atb init` | 初始化 `.taskboard/` 与默认 `config.toml`，并检查 git 版本 | 已实现 |
| `atb new "标题" --agent X [--allow G] [--deny G] [--depends ID] [--body @FILE\|-] [--copy-to DIR]` | 生成任务单；`--body` 从 UTF-8 文件或 stdin 直接写入正文，`--copy-to DIR` 额外复制一份到 `DIR/ATB_TASK_<ID>.md` | 已实现 |
| `atb edit ID [--body @FILE\|-] [--title T] [--agent A] [--add-allow G] [--remove-allow G] [--add-deny G] [--remove-deny G] [--copy-to DIR]` | 不用手改 TOML 即可替换正文/标题/agent、增删 allow/deny（选项可重复；拒绝编辑 `done`/`dropped`） | 已实现 |
| `atb list [--status S]` / `atb show ID` | 列出任务单 / 查看单个任务单 | 已实现 |
| `atb start ID [--base REV] [--force]` | 校验依赖、创建独立 worktree，并将 `base` 和 `branch` 写回任务单 | 已实现 |
| `atb prompt ID [--lang zh\|en]` | 生成完整交接文本（有返工时返工置顶，后接范围与任务正文） | 已实现 |
| `atb check ID [--test] [--strict] [--isolate-home] [--json]` | 校验已提交与未提交改动的范围，可选在隔离的临时 `HOME` 中运行测试 | 已实现 |
| `atb status [--json]` | 展示任务看板：状态、分支、领先/落后提交、改动文件数、未提交文件数(DIRTY)、越界数、空闲时长(IDLE)、最近测试、`READY`（是否可合并）及上游未推送计数 | 已实现 |
| `atb conflicts [--json]` | 两两对比活跃任务的文件重叠、`merge-tree` 真实冲突与基线漂移 | 已实现 |
| `atb review ID [--out FILE] [--lang zh\|en] [--json]` | 生成 Markdown 或结构化 JSON（含 `ready`/`blockers`）审查表，汇总检查、测试、活动与历史、doctor 提示、diffstat、冲突与验收清单（会把 `doing`/`rework` 改为 `review`） | 已实现 |
| `atb rework ID --note "..."` | 将状态改为 `rework` 并追加意见，使下次 `atb prompt` 优先展示 | 已实现 |
| `atb done ID [--merge] [--clean] [--force] [--push \| --no-push]` | 校验检查通过后执行 `merge --no-ff`；有 blocker（与 `review --json` 同一套判定）时拒绝合并——`--force` 列出并忽略，`require_ready_for_done = false` 只警告；可选清理 worktree/分支；仅在传 `--push` 或开启 `push_after_done = true`（未传 `--no-push`）时推送 | 已实现 |
| `atb sync ID [--rebase \| --merge] [--dry-run]` | 在任务 worktree 内把目标分支同步进任务分支（默认 `--rebase`；脏 worktree 拒绝、冲突自动 abort 复原、`--dry-run` 用 `git merge-tree` 预测） | 已实现 |
| `atb stats [--since YYYY-MM-DD] [--agent NAME] [--json] [--lang zh\|en]` | 按 agent 汇总产出、返工率、周期中位/P90、等待审查中位与最近测试失败率 | 已实现 |
| `atb sessions [--since SPEC] [--adapter NAME] [--redact] [--json] [--lang zh\|en]` | 只读观察本机 Claude Code / Codex / Gemini CLI / OpenCode 会话，按工作目录或分支对应到任务（其他工具经 `agent_taskboard.adapters` 入口点注册） | 已实现 |
| `atb serve [--port 8766] [--host 127.0.0.1] [--poll SECONDS] [--read-only] [--observe]` | 启动本地实时控制台与 JSON API v1（只用标准库、无 CDN、完全离线；见 `docs/api.md`）；`--observe` 只显示本机 agent 会话，可在任意目录启动 | 已实现 |
| `atb gc [ID...] [--apply] [--include-unmerged] [--json]` | 预览或安全清理 `done`/`dropped` 任务遗留的 worktree 与分支（只用 `git branch -d`） | 已实现 |
| `atb doctor [--json]` | 一次性只读环境体检：git 版本、配置、任务单、活跃 worktree、残留与孤儿 worktree | 已实现 |
| `atb log ID [--json] [--verbose]` | 按时间线追溯单个任务的历史：状态变迁、返工记录、测试运行与合并提交 | 已实现 |
| `atb note ID [TEXT] [--by NAME] [--json] [--lang zh\|en]` | 追加一条简短的任务备注（`@FILE` 读取 UTF-8 文件）或列出全部备注；备注会出现在 `atb log` 时间线中，`atb review` 显示最近 3 条 | 已实现 |
| `atb board --html out.html [--stall MIN] [--lang zh\|en]` | 导出单文件静态 HTML 看板（含未提交/空闲/可合并列，悬停显示 blockers，卡住高亮及中英双语，默认 `zh`） | 已实现 |
| `atb dispatch [--json] [--lang zh\|en]` | 输出转发清单（项目 → 工具·窗口/agent → 任务），只列活跃任务 | 已实现 |
| `atb watch [--interval S] [--timeout S] [--stall MIN] [--activity] [--json]` | 阻塞等待任务状态/分支提交/新增任务单变化，或 stall/activity 告警，逐行打印摘要后退出（超时退出码 3） | 已实现 |
| `atb export --json [--since YYYY-MM-DD] [--max-days N]` | 有版本号、只读的 JSON 导出：任务、各工具原生会话 ID 与 token 用量，供其他工具读取；见 `docs/export.md` | 已实现 |

### 监视进度

`atb watch` 适合放在主会话/协调者的终端里：阻塞等待"值得一看"的变化后退出。
加上 `--stall` 可以抓住悄悄停住的 Agent，加 `--activity` 能发现它开始改文件了：

```bash
atb watch --stall 30 --activity --timeout 3600
```

- `--stall MINUTES`：`doing`/`rework` 任务连续 MINUTES 分钟无活动（无新提交且未提交文件 mtime 不新）时告警；开始监视时已超阈值的任务会立即报出，且同一次运行内每个任务只报一次。
- `--activity`：任务未提交文件数变化时告警（如 `T009 未提交改动 0 → 5`），代表 Agent 开工了。
- `--json`：输出结构化的变化列表（`id`、`kind`、`from`、`to`、`text`），`kind` 取 `status`/`commits`/`new_task`/`stall`/`activity`/`branch_deleted`/`branch_rewritten`/`sheet_error`。

## Token 预算（可选）

`.taskboard/config.toml` 可以给每个 agent 设置 token 上限。只统计 token——
`atb` 没有价格表，也不显示金额：

```toml
[budgets.opencode]
tokens_per_day = 50000000
tokens_per_week = 200000000

[budgets.claude-code]
tokens_per_day = 30000000
```

两个键都可选；agent 名用 `atb sessions` 显示的 adapter 名（`claude-code`、
`codex`、`gemini-cli`、`opencode`）。用量按会话最后活动时间归属到本地自然日/
自然周（周一为一周开始）。`atb stats` 会多出"预算（token）"一节（✓ 正常 /
◔ 接近上限（80% 起）/ ✗ 已超出（100% 起）/ ○ 无数据），控制台在任务表下方
显示预算面板，`/api/v1/overview` 返回 `budgets` 字段；配置写错时 `atb doctor`
会报告出来。

## 设计原则

- **纯本地、零依赖**：仅使用 Python 标准库与 `git` 命令行，所有状态以纯文本保存在 `.taskboard/`。
- **默认不 push**：仅在显式运行 `atb done --merge --push`（或在 `.taskboard/config.toml` 配置 `push_after_done = true` 且未传 `--no-push`）时推送目标分支，且永远不使用 `--force`。
- **从不自动合并**：不做隐式合并，也不执行破坏性的 `reset --hard`。
- **不改 Agent 的文件**：不调用任何模型 API，也不修改你所用编程 Agent 的配置文件，只负责生成交接提示词和校验 git worktree。

## 和"直接开多个终端"相比

直接开多个终端分别跑 Agent 也能并行工作，但每次都需要手动建 worktree、重复手写允许/禁止修改的范围、靠肉眼翻 diff 排查越界、等到合并时才撞上冲突，返工意见也容易淹没在各自的聊天记录里。`atb` 把这套流程沉淀为纯文本任务单与可脚本化的 CLI 检查，同时保留你自由选择任何编码 Agent 的灵活性。
