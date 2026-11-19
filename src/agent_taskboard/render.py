"""Pure output rendering: review Markdown (zh/en) and the HTML board.

Everything here is a pure function of already-computed data; no git or file
access happens in this module.  The board is a single self-contained HTML file
with inline CSS, no external resources, and every task field is HTML-escaped.
"""

from __future__ import annotations

import html
import re
from datetime import datetime

# --------------------------------------------------------------------------- #
# Review sheet (Markdown)
# --------------------------------------------------------------------------- #

_TEST_ZH = {"ok": "✓ 通过", "fail": "✗ 失败"}
_TEST_EN = {"ok": "✓ passed", "fail": "✗ failed"}


def _conflict_lines(info: dict, zh: bool) -> list[str]:
    lines: list[str] = []
    if info["conflicts"]:
        for entry in info["conflicts"]:
            other = entry["b"] if entry["a"] == info["id"] else entry["a"]
            label = "真实冲突" if zh else "real conflict"
            lines.append(f"- {label} <-> {other}: {', '.join(entry['files'])}")
    if info["mergeable"]:
        for entry in info["mergeable"]:
            other = entry["b"] if entry["a"] == info["id"] else entry["a"]
            label = "同文件但可自动合并" if zh else "same files, auto-mergeable"
            lines.append(f"- {label} <-> {other}: {', '.join(entry['files'])}")
    if info["drift"]:
        for entry in info["drift"]:
            label = "基线漂移" if zh else "baseline drift"
            lines.append(f"- {label}: {', '.join(entry['files'])}")
    if not (info["conflicts"] or info["mergeable"] or info["drift"]):
        lines.append("- 无" if zh else "- none")
    return lines


def _format_last_test_detail(detail: dict | None, *, zh: bool) -> str:
    if not detail:
        return "— 无记录" if zh else "— no record"
    time_local = detail.get("time_local", "—")
    exit_code = detail.get("exit_code")
    duration = detail.get("duration")
    if zh:
        dur = f"（{float(duration):.1f}s）" if isinstance(duration, (int, float)) else ""
        return f"{time_local}，exit {exit_code}{dur}"
    dur = f" ({float(duration):.1f}s)" if isinstance(duration, (int, float)) else ""
    return f"{time_local}, exit {exit_code}{dur}"


def _activity_history_lines(info: dict, *, zh: bool) -> list[str]:
    dirty = info.get("dirty")
    dirty_text = str(dirty) if dirty is not None else "—"
    idle_text = info.get("idle") or "—"
    rework_count = info.get("rework_count", 0)
    last_test_text = _format_last_test_detail(info.get("last_test_detail"), zh=zh)
    status_history = info.get("status_history") or []
    doctor_fails = info.get("doctor_fails") or []

    lines: list[str] = []
    if zh:
        lines.append("## 活动与历史")
        lines.append("")
        lines.append(f"- DIRTY（未提交数）: {dirty_text}")
        lines.append(f"- IDLE（无活动时长）: {idle_text}")
        lines.append(f"- 返工次数: {rework_count}")
        lines.append(f"- 最近测试详情: {last_test_text}")
        if status_history:
            lines.append("- 状态变化时间线:")
            for evt in status_history:
                lines.append(
                    f"  - {evt['time_local']}  {evt['commit']}  {evt['from']} → {evt['to']}"
                )
        else:
            lines.append("- 状态变化时间线: （无）")
        if doctor_fails:
            lines.append("- doctor 异常:")
            for msg in doctor_fails:
                lines.append(f"  - {msg}")
        else:
            lines.append("- doctor 异常: 无")
    else:
        lines.append("## Activity and history")
        lines.append("")
        lines.append(f"- DIRTY (uncommitted count): {dirty_text}")
        lines.append(f"- IDLE: {idle_text}")
        lines.append(f"- Rework count: {rework_count}")
        lines.append(f"- Last test detail: {last_test_text}")
        if status_history:
            lines.append("- Status history:")
            for evt in status_history:
                lines.append(
                    f"  - {evt['time_local']}  {evt['commit']}  {evt['from']} -> {evt['to']}"
                )
        else:
            lines.append("- Status history: (none)")
        if doctor_fails:
            lines.append("- Doctor issues:")
            for msg in doctor_fails:
                lines.append(f"  - {msg}")
        else:
            lines.append("- Doctor issues: none")
    return lines


