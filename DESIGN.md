# agent-taskboard（`atb`）MVP 设计

> 把多个 AI 编程 Agent（Claude Code、Codex、OpenCode、Gemini CLI……）并行干活的流程工程化：
> **写任务单 → 开 worktree → 交给 Agent → 检查越界与冲突 → 审查 → 合并或打回。**
> 原型来自 `tokens` 项目里 Claude 主会话与 OpenCode 之间的真实分工（OPENCODE_TASK*.md）。

## 1. 要解决的问题（都是真实踩过的坑）

| 痛点 | 真实案例 | atb 的做法 |
|---|---|---|
| 交接全靠手写长 prompt，每次都要重复"在哪个 worktree、只能改哪些文件、不要 push" | 任务单 #1–#5 每份都重复写了一遍工作方式 | 任务单模板加 `atb prompt` 一键生成交接文本 |
| Agent 越界修改禁区文件 | 任务单里只能靠文字写"禁止修改 readers.py" | `atb check`：对 diff 做 allow/deny 通配符校验，退出码可以接 CI |
| 两个并行任务改到同一个文件 | 任务 #3 和 #4 都要改 `cli.py`，只能人工发现、人工排队 | `atb conflicts`：两两比较任务的文件重叠，再用 `git merge-tree` 试合并，不碰任何 worktree |
| 基线漂移：任务开工后主线又前进了 | 任务单 #3 的基线提交改过两次 | `atb status` 显示落后主线多少个提交、和主线新改动有没有文件重叠 |
| Agent 自报"测试全过"，实际是因为本机环境碰巧满足 | #4 的测试依赖上海时区，在 UTC 环境就失败 | `atb check --test` 在 worktree 里用配置的命令真跑一遍，并记录结果 |
| 审查标准不统一 | 每次审查都临时决定要看什么 | `atb review` 从任务单的"验收"清单生成审查表 |

## 2. 原则

- **零依赖、纯本地**：Python ≥ 3.11 标准库（用 `tomllib` 解析任务单头部），只通过 `subprocess` 调用 `git`（需要 ≥ 2.38，因为要用 `merge-tree --write-tree`）。
- **不绑定 Agent**：atb 不调用任何模型 API，只生成交接文本、检查结果。Agent 用什么工具都行。
- **安全默认**：永不 push；永不 `reset --hard`；删除分支或 worktree 必须显式加参数；合并只能通过 `atb done --merge` 显式触发，并且要求 `check` 通过（`--force` 可以绕过，但会记录下来）。
- **状态即文件**：所有状态存在仓库根目录的 `.taskboard/` 里，是纯文本，可以提交，也可以 gitignore，由用户自己选。

## 3. 数据模型

```
.taskboard/
  config.toml          # 仓库级配置
  tasks/T001.md        # 任务单：TOML 头部 + Markdown 正文
  runs/T001.jsonl      # 每次 check/test 的结果（追加写入）
```

`config.toml`：
```toml
target = "main"                       # 合并目标分支
worktree_root = "../{repo}-tasks"     # worktree 放在哪里，{repo} 替换为仓库目录名
branch_pattern = "{agent}/{slug}"     # 分支命名规则
test = "python3 -m unittest discover -s tests"   # 默认测试命令，任务单可以覆盖
id_prefix = "T"
```

任务单 `T003.md`：
```markdown
+++
id = "T003"
title = "doctor covers OpenCode"
agent = "opencode"            # 自由文本：opencode / claude / codex / gemini / human
status = "todo"               # todo | doing | review | rework | done | dropped
base = ""                     # start 时自动写入实际的基线提交
branch = ""                   # start 时自动写入
allow = ["src/pkg/doctor.py", "tests/test_doctor*.py"]
deny  = ["src/pkg/readers.py", "CHANGELOG.md"]
depends = ["T001"]            # 依赖的任务完成前，start 会拒绝开工（--force 可以跳过）
test = ""                     # 留空则使用 config.test
created = 2026-11-11T10:00:00Z
+++

## 背景
……
## 要做的事
……
## 验收
- [ ] 新测试覆盖 X
- [ ] 全量测试通过
```

