"""Clean up leftover worktrees and branches for ``done`` / ``dropped`` tasks."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from . import checks, config, gitops
from .sheet import Sheet

GC_STATUSES = ("done", "dropped")


def is_branch_merged(branch: str, target: str, cwd: str | Path) -> bool:
    """Return whether ``branch`` is an ancestor of ``target`` locally."""
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", branch, target],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _describe_targets(has_worktree: bool, has_branch: bool) -> str:
    parts: list[str] = []
    if has_worktree:
        parts.append("worktree")
    if has_branch:
        parts.append("branch")
    return " and ".join(parts)


def evaluate_candidate(
    sheet: Sheet,
    cfg: config.Config,
    repo_root: Path,
    *,
    include_unmerged: bool = False,
) -> dict[str, Any]:
    """Classify one ``done`` or ``dropped`` task for garbage collection."""
    worktree_path = checks.resolve_worktree(cfg, sheet)
    has_worktree = worktree_path.exists()
    branch_name = sheet.branch or None
    has_branch = bool(
        branch_name and gitops.branch_exists(branch_name, cwd=repo_root)
    )

    worktree_str = str(worktree_path) if has_worktree else None
    branch_str = branch_name if has_branch else None

    if not has_worktree and not has_branch:
        return {
            "id": sheet.id,
            "status": sheet.status,
            "worktree": None,
            "branch": None,
            "action": "skip: missing",
            "reason": "worktree and branch already absent",
        }

    if has_worktree:
        try:
            dirty = gitops.uncommitted_files(worktree_path)
        except gitops.GitError as exc:
            return {
                "id": sheet.id,
                "status": sheet.status,
                "worktree": worktree_str,
                "branch": branch_str,
                "action": "skip: dirty",
                "reason": f"cannot inspect worktree status: {exc}",
            }
        if dirty:
            return {
                "id": sheet.id,
                "status": sheet.status,
                "worktree": worktree_str,
                "branch": branch_str,
                "action": "skip: dirty",
                "reason": f"worktree has {len(dirty)} uncommitted file(s)",
            }

    if has_branch and branch_name is not None:
        if not is_branch_merged(branch_name, cfg.target, repo_root):
            if include_unmerged and has_worktree:
                return {
                    "id": sheet.id,
                    "status": sheet.status,
                    "worktree": worktree_str,
                    "branch": branch_str,
                    "action": "remove-worktree",
                    "reason": (
                        f"clean worktree can be removed; unmerged branch {branch_name!r} "
                        "is kept (manual confirmation required)"
                    ),
                }
            return {
                "id": sheet.id,
                "status": sheet.status,
                "worktree": worktree_str,
                "branch": branch_str,
                "action": "skip: unmerged",
                "reason": (
                    f"branch {branch_name!r} is not merged into {cfg.target!r}; "
                    "kept (manual confirmation required)"
                ),
            }

    return {
        "id": sheet.id,
        "status": sheet.status,
        "worktree": worktree_str,
        "branch": branch_str,
        "action": "remove",
        "reason": f"ready to remove {_describe_targets(has_worktree, has_branch)}",
    }


def collect_candidates(
    sheets: list[Sheet],
    cfg: config.Config,
    repo_root: Path,
    *,
    task_ids: list[str] | None = None,
    include_unmerged: bool = False,
) -> list[dict[str, Any]]:
    """Collect ``done`` / ``dropped`` gc candidates in display order."""
    by_id = {sheet.id: sheet for sheet in sheets}
    if task_ids:
        selected: list[Sheet] = []
        seen: set[str] = set()
        for task_id in task_ids:
            if task_id in seen:
                continue
            seen.add(task_id)
            sheet = by_id[task_id]
            if sheet.status in GC_STATUSES:
                selected.append(sheet)
    else:
        selected = [sheet for sheet in sheets if sheet.status in GC_STATUSES]

    candidates: list[dict[str, Any]] = []
    for sheet in selected:
        item = evaluate_candidate(
            sheet, cfg, repo_root, include_unmerged=include_unmerged
        )
        if not task_ids and item["action"] == "skip: missing":
            continue
        candidates.append(item)
    return candidates


def apply_candidate(candidate: dict[str, Any], repo_root: Path) -> str | None:
    """Execute cleanup for one actionable candidate; return error message or None."""
    action = candidate["action"]
    if action not in ("remove", "remove-worktree"):
        return None

    worktree = candidate.get("worktree")
    if worktree:
        try:
            gitops.worktree_remove(worktree, cwd=repo_root)
        except gitops.GitError as exc:
            return f"worktree_remove failed: {exc}"

    branch = candidate.get("branch")
    if action == "remove" and branch:
        try:
            gitops.branch_delete(branch, cwd=repo_root)
        except gitops.GitError as exc:
            return f"branch_delete failed: {exc}"

    return None


def run_gc(
    repo_root: Path,
    cfg: config.Config,
    sheets: list[Sheet],
    *,
    task_ids: list[str] | None = None,
    apply: bool = False,
    include_unmerged: bool = False,
) -> tuple[dict[str, Any], int]:
    """Evaluate (and optionally apply) garbage collection across task sheets."""
    candidates = collect_candidates(
        sheets,
        cfg,
        repo_root,
        task_ids=task_ids,
        include_unmerged=include_unmerged,
    )
    errors: list[dict[str, str]] = []
    if apply:
        for item in candidates:
            if item["action"] not in ("remove", "remove-worktree"):
                item["result"] = "skipped"
                continue
            err = apply_candidate(item, repo_root)
            if err is not None:
                errors.append({"id": item["id"], "error": err})
                item["error"] = err
                item["result"] = "failed"
            else:
                item["result"] = "removed"

    result = {
        "apply": apply,
        "include_unmerged": include_unmerged,
        "candidates": candidates,
        "errors": errors,
    }
    if apply:
        result["summary"] = {
            outcome: sum(1 for item in candidates if item.get("result") == outcome)
            for outcome in ("removed", "skipped", "failed")
        }
    return result, (1 if errors else 0)


def render_gc(result: dict[str, Any]) -> str:
    """Render a human-readable summary of ``run_gc`` results."""
    candidates = result["candidates"]
    if not candidates:
        return "nothing to clean up (no done/dropped worktrees or branches found)"

    lines: list[str] = []
    for item in candidates:
        wt = item["worktree"] or "-"
        br = item["branch"] or "-"
        outcome = item.get("result")
        if outcome == "removed":
            what = (
                "removed worktree; branch kept"
                if item["action"] == "remove-worktree"
                else f"removed {_describe_targets(bool(item['worktree']), bool(item['branch']))}"
            )
            lines.append(f"{item['id']} [{item['status']}] {what} (worktree={wt}, branch={br})")
        elif outcome == "failed":
            lines.append(f"{item['id']} [{item['status']}] FAILED (worktree={wt}, branch={br})")
            lines.append(f"  error: {item['error']}")
        else:
            lines.append(
                f"{item['id']} [{item['status']}] -> {item['action']}: "
                f"{item['reason']} (worktree={wt}, branch={br})"
            )

    if result["apply"]:
        summary = result.get("summary", {})
        lines.append(
            f"removed {summary.get('removed', 0)}, skipped {summary.get('skipped', 0)}, "
            f"failed {summary.get('failed', 0)}"
        )
    else:
        lines.append("dry-run only; re-run with --apply to remove eligible items")
    return "\n".join(lines)
