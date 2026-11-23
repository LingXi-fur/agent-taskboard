"""Gather agent sessions and map them to tasks.

The single place that owns the sessions → tasks relationship, shared by
``atb sessions``, per-task token usage, ``atb stats`` and the console. It
never writes anything: adapters are read-only observers.
"""

from __future__ import annotations

from pathlib import Path

from . import adapters, config
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