def _render_review_zh(info: dict) -> str:
    lines: list[str] = []
    lines.append(f"# 审查：{info['id']} {info['title']}")
    lines.append("")
    uncommitted_count = len(info["uncommitted"])
    if uncommitted_count > 0:
        lines.append(
            f"> **注意：worktree 里还有 {uncommitted_count} 个未提交文件，审查的是已提交部分。**"
        )
        lines.append("")
    lines.append(f"- ID: {info['id']}")
    lines.append(f"- Agent: {info['agent']}")
    lines.append(f"- 状态: {info['status']}")
    lines.append(f"- 分支: {info['branch']}")
    lines.append(f"- 基线: {info['base']}")
    lines.append("")
    lines.append("## 检查结果")
    lines.append("")
    lines.append(f"- 越界 (denied): {len(info['denied'])}")
    lines.append(f"- 越界 (outside): {len(info['outside'])}")
    lines.append(f"- 未提交改动: {uncommitted_count}")
    lines.append("")
    lines.append("## 最近一次测试")
    lines.append("")
    lines.append(f"- {_TEST_ZH.get(info['test'], '— 无记录')}")
    lines.append("")
    lines.extend(_activity_history_lines(info, zh=True))
    lines.append("")
    lines.append("## diffstat")
    lines.append("")
    lines.append("```text")
    lines.append(info["diffstat"] or "(no changes)")
    lines.append("```")
    lines.append("")
    lines.append("## 冲突情况")
    lines.append("")
    lines.extend(_conflict_lines(info, zh=True))
    lines.append("")
    lines.append("## 验收清单")
    lines.append("")
    lines.append(info["acceptance"] or "（无验收小节）")
    lines.append("")
    lines.append("## 审查意见")
    lines.append("")
    lines.append("（待填写）")
    return "\n".join(lines)


def _render_review_en(info: dict) -> str:
    lines: list[str] = []
    lines.append(f"# Review: {info['id']} {info['title']}")
    lines.append("")
    uncommitted_count = len(info["uncommitted"])
    if uncommitted_count > 0:
        lines.append(
            f"> **Warning: {uncommitted_count} uncommitted file(s) remain in the worktree; this review covers committed changes only.**"
        )
        lines.append("")
    lines.append(f"- ID: {info['id']}")
    lines.append(f"- Agent: {info['agent']}")
    lines.append(f"- Status: {info['status']}")
    lines.append(f"- Branch: {info['branch']}")
    lines.append(f"- Base: {info['base']}")
    lines.append("")
    lines.append("## Check results")
    lines.append("")
    lines.append(f"- Violations (denied): {len(info['denied'])}")
    lines.append(f"- Violations (outside): {len(info['outside'])}")
    lines.append(f"- Uncommitted changes: {uncommitted_count}")
    lines.append("")
    lines.append("## Last test run")
    lines.append("")
    lines.append(f"- {_TEST_EN.get(info['test'], '— no record')}")
    lines.append("")
    lines.extend(_activity_history_lines(info, zh=False))
    lines.append("")
    lines.append("## diffstat")
    lines.append("")
    lines.append("```text")
    lines.append(info["diffstat"] or "(no changes)")
    lines.append("```")
    lines.append("")
    lines.append("## Conflicts")
    lines.append("")
    lines.extend(_conflict_lines(info, zh=False))
    lines.append("")
    lines.append("## Acceptance checklist")
    lines.append("")
    lines.append(info["acceptance"] or "(no acceptance section)")
    lines.append("")
    lines.append("## Review notes")
    lines.append("")
    lines.append("(to be filled in)")
    return "\n".join(lines)


