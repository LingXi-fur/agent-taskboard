# 设计：atb 控制台

Status: accepted (2026-11-19). English version: `console.md`.

## 目标

一个本地页面，回答所有在一台机器上同时运行多个编码 agent 的人的问题：

- 现在有哪些 agent 在跑、分别在哪个仓库、各自在做什么？
- 每个任务产出了什么，能不能安全合并？
- 花了多少成本，下一单该给谁？

……并且让人能随时介入：打回返工、合并，或派发下一单。

## 面向谁

| 场景 | 需要什么 |
|---|---|
| 单人开发者，一次只开一个 agent | 看到会话、成本和审查证据，无需任务单。 |
| 一个人带多个 agent（主要场景） | 任务单、范围检查、可合并门禁、每个 agent 的时间线、按任务计的成本、"下一单给谁"的提示。 |
| 同时维护多个仓库 | 一个控制台聚合整个工作区。 |
| 混合使用多种工具 | Claude Code、Codex、Gemini CLI、OpenCode、Cursor、Aider、Copilot CLI、Antigravity……装了什么就支持什么。 |
| 从别处启动的 agent | 桌面应用、IDE 面板、终端、tmux。控制台观察它们，不需要由它来启动。 |
| 可选的自动化 | 希望 atb 无头启动 agent 的人可以按 agent 自行开启。默认保持手动。 |
| Windows、macOS、Linux | 各平台功能完全一致。 |

## 与现有工具的区别

大多数编排器（Vibe Kanban、Conductor、Claude Squad、Agent Deck、AgentGrid……）自己**启动** agent，页面以并行运行的看板为中心。atb 从另一个方向出发：

1. **观察，不接管。** 只读取各工具在本机的会话存储，按工作目录把会话对应到任务。agent 继续在用户原本的地方运行。
2. **带证据合并。** 范围契约、可合并门禁、测试、CI 和冲突都是一等公民。要回答的问题是"这个合并安全吗？"，而不是"能同时开几个 agent？"。
3. **按任务计成本。** 每个任务、每个 agent 的 token 用量与花费，以及返工带来的额外成本。
4. **预算感知的派发。** 可选的按 agent 预算和任务冲突信息，汇成"下一单给谁"的提示。
5. **一条可读的时间线。** agent 备注、测试运行、CI 结果和审查结论组成每个任务一条时间线。
6. **从第一天起就双语**（中英），零运行时依赖。

## 架构

```
 session stores ──► adapters ──► sessions ─┐
 (~/.claude, ~/.codex, …)                   ├─► model ──► JSON API ──► web UI
 .taskboard/ (tasks, history, events) ─────┘                └──► CLI (status, board, …)
```

### 1. Adapters（只读观察器）

- 包 `agent_taskboard.adapters`。每个工具一个模块，实现：
  ```python
  class Adapter(Protocol):
      name: str                    # "claude-code", "codex", "gemini-cli", "opencode", …
      def available(self) -> bool  # store exists on this machine
      def sessions(self, since: datetime) -> Iterable[Session]
  ```
- `Session`：`adapter`、`id`、`cwd`、`started_at`、`last_active_at`、`state`（`working` / `waiting` / `idle` / `unknown`）、`model`、`last_message`（摘要、可截断、可脱敏）、`usage`（已知时的 input/output/cache token）。
- 内置 adapter：Claude Code、Codex、Gemini CLI、OpenCode。第三方通过 `agent_taskboard.adapters` entry-point 组注册。
- Adapter 只读，绝不写入存储、绝不持有锁（SQLite 以只读方式打开，并在安全的前提下使用 `immutable`），失败即降级：损坏的 adapter 在 `atb doctor` 和页面中显示为 "adapter error"，绝不会弄垮整个页面。
- 存储格式会变化。每个 adapter 都声明它测试过的版本，并配有取自真实脱敏样本的 fixture 测试。

### 2. 会话对应到任务

- 会话的 `cwd` 落在某个任务的 worktree 内，即归属于该任务；否则，当它位于该任务的分支上时也归属于该任务。
- 无法对应的会话显示为"自由会话"（在纯观察模式下依然有用）。
- 多个会话可以对应到同一个任务（例如一个编码 agent 加一个审查 agent）。

### 3. 事件与时间线

- `.taskboard/events/` 下的追加式 JSONL，每个任务一个文件：`note`（来自 `atb note ID "…"`）、`status`、`test`、`ci`、`review`、`merge`。
- `atb prompt` 要求 agent 在里程碑处运行 `atb note`。备注是简短的纯文本。

### 4. 成本与预算

- 用量来自 adapter。价格来自内置价格表，用户可覆盖（`pricing.toml`）。未知模型只显示 token，不显示价格。
- 配置中可选按 agent 预算（`[budgets.opencode] usd = 500`，或按天的 token 数）。没有预算时，面板只显示用量。

### 5. Serve

- `atb serve [--port 8766] [--workspace FILE] [--read-only] [--observe]`。只用标准库（`http.server`），原生 JS 和 CSS，无构建步骤、无 CDN，完全离线可用。
- 默认绑定 `127.0.0.1`。改为其他地址需要显式 `--host`，并打印警告。
- 带版本的 JSON API：`/api/v1/overview`、`/api/v1/tasks`、`/api/v1/tasks/{id}`、`/api/v1/sessions`、`/api/v1/events`。更新通过 Server-Sent Events 推送。该 API 有文档，是其他 UI 的扩展点。
- 操作（rework、merge、push、复制交接文本、dispatch）是 POST 请求，受启动时打印的每次运行专用令牌保护，并经 `Origin` 校验。`--read-only` 会禁用这些操作。
- `--observe`：不需要 `.taskboard/`，只显示会话。
- 工作区：仓库列表（`~/.config/atb/workspace.toml`）聚合在同一个页面上。

### 6. 派发模式（按 agent）

- `manual`（默认）："复制交接文本" 把 `atb prompt` 的输出放到剪贴板。
- `command`（需自行开启）：用户配置的命令模板，例如 `opencode run --auto {prompt_file}`，在任务 worktree 中运行，输出保存在 `.taskboard/runs/` 下。默认关闭，文档中附有关于自动批准的警告。

### 7. UI

- 布局：汇总条 → 按 agent 分泳道的今日时间线 → 任务详情（证据标签、成本、时间线、操作）→ 带"下一单给谁"提示的预算面板。
- 冷色调（青、蓝、灰蓝）。亮色、暗色和高对比三套主题跟随 `prefers-color-scheme`。状态绝不仅靠颜色表达：图标和文字同时出现。
- 支持键盘操作。1280px 和窄屏笔记本均可正常使用。中英文字符串存放在 locale JSON 文件中。

## 隐私与安全

- 一切都不离开本机。唯一可选的网络调用是用 `gh` 查询 CI 状态。
- 会话文本截断显示，默认不展示全文，`--redact` 时完全隐藏消息文本。
- 无遥测。

## 暂不做（现阶段）

- 托管、账号、跨机器的团队协作。
- 取代 agent 自带的 UI 或终端。
- 默认启动 agent。

## 阶段

1. `atb serve`，基于已有的任务模型（状态、就绪情况、CI、diffstat、历史）。泳道来自任务历史。JSON API v1。
2. Adapter 框架，加上四个内置 adapter 和会话→任务对应。纯观察模式。
3. `atb note`、事件与时间线、按任务计成本。
4. 预算、派发提示、操作、工作区与多仓库；最后是自行开启的 `command` 派发。
