"""Command-line interface: ``atb init/new/list/show/start/prompt/check``.

This module only wires arguments to output; the parsing/scope/git logic lives
in ``config.py``, ``sheet.py``, ``scope.py``, ``gitops.py`` and ``prompts.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from . import checks, config, doctor, gc, gitops, history, prompts, render, scope, stats
from .errors import AtbError, CheckError, ConfigError, SheetError
from .sheet import (
    STATUSES,
    Sheet,
    TEMPLATE_BODY,
    append_rework_note,
    dump,
    extract_section,
    next_id,
    normalize_body,
    parse,
    slugify,
    to_dict,
)


def get_version() -> str:
    """Return the package version, preferring installed metadata.

    Falls back to the in-package ``__version__`` when the distribution is not
    installed (e.g. running straight from ``src`` via ``PYTHONPATH``).
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("agent-taskboard")
    except PackageNotFoundError:
        from . import __version__

        return __version__


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #

def _display_width(text: str) -> int:
    """Display width of ``text`` counting East-Asian wide chars as 2 columns."""
    return sum(
        2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        for char in text
    )


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _render_table(rows: list[tuple[str, str, str, str]]) -> str:
    """Render ``(id, status, agent, title)`` rows as an aligned text table."""
    headers = ("ID", "STATUS", "AGENT", "TITLE")
    columns = list(zip(headers, *rows)) if rows else list(zip(headers))
    widths = [max(_display_width(cell) for cell in col) for col in columns]

    def fmt(cells: tuple[str, ...]) -> str:
        return "  ".join(_pad(cells[i], widths[i]) for i in range(len(cells)))

    lines = [fmt(headers)]
    lines.extend(fmt(tuple(row)) for row in rows)
    return "\n".join(lines)


def _format_field(key: str, value: object) -> str:
    if isinstance(value, list):
        if not value:
            return f"{key}: []"
        lines = [f"{key}:"]
        lines.extend(f"  - {item}" for item in value)
        return "\n".join(lines)
    return f"{key}: {value}"


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _tasks_dir(repo_root: Path) -> Path:
    return repo_root / ".taskboard" / "tasks"


def _require_initialized(repo_root: Path) -> None:
    """Raise a friendly error when ``.taskboard/`` has not been initialised."""
    if not (repo_root / ".taskboard" / "config.toml").exists():
        raise ConfigError("no .taskboard/ found here; run 'atb init' first")


def _require_commits(repo_root: Path) -> None:
    """Raise a friendly error when the repository has no commits yet."""
    if not gitops.has_commits(repo_root):
        raise ConfigError(
            "this repository has no commits yet; make an initial commit first"
        )


def _load_task(repo_root: Path, task_id: str) -> Sheet:
    path = _tasks_dir(repo_root) / f"{task_id}.md"
    if not path.exists():
        raise AtbError(f"task {task_id!r} not found")
    try:
        return parse(path.read_text(encoding="utf-8"))
    except SheetError as exc:
        raise SheetError(f"{path.name}: {exc}") from exc
    except (UnicodeDecodeError, OSError) as exc:
        raise SheetError(f"{path.name}: cannot read task sheet ({exc})") from exc


def _load_tasks(repo_root: Path, status: str | None = None) -> list[Sheet]:
    tasks_dir = _tasks_dir(repo_root)
    sheets: list[Sheet] = []
    if not tasks_dir.is_dir():
        return sheets
    for path in sorted(tasks_dir.glob("*.md")):
        try:
            sheet = parse(path.read_text(encoding="utf-8"))
        except (AtbError, UnicodeDecodeError, OSError):
            continue  # skip sheets that cannot be read or do not parse
        if status is not None and sheet.status != status:
            continue
        sheets.append(sheet)
    return sheets


def _render_branch(pattern: str, agent: str, slug: str) -> str:
    return pattern.replace("{agent}", agent).replace("{slug}", slug)


def _dedup(paths: list[str]) -> list[str]:
    return list(dict.fromkeys(paths))


# --------------------------------------------------------------------------- #
# init / new / list / show
# --------------------------------------------------------------------------- #