def render_review(info: dict, lang: str = "zh") -> str:
    """Render the review Markdown for ``info`` in ``lang`` (``zh``/``en``)."""
    if lang == "en":
        return _render_review_en(info)
    return _render_review_zh(info)


_CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s*(.*)$")


def parse_acceptance_items(acceptance_text: str) -> list[dict[str, object]]:
    """Parse Markdown checklist items into ``{"text": str, "checked": bool}``."""
    items: list[dict[str, object]] = []
    for line in acceptance_text.splitlines():
        match = _CHECKBOX_RE.match(line)
        if not match:
            continue
        items.append({
            "text": match.group(2).strip(),
            "checked": match.group(1).lower() == "x",
        })
    return items


def evaluate_readiness(
    *,
    task_id: str,
    denied: list[str] | tuple[str, ...] = (),
    outside: list[str] | tuple[str, ...] = (),
    uncommitted: list[str] | tuple[str, ...] = (),
    test_result: str | None = None,
    target: str = "main",
    target_conflicts: list[str] | tuple[str, ...] = (),
    target_merge_error: str | None = None,
    conflicts: list[dict] | tuple[dict, ...] = (),
    doctor_fails: list[str] | tuple[str, ...] = (),
    missing_reason: str | None = None,
    lang: str = "zh",
) -> tuple[bool, list[dict[str, str]]]:
    """Compute ``(ready, blockers)`` from pre-collected review/board inputs."""
    zh = lang != "en"
    blockers: list[dict[str, str]] = []
    if missing_reason:
        blockers.append({
            "code": "missing_branch",
            "message": (
                f"分支或 worktree 缺失：{missing_reason}"
                if zh
                else f"branch or worktree missing: {missing_reason}"
            ),
        })
    if denied or outside:
        viol_paths = ", ".join(list(denied) + list(outside))
        blockers.append({
            "code": "scope_violation",
            "message": (
                f"存在范围越界文件：{viol_paths}"
                if zh
                else f"scope violations: {viol_paths}"
            ),
        })
    if uncommitted:
        paths = ", ".join(uncommitted)
        blockers.append({
            "code": "uncommitted",
            "message": (
                f"存在未提交改动：{paths}"
                if zh
                else f"uncommitted changes: {paths}"
            ),
        })
    if test_result != "ok":
        blockers.append({
            "code": "test_not_passed",
            "message": (
                "最近一次测试未通过或无记录"
                if zh
                else "last test run did not pass or has no record"
            ),
        })
    if target_merge_error:
        blockers.append({
            "code": "merge_conflict",
            "message": (
                f"无法检测与目标分支 {target} 的合并状态：{target_merge_error}"
                if zh
                else f"cannot verify merge into target {target}: {target_merge_error}"
            ),
        })
    elif target_conflicts or conflicts:
        parts: list[str] = []
        if target_conflicts:
            files = ", ".join(target_conflicts)
            parts.append(
                f"与目标分支 {target} 冲突（{files}）"
                if zh
                else f"conflicts with target {target} ({files})"
            )
        if conflicts:
            other_parts = []
            for entry in conflicts:
                other = entry["b"] if entry["a"] == task_id else entry["a"]
                other_parts.append(f"{other}: {', '.join(entry['files'])}")
            desc = "; ".join(other_parts)
            parts.append(
                f"与活跃任务冲突（{desc}）"
                if zh
                else f"conflicts with active task(s) ({desc})"
            )
        detail = "；".join(parts) if zh else "; ".join(parts)
        blockers.append({
            "code": "merge_conflict",
            "message": (
                f"存在合并冲突，当前不可直接合并：{detail}"
                if zh
                else f"not cleanly mergeable: {detail}"
            ),
        })
    if doctor_fails:
        desc = "；".join(doctor_fails) if zh else "; ".join(doctor_fails)
        blockers.append({
            "code": "doctor_fail",
            "message": (
                f"doctor 检查存在 FAIL：{desc}"
                if zh
                else f"doctor reported FAIL(s): {desc}"
            ),
        })
    return len(blockers) == 0, blockers


