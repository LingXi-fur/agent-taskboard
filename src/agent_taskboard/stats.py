"""Read-only per-agent statistics (``atb stats``).

Aggregates the same history ``atb log`` reads — status transitions recorded
in git, test runs under ``.taskboard/runs/`` and rework notes in sheet bodies —
into one line per agent: throughput, rework rate, cycle time and test health.

Everything here is read-only. The only git call is one ``git log -p`` over
``.taskboard/tasks`` (run with :func:`agent_taskboard.gitops.git_env`, so no
index lock is ever taken); status transitions are reconstructed from the diff
instead of one ``git log``/``git show`` pair per task, which keeps 200 tasks
well under half a second.

Tasks whose sheets were never committed (or whose status changes were never
committed) show up as "missing history": they are counted in the totals but
excluded from the cycle-time metrics, and the table footer says how many there
are. A missing metric prints ``—`` instead of failing.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import gitops
from .history import rework_count
from .sheet import STATUSES, Sheet

#: Statuses that count as "in progress" for the totals column.
ACTIVE_STATUSES = ("doing", "review", "rework")

_COMMIT_LINE_RE = re.compile(r"^__COMMIT__(?P<ct>\d+)$")
_DIFF_LINE_RE = re.compile(r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)$")
_TASK_PATH_RE = re.compile(r"^\.taskboard/tasks/(?P<task>.+)\.md$")
_STATUS_LINE_RE = re.compile(r'^[+-]status = "(?P<status>[a-z]+)"$')


# --------------------------------------------------------------------------- #
# status transitions (one git call for the whole board)
# --------------------------------------------------------------------------- #

def _status_history(
    repo_root: Path,
) -> tuple[dict[str, list[tuple[float, str, str]]], dict[str, str]]:
    """Return ``({task: [(ts, from, to)], ...}, {task: last committed status})``.

    One ``git log --reverse -p --unified=0`` over ``.taskboard/tasks`` feeds
    both maps: every ``+status = "..."`` line either introduces a task (first
    appearance) or is a transition from the previously seen status. Merge
    commits can replay the same ``-old``/``+new`` pair against the first
    parent; because the "from" state is tracked separately, a repeated pair is
    detected and not recorded twice.
    """
    proc = subprocess.run(
        [
            "git",
            "log",
            "--reverse",
            "--unified=0",
            "--format=__COMMIT__%ct",
            "-p",
            "--",
            ".taskboard/tasks",
        ],
        cwd=str(repo_root),
        env=gitops.git_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    transitions: dict[str, list[tuple[float, str, str]]] = {}
    committed: dict[str, str] = {}
    if proc.returncode != 0:
        return transitions, committed

    timestamp: float | None = None
    task: str | None = None
    for line in proc.stdout.splitlines():
        commit = _COMMIT_LINE_RE.match(line)
        if commit is not None:
            timestamp = float(commit.group("ct"))
            task = None
            continue
        diff = _DIFF_LINE_RE.match(line)
        if diff is not None:
            path = _TASK_PATH_RE.match(diff.group("a"))
            task = path.group("task") if path is not None else None
            continue
        status = _STATUS_LINE_RE.match(line)
        if status is None or timestamp is None or task is None:
            continue
        new = status.group("status")
        if new not in STATUSES or line[0] != "+":
            continue
        previous = committed.get(task)
        if previous is not None and previous != new:
            transitions.setdefault(task, []).append((timestamp, previous, new))
        committed[task] = new
    return transitions, committed


# --------------------------------------------------------------------------- #
# per-task facts
# --------------------------------------------------------------------------- #

def _created_ts(sheet: Sheet) -> float | None:
    value = sheet.created
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if isinstance(value, str):
        raw = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return None


def _last_test(repo_root: Path, task_id: str) -> tuple[float | None, int | None]:
    """Return ``(timestamp, exit_code)`` of the last recorded test run."""
    path = repo_root / ".taskboard" / "runs" / f"{task_id}.jsonl"
    if not path.exists():
        return None, None
    last: dict[str, Any] | None = None
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            last = json.loads(line)
        except json.JSONDecodeError:
            continue
    if last is None:
        return None, None
    exit_code = last.get("exit_code")
    stamp = str(last.get("timestamp", ""))
    if stamp.endswith("Z"):
        stamp = stamp[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(stamp)
    except ValueError:
        return None, exit_code
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp(), exit_code


def collect_task_facts(repo_root: Path, sheets: list[Sheet]) -> list[dict[str, Any]]:
    """Gather the raw per-task numbers, all from read-only sources."""
    transitions, committed = _status_history(repo_root)
    facts: list[dict[str, Any]] = []
    for sheet in sheets:
        events = list(transitions.get(sheet.id, []))
        last_committed = committed.get(sheet.id)
        sheet_path = repo_root / ".taskboard" / "tasks" / f"{sheet.id}.md"
        if (
            last_committed is not None
            and sheet.status != last_committed
            and sheet_path.exists()
        ):
            # an uncommitted status change: mirror `atb log` and date it by
            # the sheet's mtime so a finished-but-uncommitted task still counts
            events.append((sheet_path.stat().st_mtime, last_committed, sheet.status))

        done_ts = next((ts for ts, _f, to in events if to == "done"), None)
        start_ts = next((ts for ts, _f, to in events if to == "doing"), None)
        reviews = [
            ts
            for ts, _f, to in events
            if to == "review" and (done_ts is None or ts <= done_ts)
        ]
        test_ts, test_exit = _last_test(repo_root, sheet.id)
        created = _created_ts(sheet)
        activity = [ts for ts, _f, _to in events]
        activity += [value for value in (test_ts, created) if value is not None]
        facts.append({
            "id": sheet.id,
            "agent": sheet.agent or "(unknown)",
            "status": sheet.status,
            "start_ts": start_ts,
            "done_ts": done_ts,
            "last_review_ts": reviews[-1] if reviews else None,
            "rework_count": rework_count(sheet),
            "last_test_exit": test_exit,
            "last_activity": max(activity) if activity else None,
            "has_history": bool(events),
        })
    return facts


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #

def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _p90(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(0.9 * len(ordered)))
    return ordered[min(len(ordered), rank) - 1]


def _rate(part: int, total: int) -> float | None:
    return round(part / total, 4) if total else None


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def summarize(
    repo_root: Path,
    sheets: list[Sheet],
    *,
    since: float | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """Aggregate per-agent statistics; never raises on missing history."""
    facts = collect_task_facts(repo_root, sheets)
    if since is not None:
        # A task with no recorded activity at all cannot be dated; when a
        # --since cut is requested such tasks are left out rather than
        # silently counted as recent.
        facts = [
            fact
            for fact in facts
            if fact["last_activity"] is not None
            and fact["last_activity"] >= since
        ]
    if agent is not None:
        facts = [fact for fact in facts if fact["agent"] == agent]

    groups: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        groups.setdefault(fact["agent"], []).append(fact)

    agents: list[dict[str, Any]] = []
    missing_total = 0
    for name in sorted(groups):
        tasks = groups[name]
        done = [fact for fact in tasks if fact["status"] == "done"]
        dropped = [fact for fact in tasks if fact["status"] == "dropped"]
        active = [fact for fact in tasks if fact["status"] in ACTIVE_STATUSES]
        reworked = [fact for fact in tasks if fact["rework_count"] > 0]
        cycles = [
            fact["done_ts"] - fact["start_ts"]
            for fact in done
            if fact["start_ts"] is not None
            and fact["done_ts"] is not None
            and fact["done_ts"] >= fact["start_ts"]
        ]
        waits = [
            fact["done_ts"] - fact["last_review_ts"]
            for fact in done
            if fact["last_review_ts"] is not None
            and fact["done_ts"] is not None
            and fact["done_ts"] >= fact["last_review_ts"]
        ]
        tested = [fact for fact in done if fact["last_test_exit"] is not None]
        failed = [fact for fact in tested if fact["last_test_exit"] != 0]
        missing = [fact for fact in tasks if not fact["has_history"]]
        missing_total += len(missing)
        agents.append({
            "agent": name,
            "tasks": len(tasks),
            "done": len(done),
            "dropped": len(dropped),
            "active": len(active),
            "rework_rate": _rate(len(reworked), len(tasks)),
            "avg_rework_count": _mean(
                [float(fact["rework_count"]) for fact in tasks]
            ),
            "cycle_median_seconds": _median(cycles),
            "cycle_p90_seconds": _p90(cycles),
            "cycle_samples": len(cycles),
            "review_wait_median_seconds": _median(waits),
            "review_wait_samples": len(waits),
            "test_failure_rate": _rate(len(failed), len(tested)),
            "test_samples": len(tested),
            "missing_history": len(missing),
        })

    return {
        "since": (
            datetime.fromtimestamp(since, tz=timezone.utc).isoformat()
            if since is not None
            else None
        ),
        "agent_filter": agent,
        "agents": agents,
        "missing_history_total": missing_total,
    }


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

def _fmt_pct(rate: float | None) -> str:
    return "—" if rate is None else f"{rate * 100:.0f}%"


def _fmt_number(value: float | None) -> str:
    return "—" if value is None else f"{value:g}"


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    value = float(seconds)
    if value < 60:
        return f"{value:.0f}s"
    if value < 3600:
        return f"{value / 60:.0f}m"
    if value < 48 * 3600:
        return f"{value / 3600:.1f}h"
    return f"{value / 86400:.1f}d"


_TEXTS: dict[str, dict[str, Any]] = {
    "zh": {
        "title": "按 Agent 统计",
        "headers": (
            "agent", "done", "dropped", "进行中", "返工率",
            "平均返工", "周期中位", "周期P90", "审查等待中位", "测试失败率",
        ),
        "missing": "注：{count} 个任务缺少状态历史，未计入周期统计。",
        "missing_none": "注：无缺少历史的任务。",
        "no_tasks": "（没有任务）",
        "no_data": "（无数据）",
    },
    "en": {
        "title": "Per-agent statistics",
        "headers": (
            "agent", "done", "dropped", "active", "rework rate",
            "avg reworks", "cycle median", "cycle P90", "review wait median",
            "test failures",
        ),
        "missing": "Note: {count} task(s) have no status history and are "
                   "excluded from cycle times.",
        "missing_none": "Note: no tasks are missing history.",
        "no_tasks": "(no tasks)",
        "no_data": "(no data)",
    },
}


def render_stats(summary: dict[str, Any], *, lang: str = "zh") -> str:
    """Render the summarize() payload as a fixed-width table."""
    texts = _TEXTS.get(lang, _TEXTS["zh"])
    lines: list[str] = [texts["title"]]
    agents = summary["agents"]
    if not agents:
        lines.append(texts["no_tasks"])
    else:
        rows = []
        for entry in agents:
            rows.append([
                entry["agent"],
                str(entry["done"]),
                str(entry["dropped"]),
                str(entry["active"]),
                _fmt_pct(entry["rework_rate"]),
                _fmt_number(entry["avg_rework_count"]),
                _fmt_duration(entry["cycle_median_seconds"]),
                _fmt_duration(entry["cycle_p90_seconds"]),
                _fmt_duration(entry["review_wait_median_seconds"]),
                _fmt_pct(entry["test_failure_rate"]),
            ])
        widths = [
            max(len(header), *(len(row[index]) for row in rows))
            for index, header in enumerate(texts["headers"])
        ]
        header_line = "  ".join(
            header.ljust(widths[index])
            for index, header in enumerate(texts["headers"])
        )
        lines.append(header_line)
        for row in rows:
            lines.append(
                "  ".join(
                    cell.ljust(widths[index]) for index, cell in enumerate(row)
                )
            )
    missing = summary.get("missing_history_total", 0)
    lines.append(
        texts["missing"].format(count=missing)
        if missing
        else texts["missing_none"]
    )
    return "\n".join(lines)