def cmd_init(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    tb_dir = repo_root / ".taskboard"
    config_path = tb_dir / "config.toml"
    tasks_dir = tb_dir / "tasks"

    already_exists = config_path.exists()
    tb_dir.mkdir(parents=True, exist_ok=True)
    tasks_dir.mkdir(parents=True, exist_ok=True)

    if already_exists:
        message = f".taskboard already exists; nothing overwritten ({config_path})"
    else:
        config_path.write_text(config.DEFAULT_CONFIG_TOML, encoding="utf-8")
        message = f"initialized {config_path}"

    version = config.git_version()
    if version is not None and version < (2, 38):
        print(
            f"warning: git {version[0]}.{version[1]} is older than 2.38; "
            "'atb conflicts' needs merge-tree --write-tree",
            file=sys.stderr,
        )

    if args.json:
        print(json.dumps({
            "initialized": not already_exists,
            "already_exists": already_exists,
            "config_path": str(config_path),
        }, ensure_ascii=False))
    else:
        print(message)
    return 0


def _read_body_argument(value: str) -> str:
    """Read a ``--body`` value: ``-`` is stdin, ``@FILE`` is UTF-8, else literal."""
    if value == "-":
        buffer = getattr(sys.stdin, "buffer", None)
        if buffer is not None:
            return buffer.read().decode("utf-8", errors="replace")
        return sys.stdin.read()
    if value.startswith("@"):
        path = Path(value[1:])
        if not path.is_file():
            raise AtbError(f"body file {path} not found")
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise AtbError(f"body file {path} is not valid UTF-8: {exc}") from exc
        except OSError as exc:
            raise AtbError(f"cannot read body file {path}: {exc}") from exc
    return value


def _copy_directory(copy_to: str | None) -> Path | None:
    """Validate ``--copy-to`` and return it; never creates the directory."""
    if not copy_to:
        return None
    directory = Path(copy_to)
    if not directory.exists():
        raise AtbError(f"--copy-to directory {directory} does not exist")
    if not directory.is_dir():
        raise AtbError(f"--copy-to path {directory} is not a directory")
    return directory


def _dump_checked(sheet: Sheet) -> str:
    """Serialize ``sheet`` and refuse to return text that would not parse."""
    try:
        text = dump(sheet)
        parse(text)
    except SheetError as exc:
        raise AtbError(f"refusing to write an invalid task sheet: {exc}") from exc
    return text


def _copy_sheet(path: Path, copy_dir: Path | None, task_id: str) -> Path | None:
    """Copy a freshly written sheet to ``DIR/ATB_TASK_<ID>.md``."""
    if copy_dir is None:
        return None
    destination = copy_dir / f"ATB_TASK_{task_id}.md"
    try:
        shutil.copyfile(path, destination)
    except OSError as exc:
        raise AtbError(
            f"wrote {path} but could not copy it to {destination}: {exc}"
        ) from exc
    return destination


def cmd_new(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    cfg = config.load_config(repo_root)
    tasks_dir = _tasks_dir(repo_root)

    body = TEMPLATE_BODY
    if args.body is not None:
        body = normalize_body(_read_body_argument(args.body))
    copy_dir = _copy_directory(args.copy_to)

    tasks_dir.mkdir(parents=True, exist_ok=True)

    for dep in args.depends:
        if not (tasks_dir / f"{dep}.md").exists():
            raise AtbError(f"dependency {dep!r} does not exist under {tasks_dir}")

    task_id = next_id(tasks_dir, cfg.id_prefix)
    sheet = Sheet(
        id=task_id,
        title=args.title,
        agent=args.agent,
        status="todo",
        allow=list(args.allow),
        deny=list(args.deny),
        depends=list(args.depends),
        created=datetime.now(timezone.utc).replace(microsecond=0),
        body=body,
    )
    path = tasks_dir / f"{task_id}.md"
    path.write_text(_dump_checked(sheet), encoding="utf-8")
    copy_path = _copy_sheet(path, copy_dir, task_id)

    if args.json:
        payload = {
            "id": task_id,
            "path": str(path),
            "title": args.title,
        }
        if copy_path is not None:
            payload["copy_path"] = str(copy_path)
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(str(path))
        if copy_path is not None:
            print(f"copied to {copy_path}")
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    sheet = _load_task(repo_root, args.id)

    if sheet.status in ("done", "dropped"):
        raise AtbError(
            f"task {args.id} has status {sheet.status!r}; "
            "done/dropped tasks cannot be edited"
        )

    copy_dir = _copy_directory(args.copy_to)

    changed = False
    if args.body is not None:
        sheet.body = normalize_body(_read_body_argument(args.body))
        changed = True
    if args.title is not None:
        sheet.title = args.title
        changed = True
    if args.agent is not None:
        sheet.agent = args.agent
        changed = True

    if args.remove_allow:
        removed = set(args.remove_allow)
        sheet.allow = [glob for glob in sheet.allow if glob not in removed]
        changed = True
    if args.remove_deny:
        removed = set(args.remove_deny)
        sheet.deny = [glob for glob in sheet.deny if glob not in removed]
        changed = True
    for glob in args.add_allow:
        if not glob:
            raise AtbError("--add-allow values must not be empty")
        if glob not in sheet.allow:
            sheet.allow.append(glob)
        changed = True
    for glob in args.add_deny:
        if not glob:
            raise AtbError("--add-deny values must not be empty")
        if glob not in sheet.deny:
            sheet.deny.append(glob)
        changed = True

    path = _tasks_dir(repo_root) / f"{sheet.id}.md"
    if changed:
        sheet.body = normalize_body(sheet.body)
        path.write_text(_dump_checked(sheet), encoding="utf-8")
    copy_path = _copy_sheet(path, copy_dir, sheet.id)

    print(str(path))
    if copy_path is not None:
        print(f"copied to {copy_path}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    sheets = _load_tasks(repo_root, status=args.status)

    if args.json:
        payload = [
            {"id": s.id, "status": s.status, "agent": s.agent, "title": s.title}
            for s in sheets
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif not sheets:
        print("(no tasks)")
    else:
        print(_render_table([(s.id, s.status, s.agent, s.title) for s in sheets]))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    sheet = _load_task(repo_root, args.id)

    if args.json:
        print(json.dumps(to_dict(sheet), ensure_ascii=False, indent=2))
    else:
        data = to_dict(sheet)
        body = data.pop("body", "")
        for key, value in data.items():
            print(_format_field(key, value))
        if body.strip():
            print()
            print(body.strip("\n"))
    return 0


# --------------------------------------------------------------------------- #
# start
# --------------------------------------------------------------------------- #

def cmd_start(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    _require_commits(repo_root)
    cfg = config.load_config(repo_root)
    sheet = _load_task(repo_root, args.id)
    tasks_dir = _tasks_dir(repo_root)

    if sheet.status not in ("todo", "rework"):
        raise AtbError(
            f"task {args.id} has status {sheet.status!r}; only 'todo' or 'rework' can start"
        )

    # dependency check: all depends must be done (--force skips with a warning)
    unmet: list[str] = []
    for dep in sheet.depends:
        dep_path = tasks_dir / f"{dep}.md"
        if not dep_path.exists() or parse(dep_path.read_text(encoding="utf-8")).status != "done":
            unmet.append(dep)
    if unmet:
        message = f"task {args.id} depends on unfinished task(s): {', '.join(unmet)}"
        if not args.force:
            raise CheckError(message)
        print(f"warning: {message} (continuing because of --force)", file=sys.stderr)

    # baseline: --base > existing base > config.target HEAD
    if args.base:
        base = gitops.rev_parse(args.base, cwd=repo_root)
    elif sheet.base:
        base = sheet.base
    else:
        base = gitops.rev_parse(cfg.target, cwd=repo_root)

    # branch: reuse the sheet's, otherwise generate from branch_pattern
    if sheet.branch:
        branch = sheet.branch
    else:
        slug = slugify(sheet.title, sheet.id)
        branch = _render_branch(cfg.branch_pattern, sheet.agent, slug)

    worktree = config.resolve_worktree_root(cfg) / sheet.id

    if sheet.status == "todo":
        if gitops.branch_exists(branch, cwd=repo_root):
            raise AtbError(f"branch {branch!r} already exists")
        if worktree.exists():
            raise AtbError(f"worktree path {worktree} already exists")
        gitops.worktree_add(worktree, branch, base, cwd=repo_root)
    else:  # rework: reuse the existing worktree and branch
        if not worktree.exists():
            raise AtbError(f"worktree path {worktree} does not exist (cannot rework)")

    # write base/branch/status back, keep the body untouched; never auto-commit
    sheet.base = base
    sheet.branch = branch
    sheet.status = "doing"
    sheet_path = tasks_dir / f"{sheet.id}.md"
    sheet_path.write_text(dump(sheet), encoding="utf-8")

    if args.json:
        print(json.dumps({
            "id": sheet.id,
            "status": sheet.status,
            "base": base,
            "branch": branch,
            "worktree": str(worktree),
        }, ensure_ascii=False))
    else:
        print(f"started {sheet.id}: branch={branch}")
        print(f"worktree: {worktree}")
        print(f"base: {base}")
        print(f"status -> doing")
        print(f"task sheet updated ({sheet_path}); commit it yourself (not auto-committed)")
    return 0


# --------------------------------------------------------------------------- #
# prompt
# --------------------------------------------------------------------------- #

def cmd_prompt(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    cfg = config.load_config(repo_root)
    sheet = _load_task(repo_root, args.id)
    if not sheet.branch:
        raise AtbError(f"task {args.id} has no branch; run 'atb start {args.id}' first")

    worktree = config.resolve_worktree_root(cfg) / sheet.id
    test_cmd = sheet.test or cfg.test
    text = prompts.render_prompt(
        sheet, str(worktree), sheet.branch, test_cmd, lang=args.lang
    )

    if args.json:
        print(json.dumps({
            "text": text,
            "worktree": str(worktree),
            "branch": sheet.branch,
        }, ensure_ascii=False))
    else:
        print(text)
    return 0


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #

def _last_4kb(text: str) -> str:
    data = text.encode("utf-8")
    if len(data) <= 4096:
        return text
    return data[-4096:].decode("utf-8", errors="replace")


def _run_test(
    command: str, worktree: Path, timeout: int, env: dict | None = None
) -> dict:
    argv = shlex.split(command)
    start = time.monotonic()
    if not argv:
        return {"exit_code": 2, "duration": 0.0, "output": "(empty test command)"}
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(worktree),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return {
            "exit_code": 127,
            "duration": round(time.monotonic() - start, 3),
            "output": f"command not found: {argv[0]}",
        }
    try:
        out, _ = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        out = (out or "") + f"\n[timeout after {timeout}s]"
        code = -1
    return {
        "exit_code": code,
        "duration": round(time.monotonic() - start, 3),
        "output": out or "",
    }


def _iter_home_files(root: Path) -> set[str]:
    if not root.exists():
        return set()
    # Always report repo-style separators so the output (and recorded JSON)
    # matches the documented POSIX form on Windows too.
    return {
        p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
    }


def _run_test_isolated(
    command: str, worktree: Path, timeout: int
) -> tuple[dict, list[str]]:
    """Run tests with a throwaway HOME and return files they created under it."""
    home = Path(tempfile.mkdtemp(prefix="atb-home-"))
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CACHE_HOME"] = str(home / ".cache")
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    # Windows tools (and Python's ``Path.home``) look at these instead of HOME.
    env["USERPROFILE"] = str(home)
    env["APPDATA"] = str(home / "AppData" / "Roaming")
    env["LOCALAPPDATA"] = str(home / "AppData" / "Local")
    # The throwaway HOME has no ~/.gitconfig, so give tests a usable identity.
    env.setdefault("GIT_AUTHOR_NAME", "atb")
    env.setdefault("GIT_AUTHOR_EMAIL", "atb@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", "atb")
    env.setdefault("GIT_COMMITTER_EMAIL", "atb@example.invalid")
    before = _iter_home_files(home)
    try:
        result = _run_test(command, worktree, timeout, env=env)
    finally:
        after = _iter_home_files(home)
        shutil.rmtree(home, ignore_errors=True)
    return result, sorted(after - before)


def _append_run(repo_root: Path, task_id: str, command: str, result: dict) -> None:
    runs_dir = repo_root / ".taskboard" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "exit_code": result["exit_code"],
        "duration": result["duration"],
        "command": command,
        "output": _last_4kb(result["output"]),
    }
    if "home_writes" in result:
        record["home_writes"] = result["home_writes"]
        record["home_files"] = result.get("home_files", [])
        if result.get("home_files_more"):
            record["home_files_more"] = result["home_files_more"]
    path = runs_dir / f"{task_id}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def cmd_check(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    _require_commits(repo_root)
    cfg = config.load_config(repo_root)
    sheet = _load_task(repo_root, args.id)

    if not sheet.base:
        raise AtbError(f"task {args.id} not started (base is empty); run 'atb start {args.id}' first")
    if not sheet.branch:
        raise AtbError(f"task {args.id} has no branch")

    worktree = config.resolve_worktree_root(cfg) / sheet.id

    committed = gitops.changed_files(sheet.base, sheet.branch, cwd=repo_root)
    uncommitted = gitops.uncommitted_files(worktree) if worktree.exists() else []
    all_paths = _dedup(committed + uncommitted)

    classification = scope.classify(all_paths, sheet.allow, sheet.deny)
    denied = [p for p in all_paths if classification[p] == "denied"]
    outside = [p for p in all_paths if classification[p] == "outside"]

    ahead, behind = gitops.rev_list_count(sheet.base, sheet.branch, cwd=repo_root)

    exit_code = 1 if (denied or outside) else 0

    test_result: dict | None = None
    if args.test:
        if not worktree.exists():
            raise AtbError(f"worktree {worktree} does not exist")
        command = sheet.test or cfg.test
        isolate = cfg.isolate_home if args.isolate_home is None else args.isolate_home
        if isolate:
            test_result, home_files = _run_test_isolated(
                command, worktree, cfg.test_timeout
            )
            test_result["home_writes"] = bool(home_files)
            test_result["home_files"] = home_files[:20]
            test_result["home_files_more"] = max(0, len(home_files) - 20)
        else:
            test_result = _run_test(command, worktree, cfg.test_timeout)
        _append_run(repo_root, sheet.id, command, test_result)
        if test_result["exit_code"] != 0:
            exit_code = 1
        if test_result.get("home_writes"):
            exit_code = 1

    if uncommitted and args.strict:
        exit_code = 1

    if args.json:
        payload: dict = {
            "id": sheet.id,
            "ahead": ahead,
            "behind": behind,
            "changed_files": len(all_paths),
            "denied": denied,
            "outside": outside,
            "uncommitted": uncommitted,
            "ok": exit_code == 0,
        }
        if test_result is not None:
            payload["test"] = test_result
            if "home_writes" in test_result:
                payload["home_writes"] = test_result["home_writes"]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"ahead: {ahead}")
        print(f"behind: {behind}")
        print(f"changed files: {len(all_paths)}")
        print("denied:")
        if denied:
            for path in denied:
                print(f"  - {path}")
        else:
            print("  (none)")
        print("outside:")
        if outside:
            for path in outside:
                print(f"  - {path}")
        else:
            print("  (none)")
        print("uncommitted:")
        if uncommitted:
            for path in uncommitted:
                print(f"  - {path}")
        else:
            print("  (none)")
        if test_result is not None:
            print(f"test: exit {test_result['exit_code']} in {test_result['duration']}s")
            if test_result.get("home_writes"):
                print("home: 测试写入了 HOME")
                for name in test_result.get("home_files", []):
                    print(f"  - {name}")
                more = test_result.get("home_files_more", 0)
                if more:
                    print(f"  (另有 {more} 个)")

    if uncommitted and not args.strict:
        print("warning: uncommitted changes present (use --strict to fail)", file=sys.stderr)

    return exit_code


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #

MAX_TITLE_WIDTH = 24


def _truncate_width(text: str, max_width: int) -> str:
    """Truncate ``text`` to ``max_width`` display columns, appending an ellipsis."""
    if _display_width(text) <= max_width:
        return text
    ellipsis = "…"
    limit = max_width - _display_width(ellipsis)
    result = ""
    width = 0
    for char in text:
        w = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        if width + w > limit:
            break
        result += char
        width += w
    return result + ellipsis


def format_idle(seconds: int) -> str:
    """Format an idle duration as ``45s`` / ``3m`` / ``2h`` / ``1d``."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _last_activity(
    worktree: Path, branch: str, repo_root: Path, uncommitted_paths: list[str],
    *, commit_ts: float | None = None,
) -> float:
    """Latest of: branch tip commit, uncommitted file mtimes, worktree creation.

    The worktree's ``.git`` file is written by ``git worktree add``, so a task
    that was just started does not inherit the age of its base commit.
    """
    if commit_ts is None:
        commit_ts = gitops.commit_time(branch, cwd=repo_root)
    activity = commit_ts
    latest = gitops.latest_mtime(worktree, uncommitted_paths)
    if latest is not None:
        activity = max(activity, latest)
    try:
        activity = max(activity, (worktree / ".git").stat().st_mtime)
    except OSError:
        pass
    return activity


def _branch_snapshot(cfg: config.Config, repo_root: Path, *, ahead_behind: bool):
    """One ``for-each-ref`` for every local branch (falls back on old git)."""
    if ahead_behind:
        try:
            return gitops.branch_snapshot(
                cfg.target, cwd=repo_root, ahead_behind=True
            )
        except gitops.GitError:
            pass
    return gitops.branch_snapshot(cfg.target, cwd=repo_root, ahead_behind=False)


_BLOCKER_ORDER = {
    "missing_branch": 0,
    "scope_violation": 1,
    "uncommitted": 2,
    "test_not_passed": 3,
    "merge_conflict": 4,
    "doctor_fail": 5,
}


def _mark_missing_row(
    row: dict, sheet: Sheet, *, reason: str, lang: str = "zh"
) -> dict:
    row["missing"] = True
    if sheet.status == "review":
        ready, blockers = render.evaluate_readiness(
            task_id=sheet.id,
            test_result=row["test"],
            missing_reason=reason,
            lang=lang,
        )
        row["ready"] = ready
        row["blockers"] = blockers
    return row


def _task_board_row(
    sheet: Sheet,
    cfg: config.Config,
    repo_root: Path,
    branches: dict | None = None,
    *,
    lang: str = "zh",
) -> dict:
    row: dict = {
        "id": sheet.id,
        "status": sheet.status,
        "agent": sheet.agent,
        "title": sheet.title,
        "branch": sheet.branch or "",
        "missing": False,
        "ahead": None,
        "behind": None,
        "changed_files": None,
        "violations": None,
        "test": checks.last_test_result(repo_root, sheet.id),
        "dirty": None,
        "idle_seconds": None,
        "ready": None,
        "blockers": [],
    }
    if sheet.status not in checks.ACTIVE_STATUSES:
        return row
    if not sheet.branch:
        return _mark_missing_row(row, sheet, reason="no branch set", lang=lang)
    worktree = checks.resolve_worktree(cfg, sheet)
    entry = branches.get(sheet.branch) if (branches is not None and sheet.branch) else None
    if branches is not None:
        if entry is None or not worktree.exists():
            return _mark_missing_row(
                row, sheet, reason="branch or worktree missing", lang=lang
            )
        commit_ts = entry["committer_time"]
    else:
        if not gitops.branch_exists(sheet.branch, cwd=repo_root) or not worktree.exists():
            return _mark_missing_row(
                row, sheet, reason="branch or worktree missing", lang=lang
            )
        commit_ts = None
    try:
        if branches is not None and "ahead" in entry:
            ahead, behind = entry["ahead"], entry["behind"]
        else:
            ahead, behind = gitops.rev_list_count(cfg.target, sheet.branch, cwd=repo_root)
        entries = gitops.uncommitted_entries(worktree)
        uncommitted_paths = [path for _status, paths in entries for path in paths]
        committed = gitops.changed_files(sheet.base, sheet.branch, cwd=repo_root)
        all_paths = list(dict.fromkeys(committed + uncommitted_paths))
        denied, outside = checks.classify_violations(all_paths, sheet.allow, sheet.deny)
        row["ahead"] = ahead
        row["behind"] = behind
        row["changed_files"] = len(all_paths)
        row["violations"] = len(denied) + len(outside)
        row["dirty"] = len(entries)
        activity = _last_activity(
            worktree, sheet.branch, repo_root, uncommitted_paths, commit_ts=commit_ts
        )
        row["idle_seconds"] = max(0, int(time.time() - activity))
        if sheet.status == "review":
            target_conflicts: list[str] = []
            target_merge_error: str | None = None
            doctor_fails: list[str] = []
            try:
                target_conflicts = gitops.merge_tree_conflicts(
                    cfg.target, sheet.branch, cwd=repo_root
                )
            except gitops.GitError as exc:
                target_merge_error = str(exc)
            try:
                actual_branch = gitops.current_branch(cwd=worktree)
                if actual_branch != sheet.branch:
                    doctor_fails.append(
                        f"[active] {sheet.id} worktree is on {actual_branch!r} instead of {sheet.branch!r}"
                    )
            except gitops.GitError as exc:
                doctor_fails.append(f"[active] {sheet.id} worktree error: {exc}")
            ready, blockers = render.evaluate_readiness(
                task_id=sheet.id,
                denied=denied,
                outside=outside,
                uncommitted=uncommitted_paths,
                test_result=row["test"],
                target=cfg.target,
                target_conflicts=target_conflicts,
                target_merge_error=target_merge_error,
                doctor_fails=doctor_fails,
                lang=lang,
            )
            row["ready"] = ready
            row["blockers"] = blockers
    except (gitops.GitError, OSError):
        return _mark_missing_row(
            row, sheet, reason="branch or worktree error", lang=lang
        )
    return row


def _build_board_rows(
    sheets: list[Sheet],
    cfg: config.Config,
    repo_root: Path,
    *,
    lang: str = "zh",
    conflicts: dict | None = None,
) -> tuple[list[dict], dict | None]:
    """Collect status/board rows and merge active-task conflicts into review readiness."""
    branches = _branch_snapshot(cfg, repo_root, ahead_behind=True)
    rows = [
        _task_board_row(sheet, cfg, repo_root, branches=branches, lang=lang)
        for sheet in sheets
    ]
    has_review = any(s.status == "review" for s in sheets)
    if conflicts is None and has_review:
        active = [s for s in sheets if s.status in checks.ACTIVE_STATUSES]
        conflicts = checks.compute_conflicts(active, cfg, repo_root)
    if conflicts and conflicts.get("conflicts") and has_review:
        for row in rows:
            if row["status"] != "review" or row["ready"] is None:
                continue
            task_conflicts, _, _ = _conflicts_for(row["id"], conflicts)
            if not task_conflicts:
                continue
            _, extra_blockers = render.evaluate_readiness(
                task_id=row["id"],
                test_result="ok",
                conflicts=task_conflicts,
                lang=lang,
            )
            existing_mc = next(
                (b for b in row["blockers"] if b["code"] == "merge_conflict"),
                None,
            )
            if existing_mc is not None and extra_blockers:
                sep = "；" if lang != "en" else "; "
                existing_mc["message"] = (
                    f"{existing_mc['message']}{sep}{extra_blockers[0]['message']}"
                )
            else:
                row["blockers"].extend(extra_blockers)
                row["blockers"].sort(
                    key=lambda b: _BLOCKER_ORDER.get(b["code"], 99)
                )
            row["ready"] = False
    return rows, conflicts


def _render_columns(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    columns = list(zip(headers, *rows)) if rows else list(zip(headers))
    widths = [max(_display_width(cell) for cell in col) for col in columns]

    def fmt(cells: tuple[str, ...]) -> str:
        return "  ".join(_pad(cells[i], widths[i]) for i in range(len(cells)))

    lines = [fmt(headers)]
    lines.extend(fmt(tuple(row)) for row in rows)
    return "\n".join(lines)


def _status_cells(row: dict) -> tuple[str, ...]:
    branch = "missing" if row["missing"] else (row["branch"] or "—")
    if row["ahead"] is None:
        ahead_behind = "—"
    else:
        ahead_behind = f"+{row['ahead']}/-{row['behind']}"
    files = str(row["changed_files"]) if row["changed_files"] is not None else "—"
    dirty = str(row["dirty"]) if row["dirty"] is not None else "—"
    violations = str(row["violations"]) if row["violations"] is not None else "—"
    idle = format_idle(row["idle_seconds"]) if row["idle_seconds"] is not None else "—"
    test = {"ok": "✓", "fail": "✗"}.get(row["test"], "—")
    ready_val = row.get("ready")
    if ready_val is True:
        ready = "✓"
    elif ready_val is False:
        blockers = row.get("blockers") or []
        first_code = blockers[0]["code"] if blockers else "blocked"
        ready = f"✗ {first_code}"
    else:
        ready = "—"
    return (
        row["id"],
        row["status"],
        row["agent"],
        _truncate_width(row["title"], MAX_TITLE_WIDTH),
        branch,
        ahead_behind,
        files,
        dirty,
        violations,
        idle,
        test,
        ready,
    )


def _format_upstream(info: dict, lang: str = "en") -> str:
    """One-line summary of the target branch vs its upstream (local refs only)."""
    target = info["target"]
    ahead, behind = info["ahead"], info["behind"]
    if lang == "zh":
        if info["upstream"] is None:
            return "上游：无上游"
        parts: list[str] = []
        if ahead:
            parts.append(f"{ahead} 个提交未推送")
        if behind:
            parts.append(f"落后 {behind} 个")
        if not parts:
            parts.append("已同步")
        return "上游：" + "，".join(parts)
    if info["upstream"] is None:
        return f"{target}: no upstream"
    parts = []
    if ahead:
        parts.append(f"{ahead} commits not pushed")
    if behind:
        parts.append(f"{behind} behind")
    if not parts:
        parts.append("up to date")
    return f"{target}: " + ", ".join(parts)


def cmd_status(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    cfg = config.load_config(repo_root)
    sheets = _load_tasks(repo_root)
    rows, _ = _build_board_rows(sheets, cfg, repo_root, lang="en")
    upstream = gitops.upstream_status(cfg.target, cwd=repo_root)

    summary = {status: 0 for status in STATUSES}
    for row in rows:
        if row["status"] in summary:
            summary[row["status"]] += 1

    if args.json:
        print(json.dumps(
            # keep the top-level keys stable; upstream rides under summary
            {"tasks": rows, "summary": {**summary, "upstream": upstream}},
            ensure_ascii=False, indent=2,
        ))
    else:
        print(_format_upstream(upstream))
        headers = ("ID", "STATUS", "AGENT", "TITLE", "BRANCH", "AHEAD/BEHIND", "FILES", "DIRTY", "VIOL", "IDLE", "TEST", "READY")
        print(_render_columns(headers, [_status_cells(row) for row in rows]))
        print("  ".join(f"{status}: {summary[status]}" for status in STATUSES))
    return 0


# --------------------------------------------------------------------------- #
# conflicts
# --------------------------------------------------------------------------- #

def _print_conflicts(result: dict) -> None:
    sections = (
        ("真实冲突:", result["conflicts"], lambda e: f"{e['a']} <-> {e['b']}: {', '.join(e['files'])}"),
        ("同文件但能自动合并:", result["mergeable"], lambda e: f"{e['a']} <-> {e['b']}: {', '.join(e['files'])}"),
        ("基线漂移:", result["drift"], lambda e: f"{e['task']}: {', '.join(e['files'])}"),
        ("无法判断:", result["errors"], lambda e: f"{' <-> '.join(e['tasks'])}: {e['message']}"),
    )
    for title, entries, fmt in sections:
        print(title)
        if not entries:
            print("  (none)")
            continue
        for entry in entries:
            print("  " + fmt(entry))


def cmd_conflicts(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    _require_commits(repo_root)
    cfg = config.load_config(repo_root)
    sheets = _load_tasks(repo_root)
    active = [s for s in sheets if s.status in checks.ACTIVE_STATUSES]
    result = checks.compute_conflicts(active, cfg, repo_root)

    if result["errors"]:
        exit_code = 2
    elif result["conflicts"]:
        exit_code = 1
    else:
        exit_code = 0

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_conflicts(result)
    return exit_code


# --------------------------------------------------------------------------- #
# review
# --------------------------------------------------------------------------- #

def _conflicts_for(sheet_id: str, result: dict) -> tuple[list, list, list]:
    conflicts = [e for e in result["conflicts"] if e["a"] == sheet_id or e["b"] == sheet_id]
    mergeable = [e for e in result["mergeable"] if e["a"] == sheet_id or e["b"] == sheet_id]
    drift = [e for e in result["drift"] if e["task"] == sheet_id]
    return conflicts, mergeable, drift


def _acceptance_text(body: str) -> str:
    """Return the ``## 验收`` section content without its heading line."""
    acceptance = extract_section(body, "## 验收")
    lines = acceptance.split("\n")
    if lines and (lines[0] == "## 验收" or lines[0].startswith("## 验收 ")):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _task_doctor_fails(repo_root: Path, task_id: str) -> list[str]:
    """Return doctor FAIL messages specifically mentioning ``task_id``."""
    doc_payload, _ = doctor.run_doctor(repo_root)
    pattern = re.compile(rf"\b{re.escape(task_id)}\b")
    fails: list[str] = []
    for item in doc_payload["checks"]:
        if item["status"] != "FAIL":
            continue
        for part in str(item["message"]).split("; "):
            if pattern.search(part):
                fails.append(f"[{item['name']}] {part}")
    return fails


def _review_info(sheet: Sheet, cfg: config.Config, repo_root: Path) -> dict:
    """Gather everything the review template needs into one plain dict."""
    committed = checks.task_committed(sheet, repo_root)
    uncommitted = checks.task_uncommitted(sheet, cfg)
    all_paths = list(dict.fromkeys(committed + uncommitted))
    denied, outside = checks.classify_violations(all_paths, sheet.allow, sheet.deny)
    other_active = [
        s for s in _load_tasks(repo_root)
        if s.status in checks.ACTIVE_STATUSES and s.id != sheet.id
    ]
    result = checks.compute_conflicts([sheet] + other_active, cfg, repo_root)
    conflicts, mergeable, drift = _conflicts_for(sheet.id, result)
    board_row = _task_board_row(sheet, cfg, repo_root)
    idle_seconds = board_row["idle_seconds"]
    idle_text = format_idle(idle_seconds) if idle_seconds is not None else "—"
    hist = history.collect_task_history(repo_root, cfg, sheet)
    status_events = [e for e in hist["events"] if e["kind"] == "status"][-10:]
    test_events = [e for e in hist["events"] if e["kind"] == "test"]
    last_test_detail = test_events[-1] if test_events else None
    return {
        "id": sheet.id,
        "title": sheet.title,
        "agent": sheet.agent,
        "status": sheet.status,
        "branch": sheet.branch,
        "base": sheet.base,
        "denied": denied,
        "outside": outside,
        "uncommitted": uncommitted,
        "test": checks.last_test_result(repo_root, sheet.id),
        "dirty": board_row["dirty"],
        "idle": idle_text,
        "idle_seconds": idle_seconds,
        "rework_count": hist["rework_count"],
        "last_test_detail": last_test_detail,
        "status_history": status_events,
        "doctor_fails": _task_doctor_fails(repo_root, sheet.id),
        "diffstat": gitops.diff_stat(sheet.base, sheet.branch, cwd=repo_root),
        "conflicts": conflicts,
        "mergeable": mergeable,
        "drift": drift,
        "acceptance": _acceptance_text(sheet.body),
    }


def _structured_diffstat(base: str, branch: str, repo_root: Path) -> dict:
    """Return structured per-file insertions/deletions and totals."""
    proc = subprocess.run(
        ["git", "diff", "--numstat", "-z", "-M", f"{base}...{branch}"],
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=gitops.git_env(),
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or "git diff --numstat failed"
        raise gitops.GitError(detail)

    tokens = proc.stdout.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    files: list[dict] = []
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        parts = entry.split("\t", 2)
        if len(parts) != 3:
            continue
        ins_raw, del_raw, path_part = parts
        binary = ins_raw == "-" or del_raw == "-"
        insertions = None if binary else int(ins_raw)
        deletions = None if binary else int(del_raw)
        if path_part == "" and i + 1 < len(tokens):
            old_path = tokens[i]
            new_path = tokens[i + 1]
            i += 2
            files.append({
                "path": new_path,
                "old_path": old_path,
                "insertions": insertions,
                "deletions": deletions,
                "binary": binary,
            })
        else:
            files.append({
                "path": path_part,
                "insertions": insertions,
                "deletions": deletions,
                "binary": binary,
            })
    return {
        "files": files,
        "totals": {
            "files": len(files),
            "insertions": sum(f["insertions"] or 0 for f in files),
            "deletions": sum(f["deletions"] or 0 for f in files),
        },
    }


def _build_review_json(
    sheet: Sheet, cfg: config.Config, repo_root: Path, lang: str = "zh"
) -> str:
    info = _review_info(sheet, cfg, repo_root)
    info["target"] = cfg.target
    info["structured_diffstat"] = _structured_diffstat(
        sheet.base, sheet.branch, repo_root
    )
    try:
        info["target_conflicts"] = gitops.merge_tree_conflicts(
            cfg.target, sheet.branch, cwd=repo_root
        )
        info["target_merge_error"] = None
    except gitops.GitError as exc:
        info["target_conflicts"] = []
        info["target_merge_error"] = str(exc)
    payload = render.build_review_json(info, lang=lang)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)


def _build_review_markdown(
    sheet: Sheet, cfg: config.Config, repo_root: Path, lang: str = "zh"
) -> str:
    return render.render_review(_review_info(sheet, cfg, repo_root), lang=lang)


def cmd_review(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    _require_commits(repo_root)
    cfg = config.load_config(repo_root)
    sheet = _load_task(repo_root, args.id)
    if sheet.status not in ("doing", "review", "rework"):
        raise AtbError(
            f"task {args.id} has status {sheet.status!r}; "
            "only 'doing', 'review' or 'rework' can be reviewed"
        )
    if not sheet.base or not sheet.branch:
        raise AtbError(f"task {args.id} not started (no base/branch); run 'atb start' first")

    # Build the review first so a failure leaves the status untouched; flip the
    # in-memory status so the sheet header shows the post-review state.
    changed = sheet.status in ("doing", "rework")
    if changed:
        sheet.status = "review"

    if args.json:
        output_text = _build_review_json(sheet, cfg, repo_root, lang=args.lang)
    else:
        output_text = _build_review_markdown(sheet, cfg, repo_root, lang=args.lang)

    if changed:
        path = _tasks_dir(repo_root) / f"{sheet.id}.md"
        path.write_text(dump(sheet), encoding="utf-8")

    if args.out:
        Path(args.out).write_text(output_text + "\n", encoding="utf-8")
        print(f"review written to {args.out}")
    else:
        print(output_text)
    return 0


# --------------------------------------------------------------------------- #
# rework
# --------------------------------------------------------------------------- #

def cmd_rework(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    sheet = _load_task(repo_root, args.id)
    if sheet.status not in ("doing", "review", "rework"):
        raise AtbError(
            f"task {args.id} has status {sheet.status!r}; "
            "only doing/review/rework can be sent to rework"
        )

    note = args.note
    if note.startswith("@"):
        note_path = Path(note[1:])
        if not note_path.exists():
            raise AtbError(f"note file {note_path} not found")
        note = note_path.read_text(encoding="utf-8").rstrip("\n")

    timestamp = datetime.now(timezone.utc).isoformat()
    sheet.status = "rework"
    sheet.body = append_rework_note(sheet.body, note, timestamp)
    path = _tasks_dir(repo_root) / f"{sheet.id}.md"
    path.write_text(dump(sheet), encoding="utf-8")
    print(f"rework {sheet.id}: note appended")
    return 0


# --------------------------------------------------------------------------- #
# done
# --------------------------------------------------------------------------- #

def _write_status(repo_root: Path, sheet: Sheet, status: str) -> None:
    sheet.status = status
    path = _tasks_dir(repo_root) / f"{sheet.id}.md"
    path.write_text(dump(sheet), encoding="utf-8")


def _done_blockers(sheet: Sheet, cfg: config.Config, repo_root: Path) -> list[str]:
    """Return the reasons ``done`` must refuse, mirroring ``check`` plus tests."""
    try:
        committed = gitops.changed_files(sheet.base, sheet.branch, cwd=repo_root)
    except gitops.GitError as exc:
        return [f"cannot read branch changes ({exc})"]
    worktree = checks.resolve_worktree(cfg, sheet)
    uncommitted = gitops.uncommitted_files(worktree) if worktree.exists() else []
    all_paths = list(dict.fromkeys(committed + uncommitted))
    denied, outside = checks.classify_violations(all_paths, sheet.allow, sheet.deny)
    blockers: list[str] = []
    if denied or outside:
        blockers.append("scope violations: " + ", ".join(denied + outside))
    if uncommitted:
        blockers.append("uncommitted changes: " + ", ".join(uncommitted))
    if checks.last_test_result(repo_root, sheet.id) != "ok":
        blockers.append("last test run did not pass (or no record)")
    return blockers


def cmd_done(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    _require_commits(repo_root)
    cfg = config.load_config(repo_root)
    sheet = _load_task(repo_root, args.id)

    if args.clean and not args.merge:
        raise AtbError("--clean can only be used together with --merge")
    if args.push and not args.merge:
        raise AtbError("--push can only be used together with --merge")

    if sheet.status == "review":
        pass
    elif sheet.status == "doing" and args.force:
        print(
            f"warning: task {args.id} is still 'doing'; continuing because of --force",
            file=sys.stderr,
        )
    else:
        hint = ""
        if sheet.status in ("doing", "rework"):
            hint = f"; run 'atb review {args.id}' first"
        raise AtbError(
            f"task {args.id} has status {sheet.status!r}; "
            f"only 'review' can be done (--force allows 'doing'){hint}"
        )

    if not sheet.base or not sheet.branch:
        raise AtbError(f"task {args.id} not started (no base/branch)")

    blockers = _done_blockers(sheet, cfg, repo_root)
    if blockers:
        raise CheckError("cannot mark done: " + "; ".join(blockers))

    if not args.merge:
        _write_status(repo_root, sheet, "done")
        print(f"marked {sheet.id} done; merge branch {sheet.branch!r} yourself (never pushed)")
        print(f"task sheet updated; commit it yourself (not auto-committed)")
        return 0

    # --merge preconditions
    if not gitops.is_main_worktree(repo_root):
        raise AtbError("--merge must be run from the main worktree")
    current = gitops.current_branch(cwd=repo_root)
    if current != cfg.target:
        raise AtbError(
            f"--merge requires the current branch to be {cfg.target!r} "
            f"(currently on {current!r})"
        )

    target_head = gitops.rev_parse(cfg.target, cwd=repo_root)
    merge_files = gitops.changed_files(target_head, sheet.branch, cwd=repo_root)
    dirty = gitops.uncommitted_files(repo_root)
    overlap = sorted(set(merge_files) & set(dirty))
    if overlap:
        raise AtbError(
            "merge would overwrite uncommitted files: " + ", ".join(overlap)
        )

    message = cfg.merge_message.format(
        branch=sheet.branch, id=sheet.id, title=sheet.title
    )
    proc = gitops.merge_no_ff(sheet.branch, message, cwd=repo_root)
    if proc.returncode != 0:
        gitops.merge_abort(cwd=repo_root)
        raise CheckError(
            f"merge of {sheet.branch!r} into {cfg.target!r} failed (conflict?); aborted"
        )

    # Push only when explicitly requested, or by config default unless --no-push.
    # Never forces; a rejected push keeps the merge and tells the user what to do.
    push_enabled = args.push or (
        not args.no_push and bool(cfg.extra.get("push_after_done", False))
    )
    pushed = False
    if push_enabled:
        upstream = gitops.upstream_name(cfg.target, cwd=repo_root)
        remote = upstream.split("/", 1)[0] if upstream else "origin"
        if not gitops.remote_exists(remote, cwd=repo_root):
            raise AtbError(
                f"cannot push {cfg.target!r}: remote {remote!r} does not exist; "
                "merge kept — push manually"
            )
        push_proc = gitops.push(remote, cfg.target, cwd=repo_root)
        if push_proc.returncode != 0:
            detail = push_proc.stderr.strip() or "push rejected"
            raise CheckError(
                f"push of {cfg.target!r} to {remote!r} failed; merge kept — "
                f"run 'git pull --rebase' then push again, or handle manually "
                f"({detail})"
            )
        pushed = True

    if args.clean:
        worktree = checks.resolve_worktree(cfg, sheet)
        gitops.worktree_remove(worktree, cwd=repo_root)  # refuses when dirty
        gitops.branch_delete(sheet.branch, cwd=repo_root)  # -d only

    _write_status(repo_root, sheet, "done")
    print(f"merged {sheet.branch!r} into {cfg.target} with --no-ff")
    if pushed:
        print(f"pushed {cfg.target!r}; marked {sheet.id} done")
    else:
        print(f"marked {sheet.id} done; task sheet updated — commit it yourself (not pushed)")
    return 0


# --------------------------------------------------------------------------- #
# board
# --------------------------------------------------------------------------- #

def cmd_board(args: argparse.Namespace) -> int:
    if args.stall < 1:
        raise AtbError("--stall must be at least 1 minute")
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    cfg = config.load_config(repo_root)
    sheets = _load_tasks(repo_root)
    active = [s for s in sheets if s.status in checks.ACTIVE_STATUSES]
    conflicts = checks.compute_conflicts(active, cfg, repo_root)
    rows, _ = _build_board_rows(
        sheets, cfg, repo_root, lang=args.lang, conflicts=conflicts
    )
    for row in rows:
        row["idle_text"] = (
            format_idle(row["idle_seconds"])
            if row["idle_seconds"] is not None
            else "—"
        )

    summary = {status: 0 for status in STATUSES}
    for row in rows:
        if row["status"] in summary:
            summary[row["status"]] += 1

    upstream_text = _format_upstream(
        gitops.upstream_status(cfg.target, cwd=repo_root), lang=args.lang
    )

    html_text = render.render_board_html(
        rows, summary, conflicts, stall_minutes=args.stall, lang=args.lang,
        upstream_text=upstream_text,
    )
    Path(args.html).write_text(html_text, encoding="utf-8")
    print(f"board written to {args.html}")
    return 0


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #

DISPATCH_STATUSES = ("todo", "doing", "rework")


def _dispatch_message(repo_root: Path, sheet: Sheet, lang: str) -> str:
    repo = str(repo_root)
    if lang == "en":
        if sheet.status == "rework":
            return (
                f"{sheet.id} was sent back. In {repo}, run "
                f"`PYTHONPATH=src python3 -m agent_taskboard prompt {sheet.id}`; "
                f'the "Rework" section at the top is what to change. '
                f"Reply in English; do not push or merge when done."
            )
        if sheet.status == "todo":
            return (
                f"In {repo}, first run `PYTHONPATH=src python3 -m agent_taskboard "
                f"start {sheet.id}`, then run `PYTHONPATH=src python3 -m "
                f"agent_taskboard prompt {sheet.id}` and follow the handoff. "
                f"Reply in English; do not push or merge when done."
            )
        return (
            f"In {repo}, run `PYTHONPATH=src python3 -m agent_taskboard prompt "
            f"{sheet.id}` and follow the handoff. Reply in English; do not push "
            f"or merge when done."
        )
    if sheet.status == "rework":
        return (
            f'{sheet.id} 被打回了，请在 {repo} 运行 '
            f'PYTHONPATH=src python3 -m agent_taskboard prompt {sheet.id}，'
            f'最前面的"返工"小节就是要改的内容。全程中文，完成后不要 push、不要合并。'
        )
    if sheet.status == "todo":
        return (
            f"请在 {repo} 先运行 PYTHONPATH=src python3 -m agent_taskboard start "
            f"{sheet.id}，再运行 PYTHONPATH=src python3 -m agent_taskboard prompt "
            f"{sheet.id}，按交接文本执行。全程中文，完成后不要 push、不要合并。"
        )
    return (
        f"请在 {repo} 运行 PYTHONPATH=src python3 -m agent_taskboard prompt "
        f"{sheet.id}，按交接文本执行。全程中文，完成后不要 push、不要合并。"
    )


def cmd_dispatch(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    sheets = _load_tasks(repo_root)
    active = [s for s in sheets if s.status in DISPATCH_STATUSES]

    grouped: dict[str, list[dict]] = {}
    for sheet in active:
        group_name = sheet.window or sheet.agent or "(unknown)"
        grouped.setdefault(group_name, []).append({
            "id": sheet.id,
            "title": sheet.title,
            "agent": sheet.agent,
            "status": sheet.status,
            "window": sheet.window,
            "message": _dispatch_message(repo_root, sheet, args.lang),
        })

    groups = [{"name": name, "tasks": grouped[name]} for name in sorted(grouped)]
    project = repo_root.name

    if args.json:
        print(json.dumps(
            {"project": project, "groups": groups}, ensure_ascii=False, indent=2
        ))
    else:
        print(render.render_dispatch(project, groups, lang=args.lang))
    return 0


# --------------------------------------------------------------------------- #
# gc
# --------------------------------------------------------------------------- #

def cmd_gc(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    _require_commits(repo_root)
    cfg = config.load_config(repo_root)
    for task_id in args.ids:
        _load_task(repo_root, task_id)
    sheets = _load_tasks(repo_root)
    result, exit_code = gc.run_gc(
        repo_root,
        cfg,
        sheets,
        task_ids=args.ids or None,
        apply=args.apply,
        include_unmerged=args.include_unmerged,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(gc.render_gc(result))
    return exit_code


# --------------------------------------------------------------------------- #
# watch
# --------------------------------------------------------------------------- #

WATCH_STALL_STATUSES = ("doing", "rework")


def _watch_activity(cfg, sheet, repo_root, tip, commit_ts=None):
    """Return ``(dirty, idle_seconds)`` for a task, or ``(None, None)``."""
    if tip is None:
        return None, None
    worktree = checks.resolve_worktree(cfg, sheet)
    if not worktree.exists():
        return None, None
    try:
        entries = gitops.uncommitted_entries(worktree)
        paths = [path for _status, ps in entries for path in ps]
        activity = _last_activity(
            worktree, sheet.branch, repo_root, paths, commit_ts=commit_ts
        )
        return len(entries), max(0, int(time.time() - activity))
    except (gitops.GitError, OSError):
        return None, None


def _watch_scan(
    repo_root: Path, cfg, *, use_snapshot: bool = True
) -> tuple[dict[str, dict], set[str]]:
    """Snapshot each task plus the ids whose sheets failed to parse.

    Reading only refs and task sheets; never fetches or writes anything.
    """
    snapshot: dict[str, dict] = {}
    failed: set[str] = set()
    tasks_dir = _tasks_dir(repo_root)
    if not tasks_dir.is_dir():
        return snapshot, failed
    branches = (
        _branch_snapshot(cfg, repo_root, ahead_behind=False) if use_snapshot else None
    )
    for path in sorted(tasks_dir.glob("*.md")):
        task_id = path.stem
        try:
            sheet = parse(path.read_text(encoding="utf-8"))
        except (AtbError, UnicodeDecodeError, OSError):
            failed.add(task_id)
            continue
        if branches is not None:
            entry = branches.get(sheet.branch) if sheet.branch else None
            tip = entry["tip"] if entry else None
            commit_ts = entry["committer_time"] if entry else None
        else:
            tip = None
            commit_ts = None
            if sheet.branch and gitops.branch_exists(sheet.branch, cwd=repo_root):
                try:
                    tip = gitops.rev_parse(sheet.branch, cwd=repo_root)
                except gitops.GitError:
                    tip = None
        dirty, idle = _watch_activity(cfg, sheet, repo_root, tip, commit_ts=commit_ts)
        snapshot[task_id] = {
            "status": sheet.status,
            "tip": tip,
            "dirty": dirty,
            "idle": idle,
        }
    return snapshot, failed


def _change(task_id, kind, text, *, source=None, target=None):
    return {"id": task_id, "kind": kind, "from": source, "to": target, "text": text}


def _stall_changes(snapshot: dict, stall_seconds, reported: set[str]) -> list[dict]:
    if stall_seconds is None:
        return []
    changes: list[dict] = []
    for task_id in sorted(snapshot):
        state = snapshot[task_id]
        if task_id in reported:
            continue
        if state["status"] not in WATCH_STALL_STATUSES:
            continue
        if state["idle"] is None or state["idle"] < stall_seconds:
            continue
        dirty = state["dirty"] if state["dirty"] is not None else 0
        minutes = stall_seconds / 60
        changes.append(_change(
            task_id, "stall",
            f"{task_id} 已 {format_idle(state['idle'])} 无活动"
            f"（阈值 {minutes:g} 分钟，未提交 {dirty} 个文件）",
            target=state["idle"],
        ))
        reported.add(task_id)
    return changes


def _watch_changes(
    old: dict, new: dict, repo_root: Path, *,
    stall_seconds, activity: bool, reported_stall: set[str],
    known_ids: set[str] | None = None,
) -> list[dict]:
    changes: list[dict] = []
    for task_id in sorted(set(new) - set(old) - (known_ids or set())):
        changes.append(_change(
            task_id, "new_task", f"{task_id} 新增任务",
            target=new[task_id]["status"],
        ))
    for task_id in sorted(set(old) & set(new)):
        before, after = old[task_id], new[task_id]
        if before["status"] != after["status"]:
            changes.append(_change(
                task_id, "status",
                f"{task_id} {before['status']} → {after['status']}",
                source=before["status"], target=after["status"],
            ))
        if before["tip"] and after["tip"] and before["tip"] != after["tip"]:
            try:
                fast_forward = (
                    gitops.merge_base(before["tip"], after["tip"], cwd=repo_root)
                    == before["tip"]
                )
            except gitops.GitError:
                fast_forward = False
            if fast_forward:
                try:
                    count = gitops.rev_list_count(
                        before["tip"], after["tip"], cwd=repo_root
                    )[0]
                except gitops.GitError:
                    count = 0
                if count > 0:
                    changes.append(_change(
                        task_id, "commits",
                        f"{task_id} 新提交 {count} 个（{after['tip'][:7]}）",
                        source=before["tip"], target=after["tip"],
                    ))
            else:
                changes.append(_change(
                    task_id, "branch_rewritten",
                    f"{task_id} 分支被改写（{before['tip'][:7]} → {after['tip'][:7]}）",
                    source=before["tip"], target=after["tip"],
                ))
        elif before["tip"] and not after["tip"]:
            changes.append(_change(
                task_id, "branch_deleted", f"{task_id} 分支已删除",
                source=before["tip"],
            ))
        if (
            activity
            and before["dirty"] is not None
            and after["dirty"] is not None
            and before["dirty"] != after["dirty"]
        ):
            changes.append(_change(
                task_id, "activity",
                f"{task_id} 未提交改动 {before['dirty']} → {after['dirty']}",
                source=before["dirty"], target=after["dirty"],
            ))
    changes.extend(_stall_changes(new, stall_seconds, reported_stall))
    return changes


def _watch_report(args: argparse.Namespace, changes: list[dict]) -> int:
    if args.json:
        print(json.dumps(
            {"changed": True, "changes": changes, "timeout": False},
            ensure_ascii=False, indent=2,
        ))
    else:
        for change in changes:
            print(change["text"])
    return 0


def _watch_timeout(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps(
            {"changed": False, "changes": [], "timeout": True},
            ensure_ascii=False, indent=2,
        ))
    else:
        print("无变化")
    return 3


def cmd_watch(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    cfg = config.load_config(repo_root)
    interval = args.interval
    stall_seconds = args.stall * 60 if args.stall is not None else None
    deadline = time.monotonic() + args.timeout if args.timeout is not None else None

    baseline, failed = _watch_scan(repo_root, cfg)
    failure_counts = {task_id: 1 for task_id in failed}
    reported_stall: set[str] = set()
    reported_error: set[str] = set()
    # Sheets that exist at start (even if unparsable then) are not "new" later.
    known_ids: set[str] = set(baseline) | set(failed)

    # Report tasks that are already stalled when the watch starts (immediately).
    immediate = _stall_changes(baseline, stall_seconds, reported_stall)
    if immediate:
        return _watch_report(args, immediate)

    previous = baseline
    while True:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return _watch_timeout(args)
            wait = min(interval, remaining)
        else:
            wait = interval
        time.sleep(wait)

        current, failed = _watch_scan(repo_root, cfg)
        # A sheet that fails to parse this round keeps its last known state,
        # so it is neither dropped nor later re-reported as a new task.
        for task_id in failed:
            if task_id in previous:
                current[task_id] = previous[task_id]
        for task_id in list(failure_counts):
            if task_id not in failed:
                failure_counts.pop(task_id, None)
        for task_id in failed:
            failure_counts[task_id] = failure_counts.get(task_id, 0) + 1

        changes = _watch_changes(
            previous, current, repo_root,
            stall_seconds=stall_seconds,
            activity=args.activity,
            reported_stall=reported_stall,
            known_ids=known_ids,
        )
        known_ids.update(current)
        for task_id in sorted(failed):
            if failure_counts.get(task_id, 0) >= 3 and task_id not in reported_error:
                changes.append(_change(
                    task_id, "sheet_error", f"{task_id} 任务单无法解析",
                ))
                reported_error.add(task_id)

        if changes:
            return _watch_report(args, changes)
        previous = current
        if deadline is not None and time.monotonic() >= deadline:
            return _watch_timeout(args)


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #

def cmd_doctor(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    result, exit_code = doctor.run_doctor(repo_root)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(doctor.render_doctor(result))
    return exit_code


# --------------------------------------------------------------------------- #
# log
# --------------------------------------------------------------------------- #

def cmd_log(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    _require_commits(repo_root)
    cfg = config.load_config(repo_root)
    sheet = _load_task(repo_root, args.id)
    payload = history.collect_task_history(
        repo_root, cfg, sheet, verbose=args.verbose
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(history.render_task_history(payload, verbose=args.verbose))
    return 0


# --------------------------------------------------------------------------- #
# stats
# --------------------------------------------------------------------------- #

def _parse_since(value: str) -> float:
    """Turn ``YYYY-MM-DD`` into a unix timestamp at local midnight."""
    try:
        naive = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise AtbError(
            f"invalid --since {value!r}: expected YYYY-MM-DD"
        ) from exc
    return naive.astimezone().timestamp()


def cmd_stats(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    sheets = _load_tasks(repo_root)
    since = _parse_since(args.since) if args.since else None
    summary = stats.summarize(
        repo_root, sheets, since=since, agent=args.agent
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(stats.render_stats(summary, lang=args.lang))
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atb",
        description="agent-taskboard: hand off and review parallel AI coding tasks.",
    )
    parser.add_argument(
        "--version", action="version", version=f"atb {get_version()}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="create .taskboard/ and default config")
    p_init.add_argument("--json", action="store_true")
    p_init.set_defaults(func=cmd_init)

    p_new = sub.add_parser("new", help="create a task sheet")
    p_new.add_argument("title", help="task title")
    p_new.add_argument("--agent", required=True, help="agent name (opencode/claude/...)")
    p_new.add_argument("--allow", action="append", default=[], help="allowed glob (repeatable)")
    p_new.add_argument("--deny", action="append", default=[], help="denied glob (repeatable)")
    p_new.add_argument("--depends", action="append", default=[], help="dependency task id (repeatable)")
    p_new.add_argument(
        "--body",
        metavar="@FILE|-",
        help="body text; @FILE reads a UTF-8 file, - reads stdin (default: template)",
    )
    p_new.add_argument(
        "--copy-to",
        metavar="DIR",
        help="copy the finished sheet to DIR/ATB_TASK_<ID>.md (DIR must exist)",
    )
    p_new.add_argument("--json", action="store_true")
    p_new.set_defaults(func=cmd_new)

    p_edit = sub.add_parser("edit", help="edit a task sheet without hand-editing TOML")
    p_edit.add_argument("id", help="task id, e.g. T001")
    p_edit.add_argument(
        "--body",
        metavar="@FILE|-",
        help="replace the body; @FILE reads a UTF-8 file, - reads stdin",
    )
    p_edit.add_argument("--title", help="replace the title")
    p_edit.add_argument("--agent", help="replace the agent name")
    p_edit.add_argument(
        "--add-allow", action="append", default=[], metavar="GLOB",
        help="add an allowed glob (repeatable)",
    )
    p_edit.add_argument(
        "--remove-allow", action="append", default=[], metavar="GLOB",
        help="remove an allowed glob (repeatable)",
    )
    p_edit.add_argument(
        "--add-deny", action="append", default=[], metavar="GLOB",
        help="add a denied glob (repeatable)",
    )
    p_edit.add_argument(
        "--remove-deny", action="append", default=[], metavar="GLOB",
        help="remove a denied glob (repeatable)",
    )
    p_edit.add_argument(
        "--copy-to",
        metavar="DIR",
        help="copy the finished sheet to DIR/ATB_TASK_<ID>.md (DIR must exist)",
    )
    p_edit.set_defaults(func=cmd_edit)

    p_list = sub.add_parser("list", help="list task sheets")
    p_list.add_argument("--status", help="only show tasks with this status")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="show one task sheet")
    p_show.add_argument("id", help="task id, e.g. T001")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)

    p_start = sub.add_parser("start", help="validate deps and open a worktree for a task")
    p_start.add_argument("id", help="task id, e.g. T001")
    p_start.add_argument("--base", help="baseline commit for the worktree")
    p_start.add_argument("--force", action="store_true", help="skip the dependency check")
    p_start.add_argument("--json", action="store_true")
    p_start.set_defaults(func=cmd_start)

    p_prompt = sub.add_parser("prompt", help="print the agent handoff prompt")
    p_prompt.add_argument("id", help="task id, e.g. T001")
    p_prompt.add_argument("--lang", choices=["zh", "en"], default="zh", help="prompt language")
    p_prompt.add_argument("--json", action="store_true")
    p_prompt.set_defaults(func=cmd_prompt)

    p_check = sub.add_parser("check", help="scope-check a task's changes")
    p_check.add_argument("id", help="task id, e.g. T001")
    p_check.add_argument("--test", action="store_true", help="run the test command in the worktree")
    p_check.add_argument("--strict", action="store_true", help="fail on uncommitted changes too")
    p_check.add_argument(
        "--isolate-home",
        action="store_true",
        default=None,
        help="run tests with a throwaway HOME and flag files they write there",
    )
    p_check.add_argument("--json", action="store_true")
    p_check.set_defaults(func=cmd_check)

    p_status = sub.add_parser("status", help="show the task board")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=cmd_status)

    p_conflicts = sub.add_parser("conflicts", help="detect cross-task conflicts and drift")
    p_conflicts.add_argument("--json", action="store_true")
    p_conflicts.set_defaults(func=cmd_conflicts)

    p_review = sub.add_parser("review", help="generate a review sheet for a task")
    p_review.add_argument("id", help="task id, e.g. T001")
    p_review.add_argument("--out", help="write the review to FILE instead of stdout")
    p_review.add_argument("--lang", choices=["zh", "en"], default="zh", help="review language")
    p_review.add_argument("--json", action="store_true", help="print a structured JSON review with ready/blockers")
    p_review.set_defaults(func=cmd_review)

    p_rework = sub.add_parser("rework", help="send a task back for rework")
    p_rework.add_argument("id", help="task id, e.g. T001")
    p_rework.add_argument("--note", required=True, help="rework note, or @file to read from a file")
    p_rework.set_defaults(func=cmd_rework)

    p_done = sub.add_parser("done", help="verify and finish a task")
    p_done.add_argument("id", help="task id, e.g. T001")
    p_done.add_argument("--merge", action="store_true", help="merge the branch into target with --no-ff")
    p_done.add_argument("--clean", action="store_true", help="remove worktree and branch after merging (needs --merge)")
    p_done.add_argument("--force", action="store_true", help="allow finishing a 'doing' task (never skips scope checks)")
    push_group = p_done.add_mutually_exclusive_group()
    push_group.add_argument("--push", action="store_true", help="push the target branch after merging (needs --merge)")
    push_group.add_argument("--no-push", action="store_true", help="do not push even if push_after_done is enabled")
    p_done.set_defaults(func=cmd_done)

    p_board = sub.add_parser("board", help="write a standalone static HTML board")
    p_board.add_argument("--html", required=True, metavar="FILE", help="output HTML file")
    p_board.add_argument(
        "--stall",
        type=int,
        default=120,
        metavar="MINUTES",
        help="highlight doing/rework tasks idle >= MINUTES (default: 120)",
    )
    p_board.add_argument("--lang", choices=["zh", "en"], default="zh", help="board language")
    p_board.set_defaults(func=cmd_board)

    p_dispatch = sub.add_parser("dispatch", help="print a per-window dispatch checklist")
    p_dispatch.add_argument("--json", action="store_true")
    p_dispatch.add_argument("--lang", choices=["zh", "en"], default="zh", help="checklist language")
    p_dispatch.set_defaults(func=cmd_dispatch)

    p_gc = sub.add_parser("gc", help="clean up leftover worktrees and branches for done/dropped tasks")
    p_gc.add_argument("ids", nargs="*", help="optional task ids to inspect/clean (e.g. T001 T003)")
    p_gc.add_argument("--apply", action="store_true", help="actually remove eligible worktrees/branches")
    p_gc.add_argument("--include-unmerged", action="store_true", help="remove clean worktrees for unmerged branches (branches are still kept)")
    p_gc.add_argument("--json", action="store_true")
    p_gc.set_defaults(func=cmd_gc)

    p_watch = sub.add_parser("watch", help="wait until a task changes, then exit")
    p_watch.add_argument("--interval", type=float, default=30.0, help="poll interval in seconds")
    p_watch.add_argument("--timeout", type=float, default=None, help="give up after SECONDS (exit 3)")
    p_watch.add_argument("--stall", type=float, default=None, help="alert when a doing/rework task has been idle for MINUTES")
    p_watch.add_argument("--activity", action="store_true", help="alert when a task's uncommitted file count changes")
    p_watch.add_argument("--json", action="store_true")
    p_watch.set_defaults(func=cmd_watch)

    p_doctor = sub.add_parser("doctor", help="run a one-shot read-only health check")
    p_doctor.add_argument("--json", action="store_true")
    p_doctor.set_defaults(func=cmd_doctor)
    p_log = sub.add_parser("log", help="show chronological history for a task")
    p_log.add_argument("id", help="task id, e.g. T001")
    p_log.add_argument("--json", action="store_true")
    p_log.add_argument("--verbose", action="store_true", help="include the last 5 lines of each test run output")
    p_log.set_defaults(func=cmd_log)

    p_stats = sub.add_parser(
        "stats", help="per-agent throughput, rework and cycle time"
    )
    p_stats.add_argument(
        "--since", metavar="YYYY-MM-DD",
        help="only count tasks with recorded activity on or after this date",
    )
    p_stats.add_argument("--agent", help="only show this agent")
    p_stats.add_argument("--json", action="store_true")
    p_stats.add_argument("--lang", choices=["zh", "en"], default="zh", help="output language")
    p_stats.set_defaults(func=cmd_stats)

    return parser


def _force_utf8_stdio() -> None:
    """Make stdout/stderr UTF-8 so non-ASCII output works everywhere.

    On Windows the default encoding for redirected streams is the ANSI code
    page (cp1252 on many hosts); printing Chinese task titles or arrows then
    raises ``UnicodeEncodeError``.  atb intentionally emits UTF-8 everywhere.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except AtbError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"atb: error: {exc}", file=sys.stderr)
        return exc.exit_code
