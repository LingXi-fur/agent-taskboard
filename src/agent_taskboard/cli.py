"""Command-line interface: ``atb init/new/list/show``.

This module only wires arguments to output; the parsing/scope logic lives in
``config.py``, ``sheet.py`` and ``scope.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .errors import AtbError
from .sheet import Sheet, TEMPLATE_BODY, dump, next_id, parse, slugify, to_dict

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
# Commands
# --------------------------------------------------------------------------- #

def _tasks_dir(repo_root: Path) -> Path:
    return repo_root / ".taskboard" / "tasks"


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
    path = _tasks_dir(repo_root) / f"{args.id}.md"
    if not path.exists():
        raise AtbError(f"task {args.id!r} not found")
    sheet = parse(path.read_text(encoding="utf-8"))

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
# Entry point
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atb",
        description="agent-taskboard: hand off and review parallel AI coding tasks.",
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
