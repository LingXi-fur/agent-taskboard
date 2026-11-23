"""One-shot read-only environment and board health checks (``atb doctor``)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import checks, config, gc, gitops
from .errors import AtbError
from .sheet import Sheet, parse

MIN_GIT_VERSION = (2, 38)


def _check_entry(
    name: str, status: str, message: str, hint: str | None = None
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "message": message,
        "hint": hint,
    }


def _writable_probe(path: Path) -> Path:
    """Return ``path`` or its nearest existing ancestor without touching disk."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return probe


def _check_git_version() -> dict[str, Any]:
    ver = config.git_version()
    if ver is None:
        return _check_entry(
            "git",
            "FAIL",
            "git executable not found or version unreadable",
            "install git >= 2.38",
        )
    ver_str = f"{ver[0]}.{ver[1]}"
    if ver < MIN_GIT_VERSION:
        return _check_entry(
            "git",
            "FAIL",
            f"git {ver_str} < 2.38; conflict detection needs git merge-tree --write-tree",
            "upgrade git to >= 2.38",
        )
    return _check_entry("git", "OK", f"git {ver_str} (>= 2.38)")


def _check_config(repo_root: Path) -> tuple[dict[str, Any], config.Config | None]:
    config_path = repo_root / ".taskboard" / "config.toml"
    if not config_path.exists():
        return (
            _check_entry(
                "config",
                "FAIL",
                ".taskboard/config.toml not found",
                "run: atb init",
            ),
            None,
        )
    try:
        cfg = config.load_config(repo_root)
    except AtbError as exc:
        return (
            _check_entry(
                "config",
                "FAIL",
                f"cannot load config: {exc}",
                "fix .taskboard/config.toml",
            ),
            None,
        )

    issues: list[str] = []
    hints: list[str] = []
    if not gitops.branch_exists(cfg.target, cwd=repo_root):
        issues.append(f"target branch {cfg.target!r} does not exist")
        hints.append(f"create {cfg.target!r} or update target in .taskboard/config.toml")

    wt_root = config.resolve_worktree_root(cfg, repo_root)
    probe = _writable_probe(wt_root if wt_root.exists() else wt_root.parent)
    if not os.access(probe, os.W_OK | os.X_OK):
        issues.append(f"worktree_root location {str(probe)!r} is not writable")
        hints.append("fix directory permissions or change worktree_root in .taskboard/config.toml")

    if issues:
        return (
            _check_entry("config", "FAIL", "; ".join(issues), "; ".join(hints)),
            cfg,
        )
    return (
        _check_entry(
            "config",
            "OK",
            f"config loaded (target={cfg.target}, worktree_root={wt_root})",
        ),
        cfg,
    )


