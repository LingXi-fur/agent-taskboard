"""Correctness and git-call-count tests for the batched status/watch path."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_taskboard import cli, config, gitops
from agent_taskboard import sheet as sheet_mod

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
FIXED_NOW = 1_800_000_000.0


class PerfStatusTests(unittest.TestCase):
    def _env(self, base: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(base)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=str(cwd), env=self._env(cwd),
            capture_output=True, text=True, check=True,
        ).stdout

    def _setup(self, base: Path, *, active: int = 3, todos: int = 2):
        repo = base / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "config", "user.name", "t")
        self._git(repo, "config", "user.email", "t@t")
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text("line\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base")
        base_sha = self._git(repo, "rev-parse", "HEAD").strip()
        (repo / ".taskboard" / "tasks").mkdir(parents=True)
        (repo / ".taskboard" / "config.toml").write_text(
            'worktree_root = "../wts"\n', encoding="utf-8"
        )

        for i in range(1, active + 1):
            tid = f"T{i:03d}"
            branch = f"b{i}"
            wt = base / "wts" / tid
            self._git(repo, "worktree", "add", "-q", "-b", branch, str(wt), "main")
            (wt / "src" / "app.py").write_text(f"line{i}\n", encoding="utf-8")
            self._git(wt, "add", "-A")
            self._git(wt, "commit", "-qm", f"work {i}")
            (wt / "src" / f"dirty_{i}.py").write_text("d\n", encoding="utf-8")
            sheet = sheet_mod.Sheet(
                id=tid, title=f"Active {i}", agent="opencode", status="doing",
                base=base_sha, branch=branch, allow=["src/**"],
                body="\n## 验收\n\n- [ ] x\n",
            )
            (repo / ".taskboard" / "tasks" / f"{tid}.md").write_text(
                sheet_mod.dump(sheet), encoding="utf-8"
            )

        for i in range(active + 1, active + todos + 1):
            tid = f"T{i:03d}"
            sheet = sheet_mod.Sheet(
                id=tid, title=f"Todo {i}", agent="opencode", status="todo",
                body="\n## 验收\n\n- [ ] x\n",
            )
            (repo / ".taskboard" / "tasks" / f"{tid}.md").write_text(
                sheet_mod.dump(sheet), encoding="utf-8"
            )

        return repo, base_sha

    def _count_calls(self, fn):
        original = gitops._run
        counter = {"n": 0}

        def counting(*args, **kwargs):
            counter["n"] += 1
            return original(*args, **kwargs)

        gitops._run = counting
        try:
            fn()
        finally:
            gitops._run = original
        return counter["n"]

    def _context(self, repo):
        cfg = config.load_config(repo)
        sheets = cli._load_tasks(repo)
        return cfg, sheets

    def test_batched_rows_match_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, _ = self._setup(base)
            cfg, sheets = self._context(repo)

            with mock.patch("agent_taskboard.cli.time.time", return_value=FIXED_NOW):
                branches = cli._branch_snapshot(cfg, repo, ahead_behind=True)
                batched = [
                    cli._task_board_row(s, cfg, repo, branches=branches)
                    for s in sheets
                ]
                legacy = [cli._task_board_row(s, cfg, repo) for s in sheets]

            self.assertEqual(batched, legacy)

    def test_batched_watch_matches_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, _ = self._setup(base)
            cfg, _ = self._context(repo)

            with mock.patch("agent_taskboard.cli.time.time", return_value=FIXED_NOW):
                batched, _ = cli._watch_scan(repo, cfg, use_snapshot=True)
                legacy, _ = cli._watch_scan(repo, cfg, use_snapshot=False)

            self.assertEqual(batched, legacy)

    def test_board_html_identical(self):
        from agent_taskboard import render

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, _ = self._setup(base)
            cfg, sheets = self._context(repo)

            with mock.patch("agent_taskboard.cli.time.time", return_value=FIXED_NOW):
                branches = cli._branch_snapshot(cfg, repo, ahead_behind=True)
                batched = [
                    cli._task_board_row(s, cfg, repo, branches=branches)
                    for s in sheets
                ]
                legacy = [cli._task_board_row(s, cfg, repo) for s in sheets]

            for rows in (batched, legacy):
                for row in rows:
                    row["idle_text"] = (
                        cli.format_idle(row["idle_seconds"])
                        if row["idle_seconds"] is not None
                        else "—"
                    )
            summary = {status: 0 for status in sheet_mod.STATUSES}
            empty = {"conflicts": [], "mergeable": [], "drift": [], "errors": []}
            fixed_at = "2026-01-01 00:00"
            html_batched = render.render_board_html(
                batched, summary, empty, generated_at=fixed_at
            )
            html_legacy = render.render_board_html(
                legacy, summary, empty, generated_at=fixed_at
            )
            self.assertEqual(html_batched, html_legacy)

    def test_snapshot_matches_per_task_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, _ = self._setup(base)
            cfg, sheets = self._context(repo)
            branches = cli._branch_snapshot(cfg, repo, ahead_behind=True)

            for sheet in sheets:
                if not sheet.branch:
                    continue
                entry = branches[sheet.branch]
                self.assertEqual(entry["tip"], gitops.rev_parse(sheet.branch, cwd=repo))
                self.assertEqual(
                    entry["committer_time"], gitops.commit_time(sheet.branch, cwd=repo)
                )
                self.assertEqual(
                    (entry["ahead"], entry["behind"]),
                    gitops.rev_list_count(cfg.target, sheet.branch, cwd=repo),
                )

    def test_status_git_calls_at_least_halved(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, _ = self._setup(base, active=4, todos=3)
            cfg, sheets = self._context(repo)

            def legacy():
                for sheet in sheets:
                    cli._task_board_row(sheet, cfg, repo)

            def batched():
                branches = cli._branch_snapshot(cfg, repo, ahead_behind=True)
                for sheet in sheets:
                    cli._task_board_row(sheet, cfg, repo, branches=branches)

            n_legacy = self._count_calls(legacy)
            n_batched = self._count_calls(batched)
            self.assertGreater(n_legacy, 0)
            self.assertLessEqual(
                n_batched * 2, n_legacy, f"batched={n_batched} legacy={n_legacy}"
            )

    def test_watch_git_calls_at_least_halved(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, _ = self._setup(base, active=4, todos=3)
            cfg, _ = self._context(repo)

            n_legacy = self._count_calls(
                lambda: cli._watch_scan(repo, cfg, use_snapshot=False)
            )
            n_batched = self._count_calls(
                lambda: cli._watch_scan(repo, cfg, use_snapshot=True)
            )
            self.assertGreater(n_legacy, 0)
            self.assertLessEqual(
                n_batched * 2, n_legacy, f"batched={n_batched} legacy={n_legacy}"
            )


if __name__ == "__main__":
    unittest.main()