范围规则：
- 用 `fnmatch` 风格的通配符，`**` 匹配多级目录。
- deny 优先于 allow。
- allow 为空表示不限制，但 deny 仍然生效。
- 改名（rename）按新旧两个路径各检查一次。

## 4. 命令（MVP）

| 命令 | 作用 | 期 |
|---|---|---|
| `atb init` | 创建 `.taskboard/` 和默认配置；检查 git 版本 | T01 |
| `atb new "标题" --agent X [--allow G]... [--deny G]... [--depends ID]...` | 按模板生成任务单，打印路径 | T01 |
| `atb list [--status S]` / `atb show ID` | 列出任务 / 查看单个任务 | T01 |
| `atb start ID [--base REV]` | 校验依赖 → `git worktree add` → 把 base/branch 写回任务单 → 状态改为 doing | T02 |
| `atb prompt ID [--lang zh\|en]` | 输出给 Agent 的完整交接文本：worktree 路径、可改与禁改文件、不要 push、回报格式，加上任务单正文 | T02 |
| `atb check ID [--test] [--json]` | 范围校验（已提交的 diff 加未提交的改动）、提交数、diffstat；`--test` 时在 worktree 里跑测试并写入 runs/；失败返回非零 | T02 |
| `atb status` | 看板：ID、标题、Agent、状态、分支、领先/落后提交数、改动文件数、越界数、最近一次测试结果 | T03 |
| `atb conflicts [--json]` | 活跃任务两两比较：文件重叠，再用 `merge-tree` 确认真实冲突；同时对比每个任务和 target 的新改动 | T03 |
| `atb review ID` | 生成审查表 Markdown：check 结果 + 测试结果 + 任务单"验收"里的复选框 + diffstat | T03 |
| `atb rework ID --note "…"` | 状态改为 rework，把审查意见追加到任务单的"返工"小节；`atb prompt` 会带上这一节 | T03 |
| `atb done ID [--merge] [--clean]` | 要求 check 通过 → `merge --no-ff`（提交信息模板可配置）→ 可选删除 worktree | T04 |
| `atb board --html out.html` | 单文件静态看板（暗色优先），适合截图放进 README | T04 |

退出码：0 表示通过；1 表示检查失败（越界、冲突或测试失败）；2 表示用法或环境错误。所有命令支持 `--json`，方便脚本和 CI 调用。

## 5. 代码结构

```
src/agent_taskboard/
  __init__.py  __main__.py
  cli.py        # argparse 子命令，只做参数接线与输出
  config.py     # 读写 config.toml，定位仓库根目录
  sheet.py      # 任务单解析/序列化（TOML 头部 + 正文）、ID 分配、模板
  scope.py      # allow/deny 通配符匹配（纯函数）
  gitops.py     # 对 git 的薄封装：worktree、diff --name-status、rev-list、merge-tree
  checks.py     # check/conflicts/review 的业务逻辑
  render.py     # 文本表格 / JSON / HTML 输出
  prompts.py    # 交接文本模板（中英）
tests/          # unittest；每个测试在临时目录里建真实 git 仓库
```

纯函数（`scope`、`sheet` 解析、冲突配对）与 git 调用分开：纯函数用表驱动测试，git 部分在临时仓库里做端到端测试。

## 6. 任务拆分（交给 OpenCode，按顺序执行，每期一个分支）

- **T01 骨架**：config、sheet、scope、cli（init/new/list/show），加测试。
- **T02 开工与检查**：gitops（worktree/diff）、start、prompt、check（含 `--test`）。
- **T03 看板与冲突**：status、conflicts（merge-tree）、review、rework。
- **T04 收尾与展示**：done、board --html、README 中英文版、示例动图脚本。

每一期都按 atb 自己的规范写任务单，从 T02 起改用 atb 管理 atb 自己的开发（dogfooding）。

## 7. 暂不做

- 不做 Agent 自动调用和守护进程（以后可以做 `atb run --agent opencode`，但 MVP 只生成交接文本）。
- 不做 GitHub PR、Issue 集成。
- 不做多仓库任务。
