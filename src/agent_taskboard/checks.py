"""Shared business logic for ``check``, ``status``, ``conflicts`` and ``review``.

These helpers compute the git-backed metrics (changed paths, violations,
ahead/behind, last test result) and the cross-task conflict pairing.  Pure
pieces (path intersection) are kept separate from the git calls so they stay
easy to reason about.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config, gitops, scope
from .sheet import Sheet

ACTIVE_STATUSES = ("doing", "review", "rework")


def resolve_worktree(cfg: config.Config, sheet: Sheet) -> Path:
    return config.resolve_worktree_root(cfg) / sheet.id


def task_committed(sheet: Sheet, repo_root: Path) -> list[str]:
    return gitops.changed_files(sheet.base, sheet.branch, cwd=repo_root)


def task_uncommitted(sheet: Sheet, cfg: config.Config) -> list[str]:
    worktree = resolve_worktree(cfg, sheet)
    if not worktree.exists():
        return []
    return gitops.uncommitted_files(worktree)


def task_changed_paths(sheet: Sheet, cfg: config.Config, repo_root: Path) -> list[str]:
    return list(dict.fromkeys(task_committed(sheet, repo_root) + task_uncommitted(sheet, cfg)))


def classify_violations(
    paths: list[str], allow: list[str], deny: list[str]
) -> tuple[list[str], list[str]]:
    """Return ``(denied, outside)`` paths for ``paths`` under ``allow``/``deny``."""
    classification = scope.classify(paths, allow, deny)
    denied = [p for p in paths if classification[p] == "denied"]
    outside = [p for p in paths if classification[p] == "outside"]
    return denied, outside


def last_test_result(repo_root: Path, task_id: str) -> str | None:
    """Return ``"ok"``, ``"fail"`` or ``None`` from the last run record."""
    path = repo_root / ".taskboard" / "runs" / f"{task_id}.jsonl"
    if not path.exists():
        return None
    last_line: str | None = None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                last_line = line.strip()
    if last_line is None:
        return None
    try:
        record = json.loads(last_line)
    except json.JSONDecodeError:
        return None
    return "ok" if record.get("exit_code") == 0 else "fail"


def compute_conflicts(
    active_sheets: list[Sheet], cfg: config.Config, repo_root: Path
) -> dict[str, list[dict[str, Any]]]:
    """Compute the three conflict categories across active tasks.

    Returns ``{"conflicts": [...], "mergeable": [...], "drift": [...]}`` where
    ``conflicts`` entries are ``{"a", "b", "files"}`` (real merge conflicts),
    ``mergeable`` entries are ``{"a", "b", "files"}`` (same files but a clean
    auto-merge) and ``drift`` entries are ``{"task", "files"}`` (target moved
    files the task also touched after its baseline).
    """
    result: dict[str, list[dict[str, Any]]] = {
        "conflicts": [],
        "mergeable": [],
        "drift": [],
    }

    files_by_id: dict[str, list[str]] = {}
    sheets_by_id: dict[str, Sheet] = {}
    for sheet in active_sheets:
        if not sheet.base or not sheet.branch:
            continue
        try:
            files_by_id[sheet.id] = task_changed_paths(sheet, cfg, repo_root)
        except gitops.GitError:
            continue  # branch already gone: skip this task
        sheets_by_id[sheet.id] = sheet

    ids = sorted(files_by_id)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            common = sorted(set(files_by_id[a]) & set(files_by_id[b]))
            if not common:
                continue
            try:
                conflicts = gitops.merge_tree_conflicts(
                    sheets_by_id[a].branch, sheets_by_id[b].branch, cwd=repo_root
                )
            except gitops.GitError:
                continue
            if conflicts:
                result["conflicts"].append(
                    {"a": a, "b": b, "files": sorted(set(conflicts))}
                )
            else:
                result["mergeable"].append({"a": a, "b": b, "files": common})

    try:
        target_head = gitops.rev_parse(cfg.target, cwd=repo_root)
    except gitops.GitError:
        target_head = None
    if target_head is not None:
        for sheet in active_sheets:
            if sheet.id not in files_by_id or not sheet.base:
                continue
            try:
                target_changed = gitops.changed_files(
                    sheet.base, target_head, cwd=repo_root
                )
            except gitops.GitError:
                continue
            drift = sorted(set(files_by_id[sheet.id]) & set(target_changed))
            if drift:
                result["drift"].append({"task": sheet.id, "files": drift})

    return result
