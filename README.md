# agent-taskboard (`atb`)

> Status: pre-alpha, under construction. See [DESIGN.md](DESIGN.md).

Run several AI coding agents (Claude Code, Codex, OpenCode, Gemini CLI, …) in parallel without losing control:
write a task sheet, hand it off in its own git worktree, check that the agent stayed in scope,
catch cross-task conflicts before merging, and review against the sheet's acceptance list.

Zero dependencies · local-only · never pushes.

---

多个 AI 编程 Agent 并行干活时的任务看板：任务单 → 独立 worktree 交接 → 越界检查 → 跨任务冲突检测 → 按验收清单审查。零依赖、纯本地、从不 push。