def build_review_json(info: dict, *, lang: str = "zh") -> dict:
    """Build a structured JSON dict corresponding to the review sheet."""
    denied = list(info["denied"])
    outside = list(info["outside"])
    uncommitted = list(info["uncommitted"])
    doctor_fails = list(info.get("doctor_fails") or [])
    conflicts = list(info["conflicts"])
    target_conflicts = list(info.get("target_conflicts") or [])
    target_merge_error = info.get("target_merge_error")
    target = info.get("target") or "main"

    ready, blockers = evaluate_readiness(
        task_id=info["id"],
        denied=denied,
        outside=outside,
        uncommitted=uncommitted,
        test_result=info["test"],
        target=target,
        target_conflicts=target_conflicts,
        target_merge_error=target_merge_error,
        conflicts=conflicts,
        doctor_fails=doctor_fails,
        lang=lang,
    )

    last_detail = info.get("last_test_detail")
    last_run = None
    if last_detail is not None:
        last_run = {
            "timestamp": last_detail.get("timestamp"),
            "time_local": last_detail.get("time_local"),
            "exit_code": last_detail.get("exit_code"),
            "duration": last_detail.get("duration"),
        }

    return {
        "id": info["id"],
        "title": info["title"],
        "agent": info["agent"],
        "status": info["status"],
        "branch": info["branch"],
        "base": info["base"],
        "scope": {
            "denied": denied,
            "outside": outside,
            "uncommitted": uncommitted,
        },
        "test": {
            "result": info["test"],
            "last_run": last_run,
        },
        "activity": {
            "dirty": info.get("dirty"),
            "idle_seconds": info.get("idle_seconds"),
            "rework_count": info.get("rework_count", 0),
            "status_history": list(info.get("status_history") or []),
        },
        "doctor_fails": doctor_fails,
        "diffstat": info.get("structured_diffstat") or {"files": [], "totals": {"files": 0, "insertions": 0, "deletions": 0}},
        "conflicts": conflicts,
        "mergeable": list(info["mergeable"]),
        "drift": list(info["drift"]),
        "acceptance": parse_acceptance_items(info.get("acceptance") or ""),
        "ready": ready,
        "blockers": blockers,
    }


# --------------------------------------------------------------------------- #
# HTML board
# --------------------------------------------------------------------------- #

