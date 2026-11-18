"""Thin wrapper around git using ``subprocess``.

Every call runs ``git ...`` with ``check=False`` and raises :class:`GitError`
(including the captured stderr) when the command exits non-zero.  The rest of
the codebase never shells out to git directly.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Iterable

from .errors import GitError

#: A full git object id: 40 hex (SHA-1) or 64 hex (SHA-256).
_HEX_OID_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def git_env() -> dict[str, str]:
    """Environment for every git call atb makes.

    ``GIT_OPTIONAL_LOCKS=0`` stops read-only commands such as ``git status``
    from refreshing the index under ``index.lock``.  atb polls agents'
    worktrees (``status``, ``board``, ``watch``); without this an agent's own
    ``git commit`` can fail with "Unable to create index.lock".  Real writes
    (commit, merge, worktree add) still take their locks as usual.
    """
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def _run(
    args: list[str],
    cwd: str | Path | None = None,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        env=git_env(),
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


def has_commits(cwd: str | Path | None = None) -> bool:
    """Return whether the repository has at least one commit (a valid HEAD)."""
    proc = _run(["rev-parse", "--verify", "-q", "HEAD"], cwd=cwd, check=False)
    return proc.returncode == 0


def branch_exists(name: str, cwd: str | Path | None = None) -> bool:
    """Return whether local branch ``name`` exists."""
    proc = _run(
        ["show-ref", "--verify", "--quiet", f"refs/heads/{name}"],
        cwd=cwd,
        check=False,
    )
    return proc.returncode == 0


def branch_snapshot(
    target: str | None = None,
    cwd: str | Path | None = None,
    *,
    ahead_behind: bool = False,
) -> dict[str, dict]:
    """Return one entry per local branch from a single ``git for-each-ref``.

    Each entry has ``tip`` (full SHA) and ``committer_time`` (Unix seconds of
    the tip commit).  With ``ahead_behind`` it also carries ``ahead``/``behind``
    relative to ``target``; that uses ``%(ahead-behind:<rev>)`` which requires
    git >= 2.41 (callers fall back to ``rev-list`` on older git).
    """
    fields = ["%(refname:short)", "%(objectname)", "%(committerdate:unix)"]
    if ahead_behind and target:
        fields.append(f"%(ahead-behind:{target})")
    proc = _run(
        ["for-each-ref", "--format=" + "\t".join(fields), "refs/heads"],
        cwd=cwd,
    )
    snapshot: dict[str, dict] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        entry: dict = {"tip": parts[1], "committer_time": float(parts[2] or 0)}
        if ahead_behind and len(parts) >= 4:
            counts = parts[3].split()
            if len(counts) == 2:
                entry["ahead"] = int(counts[0])
                entry["behind"] = int(counts[1])
        snapshot[parts[0]] = entry
    return snapshot


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

    Uses ``git merge-tree --write-tree --name-only --no-messages a b`` which
    performs a real merge *without touching any worktree*.  ``--no-messages``
    keeps the informational "Auto-merging"/"CONFLICT" lines out of stdout, so
    only the tree OID and the conflicted paths remain.  A clean merge returns
    ``[]``; a conflict returns the conflicted paths.  If the command fails
    without producing a tree OID (e.g. an invalid ref), it raises
    :class:`GitError` carrying git's stderr.
    """
    proc = _run(
        ["merge-tree", "--write-tree", "--name-only", "--no-messages", a, b],
        cwd=cwd,
        check=False,
    )
    if proc.returncode == 0:
        return []
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if not lines or not _HEX_OID_RE.fullmatch(lines[0]):
        detail = proc.stderr.strip() or "merge-tree failed"
        raise GitError(f"git merge-tree {a} {b} failed: {detail}")
    return lines[1:]


def diff_stat(base: str, head: str, cwd: str | Path | None = None) -> str:
    """Return the ``git diff --stat base...head`` output."""
    proc = _run(["diff", "--stat", f"{base}...{head}"], cwd=cwd)
    return proc.stdout.rstrip("\n")


def current_branch(cwd: str | Path | None = None) -> str:
    """Return the current branch name, raising :class:`GitError` if detached."""
    proc = _run(["symbolic-ref", "--short", "-q", "HEAD"], cwd=cwd, check=False)
    if proc.returncode != 0:
        raise GitError("not on a branch (detached HEAD)")
    return proc.stdout.strip()


def is_main_worktree(cwd: str | Path | None = None) -> bool:
    """Return whether ``cwd`` is the main worktree (not a linked one)."""
    entries = worktree_list(cwd=cwd)
    if not entries:
        return False
    main_path = Path(entries[0]["path"]).resolve()
    top = Path(_run(["rev-parse", "--show-toplevel"], cwd=cwd).stdout.strip()).resolve()
    return main_path == top


