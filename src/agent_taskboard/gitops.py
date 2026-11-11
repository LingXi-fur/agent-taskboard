"""Thin wrapper around git using ``subprocess``.

Every call runs ``git ...`` with ``check=False`` and raises :class:`GitError`
(including the captured stderr) when the command exits non-zero.  The rest of
the codebase never shells out to git directly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable

from .errors import GitError


def _run(
    args: list[str],
    cwd: str | Path | None = None,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        detail = proc.stderr.strip() or "command failed"
        raise GitError(f"git {' '.join(args)} failed: {detail}")
    return proc


def rev_parse(rev: str, cwd: str | Path | None = None) -> str:
    """Return the full object name (SHA) of ``rev``."""
    proc = _run(["rev-parse", "--verify", rev], cwd=cwd)
    return proc.stdout.strip()


def branch_exists(name: str, cwd: str | Path | None = None) -> bool:
    """Return whether local branch ``name`` exists."""
    proc = _run(
        ["show-ref", "--verify", "--quiet", f"refs/heads/{name}"],
        cwd=cwd,
        check=False,
    )
    return proc.returncode == 0


def worktree_add(
    path: str | Path, branch: str, base: str, cwd: str | Path | None = None
) -> None:
    """Create a new worktree at ``path`` on new branch ``branch`` from ``base``."""
    _run(["worktree", "add", "-b", branch, str(path), base], cwd=cwd)


def worktree_list(cwd: str | Path | None = None) -> list[dict[str, str]]:
    """Return the registered worktrees as parsed ``--porcelain`` dicts.

    Each dict has ``path`` and ``head`` keys, plus ``branch`` when the worktree
    is on a branch (short name, without ``refs/heads/``).
    """
    proc = _run(["worktree", "list", "--porcelain"], cwd=cwd)
    result: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if line == "":
            if current:
                result.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            branch = value
            if branch.startswith("refs/heads/"):
                branch = branch[len("refs/heads/"):]
            current["branch"] = branch
        elif key == "detached":
            current["detached"] = "true"
        elif key == "locked":
            current["locked"] = "true"
    if current:
        result.append(current)
    return result


def rev_list_count(
    a: str, b: str, cwd: str | Path | None = None
) -> tuple[int, int]:
    """Return ``(ahead, behind)`` commit counts of ``b`` relative to ``a``.

    ``ahead`` is the number of commits reachable from ``b`` but not ``a``;
    ``behind`` is the number reachable from ``a`` but not ``b``.
    """
    ahead = _run(["rev-list", "--count", f"{a}..{b}"], cwd=cwd).stdout.strip()
    behind = _run(["rev-list", "--count", f"{b}..{a}"], cwd=cwd).stdout.strip()
    return int(ahead or 0), int(behind or 0)


def merge_base(a: str, b: str, cwd: str | Path | None = None) -> str:
    """Return the merge base of revs ``a`` and ``b``."""
    proc = _run(["merge-base", a, b], cwd=cwd)
    return proc.stdout.strip()


def merge_tree_conflicts(
    a: str, b: str, cwd: str | Path | None = None
) -> list[str]:
    """Return the paths that actually conflict when merging ``a`` into ``b``.

    Uses ``git merge-tree --write-tree --name-only a b`` which performs a real
    merge *without touching any worktree*.  On a clean merge it returns ``[]``;
    on a conflict the first stdout line is the resulting tree OID and the
    remaining lines are the conflicted paths.
    """
    proc = _run(
        ["merge-tree", "--write-tree", "--name-only", a, b],
        cwd=cwd,
        check=False,
    )
    if proc.returncode == 0:
        return []
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return lines[1:] if lines else []


def diff_stat(base: str, head: str, cwd: str | Path | None = None) -> str:
    """Return the ``git diff --stat base...head`` output."""
    proc = _run(["diff", "--stat", f"{base}...{head}"], cwd=cwd)
    return proc.stdout.rstrip("\n")


def _dedup(paths: Iterable[str]) -> list[str]:
    """Deduplicate ``paths`` while preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def changed_files(
    base: str, head: str, cwd: str | Path | None = None
) -> list[str]:
    """Return paths changed between ``base...head`` (committed changes).

    Uses ``git diff --name-status -z -M`` so renames are detected and both the
    old and new paths are returned.
    """
    proc = _run(["diff", "--name-status", "-z", "-M", f"{base}...{head}"], cwd=cwd)
    tokens = proc.stdout.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    paths: list[str] = []
    i = 0
    while i < len(tokens):
        status = tokens[i]
        i += 1
        if status[:1] in ("R", "C"):  # rename/copy: two paths follow
            paths.append(tokens[i])
            paths.append(tokens[i + 1])
            i += 2
        else:
            paths.append(tokens[i])
            i += 1
    return _dedup(paths)


def uncommitted_files(
    worktree: str | Path, cwd: str | Path | None = None
) -> list[str]:
    """Return uncommitted paths (including untracked files) in ``worktree``.

    Uses ``git status --porcelain=v1 -z --untracked-files=all`` and handles the
    two-path form used for staged renames/copies.
    """
    proc = _run(
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=worktree,
    )
    tokens = proc.stdout.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    paths: list[str] = []
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        if len(entry) < 3:
            continue
        xy = entry[:2]
        path = entry[3:]  # skip the two status chars and the separating space
        paths.append(path)
        if xy[0] in ("R", "C") and i < len(tokens):
            paths.append(tokens[i])  # original path
            i += 1
    return _dedup(paths)
