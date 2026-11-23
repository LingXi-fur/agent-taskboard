"""Per-task token usage, aggregated from mapped agent sessions.

Read-only: sessions come from the adapters through
:mod:`agent_taskboard.sessions`, using the same session → task mapping as
``atb sessions``. Only token counts are reported — no prices.

Missing data stays missing: a key that no mapped session recorded is
omitted, and the number of sessions without any usage record is counted.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, sessions
from .adapters.base import USAGE_KEYS
from .sheet import Sheet

#: How far back sessions are scanned for usage. Bounded on purpose: the
#: adapters read metadata for every candidate file, so a huge window would
#: slow `atb review` down. Override with ATB_USAGE_DAYS.
DEFAULT_WINDOW_DAYS = 7


def usage_window() -> timedelta:
    raw = os.environ.get("ATB_USAGE_DAYS")
    try:
        days = int(raw) if raw else DEFAULT_WINDOW_DAYS
    except ValueError:
        days = DEFAULT_WINDOW_DAYS
    return timedelta(days=max(1, days))


def summarize_usage(mapped_sessions) -> dict:
    """Sum ``Session.usage`` for a group of sessions.

    Usage is resolved lazily: adapters that would have to stream a big file
    (claude-code) only do so for sessions actually mapped to a task, which
    keeps `atb review` fast. Returns ``{"sessions", "with_usage", "missing"}``
    plus only the token keys that were actually recorded, summed.
    """
    totals: dict[str, int] = {}
    present: set[str] = set()
    count = 0
    missing = 0
    for session in mapped_sessions:
        count += 1
        resolver = getattr(session, "resolved_usage", None)
        usage = resolver() if callable(resolver) else getattr(session, "usage", None)
        if not usage:
            missing += 1
            continue
        for key in USAGE_KEYS:
            value = usage.get(key)
            if value is None:
                continue
            totals[key] = totals.get(key, 0) + int(value)
            present.add(key)
    result: dict = {
        "sessions": count,
        "with_usage": count - missing,
        "missing": missing,
    }
    for key in USAGE_KEYS:
        if key in present:
            result[key] = totals[key]
    return result


def scan_since(sheet: Sheet | None = None) -> datetime:
    """The session-scan start: the bounded window, never earlier."""
    return datetime.now(timezone.utc) - usage_window()


def task_usage(
    repo_root: Path,
    cfg: config.Config,
    sheets: list[Sheet],
    *,
    since: datetime | None = None,
    task_id: str | None = None,
    usage_sessions=None,
) -> dict:
    """Aggregate usage per task, or for one task when ``task_id`` is given.

    ``usage_sessions`` lets a caller pass already-collected sessions (the
    console collects them once for the whole page); otherwise the adapters
    are scanned with ``since`` (or the task's lifetime).
    """
    sheets = list(sheets)
    reference = next(
        (sheet for sheet in sheets if sheet.id == task_id), None
    ) if task_id is not None else None
    collected = usage_sessions
    if collected is None:
        collected, _entries = sessions.collect_sessions(
            since or scan_since(reference), None
        )
    worktrees, branches = sessions.session_task_maps(cfg, sheets)
    groups: dict[str, list] = {}
    for session in collected:
        mapped = sessions.map_session_to_task(session, worktrees, branches)
        if mapped is None:
            continue
        groups.setdefault(mapped, []).append(session)
    if task_id is not None:
        return summarize_usage(groups.get(task_id, []))
    return {
        mapped: summarize_usage(items) for mapped, items in groups.items()
    }
