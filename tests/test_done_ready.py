"""Tests for the ``done --merge`` readiness gate (same inputs as review --json)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_taskboard import sheet as sheet_mod

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

BASE_CONTENT = "line1\nline2\nline3\n"


class DoneReadyTests(unittest.TestCase):
    def _env(self, base: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["HOME"] = str(base)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _git(self, cwd: Path, *args: str, check: bool = True):
        return subprocess.run(
            ["git", *args], cwd=str(cwd), env=self._env(cwd),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=check,
        )

    def _atb(self, repo: Path, *args: str):
        return subprocess.run(
            [sys.executable, "-m", "agent_taskboard", *args],
            cwd=str(repo), env=self._env(repo), capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )

    def _setup(self, base: Path, *, config: str = 'worktree_root = "../wts"\n') -> Path:
        repo = base / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "config", "user.name", "t")
        self._git(repo, "config", "user.email", "t@t")
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text(BASE_CONTENT, encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base")
        (repo / ".taskboard" / "tasks").mkdir(parents=True)
        (repo / ".taskboard" / "config.toml").write_text(config, encoding="utf-8")
        return repo

    def _write_sheet(self, repo: Path, *, test: str = "python3 -c 'pass'", deny=()):
        sheet = sheet_mod.Sheet(
            id="T001", title="Finish me", agent="opencode", status="todo",
            allow=["src/**"], deny=list(deny), test=test,
            body="\n## 背景\n\n## 要做的事\n\n## 验收\n\n- [ ] item\n",
        )
        (repo / ".taskboard" / "tasks" / "T001.md").write_text(
            sheet_mod.dump(sheet), encoding="utf-8"
        )

    def _write_run(self, repo: Path, exit_code: int, tid: str = "T001") -> None:
        runs = repo / ".taskboard" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": "2026-11-19T00:00:00+00:00",
            "exit_code": exit_code,
            "duration": 0.1,
            "command": "python3 -c 'pass'",
            "output": "",
        }
        with (runs / f"{tid}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _start_and_commit(self, base: Path, repo: Path) -> Path:
        self.assertEqual(self._atb(repo, "start", "T001").returncode, 0)
        wt = base / "wts" / "T001"
        (wt / "src" / "app.py").write_text("line1\nline2\nT001\n", encoding="utf-8")
        self._git(wt, "add", "-A")
        self._git(wt, "commit", "-qm", "agent work")
        return wt

    def _in_review(
        self, base: Path, repo: Path, *, run_exit_code: int | None = 0
    ) -> Path:
        """T001 in review with one commit; ``run_exit_code=None`` skips the record."""
        self._write_sheet(repo)
        wt = self._start_and_commit(base, repo)
        if run_exit_code is not None:
            self._write_run(repo, run_exit_code)
        self.assertEqual(self._atb(repo, "review", "T001").returncode, 0)
        return wt

    def _head(self, repo: Path) -> str:
        return self._git(repo, "rev-parse", "HEAD").stdout.strip()

    def _status(self, repo: Path) -> str:
        return sheet_mod.parse(
            (repo / ".taskboard" / "tasks" / "T001.md").read_text(encoding="utf-8")
        ).status

    # ------------------------------------------------------------------ #
    # each blocker refuses the merge and changes nothing
    # ------------------------------------------------------------------ #

    def test_merge_rejects_scope_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            (repo / "src" / "core.py").write_text("v1\n", encoding="utf-8")
            self._git(repo, "add", "-A")
            self._git(repo, "commit", "-qm", "add core")
            self._write_sheet(repo, deny=("src/core.py",))
            wt = self._start_and_commit(base, repo)
            (wt / "src" / "core.py").write_text("v2\n", encoding="utf-8")
            self._git(wt, "add", "-A")
            self._git(wt, "commit", "-qm", "touch core")
            self._write_run(repo, 0)
            self.assertEqual(self._atb(repo, "review", "T001").returncode, 0)
            before = self._head(repo)

            result = self._atb(repo, "done", "T001", "--merge")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("[scope_violation]", result.stderr)
            self.assertEqual(self._head(repo), before)
            self.assertEqual(self._status(repo), "review")

    def test_merge_rejects_uncommitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            wt = self._in_review(base, repo)
            before = self._head(repo)
            (wt / "src" / "app.py").write_text("uncommitted\n", encoding="utf-8")

            result = self._atb(repo, "done", "T001", "--merge")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("[uncommitted]", result.stderr)
            self.assertEqual(self._head(repo), before)
            self.assertEqual(self._status(repo), "review")

    def test_merge_rejects_missing_test_record_with_config_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            # no test command in the sheet and no run record at all
            self._write_sheet(repo, test="")
            self._start_and_commit(base, repo)
            self.assertEqual(self._atb(repo, "review", "T001").returncode, 0)
            before = self._head(repo)

            result = self._atb(repo, "done", "T001", "--merge")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("[test_not_passed]", result.stderr)
            self.assertIn("atb check T001 --test", result.stderr)
            self.assertIn("test 字段为空", result.stderr)
            self.assertEqual(self._head(repo), before)
            self.assertEqual(self._status(repo), "review")

    def test_merge_rejects_failed_test_without_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo)  # test command is configured
            self._start_and_commit(base, repo)
            self._write_run(repo, 3)
            self.assertEqual(self._atb(repo, "review", "T001").returncode, 0)

            result = self._atb(repo, "done", "T001", "--merge")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("[test_not_passed]", result.stderr)
            # the "configure a test" hint only applies when no test is set up
            self.assertNotIn("test 字段为空", result.stderr)

    def test_merge_rejects_conflict_with_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo)
            self._start_and_commit(base, repo)
            self._write_run(repo, 0)
            self.assertEqual(self._atb(repo, "review", "T001").returncode, 0)
            # the target moves the same line after the task branched off
            (repo / "src" / "app.py").write_text("line1\nMAIN\nline3\n", encoding="utf-8")
            self._git(repo, "add", "-A")
            self._git(repo, "commit", "-qm", "main edit")
            before = self._head(repo)

            result = self._atb(repo, "done", "T001", "--merge")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("[merge_conflict]", result.stderr)
            self.assertEqual(self._head(repo), before)
            self.assertEqual(self._status(repo), "review")

    def test_merge_rejects_doctor_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            wt = self._in_review(base, repo)
            before = self._head(repo)
            # doctor FAILs when the worktree is checked out on another branch
            self._git(wt, "checkout", "-q", "-b", "stray-branch")

            result = self._atb(repo, "done", "T001", "--merge")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("[doctor_fail]", result.stderr)
            self.assertEqual(self._head(repo), before)
            self.assertEqual(self._status(repo), "review")

    # ------------------------------------------------------------------ #
    # --force, require_ready_for_done and the happy path
    # ------------------------------------------------------------------ #

    def test_force_merges_and_lists_ignored_blockers(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            wt = self._in_review(base, repo, run_exit_code=None)
            before = self._head(repo)
            # two blockers: uncommitted files and no test record
            (wt / "src" / "app.py").write_text("uncommitted\n", encoding="utf-8")

            result = self._atb(repo, "done", "T001", "--merge", "--force")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("已忽略的 blocker", result.stderr)
            self.assertIn("[uncommitted]", result.stderr)
            self.assertIn("[test_not_passed]", result.stderr)
            self.assertNotEqual(self._head(repo), before)
            self.assertEqual(self._status(repo), "done")

    def test_require_ready_false_only_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(
                base,
                config='worktree_root = "../wts"\nrequire_ready_for_done = false\n',
            )
            self._in_review(base, repo, run_exit_code=None)
            before = self._head(repo)

            # no test record at all: old behaviour, warn but merge anyway
            result = self._atb(repo, "done", "T001", "--merge")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("require_ready_for_done = false", result.stderr)
            self.assertIn("[test_not_passed]", result.stderr)
            self.assertNotEqual(self._head(repo), before)
            self.assertEqual(self._status(repo), "done")

    def test_merge_allows_overlap_with_other_active_task(self):
        """Two tasks on the same file: neither conflicts with main, so the
        first merge goes through and the overlap is only a notice."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._in_review(base, repo)  # T001 edits line 3 of src/app.py

            sheet2 = sheet_mod.Sheet(
                id="T002", title="Other", agent="opencode", status="todo",
                allow=["src/**"], test="python3 -c 'pass'",
                body="\n## 背景\n\nx\n",
            )
            (repo / ".taskboard" / "tasks" / "T002.md").write_text(
                sheet_mod.dump(sheet2), encoding="utf-8"
            )
            self.assertEqual(self._atb(repo, "start", "T002").returncode, 0)
            wt2 = base / "wts" / "T002"
            (wt2 / "src" / "app.py").write_text(
                "line1\nline2\nT002\n", encoding="utf-8"
            )
            self._git(wt2, "add", "-A")
            self._git(wt2, "commit", "-qm", "t2 work")
            self._write_run(repo, 0, "T002")
            self.assertEqual(self._atb(repo, "review", "T002").returncode, 0)

            result = self._atb(repo, "done", "T001", "--merge")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("merged", result.stdout)
            self.assertIn("T002", result.stderr)
            self.assertIn("不阻止合并", result.stderr)
            self.assertEqual(self._status(repo), "done")

    def test_ready_task_merges_normally(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo)
            self._start_and_commit(base, repo)
            # run the real test command so the last run record is a pass
            self.assertEqual(
                self._atb(repo, "check", "T001", "--test").returncode, 0
            )
            self.assertEqual(self._atb(repo, "review", "T001").returncode, 0)

            result = self._atb(repo, "done", "T001", "--merge")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("merged", result.stdout)
            self.assertEqual(self._status(repo), "done")

    def test_force_still_requires_review_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self._setup(base)
            self._write_sheet(repo)
            self._start_and_commit(base, repo)
            # still 'doing' with no test record: --force covers both meanings
            result = self._atb(repo, "done", "T001", "--merge", "--force")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("still 'doing'", result.stderr)
            self.assertIn("已忽略的 blocker", result.stderr)
            self.assertIn("[test_not_passed]", result.stderr)
            self.assertEqual(self._status(repo), "done")


if __name__ == "__main__":
    unittest.main()
