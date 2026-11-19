#!/usr/bin/env python3
"""Benchmark ``atb stats`` data collection at scale.

Builds a throwaway git repository with ``--tasks`` task sheets whose status
transitions (todo → doing → review → done) are committed in four batch
commits, plus one test-run record per done task, then measures the wall time
of ``stats.summarize``. The acceptance for ``atb stats`` is 200 tasks in under
0.5 seconds.

    python3 scripts/bench_stats.py
    python3 scripts/bench_stats.py --tasks 400
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

from agent_taskboard import stats  # noqa: E402
from agent_taskboard import sheet as sheet_mod  # noqa: E402

T0 = 1_780_000_000  # fixed epoch so cycle times are deterministic


def _env(base: Path, ts: int | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["HOME"] = str(base)
    if ts is not None:
        env["GIT_COMMITTER_DATE"] = f"{ts} +0000"
        env["GIT_AUTHOR_DATE"] = f"{ts} +0000"
    return env


def _git(cwd: Path, env: dict[str, str], *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), env=env, check=True, capture_output=True
    )


def build(base: Path, tasks: int) -> Path:
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

    tasks_dir = repo / ".taskboard" / "tasks"
    tasks_dir.mkdir(parents=True)
    (repo / ".taskboard" / "config.toml").write_text(
        'worktree_root = "../wts"\n', encoding="utf-8"
    )
    runs_dir = repo / ".taskboard" / "runs"
    runs_dir.mkdir(parents=True)

    def stage(status: str, offset: int) -> None:
        for i in range(1, tasks + 1):
            task_id = f"T{i:03d}"
            agent = "opencode" if i % 2 else "gemini"
            body = (
                "\n## 背景\n\nx\n\n## 返工\n\n"
                "- [2026-05-29T10:00:00+00:00] fix it\n"
                if i % 3 == 0
                else "\n## 背景\n\nx\n"
            )
            sheet = sheet_mod.Sheet(
                id=task_id, title=f"Task {i}", agent=agent, status=status,
                allow=["src/**"], body=body,
            )
            (tasks_dir / f"{task_id}.md").write_text(
                sheet_mod.dump(sheet), encoding="utf-8"
            )
            if status == "done":
                record = {
                    "timestamp": "2026-05-29T12:00:00+00:00",
                    "exit_code": 0 if i % 5 else 1,
                    "duration": 0.1,
                    "command": "python3 -c 'pass'",
                    "output": "",
                }
                with (runs_dir / f"{task_id}.jsonl").open(
                    "a", encoding="utf-8"
                ) as handle:
                    handle.write(__import__("json").dumps(record) + "\n")
        _git(repo, env, "add", ".taskboard")
        _git(
            repo, _env(base, ts=T0 + offset),
            "commit", "-qm", f"batch {status}",
        )

    stage("todo", 0)
    stage("doing", 60)
    stage("review", 3600)
    stage("done", 7200)
    return repo


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=200)
    args = parser.parse_args()

    import shutil

    # mkdtemp + ignore_errors: git's background maintenance can still touch
    # .git while TemporaryDirectory's strict cleanup runs on macOS.
    tmp = Path(tempfile.mkdtemp(prefix="atb-bench-stats-"))
    try:
        repo = build(tmp, args.tasks)
        sheets = [
            sheet_mod.parse(
                (repo / ".taskboard" / "tasks" / f"T{i:03d}.md").read_text(
                    encoding="utf-8"
                )
            )
            for i in range(1, args.tasks + 1)
        ]
        start = time.perf_counter()
        summary = stats.summarize(repo, sheets)
        elapsed = time.perf_counter() - start
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for agent in summary["agents"]:
        print(
            f"{agent['agent']}: tasks={agent['tasks']} done={agent['done']} "
            f"cycle_median={agent['cycle_median_seconds']}s "
            f"missing={agent['missing_history']}"
        )
    print(f"tasks: {args.tasks}")
    print(f"stats.summarize: {elapsed * 1000:.0f} ms")
    if elapsed >= 0.5:
        print("FAILED: slower than the 0.5 s budget")
        return 1
    print("OK: within the 0.5 s budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
