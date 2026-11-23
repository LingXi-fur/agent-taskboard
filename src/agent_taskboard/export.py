"""Stable, read-only JSON export for other tools (``atb export --json``).

The export is a *contract*: within one ``schema`` version fields are only
ever added — never removed and never repurposed. A breaking change bumps
``schema``. See docs/export.md (English) and docs/export.zh-CN.md.

It is read-only: it never writes a session store and never touches
``.taskboard/``. Sessions are the same objects ``atb sessions`` shows, with
each tool's **native** session id, so another tool can join the two data
sets.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config, gitops, history, sessions, usage
from .adapters.base import USAGE_KEYS
from .sheet import Sheet

#: Bump only for a breaking change (see the module docstring).
SCHEMA_VERSION = 1

#: Scan every session when --max-days is not given.
_ALL_HISTORY = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _iso(value: Any) -> str | None:
    """Render a datetime / epoch seconds as a UTC ISO-8601 string."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        value = datetime.fromtimestamp(float(value), tz=timezone.utc)
    if not isinstance(value, datetime):
        return str(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _created(sheet: Sheet) -> str | None:
    value = sheet.created
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, str) and value:
        raw = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            return _iso(datetime.fromisoformat(raw))
        except ValueError:
            return None
    return None


def _created_ts(sheet: Sheet) -> float | None:
    value = sheet.created
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    if isinstance(value, str) and value:
        raw = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            moment = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    return None


def _merge_map(repo_root: Path, cfg: config.Config) -> dict[str, str]:
    """Map task id -> merge commit sha on the target (one git log pass)."""
    try:
        proc = subprocess.run(
            ["git", "log", "--format=%H%x09%s", cfg.target],
            cwd=str(repo_root),
            env=gitops.git_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return {}
    if proc.returncode != 0:
        return {}
    pattern = re.compile(
        rf"^Merge \S+ \((?P<id>{re.escape(cfg.id_prefix)}\d{{3,}}): "
    )
    merges: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        sha, _, subject = line.partition("\t")
        match = pattern.match(subject)
        if match:
            task_id = match.group("id")
            # git log is newest first: keep the first (latest) merge
            merges.setdefault(task_id, sha)
    return merges


def _run_last_ts(repo_root: Path, task_id: str) -> float | None:
    path = repo_root / ".taskboard" / "runs" / f"{task_id}.jsonl"
    if not path.exists():
        return None
    last: dict | None = None
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                continue
    except OSError:
        return None
    if not last:
        return None
    stamp = str(last.get("timestamp") or "")
    raw = stamp[:-1] + "+00:00" if stamp.endswith("Z") else stamp
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _usage_field(group: list) -> dict[str, int] | None:
    summary = usage.summarize_usage(group)
    tokens = {key: summary[key] for key in USAGE_KEYS if key in summary}
    return tokens or None


def build_export(
    repo_root: Path,
    cfg: config.Config,
    sheets: list[Sheet],
    *,
    since_ts: float | None = None,
    max_days: int | None = None,
) -> dict:
    """Build the schema-1 export payload (read-only)."""
    repo_root = Path(repo_root)
    transitions, _committed = _transitions(repo_root)
    merges = _merge_map(repo_root, cfg)

    scan_since = _ALL_HISTORY
    if max_days is not None and max_days > 0:
        from datetime import timedelta

        scan_since = datetime.now(timezone.utc) - timedelta(days=max_days)
    collected, _entries = sessions.collect_sessions(scan_since, None)
    worktrees, branches = sessions.session_task_maps(cfg, sheets)
    by_task: dict[str, list] = {}
    for session in collected:
        mapped = sessions.map_session_to_task(session, worktrees, branches)
        if mapped is not None:
            by_task.setdefault(mapped, []).append(session)

    tasks: list[dict] = []
    for sheet in sorted(sheets, key=lambda item: item.id):
        task_transitions = transitions.get(sheet.id, [])
        created_ts = _created_ts(sheet)
        run_ts = _run_last_ts(repo_root, sheet.id)
        activity_times = [ts for ts, _f, _t in task_transitions]
        if created_ts is not None:
            activity_times.append(created_ts)
        if run_ts is not None:
            activity_times.append(run_ts)
        last_activity = max(activity_times) if activity_times else None
        if since_ts is not None and (
            last_activity is None or last_activity < since_ts
        ):
            continue

        done_ts = None
        for stamp, _from, to in task_transitions:
            if to == "done":
                done_ts = stamp
        group = by_task.get(sheet.id, [])
        sessions_payload = [
            {
                "adapter": session.adapter,
                "id": session.id,
                "started_at": _iso(session.started_at),
                "last_active_at": _iso(session.last_active_at),
            }
            for session in sorted(
                group,
                key=lambda item: (
                    item.started_at is None,
                    item.started_at or datetime.min.replace(tzinfo=timezone.utc),
                    item.id,
                ),
            )
        ]
        merged = sheet.id in merges
        tasks.append({
            "id": sheet.id,
            "title": sheet.title,
            "agent": sheet.agent,
            "status": sheet.status,
            "branch": sheet.branch or None,
            "created": _created(sheet),
            "done_at": _iso(done_ts),
            "merged": merged,
            "merge_commit": merges.get(sheet.id),
            "rework_count": history.rework_count(sheet),
            "sessions": sessions_payload,
            "usage": _usage_field(group),
        })

    return {
        "schema": SCHEMA_VERSION,
        "generated": _iso(datetime.now(timezone.utc)),
        "repo": str(gitops.main_worktree_root(repo_root)),
        "target": cfg.target,
        "tasks": tasks,
    }


def _transitions(
    repo_root: Path,
) -> tuple[dict[str, list[tuple[float, str, str]]], dict[str, str]]:
    """Reuse the one-git-call status history from ``atb stats``."""
    from . import stats

    return stats._status_history(repo_root)
