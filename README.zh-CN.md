# agent-taskboard (`atb`)

> [English](README.md) · 状态：pre-alpha，持续开发中。设计文档见 [DESIGN.md](DESIGN.md)。

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

从源码安装（需要联网一次，用于获取构建依赖 `setuptools>=70.1`）：

```bash
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

想预览静态 HTML 看板，可在任意含任务单的仓库中生成后，用浏览器打开：

```bash
atb board --html board.html
```

然后双击 `board.html`，或在该目录运行 `python3 -m http.server` 后打开提示的地址。
看板是单文件、自包含的（内联 CSS，暗色优先），不加载任何外部资源。

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
| `atb new "标题" --agent X [--allow G] [--deny G] [--depends ID]` | 按模板生成任务单 | 已实现 |
| `atb list [--status S]` / `atb show ID` | 列出任务单 / 查看单个任务单 | 已实现 |
| `atb start ID [--base REV] [--force]` | 校验依赖、创建独立 worktree，并将 `base` 和 `branch` 写回任务单 | 已实现 |
| `atb prompt ID [--lang zh\|en]` | 生成完整交接文本（有返工时返工置顶，后接范围与任务正文） | 已实现 |
| `atb check ID [--test] [--strict] [--json]` | 校验已提交与未提交改动的范围，可选在 worktree 中运行测试 | 已实现 |
| `atb status [--json]` | 展示任务看板：状态、分支、领先/落后提交、改动文件数、越界数、最近测试 | 已实现 |
| `atb conflicts [--json]` | 两两对比活跃任务的文件重叠、`merge-tree` 真实冲突与基线漂移 | 已实现 |
| `atb review ID [--out FILE] [--lang zh\|en]` | 生成汇总检查、测试、diffstat 与验收清单的 Markdown 审查表（中/英） | 已实现 |
| `atb rework ID --note "..."` | 将状态改为 `rework` 并追加意见，使下次 `atb prompt` 优先展示 | 已实现 |
| `atb done ID [--merge] [--clean] [--force]` | 校验检查通过后执行 `merge --no-ff`，可选清理 worktree 与分支 | 已实现 |
| `atb board --html out.html` | 导出单文件静态 HTML 看板 | 已实现 |

## 设计原则

- **纯本地、零依赖**：仅使用 Python 标准库与 `git` 命令行，所有状态以纯文本保存在 `.taskboard/`。
- **从不 push**：`atb` 永远不会执行 `git push`。
- **从不自动合并**：不做隐式合并，也不执行破坏性的 `reset --hard`。
- **不改 Agent 的文件**：不调用任何模型 API，也不修改你所用编程 Agent 的配置文件，只负责生成交接提示词和校验 git worktree。

## 和"直接开多个终端"相比

直接开多个终端分别跑 Agent 也能并行工作，但每次都需要手动建 worktree、重复手写允许/禁止修改的范围、靠肉眼翻 diff 排查越界、等到合并时才撞上冲突，返工意见也容易淹没在各自的聊天记录里。`atb` 把这套流程沉淀为纯文本任务单与可脚本化的 CLI 检查，同时保留你自由选择任何编码 Agent 的灵活性。
