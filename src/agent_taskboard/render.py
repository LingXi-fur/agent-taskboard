"""Pure output rendering: review Markdown (zh/en) and the HTML board.

Everything here is a pure function of already-computed data; no git or file
access happens in this module.  The board is a single self-contained HTML file
with inline CSS, no external resources, and every task field is HTML-escaped.
"""

from __future__ import annotations

import html
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


def _render_review_zh(info: dict) -> str:
    lines: list[str] = []
    lines.append(f"# 审查：{info['id']} {info['title']}")
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
    lines.append(f"- 未提交改动: {len(info['uncommitted'])}")
    lines.append("")
    lines.append("## 最近一次测试")
    lines.append("")
    lines.append(f"- {_TEST_ZH.get(info['test'], '— 无记录')}")
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
    lines.append(f"- Uncommitted changes: {len(info['uncommitted'])}")
    lines.append("")
    lines.append("## Last test run")
    lines.append("")
    lines.append(f"- {_TEST_EN.get(info['test'], '— no record')}")
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
.muted { color: var(--muted); }
.summary { margin-top: 1rem; color: var(--muted); }
.board-meta { margin: 0 0 1rem; color: var(--muted); font-size: 0.9rem; }
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


def _is_stalled(row: dict, stall_minutes: int) -> bool:
    idle_seconds = row.get("idle_seconds")
    return (
        row.get("status") in _STALL_STATUSES
        and isinstance(idle_seconds, int)
        and idle_seconds >= stall_minutes * 60
    )


def _esc(value: object) -> str:
    return html.escape(str(value))


def _status_badge(status: str) -> str:
    cls = _esc(status)
    return f'<span class="badge {cls}">{_esc(status)}</span>'


def _cell(value: object, cls: str | None = None) -> str:
    attr = f' class="{cls}"' if cls else ""
    return f"<td{attr}>{_esc(value)}</td>"


def _task_row_html(row: dict, *, stall_minutes: int = 120) -> str:
    if row["missing"]:
        branch_cell = _cell("missing", "missing")
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
        idle_cell = f'<td>{_esc(idle_text)}<span class="stall-tag">可能卡住</span></td>'
    else:
        idle_cell = _cell(idle_text)
    test_mark = {"ok": "✓", "fail": "✗"}.get(row["test"], "—")
    row_attr = ' class="stall-row"' if stalled else ""
    return (
        f"<tr{row_attr}>"
        + _cell(row["id"])
        + f"<td>{_status_badge(row['status'])}</td>"
        + _cell(row["agent"])
        + _cell(row["title"])
        + branch_cell
        + _cell(ahead_behind)
        + _cell(files)
        + _cell(dirty)
        + _cell(violations, viol_cls)
        + idle_cell
        + _cell(test_mark)
        + "</tr>"
    )


def _conflict_list_html(entries: list[dict], kind: str) -> str:
    if not entries:
        return '<p class="muted">none</p>'
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
) -> str:
    """Render a self-contained static HTML board."""
    headers = (
        "ID", "Status", "Agent", "Title", "Branch",
        "Ahead/Behind", "Files", "Dirty", "Violations", "Idle", "Test",
    )
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    rows = "".join(_task_row_html(row, stall_minutes=stall_minutes) for row in tasks)
    stall_count = sum(1 for row in tasks if _is_stalled(row, stall_minutes))
    summary_text = " · ".join(f"{_esc(k)}: {summary[k]}" for k in summary)
    if generated_at is None:
        generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    stall_summary_cls = "stall-count-alert" if stall_count > 0 else ""
    stall_summary_html = (
        f'<span class="{stall_summary_cls}">可能卡住：{stall_count}</span>'
        if stall_summary_cls
        else f"可能卡住：{stall_count}"
    )
    top_summary_html = (
        f'<p class="board-meta">生成于 {_esc(generated_at)}（阈值 {_esc(stall_minutes)} 分钟）'
        f" · {stall_summary_html} · {summary_text}</p>"
    )

    sections = [
        ("Real conflicts", _conflict_list_html(conflicts["conflicts"], "pair")),
        ("Same files (auto-mergeable)", _conflict_list_html(conflicts["mergeable"], "pair")),
        ("Baseline drift", _conflict_list_html(conflicts["drift"], "task")),
    ]
    if conflicts.get("errors"):
        sections.append(("Unable to determine", _conflict_list_html(conflicts["errors"], "error")))
    conflicts_html = "".join(f"<h2>{_esc(title)}</h2>{body}" for title, body in sections)

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>atb board</title>\n"
        f"<style>{_BOARD_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        "<h1>atb board</h1>\n"
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