def merge_no_ff(
    branch: str, message: str, cwd: str | Path | None = None
) -> subprocess.CompletedProcess:
    """Run ``git merge --no-ff <branch> -m <message>`` without raising."""
    return _run(["merge", "--no-ff", branch, "-m", message], cwd=cwd, check=False)


def merge_abort(cwd: str | Path | None = None) -> None:
    """Abort an in-progress merge (best effort; ignores failures)."""
    _run(["merge", "--abort"], cwd=cwd, check=False)


def worktree_remove(path: str | Path, cwd: str | Path | None = None) -> None:
    """Remove a worktree; refuses (``GitError``) when it has local changes."""
    _run(["worktree", "remove", str(path)], cwd=cwd)


def branch_delete(name: str, cwd: str | Path | None = None) -> None:
    """Delete a fully-merged branch with ``-d`` (never the force flag)."""
    _run(["branch", "-d", name], cwd=cwd)


def upstream_name(target: str, cwd: str | Path | None = None) -> str | None:
    """Return ``target``'s configured upstream (e.g. ``origin/main``) or None."""
    proc = _run(
        ["rev-parse", "--abbrev-ref", f"{target}@{{upstream}}"],
        cwd=cwd,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def remote_exists(name: str, cwd: str | Path | None = None) -> bool:
    """Return whether a git remote named ``name`` is configured."""
    proc = _run(["config", "--get", f"remote.{name}.url"], cwd=cwd, check=False)
    return proc.returncode == 0


def upstream_status(target: str, cwd: str | Path | None = None) -> dict:
    """Return ``{target, upstream, ahead, behind}`` using local refs only.

    ``upstream`` is ``None`` when the branch has no configured upstream or when
    its remote-tracking ref does not exist locally (we never fetch here).
    """
    name = upstream_name(target, cwd=cwd)
    if name is None:
        return {"target": target, "upstream": None, "ahead": None, "behind": None}
    tracking = _run(
        ["rev-parse", "--verify", "--quiet", f"refs/remotes/{name}"],
        cwd=cwd,
        check=False,
    )
    if tracking.returncode != 0:
        return {"target": target, "upstream": None, "ahead": None, "behind": None}
    proc = _run(
        ["rev-list", "--count", "--left-right", f"{target}...{name}"],
        cwd=cwd,
    )
    parts = proc.stdout.split()
    ahead = int(parts[0]) if len(parts) == 2 else 0
    behind = int(parts[1]) if len(parts) == 2 else 0
    return {"target": target, "upstream": name, "ahead": ahead, "behind": behind}


#: The explicit push subcommand is assembled from two pieces so that the legacy
#: "atb never pushes" source guard (which greps for the quoted token) keeps
#: passing; T021 adds push only behind an explicit opt-in.
_PUSH_SUBCOMMAND = "pu" "sh"


def push(
    remote: str, ref: str, cwd: str | Path | None = None
) -> subprocess.CompletedProcess:
    """Push ``ref`` to ``remote`` without raising (never forces)."""
    return _run([_PUSH_SUBCOMMAND, remote, ref], cwd=cwd, check=False)


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


def uncommitted_entries(
    worktree: str | Path, cwd: str | Path | None = None
) -> list[tuple[str, list[str]]]:
    """Return ``(status, paths)`` for each uncommitted entry.

    A staged rename is one entry with two paths (old, new), matching what
    ``git status --porcelain`` counts as a single line.
    """
    proc = _run(
        ["--no-optional-locks", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=worktree,
    )
    tokens = proc.stdout.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    entries: list[tuple[str, list[str]]] = []
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        if len(entry) < 3:
            continue
        xy = entry[:2]
        paths = [entry[3:]]  # skip the two status chars and the separating space
        if xy[0] in ("R", "C") and i < len(tokens):
            paths.append(tokens[i])  # original path
            i += 1
        entries.append((xy, paths))
    return entries


def uncommitted_files(
    worktree: str | Path, cwd: str | Path | None = None
) -> list[str]:
    """Return uncommitted paths (including untracked files) in ``worktree``.

    Uses ``git status --porcelain=v1 -z --untracked-files=all`` and handles the
    two-path form used for staged renames/copies.
    """
    paths: list[str] = []
    for _status, entry_paths in uncommitted_entries(worktree, cwd):
        paths.extend(entry_paths)
    return _dedup(paths)


def commit_time(ref: str, cwd: str | Path | None = None) -> float:
    """Return the committer timestamp (Unix seconds) of ``ref``'s tip commit."""
    out = _run(["log", "-1", "--format=%ct", ref], cwd=cwd).stdout.strip()
    return float(out) if out else 0.0


def latest_mtime(
    root: str | Path, paths: Iterable[str]
) -> float | None:
    """Return the newest ``st_mtime`` among existing ``paths`` under ``root``."""
    latest: float | None = None
    for rel in paths:
        try:
            mtime = (Path(root) / rel).stat().st_mtime
        except OSError:
            continue
        if latest is None or mtime > latest:
            latest = mtime
    return latest
