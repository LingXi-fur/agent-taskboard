"""Handoff prompt templates (Chinese and English) for ``atb prompt``.

The prompt is self-contained: it gives the agent the worktree location, the
allow/deny scope, the ground rules (never push/merge, run the tests before
committing), the report format and the task body verbatim.  A ``## 返工``
section, when present, is moved to the very top with a prominent marker.
"""

from __future__ import annotations

from .sheet import Sheet


def split_rework(body: str) -> tuple[str, str]:
    """Split ``body`` into ``(rework_section, rest)``.

    The ``## 返工`` section (from that heading up to the next ``## `` heading)
    is separated out so the prompt can place it up front.  Returns
    ``("", body)`` when there is no rework section.
    """
    lines = body.split("\n")
    rework: list[str] = []
    rest: list[str] = []
    in_rework = False
    for line in lines:
        if line == "## 返工" or line.startswith("## 返工 "):
            in_rework = True
        elif line.startswith("## ") and in_rework:
            in_rework = False
        if in_rework:
            rework.append(line)
        else:
            rest.append(line)
    return "\n".join(rework), "\n".join(rest)


def _bullet(items: list[str]) -> str:
    if not items:
        return "（无）"
    return "\n".join(f"- {item}" for item in items)


def _rework_hint(sheet: Sheet, lang: str) -> str:
    """Fixed follow-up hint appended to the rework block while status is rework."""
    if sheet.status != "rework":
        return ""
    if lang == "en":
        return (
            f"After fixing, append commits on the original branch, then run "
            f"`atb check {sheet.id} --test` and report the results."
        )
    return (
        f"在原分支追加提交，完成后运行 `atb check {sheet.id} --test` 并回报结果。"
    )


def render_prompt(
    sheet: Sheet,
    worktree: str,
    branch: str,
    test_cmd: str,
    lang: str = "zh",
) -> str:
    """Render the handoff prompt for ``sheet`` in language ``lang``."""
    rework, body = split_rework(sheet.body)
    if lang == "en":
        return _render_en(sheet, worktree, branch, test_cmd, rework, body)
    return _render_zh(sheet, worktree, branch, test_cmd, rework, body)


def _render_zh(
    sheet: Sheet, worktree: str, branch: str, test_cmd: str,
    rework: str, body: str,
) -> str:
    parts: list[str] = []
    if rework.strip():
        parts.append("## ⚠️⚠️ 返工内容（请优先处理）⚠️⚠️\n")
        parts.append(rework.strip())
        hint = _rework_hint(sheet, "zh")
        if hint:
            parts.append("")
            parts.append(hint)
        parts.append("")

    parts.append(f"# 任务交接：{sheet.id} {sheet.title}")
    parts.append("")
    parts.append("## 工作位置")
    parts.append("")
    parts.append(f"- worktree（绝对路径）：{worktree}")
    parts.append(f"- 分支：{branch}")
    parts.append("")
    parts.append("```bash")
    parts.append(f"cd {worktree}")
    parts.append("```")
    parts.append("")
    parts.append("## 允许修改（allow）")
    parts.append("")
    parts.append(_bullet(sheet.allow))
    parts.append("")
    parts.append("## 禁止修改（deny）")
    parts.append("")
    parts.append(_bullet(sheet.deny))
    parts.append("")
    parts.append("## 规则")
    parts.append("")
    parts.append("- 不要 push，不要合并。")
    parts.append("- 不要修改 allow 范围之外的文件。")
    parts.append(f"- 提交前先运行测试：`{test_cmd}`。")
    parts.append("")
    parts.append("## 回报格式")
    parts.append("")
    parts.append("请回报以下内容：")
    parts.append("- 改了哪些文件")
    parts.append("- 测试结果（通过/失败的用例数）")
    parts.append("- 分支名")
    parts.append("- 遇到的问题")
    parts.append("")
    parts.append("## 任务正文")
    parts.append("")
    parts.append(body)
    return "\n".join(parts)


def _render_en(
    sheet: Sheet, worktree: str, branch: str, test_cmd: str,
    rework: str, body: str,
) -> str:
    parts: list[str] = []
    if rework.strip():
        parts.append("## ⚠️⚠️ REWORK (handle first) ⚠️⚠️\n")
        parts.append(rework.strip())
        hint = _rework_hint(sheet, "en")
        if hint:
            parts.append("")
            parts.append(hint)
        parts.append("")

    parts.append(f"# Task handoff: {sheet.id} {sheet.title}")
    parts.append("")
    parts.append("## Worktree")
    parts.append("")
    parts.append(f"- worktree (absolute path): {worktree}")
    parts.append(f"- branch: {branch}")
    parts.append("")
    parts.append("```bash")
    parts.append(f"cd {worktree}")
    parts.append("```")
    parts.append("")
    parts.append("## Allowed (allow)")
    parts.append("")
    parts.append(_bullet(sheet.allow))
    parts.append("")
    parts.append("## Forbidden (deny)")
    parts.append("")
    parts.append(_bullet(sheet.deny))
    parts.append("")
    parts.append("## Rules")
    parts.append("")
    parts.append("- Do NOT push. Do NOT merge.")
    parts.append("- Do NOT modify files outside the allow list.")
    parts.append(f"- Run the tests before committing: `{test_cmd}`.")
    parts.append("")
    parts.append("## Report format")
    parts.append("")
    parts.append("Report the following:")
    parts.append("- which files you changed")
    parts.append("- test results (passed/failed counts)")
    parts.append("- the branch name")
    parts.append("- any problems you hit")
    parts.append("")
    parts.append("## Task body")
    parts.append("")
    parts.append(body)
    return "\n".join(parts)
