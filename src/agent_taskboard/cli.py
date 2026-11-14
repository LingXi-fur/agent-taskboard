"""Command-line interface: ``atb init/new/list/show/start/prompt/check``.

This module only wires arguments to output; the parsing/scope/git logic lives
in ``config.py``, ``sheet.py``, ``scope.py``, ``gitops.py`` and ``prompts.py``.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from . import checks, config, gitops, prompts, render, scope
from .errors import AtbError, CheckError, ConfigError, SheetError
from .sheet import (
    STATUSES,
    Sheet,
    TEMPLATE_BODY,
    append_rework_note,
    dump,
    extract_section,
    next_id,
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


def _load_tasks(repo_root: Path, status: str | None = None) -> list[Sheet]:
    tasks_dir = _tasks_dir(repo_root)
    sheets: list[Sheet] = []
    if not tasks_dir.is_dir():
        return sheets
    for path in sorted(tasks_dir.glob("*.md")):
        try:
            sheet = parse(path.read_text(encoding="utf-8"))
        except AtbError:
            continue  # skip sheets that do not parse
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


def cmd_new(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    cfg = config.load_config(repo_root)
    tasks_dir = _tasks_dir(repo_root)
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
        body=TEMPLATE_BODY,
    )
    path = tasks_dir / f"{task_id}.md"
    path.write_text(dump(sheet), encoding="utf-8")

    if args.json:
        print(json.dumps({
            "id": task_id,
            "path": str(path),
            "title": args.title,
        }, ensure_ascii=False))
    else:
        print(str(path))
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


def _run_test(command: str, worktree: Path, timeout: int) -> dict:
    argv = shlex.split(command)
    start = time.monotonic()
    if not argv:
        return {"exit_code": 2, "duration": 0.0, "output": "(empty test command)"}
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(worktree),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
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
        test_result = _run_test(command, worktree, cfg.test_timeout)
        _append_run(repo_root, sheet.id, command, test_result)
        if test_result["exit_code"] != 0:
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


def _task_board_row(sheet: Sheet, cfg: config.Config, repo_root: Path) -> dict:
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
    }
    if sheet.status not in checks.ACTIVE_STATUSES:
        return row
    if not sheet.branch:
        row["missing"] = True
        return row
    worktree = checks.resolve_worktree(cfg, sheet)
    if not gitops.branch_exists(sheet.branch, cwd=repo_root) or not worktree.exists():
        row["missing"] = True
        return row
    try:
        ahead, behind = gitops.rev_list_count(cfg.target, sheet.branch, cwd=repo_root)
        paths = checks.task_changed_paths(sheet, cfg, repo_root)
        denied, outside = checks.classify_violations(paths, sheet.allow, sheet.deny)
        row["ahead"] = ahead
        row["behind"] = behind
        row["changed_files"] = len(paths)
        row["violations"] = len(denied) + len(outside)
    except gitops.GitError:
        row["missing"] = True
    return row


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
    violations = str(row["violations"]) if row["violations"] is not None else "—"
    test = {"ok": "✓", "fail": "✗"}.get(row["test"], "—")
    return (
        row["id"],
        row["status"],
        row["agent"],
        _truncate_width(row["title"], MAX_TITLE_WIDTH),
        branch,
        ahead_behind,
        files,
        violations,
        test,
    )


def cmd_status(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    cfg = config.load_config(repo_root)
    sheets = _load_tasks(repo_root)
    rows = [_task_board_row(sheet, cfg, repo_root) for sheet in sheets]

    summary = {status: 0 for status in STATUSES}
    for row in rows:
        if row["status"] in summary:
            summary[row["status"]] += 1

    if args.json:
        print(json.dumps({"tasks": rows, "summary": summary}, ensure_ascii=False, indent=2))
    else:
        headers = ("ID", "STATUS", "AGENT", "TITLE", "BRANCH", "AHEAD/BEHIND", "FILES", "VIOL", "TEST")
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
        "diffstat": gitops.diff_stat(sheet.base, sheet.branch, cwd=repo_root),
        "conflicts": conflicts,
        "mergeable": mergeable,
        "drift": drift,
        "acceptance": _acceptance_text(sheet.body),
    }


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
    if not sheet.base or not sheet.branch:
        raise AtbError(f"task {args.id} not started (no base/branch); run 'atb start' first")

    # Build the review first so a failure leaves the status untouched; flip the
    # in-memory status so the sheet header shows the post-review state.
    changed = sheet.status == "doing"
    if changed:
        sheet.status = "review"

    markdown = _build_review_markdown(sheet, cfg, repo_root, lang=args.lang)

    if changed:
        path = _tasks_dir(repo_root) / f"{sheet.id}.md"
        path.write_text(dump(sheet), encoding="utf-8")

    if args.out:
        Path(args.out).write_text(markdown + "\n", encoding="utf-8")
        print(f"review written to {args.out}")
    else:
        print(markdown)
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

    if sheet.status == "review":
        pass
    elif sheet.status == "doing" and args.force:
        print(
            f"warning: task {args.id} is still 'doing'; continuing because of --force",
            file=sys.stderr,
        )
    else:
        raise AtbError(
            f"task {args.id} has status {sheet.status!r}; "
            "only 'review' can be done (--force allows 'doing')"
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

    if args.clean:
        worktree = checks.resolve_worktree(cfg, sheet)
        gitops.worktree_remove(worktree, cwd=repo_root)  # refuses when dirty
        gitops.branch_delete(sheet.branch, cwd=repo_root)  # -d only

    _write_status(repo_root, sheet, "done")
    print(f"merged {sheet.branch!r} into {cfg.target} with --no-ff")
    print(f"marked {sheet.id} done; task sheet updated — commit it yourself (never pushed)")
    return 0


# --------------------------------------------------------------------------- #
# board
# --------------------------------------------------------------------------- #

def cmd_board(args: argparse.Namespace) -> int:
    repo_root = config.find_repo_root()
    _require_initialized(repo_root)
    cfg = config.load_config(repo_root)
    sheets = _load_tasks(repo_root)
    rows = [_task_board_row(sheet, cfg, repo_root) for sheet in sheets]

    summary = {status: 0 for status in STATUSES}
    for row in rows:
        if row["status"] in summary:
            summary[row["status"]] += 1

    active = [s for s in sheets if s.status in checks.ACTIVE_STATUSES]
    conflicts = checks.compute_conflicts(active, cfg, repo_root)

    html_text = render.render_board_html(rows, summary, conflicts)
    Path(args.html).write_text(html_text, encoding="utf-8")
    print(f"board written to {args.html}")
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
    p_new.add_argument("--json", action="store_true")
    p_new.set_defaults(func=cmd_new)

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
    p_done.set_defaults(func=cmd_done)

    p_board = sub.add_parser("board", help="write a standalone static HTML board")
    p_board.add_argument("--html", required=True, metavar="FILE", help="output HTML file")
    p_board.set_defaults(func=cmd_board)

    return parser


def main(argv: list[str] | None = None) -> int:
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