#: Inline, dark-first stylesheet.  No external fonts, images or URLs anywhere.
_BOARD_CSS = """
:root {
  color-scheme: dark light;
  --bg: #0e1116;
  --panel: #161b22;
  --border: #30363d;
  --fg: #e6edf3;
  --muted: #8b949e;
  --accent: #58a6ff;
  --ok: #3fb950;
  --bad: #f85149;
  --warn: #d29922;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f6f8fa;
    --panel: #ffffff;
    --border: #d0d7de;
    --fg: #1f2328;
    --muted: #57606a;
    --accent: #0969da;
    --ok: #1a7f37;
    --bad: #cf222e;
    --warn: #9a6700;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 2rem;
  background: var(--bg);
  color: var(--fg);
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  line-height: 1.5;
}
h1 { font-size: 1.5rem; margin: 0 0 1rem; }
h2 { font-size: 1.1rem; margin: 2rem 0 0.5rem; }
table {
  width: 100%;
  border-collapse: collapse;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
th, td {
  padding: 0.5rem 0.75rem;
  text-align: left;
  border-bottom: 1px solid var(--border);
  font-size: 0.9rem;
  vertical-align: top;
}
th { background: var(--panel); color: var(--muted); font-weight: 600; }
tr:last-child td { border-bottom: none; }
.badge {
  display: inline-block;
  padding: 0.05rem 0.5rem;
  border-radius: 999px;
  border: 1px solid var(--border);
  font-size: 0.8rem;
}
.badge.todo { color: var(--muted); }
.badge.doing { color: var(--accent); border-color: var(--accent); }
.badge.review { color: var(--warn); border-color: var(--warn); }
.badge.rework { color: var(--warn); border-color: var(--warn); }
.badge.done { color: var(--ok); border-color: var(--ok); }
.badge.dropped { color: var(--muted); text-decoration: line-through; }
.missing { color: var(--bad); }
.violations-bad { color: var(--bad); font-weight: 700; }
.ready-ok { color: var(--ok); font-weight: 700; }
.ready-bad { color: var(--bad); font-weight: 700; }
.muted { color: var(--muted); }
.summary { margin-top: 1rem; color: var(--muted); }
.board-meta { margin: 0 0 1rem; color: var(--muted); font-size: 0.9rem; }
.board-upstream { margin: 0 0 0.5rem; color: var(--accent); font-size: 0.95rem; }
.board-meta .stall-count-alert { color: var(--bad); font-weight: 700; }
tr.stall-row { background: rgba(207, 34, 46, 0.08); }
tr.stall-row td:first-child { box-shadow: inset 3px 0 0 var(--bad); }
.stall-tag {
  display: inline-block;
  margin-left: 0.35rem;
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
  border: 1px solid var(--bad);
  color: var(--bad);
  font-size: 0.75rem;
  font-weight: 700;
}
ul { padding-left: 1.25rem; }
.conflict-bad { color: var(--bad); }
"""


_STALL_STATUSES = ("doing", "rework")

_BOARD_I18N: dict[str, dict[str, Any]] = {
    "zh": {
        "html_lang": "zh-CN",
        "title": "atb 任务看板",
        "heading": "atb 任务看板",
        "headers": (
            "ID", "状态", "Agent", "标题", "分支",
            "领先/落后", "文件", "未提交", "越界", "空闲", "测试", "可合并",
        ),
        "statuses": {
            "todo": "待办",
            "doing": "进行中",
            "review": "待审查",
            "rework": "返工",
            "done": "已完成",
            "dropped": "已废弃",
        },
        "missing": "缺失",
        "stall_tag": "可能卡住",
        "meta_prefix": "生成于",
        "threshold_fmt": "（阈值 {minutes} 分钟）",
        "stall_summary": "可能卡住：",
        "conflicts_real": "真实冲突",
        "conflicts_mergeable": "同文件（可自动合并）",
        "conflicts_drift": "基线漂移",
        "conflicts_errors": "无法判断",
        "none": "无",
    },
    "en": {
        "html_lang": "en",
        "title": "atb board",
        "heading": "atb board",
        "headers": (
            "ID", "Status", "Agent", "Title", "Branch",
            "Ahead/Behind", "Files", "Dirty", "Violations", "Idle", "Test", "Ready",
        ),
        "statuses": {
            "todo": "todo",
            "doing": "doing",
            "review": "review",
            "rework": "rework",
            "done": "done",
            "dropped": "dropped",
        },
        "missing": "missing",
        "stall_tag": "possibly stalled",
        "meta_prefix": "Generated at",
        "threshold_fmt": " (stall threshold {minutes}m)",
        "stall_summary": "Possibly stalled: ",
        "conflicts_real": "Real conflicts",
        "conflicts_mergeable": "Same files (auto-mergeable)",
        "conflicts_drift": "Baseline drift",
        "conflicts_errors": "Unable to determine",
        "none": "none",
    },
}


def _is_stalled(row: dict, stall_minutes: int) -> bool:
    idle_seconds = row.get("idle_seconds")
    return (
        row.get("status") in _STALL_STATUSES
        and isinstance(idle_seconds, int)
        and idle_seconds >= stall_minutes * 60
    )


def _esc(value: object) -> str:
    return html.escape(str(value))


