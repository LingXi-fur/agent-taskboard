# `atb export --json` —— 稳定的只读导出（schema 1）

`atb export --json` 在 stdout 打印一个 JSON 对象，供其他工具（例如成本面板）
通过子进程读取。它是**只读**的：不写任何 agent 会话存储，也不改 `.taskboard/`。

```bash
atb export --json > export.json
atb export --json --since 2026-11-01          # 只看该日期之后仍有活动的任务
atb export --json --max-days 30               # 限制会话扫描范围
```

不带 `--json` 时只打印一行用法说明、退出码 2——JSON 是唯一输出格式，stdout
里不会混入其他内容。

## 稳定性承诺

- `schema` 标识载荷结构。**同一个 schema 版本内只新增字段**，绝不删除、
  改名或改变字段含义。
- 破坏性修改会把 `schema` 加 1。读取方遇到不认识的 schema 应拒绝处理。
- 读取方必须忽略不认识的额外字段。
- 文件始终是 UTF-8 JSON，不是 HTML，也不是 NDJSON。

## 字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `schema` | int | 导出格式版本（当前为 `1`）。 |
| `generated` | ISO-8601 字符串（UTC） | 生成时间。 |
| `repo` | 字符串 | **主** worktree 的绝对路径（即使命令在 linked 任务 worktree 里运行）。 |
| `target` | 字符串 | `config.toml` 里的目标分支。 |
| `tasks` | 数组 | 每个任务一个对象，按 id 排序。 |
| `tasks[].id` | 字符串 | 任务 id，例如 `T036`。 |
| `tasks[].title` | 字符串 | 任务单标题。 |
| `tasks[].agent` | 字符串 | 任务单里的 agent/window。 |
| `tasks[].status` | 字符串 | `todo`、`doing`、`review`、`rework`、`done`、`dropped`。 |
| `tasks[].branch` | 字符串 \| null | 任务分支；从未开始时为 `null`。 |
| `tasks[].created` | ISO-8601 字符串（UTC）\| null | 任务单创建时间。 |
| `tasks[].done_at` | ISO-8601 字符串（UTC）\| null | 任务单最后一次进入 `done` 的时间。 |
| `tasks[].merged` | bool | 目标分支上存在该任务的合并提交。 |
| `tasks[].merge_commit` | 字符串 \| null | 该合并提交的完整 sha。 |
| `tasks[].rework_count` | int | 任务单里返工备注的条数。 |
| `tasks[].sessions` | 数组 | 对应到该任务的 agent 会话（先按 worktree 目录、再按分支）。 |
| `tasks[].sessions[].adapter` | 字符串 | `claude-code`、`codex`、`gemini-cli`、`opencode`，或插件 adapter 名。 |
| `tasks[].sessions[].id` | 字符串 | 各工具的**原生**会话 ID（Claude Code 的 `sessionId`、Codex rollout 的会话 id、Gemini 的 `sessionId`、OpenCode 的会话 id）——用它和你自己的数据关联。 |
| `tasks[].sessions[].started_at` | ISO-8601 字符串（UTC）\| null | 会话开始时间。 |
| `tasks[].sessions[].last_active_at` | ISO-8601 字符串（UTC）\| null | 最后活动时间。 |
| `tasks[].usage` | 对象 \| null | 对应会话的 token 合计；没有任何会话记录用量时为 `null`。只包含记录到的键：`input`、`output`、`cache_read`、`cache_write`、`total`。只统计 token，atb 没有价格表。 |

## Windows 与性能

- 与所有 atb 命令一样，`export` 支持 Windows、macOS、Linux；载荷里的路径
  是生成导出那台机器上的绝对路径。
- 默认扫描全部历史会话。在 Claude Code 存储很大的机器上，这是最慢的部分
  （数秒）；`--max-days N` 可以把扫描限制为最近 N 天有活动的会话。
- 会话元数据走 adapter 现有的头尾有界读取；usage 只对映射到任务的会话
  按需解析。
