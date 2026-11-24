"""Gather agent sessions and map them to tasks.

The single place that owns the sessions → tasks relationship, shared by
``atb sessions``, per-task token usage, ``atb stats`` and the console. It
never writes anything: adapters are read-only observers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import adapters, config
from .adapters.base import USAGE_KEYS
from .sheet import Sheet


def collect_sessions(
    since, adapter_filter: str | None
) -> tuple[list, list[dict]]:
    """Gather sessions from every adapter; one adapter failing never stops."""
    state = adapters.load_adapters()
    collected: list = []
    entries: list[dict] = []
    for error in state.errors:
        entries.append({
            "name": error["adapter"],
            "available": False,
            "error": f"failed to load: {error['error']}",
            "count": 0,
        })
    for adapter in state.adapters:
        if adapter_filter and adapter.name != adapter_filter:
            continue
        entry = {
            "name": adapter.name,
            "available": False,
            "error": None,
            "count": 0,
        }
        try:
            entry["available"] = bool(adapter.available())
        except Exception as exc:  # adapter bugs must not break the page
            entry["error"] = f"{type(exc).__name__}: {exc}"
        if entry["available"] and entry["error"] is None:
            try:
                for session in adapter.sessions(since):
                    collected.append(session)
                    entry["count"] += 1
            except Exception as exc:
                entry["error"] = f"{type(exc).__name__}: {exc}"
        entries.append(entry)
    return collected, entries


def session_task_maps(
    cfg: config.Config, sheets: list[Sheet]
) -> tuple[list[tuple[Path, str]], dict[str, str]]:
    """Worktree paths and branches of started tasks, for session mapping."""
    root = config.resolve_worktree_root(cfg)
    worktrees: list[tuple[Path, str]] = []
    branches: dict[str, str] = {}
    for sheet in sheets:
        if not sheet.branch:
            continue
        worktrees.append(((root / sheet.id).resolve(), sheet.id))
        branches.setdefault(sheet.branch, sheet.id)
    worktrees.sort(key=lambda item: item[1])
    return worktrees, branches


def map_session_to_task(
    session, worktrees: list[tuple[Path, str]], branches: dict[str, str]
) -> str | None:
    """Map a session to a task: worktree cwd first, then branch name."""
    if session.cwd:
        try:
            cwd = Path(session.cwd).resolve()
        except OSError:
            cwd = None
        if cwd is not None:
            for worktree, task_id in worktrees:
                if cwd == worktree or worktree in cwd.parents:
                    return task_id
    if session.branch:
        task_id = branches.get(session.branch)
        if task_id is not None:
            return task_id
    return None


def sessions_for_task(
    repo_root: Path,
    cfg: config.Config,
    sheets: list[Sheet],
    task_id: str,
    *,
    since,
) -> list:
    """Every session mapped to ``task_id`` (read-only)."""
    collected, _entries = collect_sessions(since, None)
    worktrees, branches = session_task_maps(cfg, sheets)
    return [
        session
        for session in collected
        if map_session_to_task(session, worktrees, branches) == task_id
    ]


# --------------------------------------------------------------------------- #
# segmenting long sessions across tasks
# --------------------------------------------------------------------------- #

#: How a session segment was attributed.
BASIS_WORKTREE = "worktree"
BASIS_COMMAND = "command"
BASIS_TIME_WINDOW = "time_window"

#: `atb start T001` / `atb check T002` ... in tool calls or messages.
ATB_COMMAND_RE = re.compile(
    r"\batb\s+(?:start|check|review|sync|done|prompt|note)\s+(?P<id>[A-Za-z]+\d+)\b"
)


@dataclass
class SessionSegment:
    """One slice of a session assigned to one task."""

    session: Session
    task_id: str
    start: datetime | None
    end: datetime | None
    basis: str
    usage: dict[str, int] | None


def _worktree_hit(
    text: str, worktrees: list[tuple[Path, str]]
) -> str | None:
    """Return the task whose worktree path appears in ``text`` (longest wins)."""
    best: tuple[int, str] | None = None
    for path, task_id in worktrees:
        for form in {str(path), path.as_posix()}:
            if form and form in text:
                if best is None or len(form) > best[0]:
                    best = (len(form), task_id)
    return best[1] if best else None


def _segment_usage(totals: dict[str, int], present: set[str]):
    if not present:
        return None
    result = {key: totals[key] for key in USAGE_KEYS if key in present}
    if "total" not in result:
        result["total"] = sum(result.values())
    return result


def segment_session(
    session: Session,
    worktrees: list[tuple[Path, str]],
    branches: dict[str, str],
    windows: dict[str, tuple[float, float | None]],
    known_ids: set[str],
) -> list[SessionSegment]:
    """Split one session into per-task segments using evidence.

    Priority: tool-call/message paths inside a task worktree, then
    ``atb <cmd> T0xx`` mentions, then the task's start→review/done time
    window when exactly one task matches. Evidence-free stretches before any
    attribution stay unattributed — nothing is guessed.
    """
    mapped = map_session_to_task(session, worktrees, branches)
    if mapped is not None:
        return [SessionSegment(
            session, mapped, session.started_at, session.last_active_at,
            BASIS_WORKTREE, session.resolved_usage(),
        )]

    events = session.resolved_events()
    if not events:
        return []

    segments: list[SessionSegment] = []
    current: dict | None = None

    def close(end: datetime | None) -> None:
        if current is None:
            return
        start = current["start"]
        if end is not None and start is not None and end < start:
            end = start
        segments.append(SessionSegment(
            session, current["task"], start, end,
            current["basis"],
            _segment_usage(current["totals"], current["present"]),
        ))

    for event in events:
        stamp = event.timestamp
        task_id: str | None = None
        basis: str | None = None
        if event.evidence:
            task_id = _worktree_hit(event.evidence, worktrees)
            if task_id is not None:
                basis = BASIS_WORKTREE
            else:
                match = ATB_COMMAND_RE.search(event.evidence)
                if match is not None:
                    candidate = match.group("id")
                    if candidate in known_ids:
                        task_id = candidate
                        basis = BASIS_COMMAND
        if task_id is None and stamp is not None:
            moment = stamp.timestamp()
            candidates = [
                candidate for candidate, (start, end) in windows.items()
                if start <= moment and (end is None or moment < end)
            ]
            if current is None:
                # nothing assigned yet: a unique window may claim this event
                if len(candidates) == 1:
                    task_id = candidates[0]
                    basis = BASIS_TIME_WINDOW
            else:
                current_window = windows.get(current["task"])
                inside = (
                    current_window is None
                    or (
                        current_window[0] <= moment
                        and (current_window[1] is None or moment < current_window[1])
                    )
                )
                if not inside:
                    # the current task's window ended: only switch when a
                    # single window matches, otherwise stop guessing
                    if len(candidates) == 1:
                        task_id = candidates[0]
                        basis = BASIS_TIME_WINDOW
                    else:
                        close(stamp)
                        current = None

        if task_id is not None and (
            current is None or task_id != current["task"]
        ):
            close(stamp)
            current = {
                "task": task_id,
                "basis": basis or BASIS_TIME_WINDOW,
                "start": stamp,
                "totals": {},
                "present": set(),
            }
        if current is not None and event.usage:
            for key in USAGE_KEYS:
                value = event.usage.get(key)
                if value is None:
                    continue
                current["totals"][key] = current["totals"].get(key, 0) + int(value)
                current["present"].add(key)

    close(session.last_active_at)
    return segments


def task_segments(
    collected: list,
    worktrees: list[tuple[Path, str]],
    branches: dict[str, str],
    windows: dict[str, tuple[float, float | None]],
    known_ids: set[str],
) -> dict[str, list[SessionSegment]]:
    """Segment every session and group the pieces by task id."""
    grouped: dict[str, list[SessionSegment]] = {}
    for session in collected:
        for segment in segment_session(
            session, worktrees, branches, windows, known_ids
        ):
            grouped.setdefault(segment.task_id, []).append(segment)
    return grouped