def _status_badge(status: str, label: str | None = None) -> str:
    cls = _esc(status)
    text = _esc(label if label is not None else status)
    return f'<span class="badge {cls}">{text}</span>'


def _cell(value: object, cls: str | None = None) -> str:
    attr = f' class="{cls}"' if cls else ""
    return f"<td{attr}>{_esc(value)}</td>"


def _task_row_html(
    row: dict, *, stall_minutes: int = 120, texts: dict[str, Any] | None = None
) -> str:
    i18n = texts or _BOARD_I18N["zh"]
    if row["missing"]:
        branch_cell = _cell(i18n["missing"], "missing")
    else:
        branch_cell = _cell(row["branch"] or "—")
    if row["ahead"] is None:
        ahead_behind = "—"
    else:
        ahead_behind = f"+{row['ahead']}/-{row['behind']}"
    files = row["changed_files"] if row["changed_files"] is not None else "—"
    dirty = row["dirty"] if row.get("dirty") is not None else "—"
    violations = row["violations"] if row["violations"] is not None else "—"
    viol_cls = "violations-bad" if isinstance(row["violations"], int) and row["violations"] > 0 else None
    idle_text = row.get("idle_text") or "—"
    stalled = _is_stalled(row, stall_minutes)
    if stalled:
        stall_tag = _esc(i18n["stall_tag"])
        idle_cell = f'<td>{_esc(idle_text)}<span class="stall-tag">{stall_tag}</span></td>'
    else:
        idle_cell = _cell(idle_text)
    test_mark = {"ok": "✓", "fail": "✗"}.get(row["test"], "—")
    ready = row.get("ready")
    if ready is None:
        ready_cell = _cell("—")
    elif ready is True:
        ready_cell = '<td class="ready-ok">✓</td>'
    else:
        blockers = row.get("blockers") or []
        first_code = blockers[0]["code"] if blockers else "blocked"
        tooltip = "\n".join(f"{b['code']}: {b['message']}" for b in blockers)
        title_attr = f' title="{_esc(tooltip)}"' if tooltip else ""
        ready_cell = f'<td class="ready-bad"{title_attr}>✗ {_esc(first_code)}</td>'
    row_attr = ' class="stall-row"' if stalled else ""
    status_label = i18n["statuses"].get(row["status"], row["status"])
    return (
        f"<tr{row_attr}>"
        + _cell(row["id"])
        + f"<td>{_status_badge(row['status'], status_label)}</td>"
        + _cell(row["agent"])
        + _cell(row["title"])
        + branch_cell
        + _cell(ahead_behind)
        + _cell(files)
        + _cell(dirty)
        + _cell(violations, viol_cls)
        + idle_cell
        + _cell(test_mark)
        + ready_cell
        + "</tr>"
    )


def _conflict_list_html(
    entries: list[dict], kind: str, *, empty_text: str = "none"
) -> str:
    if not entries:
        return f'<p class="muted">{_esc(empty_text)}</p>'
    items = []
    for entry in entries:
        if kind == "pair":
            body = f"{entry['a']} <-> {entry['b']}: {', '.join(entry['files'])}"
        elif kind == "task":
            body = f"{entry['task']}: {', '.join(entry['files'])}"
        else:  # errors
            body = f"{' <-> '.join(entry['tasks'])}: {entry['message']}"
        items.append(f"<li>{html.escape(body)}</li>")
    return "<ul>" + "".join(items) + "</ul>"