def _check_sheets(repo_root: Path) -> tuple[dict[str, Any], list[Sheet]]:
    tasks_dir = repo_root / ".taskboard" / "tasks"
    if not tasks_dir.is_dir():
        return (
            _check_entry(
                "sheets",
                "OK",
                "0 task sheet(s) (no .taskboard/tasks directory yet)",
            ),
            [],
        )

    valid_sheets: list[Sheet] = []
    problems: list[str] = []
    for path in sorted(tasks_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
            sheet = parse(text)
        except (AtbError, UnicodeDecodeError, OSError) as exc:
            problems.append(f"{path.name} failed to parse ({exc})")
            continue
        if path.stem != sheet.id:
            problems.append(f"{path.name} has mismatched id {sheet.id!r}")
        valid_sheets.append(sheet)

    by_id = {sheet.id: sheet for sheet in valid_sheets}
    for sheet in valid_sheets:
        for dep in sheet.depends:
            if dep not in by_id:
                problems.append(f"{sheet.id} depends on missing task {dep!r}")

    if problems:
        return (
            _check_entry(
                "sheets",
                "FAIL",
                "; ".join(problems),
                "fix or remove invalid fields in .taskboard/tasks/",
            ),
            valid_sheets,
        )
    return (
        _check_entry("sheets", "OK", f"{len(valid_sheets)} task sheet(s) valid"),
        valid_sheets,
    )


def _check_active_tasks(
    valid_sheets: list[Sheet], cfg: config.Config | None, repo_root: Path
) -> dict[str, Any]:
    if cfg is None:
        return _check_entry(
            "active",
            "WARN",
            "skipped active task check because config could not be loaded",
            "fix config first",
        )
    active = [s for s in valid_sheets if s.status in checks.ACTIVE_STATUSES]
    if not active:
        return _check_entry("active", "OK", "0 active task(s)")

    problems: list[str] = []
    hints: list[str] = []
    for sheet in active:
        if not sheet.branch:
            problems.append(f"{sheet.id} ({sheet.status}) has no branch set")
            hints.append(f"atb start {sheet.id}")
            continue
        has_branch = gitops.branch_exists(sheet.branch, cwd=repo_root)
        wt = checks.resolve_worktree(cfg, sheet)
        has_wt = wt.exists()
        if not has_branch or not has_wt:
            missing = []
            if not has_branch:
                missing.append(f"branch {sheet.branch!r}")
            if not has_wt:
                missing.append(f"worktree {str(wt)!r}")
            problems.append(
                f"{sheet.id} ({sheet.status}) missing {' and '.join(missing)}"
            )
            hints.append(f"atb start {sheet.id} (or update task status)")
            continue
        try:
            actual_branch = gitops.current_branch(cwd=wt)
        except gitops.GitError as exc:
            problems.append(f"{sheet.id} worktree error: {exc}")
            hints.append(f"git -C {str(wt)!r} switch {sheet.branch}")
            continue
        if actual_branch != sheet.branch:
            problems.append(
                f"{sheet.id} worktree is on {actual_branch!r} instead of {sheet.branch!r}"
            )
            hints.append(f"git -C {str(wt)!r} switch {sheet.branch}")

    if problems:
        dedup_hints = list(dict.fromkeys(hints))
        return _check_entry(
            "active", "FAIL", "; ".join(problems), "; ".join(dedup_hints)
        )
    return _check_entry(
        "active", "OK", f"all {len(active)} active task(s) healthy"
    )


def _check_gc_leftovers(
    valid_sheets: list[Sheet], cfg: config.Config | None, repo_root: Path
) -> dict[str, Any]:
    if cfg is None:
        return _check_entry(
            "leftovers",
            "WARN",
            "skipped leftover check because config could not be loaded",
            "fix config first",
        )
    candidates = gc.collect_candidates(valid_sheets, cfg, repo_root)
    if not candidates:
        return _check_entry(
            "leftovers", "OK", "no leftover worktrees or branches for done/dropped tasks"
        )
    ids = ", ".join(item["id"] for item in candidates)
    return _check_entry(
        "leftovers",
        "WARN",
        f"{len(candidates)} done/dropped task(s) still have worktree or branch: {ids}",
        "run: atb gc",
    )


def _check_orphan_worktrees(
    valid_sheets: list[Sheet], cfg: config.Config | None, repo_root: Path
) -> dict[str, Any]:
    if cfg is None:
        return _check_entry(
            "orphans",
            "WARN",
            "skipped orphan worktree check because config could not be loaded",
            "fix config first",
        )
    wt_root = config.resolve_worktree_root(cfg, repo_root).resolve()
    known_wts = {
        checks.resolve_worktree(cfg, sheet).resolve() for sheet in valid_sheets
    }
    tasks_dir = repo_root / ".taskboard" / "tasks"
    if tasks_dir.is_dir():
        for sheet_path in tasks_dir.glob("*.md"):
            known_wts.add((wt_root / sheet_path.stem).resolve())
    try:
        entries = gitops.worktree_list(cwd=repo_root)
    except gitops.GitError as exc:
        return _check_entry(
            "orphans", "FAIL", f"cannot list worktrees: {exc}", "check git worktree list"
        )

    main_path = Path(entries[0]["path"]).resolve() if entries else None
    orphans: list[str] = []
    for entry in entries:
        wt_path = Path(entry["path"]).resolve()
        if main_path is not None and wt_path == main_path:
            continue
        if wt_path.parent == wt_root and wt_path not in known_wts:
            orphans.append(str(wt_path))

    if orphans:
        return _check_entry(
            "orphans",
            "WARN",
            f"{len(orphans)} orphan worktree(s) under {wt_root}: {', '.join(orphans)}",
            f"git worktree remove {orphans[0]}",
        )
    return _check_entry("orphans", "OK", f"no orphan worktrees under {wt_root}")


def _check_upstream(cfg: config.Config | None, repo_root: Path) -> dict[str, Any]:
    """WARN when the target has unpushed commits; INFO when it has no upstream."""
    if cfg is None:
        return _check_entry(
            "upstream",
            "INFO",
            "skipped upstream check because config could not be loaded",
            "fix config first",
        )
    info = gitops.upstream_status(cfg.target, cwd=repo_root)
    if info["upstream"] is None:
        return _check_entry(
            "upstream",
            "INFO",
            f"target branch {cfg.target!r} has no upstream (push is optional)",
            f"git branch --set-upstream-to=origin/{cfg.target} {cfg.target}",
        )
    if info["ahead"] and info["ahead"] > 0:
        return _check_entry(
            "upstream",
            "WARN",
            f"{info['ahead']} commit(s) on {cfg.target!r} not pushed "
            f"to {info['upstream']!r}",
            "git push",
        )
    return _check_entry(
        "upstream", "OK", f"{cfg.target!r} is up to date with {info['upstream']!r}"
    )


def _check_gitignore(repo_root: Path) -> dict[str, Any]:
    """WARN when local-only .taskboard state could be committed by mistake."""
    path = repo_root / ".gitignore"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    lines = {line.strip() for line in text.splitlines()}
    if (
        ".taskboard/notes/" in lines
        or ".taskboard/" in lines
        or ".taskboard/*" in lines
    ):
        return _check_entry(
            "gitignore", "OK", ".taskboard/notes/ is ignored by git"
        )
    return _check_entry(
        "gitignore",
        "WARN",
        ".taskboard/notes/ is not in .gitignore (atb note stores notes there)",
        "echo '.taskboard/notes/' >> .gitignore (atb init does this for new repos)",
    )


def _check_adapters() -> list[dict[str, Any]]:
    """One entry per session adapter: OK / INFO (store missing) / WARN (error).

    A broken or missing adapter is never a FAIL: observing sessions is
    optional, so it must not turn `atb doctor` red.
    """
    from . import adapters

    items: list[dict[str, Any]] = []
    state = adapters.load_adapters()
    for failure in state.errors:
        items.append(
            _check_entry(
                f"adapter:{failure['adapter']}",
                "WARN",
                f"adapter failed to load: {failure['error']}",
                "fix or uninstall the plugin",
            )
        )
    for adapter in state.adapters:
        name = getattr(adapter, "name", type(adapter).__name__)
        try:
            available = bool(adapter.available())
        except Exception as exc:
            items.append(
                _check_entry(
                    f"adapter:{name}",
                    "WARN",
                    f"adapter error: {type(exc).__name__}: {exc}",
                    "the store may have changed; update atb",
                )
            )
            continue
        if not available:
            items.append(
                _check_entry(
                    f"adapter:{name}",
                    "INFO",
                    "store not found; adapter inactive",
                    "install the tool or point its ATB_*_HOME at the store",
                )
            )
            continue
        # cheap read probe: a store that exists but cannot be parsed is a WARN
        try:
            probe_since = datetime.now(timezone.utc) - timedelta(minutes=5)
            for _ in adapter.sessions(probe_since):
                pass  # only recent files are read, so this stays fast
        except Exception as exc:
            items.append(
                _check_entry(
                    f"adapter:{name}",
                    "WARN",
                    f"adapter error: {type(exc).__name__}: {exc}",
                    "the store format may have changed; update atb",
                )
            )
            continue
        items.append(
            _check_entry(
                f"adapter:{name}", "OK", "store found; sessions observable"
            )
        )
    return items


def run_doctor(repo_root: Path) -> tuple[dict[str, Any], int]:
    """Run all read-only health checks and return ``(payload, exit_code)``."""
    git_item = _check_git_version()
    cfg_item, cfg = _check_config(repo_root)
    sheets_item, valid_sheets = _check_sheets(repo_root)
    active_item = _check_active_tasks(valid_sheets, cfg, repo_root)
    upstream_item = _check_upstream(cfg, repo_root)
    leftover_item = _check_gc_leftovers(valid_sheets, cfg, repo_root)
    orphan_item = _check_orphan_worktrees(valid_sheets, cfg, repo_root)
    gitignore_item = _check_gitignore(repo_root)
    adapter_items = _check_adapters()

    items = [
        git_item,
        cfg_item,
        sheets_item,
        active_item,
        upstream_item,
        leftover_item,
        orphan_item,
        gitignore_item,
    ] + adapter_items
    has_fail = any(item["status"] == "FAIL" for item in items)
    has_warn = any(item["status"] == "WARN" for item in items)
    overall = "FAIL" if has_fail else ("WARN" if has_warn else "OK")
    exit_code = 1 if has_fail else 0
    return {
        "status": overall,
        "exit_code": exit_code,
        "checks": items,
    }, exit_code


def render_doctor(result: dict[str, Any]) -> str:
    """Render one check per line with OK/WARN/FAIL status, detail, and hint."""
    lines: list[str] = []
    for item in result["checks"]:
        hint = f" (hint: {item['hint']})" if item.get("hint") else ""
        lines.append(
            f"{item['status']:<4}  {item['name']}: {item['message']}{hint}"
        )
    return "\n".join(lines)
