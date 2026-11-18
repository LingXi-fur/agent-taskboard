#!/usr/bin/env python3
"""Benchmark ``atb status`` / ``board`` / ``watch`` data collection at scale.

Builds a throwaway git repository with ``--tasks`` task sheets, of which
``--active`` are ``doing`` tasks that each own a worktree, one commit and one
uncommitted file.  Then it measures wall time and the number of git
subprocesses for the legacy per-task path versus the batched
``for-each-ref`` path.

    python3 scripts/bench_status.py
    python3 scripts/bench_status.py --tasks 400 --active 40
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_taskboard import checks, cli, config, gitops, render  # noqa: E402
from agent_taskboard import sheet as sheet_mod  # noqa: E402


def _env(base: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["HOME"] = str(base)
    return env


def _git(cwd: Path, env: dict[str, str], *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), env=env, check=True,
        capture_output=True, text=True,
    ).stdout


def _body() -> str:
    return "\n## 验收\n\n- [ ] benchmark task\n"


def build(base: Path, tasks: int, active: int) -> Path:
    repo = base / "repo"
    repo.mkdir()
    env = _env(base)
    _git(repo, env, "init", "-q", "-b", "main")
    _git(repo, env, "config", "user.name", "bench")
    _git(repo, env, "config", "user.email", "bench@example.invalid")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("line\n", encoding="utf-8")
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-qm", "base")
    base_sha = _git(repo, env, "rev-parse", "HEAD").strip()
    (repo / ".taskboard" / "tasks").mkdir(parents=True)
    (repo / ".taskboard" / "config.toml").write_text(
        'worktree_root = "../wts"\n', encoding="utf-8"
    )

    for i in range(1, active + 1):
        task_id = f"T{i:03d}"
        branch = f"b{i}"
        worktree = base / "wts" / task_id
        _git(repo, env, "worktree", "add", "-q", "-b", branch, str(worktree), "main")
        (worktree / "src" / "app.py").write_text(f"line{i}\n", encoding="utf-8")
        _git(worktree, env, "add", "-A")
        _git(worktree, env, "commit", "-qm", f"work {i}")
        (worktree / "src" / f"dirty_{i}.py").write_text("d\n", encoding="utf-8")
        sheet = sheet_mod.Sheet(
            id=task_id, title=f"Active {i}", agent="opencode", status="doing",
            base=base_sha, branch=branch, allow=["src/**"], body=_body(),
        )
        (repo / ".taskboard" / "tasks" / f"{task_id}.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )

    for i in range(active + 1, tasks + 1):
        task_id = f"T{i:03d}"
        sheet = sheet_mod.Sheet(
            id=task_id, title=f"Todo {i}", agent="opencode", status="todo",
            body=_body(),
        )
        (repo / ".taskboard" / "tasks" / f"{task_id}.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )
    return repo


def measure(fn) -> tuple[float, int]:
    """Return ``(elapsed_ms, git_subprocesses)`` for one call of ``fn``."""
    original = gitops._run
    counter = {"n": 0}

    def counting(*args, **kwargs):
        counter["n"] += 1
        return original(*args, **kwargs)

    gitops._run = counting
    start = time.perf_counter()
    try:
        fn()
    finally:
        elapsed = (time.perf_counter() - start) * 1000.0
        gitops._run = original
    return elapsed, counter["n"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=200)
    parser.add_argument("--active", type=int, default=20)
    parser.add_argument(
        "--with-conflicts", action="store_true",
        help="include atb conflicts in the board metric (slower, unchanged code)",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = build(base, args.tasks, args.active)
        cfg = config.load_config(repo)
        sheets = cli._load_tasks(repo)
        active = [s for s in sheets if s.status in checks.ACTIVE_STATUSES]
        summary = {status: 0 for status in sheet_mod.STATUSES}
        empty = {"conflicts": [], "mergeable": [], "drift": [], "errors": []}

        def conflicts():
            return checks.compute_conflicts(active, cfg, repo) if args.with_conflicts else empty

        def rows(legacy: bool):
            if legacy:
                return [cli._task_board_row(s, cfg, repo) for s in sheets]
            branches = cli._branch_snapshot(cfg, repo, ahead_behind=True)
            return [cli._task_board_row(s, cfg, repo, branches=branches) for s in sheets]

        def board(legacy: bool):
            built = rows(legacy)
            for row in built:
                row["idle_text"] = (
                    cli.format_idle(row["idle_seconds"])
                    if row["idle_seconds"] is not None
                    else "—"
                )
            render.render_board_html(built, summary, conflicts(), generated_at="bench")

        cases = {
            "status": (lambda: rows(True), lambda: rows(False)),
            "watch": (
                lambda: cli._watch_scan(repo, cfg, use_snapshot=False),
                lambda: cli._watch_scan(repo, cfg, use_snapshot=True),
            ),
            "board": (lambda: board(True), lambda: board(False)),
        }

        print(f"tasks={args.tasks} active={args.active} with_conflicts={args.with_conflicts}")
        print(f"{'metric':8} {'legacy_ms':>10} {'batched_ms':>11} "
              f"{'legacy_git':>11} {'batched_git':>12} {'git_reduction':>14}")
        for name, (legacy_fn, batched_fn) in cases.items():
            measure(legacy_fn)
            measure(batched_fn)  # warm caches
            legacy_ms, legacy_calls = measure(legacy_fn)
            batched_ms, batched_calls = measure(batched_fn)
            reduction = (1 - batched_calls / legacy_calls) * 100 if legacy_calls else 0
            print(f"{name:8} {legacy_ms:10.1f} {batched_ms:11.1f} "
                  f"{legacy_calls:11d} {batched_calls:12d} {reduction:13.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