def render_board_html(
    tasks: list[dict],
    summary: dict,
    conflicts: dict,
    *,
    stall_minutes: int = 120,
    generated_at: str | None = None,
    lang: str = "zh",
    upstream_text: str | None = None,
) -> str:
    """Render a self-contained static HTML board."""
    i18n = _BOARD_I18N.get(lang, _BOARD_I18N["zh"])
    headers = i18n["headers"]
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    rows = "".join(
        _task_row_html(row, stall_minutes=stall_minutes, texts=i18n)
        for row in tasks
    )
    stall_count = sum(1 for row in tasks if _is_stalled(row, stall_minutes))
    summary_text = " · ".join(
        f"{_esc(i18n['statuses'].get(k, k))}: {summary[k]}" for k in summary
    )
    if generated_at is None:
        generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    upstream_html = (
        f'<p class="board-upstream">{_esc(upstream_text)}</p>\n'
        if upstream_text
        else ""
    )
    stall_summary_cls = "stall-count-alert" if stall_count > 0 else ""
    stall_summary_text = f"{_esc(i18n['stall_summary'])}{stall_count}"
    stall_summary_html = (
        f'<span class="{stall_summary_cls}">{stall_summary_text}</span>'
        if stall_summary_cls
        else stall_summary_text
    )
    threshold_text = i18n["threshold_fmt"].format(minutes=stall_minutes)
    top_summary_html = (
        f'<p class="board-meta">{_esc(i18n["meta_prefix"])} {_esc(generated_at)}'
        f"{_esc(threshold_text)} · {stall_summary_html} · {summary_text}</p>"
    )

    empty_text = i18n["none"]
    sections = [
        (
            i18n["conflicts_real"],
            _conflict_list_html(conflicts["conflicts"], "pair", empty_text=empty_text),
        ),
        (
            i18n["conflicts_mergeable"],
            _conflict_list_html(conflicts["mergeable"], "pair", empty_text=empty_text),
        ),
        (
            i18n["conflicts_drift"],
            _conflict_list_html(conflicts["drift"], "task", empty_text=empty_text),
        ),
    ]
    if conflicts.get("errors"):
        sections.append((
            i18n["conflicts_errors"],
            _conflict_list_html(conflicts["errors"], "error", empty_text=empty_text),
        ))
    conflicts_html = "".join(f"<h2>{_esc(title)}</h2>{body}" for title, body in sections)

    return (
        "<!DOCTYPE html>\n"
        f'<html lang="{_esc(i18n["html_lang"])}">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(i18n['title'])}</title>\n"
        f"<style>{_BOARD_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        f"<h1>{_esc(i18n['heading'])}</h1>\n"
        f"{upstream_html}"
        f"{top_summary_html}\n"
        f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>\n"
        f'<p class="summary">{summary_text}</p>\n'
        f"{conflicts_html}\n"
        "</body>\n"
        "</html>\n"
    )


# --------------------------------------------------------------------------- #
# Dispatch checklist (Markdown)
# --------------------------------------------------------------------------- #

def _md_cell(value: object) -> str:
    """Make a value safe inside a single Markdown table cell."""
    return str(value).replace("\n", " ").replace("|", "\\|")


def render_dispatch(project: str, groups: list[dict], lang: str = "zh") -> str:
    """Render the per-project dispatch checklist as Markdown.

    ``groups`` is a list of ``{"name": str, "tasks": [task, ...]}`` where each
    task has ``id``, ``title``, ``agent``, ``status`` and ``message``.
    """
    if lang == "en":
        lines = [f"## Project: {project}", ""]
        headers = ("ID", "Title", "Agent", "Status", "Message")
        empty = "(no active tasks)"
    else:
        lines = [f"## 项目：{project}", ""]
        headers = ("ID", "标题", "Agent", "状态", "发这句话")
        empty = "（没有活跃任务）"

    if not groups:
        lines.append(empty)
        return "\n".join(lines)

    for group in groups:
        lines.append(f"### {group['name']}")
        lines.append("")
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "|".join(["---"] * len(headers)) + "|")
        for task in group["tasks"]:
            cells = (
                task["id"],
                task["title"],
                task["agent"],
                task["status"],
                task["message"],
            )
            lines.append("| " + " | ".join(_md_cell(c) for c in cells) + " |")
        lines.append("")
    return "\n".join(lines).rstrip("\n")
